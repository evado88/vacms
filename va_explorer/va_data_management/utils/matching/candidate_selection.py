"""Candidate Death selection for a Verbal Autopsy (v2).

Two stages, both driven by the per-run :class:`DeathIndex`:

* Stage 1 — deterministic keys, in precedence order: VASA ``DeathID``
  (format-validated), CMS ``Event`` bridge, NRC-gated name matching, death
  registration number (inert until E_DEATH carries one), exact demographic/GPS
  composite, exact district composite. Every deterministic hit is still scored
  so a contradiction guard can route it to review.
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
    "nrc",
    "death_reg_no",
    "ea",
    "constituency_ward",
    "district_year",
    "phonetic_surname_district",
    "initials_year",
    "dod_window",
    "province",
]


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


def _dob_or_age_matches(profile, other):
    if (
        profile.dob
        and other.dob
        and profile.dob_precision == "day"
        and other.dob_precision == "day"
    ):
        return profile.dob == other.dob
    return (
        profile.age_days is not None
        and other.age_days is not None
        and profile.age_days == other.age_days
    )


def _gps_matches(profile, other):
    distance = N.gps_distance_km(profile.gps, other.gps)
    return distance is not None and distance <= 1.0


def deterministic_candidate(va, profile, index, rejected=frozenset(), *, phase="all"):
    """Return ``(death, method)`` from the strongest deterministic key.

    ``phase="primary"`` checks DeathID/Event before NRC-name matching;
    ``phase="secondary"`` checks registration number/exact composite after it.
    Reviewer-rejected pairs never match deterministically.
    """
    from va_explorer.va_data_management.models import Death, MatchMethod

    model = _model_name(va)

    # 1. VASA DeathID (format-validated) — the interviewer-entered exact key.
    if phase in {"all", "primary"} and model == "vasa":
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
    if phase in {"all", "primary"} and model == "verbalautopsy":
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

    if phase == "primary":
        return None, None

    # Death registration number (no E_DEATH source field yet — inert).
    if phase in {"all", "secondary"} and profile.death_reg_no:
        key = profile.death_reg_no.upper()
        pks = [p for p in index.by_reg_no.get(key, []) if p not in rejected]
        if len(pks) == 1:
            return index.death(pks[0]), MatchMethod.DEATH_REG_NO

    # Exact demographic/GPS composite: name token-set equality AND exact DOD
    # AND exact DOB/age AND sex AND place of death AND GPS within the scorer's
    # "exact" band. The hit must be unique.
    if (
        phase in {"all", "secondary"}
        and profile.name_tokens
        and profile.sex
        and profile.dod
        and profile.dod_precision == "day"
        and profile.place_category
        and profile.gps
    ):
        hits = []
        for pk in index.by_name_key.get(profile.name_tokens, []):
            if pk in rejected:
                continue
            other = index.profile(pk)
            if other.sex != profile.sex or other.sex is None:
                continue
            if other.dod != profile.dod or other.dod_precision != "day":
                continue
            if not _dob_or_age_matches(profile, other):
                continue
            if (
                other.place_category != profile.place_category
                or other.place_category is None
            ):
                continue
            if not _gps_matches(profile, other):
                continue
            hits.append(pk)
        if len(hits) == 1:
            return index.death(hits[0]), MatchMethod.EXACT_COMPOSITE

    # Exact district composite: name token-set equality AND sex AND exact DOD AND
    #    same district (geo term skipped for facility VAs, which have none).
    #    Like NRC, the hit must be unique — duplicate register entries fall
    #    through to probabilistic scoring, which routes ties to review.
    if (
        phase in {"all", "secondary"}
        and profile.name_tokens
        and profile.sex
        and profile.dod_precision == "day"
    ):
        hits = []
        for pk in index.by_name_key.get(profile.name_tokens, []):
            if pk in rejected:
                continue
            other = index.profile(pk)
            if other.sex != profile.sex or other.sex is None:
                continue
            if other.dod != profile.dod or other.dod_precision != "day":
                continue
            if profile.tool != "facility_va":
                va_district = N.norm_key(profile.district)
                death_district = N.norm_key(other.district)
                if not va_district or not death_district:
                    continue
                if death_district != va_district:
                    continue
            hits.append(pk)
        if len(hits) == 1:
            return index.death(hits[0]), MatchMethod.EXACT_COMPOSITE

    return None, None


def nrc_name_candidates(profile, index, rejected=frozenset()):
    """Return ranked exact-NRC candidates and component-level name evidence."""
    if not profile.nrc:
        return []
    candidates = []
    for pk in index.by_nrc.get(profile.nrc, []):
        if pk in rejected:
            continue
        death = index.death(pk)
        other = index.profile(pk)
        if death is None or other is None:
            continue
        evidence = N.nrc_name_evidence(
            profile.given_name_raw,
            profile.surname_raw,
            other.name_raw,
        )
        candidates.append((death, evidence))
    return sorted(
        candidates,
        key=lambda item: (
            bool(item[1]["exact"]),
            item[1]["overall_name_similarity"] or -1,
            item[1]["surname_min_similarity"] or -1,
            item[1]["given_name_min_similarity"] or -1,
            -item[0].pk,
        ),
        reverse=True,
    )


def _cheap_candidate_rank(profile, other):
    """Deterministically rank broad blocks before applying configured caps."""
    name_similarity = N.name_similarity(profile.name_raw, other.name_raw) or 0.0
    dod_distance = 999999
    if profile.dod and other.dod:
        dod_distance = abs((profile.dod - other.dod).days)
    return (
        profile.nrc is not None and profile.nrc == other.nrc,
        bool(profile.name_tokens and profile.name_tokens == other.name_tokens),
        name_similarity,
        -dod_distance,
        N.norm_key(profile.district) == N.norm_key(other.district),
    )


def union_block_candidates(profile, index, *, thresholds, rejected=frozenset()):
    """Union-of-blocks candidate generation. Returns ``(candidates, stats)``.

    ``candidates`` is ``[(death, [block names])]``; ``stats`` maps block name
    to its (pre-cap) candidate count plus ``total``/``truncated`` entries.
    Missing values never form a block. Contradictions, including sex, remain in
    the candidate set so the scorer can retain and explain them.
    """
    per_block_cap = int(thresholds.get("max_candidates_per_block", 200))
    total_cap = int(thresholds.get("max_total_candidates", 300))
    window_days = int(thresholds.get("dod_block_days", 60))

    blocks: dict = {}
    district = N.norm_key(profile.district)
    constituency = N.norm_key(profile.constituency)
    ward = N.norm_key(profile.ward)
    province = N.norm_key(profile.province)

    if profile.nrc:
        blocks["nrc"] = index.by_nrc.get(profile.nrc, [])
    if profile.death_reg_no:
        blocks["death_reg_no"] = index.by_reg_no.get(profile.death_reg_no.upper(), [])
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
        ranked = sorted(
            set(blocks.get(name, [])),
            key=lambda pk: (_cheap_candidate_rank(profile, index.profile(pk)), -pk),
            reverse=True,
        )
        retained = ranked[:per_block_cap]
        stats[f"{name}_retained"] = len(retained)
        stats[f"{name}_dropped"] = max(0, len(ranked) - len(retained))
        for pk in retained:
            if pk in rejected:
                continue
            sources.setdefault(pk, []).append(name)

    ranked_sources = sorted(
        sources.items(),
        key=lambda item: (
            _cheap_candidate_rank(profile, index.profile(item[0])),
            -BLOCK_ORDER.index(item[1][0]),
            -item[0],
        ),
        reverse=True,
    )
    retained_sources = ranked_sources[:total_cap]
    candidates = [(index.death(pk), names) for pk, names in retained_sources]
    truncated = max(0, len(ranked_sources) - len(retained_sources))

    stats["total"] = len(candidates)
    stats["truncated"] = truncated
    return candidates, stats
