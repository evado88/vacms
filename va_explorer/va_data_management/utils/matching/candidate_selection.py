"""Candidate Death selection for a Verbal Autopsy (v2).

Two stages, both driven by the per-run :class:`DeathIndex`:

* Stage 1 — deterministic keys, in precedence order: VASA ``DeathID``
  (format-validated), CMS ``Event`` bridge, NRC (valid + unique), death
  registration number (inert until E_DEATH carries one), exact composite
  (name token-set + sex + exact DOD + district). Every deterministic hit is
  still scored so a contradiction guard can route it to review.
* Stage 2 — union blocking (recall-oriented; missing a block loses a true
  match): EA, constituency+ward, district+death-year, phonetic surname +
  district, name-initials+year, DOD ±window regardless of geography, and a
  province block for facility VAs. Per-block candidate counts are reported.

Reviewer-rejected (VA, Death) pairs are excluded everywhere.
"""

from django.core.exceptions import ObjectDoesNotExist

from va_explorer.va_data_management.utils.death_ids import is_valid_death_id

from . import normalization as N

# Narrowest blocks first: when the global cap truncates, precise blocks win.
BLOCK_ORDER = [
    "ea",
    "constituency_ward",
    "district_year",
    "phonetic_surname_district",
    "initials_year",
    "dod_window",
    "province",
]
# Global cap on the scored candidate set (union of capped blocks, deduped).
MAX_TOTAL_CANDIDATES = 300


def _model_name(va) -> str:
    return va._meta.model_name  # "vasa" | "verbalautopsy"


def vasa_death_id(va):
    """Return the normalised DeathID carried on a VASA's social autopsy, or None."""
    if _model_name(va) != "vasa":
        return None
    try:
        sa = va.social_autopsy
    except ObjectDoesNotExist:
        return None
    if sa is None:
        return None
    return N.normalize_death_id(getattr(sa, "DeathID", None))


def rejected_pks(va) -> set:
    from va_explorer.va_data_management.models import VADeathMatchRejection

    return VADeathMatchRejection.rejected_death_pks(va)


def deterministic_candidate(va, profile, index, rejected=frozenset()):
    """Return ``(death, method)`` from the strongest deterministic key.

    Precedence: death_id -> event_bridge -> nrc -> death_reg_no ->
    exact_composite. Reviewer-rejected pairs never match deterministically —
    the rule is skipped and the next one tried. Non-unique NRC/reg-no hits
    return nothing here (the scorer will surface them for review via blocking).
    """
    from va_explorer.va_data_management.models import Death, MatchMethod

    model = _model_name(va)

    # 1. VASA DeathID (format-validated) — the interviewer-entered exact key.
    if model == "vasa":
        death_id = vasa_death_id(va)
        if death_id and is_valid_death_id(death_id):
            pk = index.by_death_id.get(death_id)
            death = index.death(pk) if pk is not None else None
            if death is None:
                # The bounded single-VA index may not contain it; ask the DB.
                death = (
                    Death.objects.filter(death_id__iexact=death_id)
                    .order_by("pk")
                    .first()
                )
                if death is not None:
                    index.add(death)
            if death is not None and death.pk not in rejected:
                return death, MatchMethod.EXACT_DEATH_ID

    # 2. VerbalAutopsy: the CMS Event bridges va -> death on one row.
    if model == "verbalautopsy":
        event = (
            va.events.exclude(death__isnull=True)
            .select_related("death")
            .order_by("-id")
            .first()
        )
        if (
            event is not None
            and event.death is not None
            and event.death.pk not in rejected
        ):
            if index.death(event.death.pk) is None:
                index.add(event.death)
            return index.death(event.death.pk), MatchMethod.EVENT_BRIDGE

    # 3. NRC — valid, non-empty and unique in the register.
    if profile.nrc:
        pks = [p for p in index.by_nrc.get(profile.nrc, []) if p not in rejected]
        if len(pks) == 1:
            return index.death(pks[0]), MatchMethod.NRC

    # 4. Death registration number (no E_DEATH source field yet — inert).
    if profile.death_reg_no:
        key = profile.death_reg_no.upper()
        pks = [p for p in index.by_reg_no.get(key, []) if p not in rejected]
        if len(pks) == 1:
            return index.death(pks[0]), MatchMethod.DEATH_REG_NO

    # 5. Exact composite: name token-set equality AND sex AND exact DOD AND
    #    same district (geo term skipped for facility VAs, which have none).
    #    Like NRC, the hit must be unique — duplicate register entries fall
    #    through to probabilistic scoring, which routes ties to review.
    if profile.name_tokens and profile.sex and profile.dod_precision == "day":
        hits = []
        for pk in index.by_name_key.get(profile.name_tokens, []):
            if pk in rejected:
                continue
            other = index.profile(pk)
            if other.sex != profile.sex or other.sex is None:
                continue
            if other.dod != profile.dod or other.dod_precision != "day":
                continue
            if profile.tool != "facility_va" and N.norm_key(
                other.district
            ) != N.norm_key(profile.district):
                continue
            hits.append(pk)
        if len(hits) == 1:
            return index.death(hits[0]), MatchMethod.EXACT_COMPOSITE

    return None, None


def union_block_candidates(profile, index, *, thresholds, rejected=frozenset()):
    """Union-of-blocks candidate generation. Returns ``(candidates, stats)``.

    ``candidates`` is ``[(death, [block names])]``; ``stats`` maps block name
    to its (pre-cap) candidate count plus ``total``/``truncated`` entries.
    Missing values never form a block (they can't), but never exclude either:
    the sex prune only fires when both sides are known and differ.
    """
    per_block_cap = int(thresholds.get("max_candidates_per_block", 200))
    window_days = int(thresholds.get("dod_block_days", 60))

    blocks: dict = {}
    district = N.norm_key(profile.district)
    constituency = N.norm_key(profile.constituency)
    ward = N.norm_key(profile.ward)
    province = N.norm_key(profile.province)

    if profile.ea:
        blocks["ea"] = index.by_ea.get(profile.ea, [])
    if constituency and ward:
        blocks["constituency_ward"] = index.by_constituency_ward.get(
            (constituency, ward), []
        )
    if district and profile.dod_year:
        blocks["district_year"] = index.by_district_year.get(
            (district, profile.dod_year), []
        )
    if profile.surname_phonetic and district:
        blocks["phonetic_surname_district"] = index.by_phonetic_district.get(
            (profile.surname_phonetic, district), []
        )
    if profile.initials and profile.dod_year:
        blocks["initials_year"] = index.by_initials_year.get(
            (profile.initials, profile.dod_year), []
        )
    if profile.dod and profile.dod_precision == "day":
        blocks["dod_window"] = index.dod_window_pks(profile.dod, window_days)
    if profile.tool == "facility_va" and province and not district:
        # Facility VAs without a derivable district fall back to province.
        blocks["province"] = index.by_province.get(province, [])

    stats = {name: len(pks) for name, pks in blocks.items()}

    sources: dict = {}  # pk -> [block names]
    for name in BLOCK_ORDER:
        for pk in blocks.get(name, [])[:per_block_cap]:
            if pk in rejected:
                continue
            sources.setdefault(pk, []).append(name)

    candidates = []
    truncated = 0
    for pk, names in sources.items():
        other = index.profile(pk)
        # Sex prune: only when both known and different.
        if profile.sex and other.sex and profile.sex != other.sex:
            continue
        if len(candidates) >= MAX_TOTAL_CANDIDATES:
            truncated += 1
            continue
        candidates.append((index.death(pk), names))

    stats["total"] = len(candidates)
    stats["truncated"] = truncated
    return candidates, stats
