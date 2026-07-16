"""Candidate Death selection (blocking) for a Verbal Autopsy.

Two front-ends, one goal: hand the scorer a small, sensible candidate set
instead of the whole Death table.

- Exact keys (trusted): VASA ``DeathID`` -> ``Death.death_id``; VerbalAutopsy
  CMS ``Event`` (``event.death``).
- Fuzzy fallback: block by geography + date-of-death window + sex, capped.
"""

from django.core.exceptions import ObjectDoesNotExist

from . import normalization as N


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


def exact_candidate(va):
    """Return ``(death, method)`` from a trusted exact key, or ``(None, None)``.

    ``method`` is one of the MatchMethod string values.
    """
    from va_explorer.va_data_management.models import Death, MatchMethod

    model = _model_name(va)

    if model == "vasa":
        death_id = vasa_death_id(va)
        if death_id:
            death = (
                Death.objects.filter(death_id__iexact=death_id).order_by("pk").first()
            )
            if death is not None:
                return death, MatchMethod.EXACT_DEATH_ID
        return None, None

    # VerbalAutopsy: the CMS Event bridges va -> death on one row.
    event = (
        va.events.exclude(death__isnull=True)
        .select_related("death")
        .order_by("-id")
        .first()
    )
    if event is not None and event.death is not None:
        return event.death, MatchMethod.EVENT_BRIDGE
    return None, None


def _va_geo(va):
    # VerbalAutopsy stores coded geography; translate the ward to a name so it
    # can block against the named Death register.
    return {
        "ward": N.norm_key(N.resolve_ward_name(getattr(va, "ward", None))),
        "district": N.norm_key(getattr(va, "district", None)),
        "province": N.norm_key(getattr(va, "province", None)),
    }


def fuzzy_candidates(va, *, window_days, max_candidates):
    """Return a bounded list of Death rows worth fuzzy-scoring for this VA.

    Blocks by the narrowest available geography first (ward -> district ->
    province), then filters to the date-of-death window in Python (DE_06 is free
    text so it cannot be range-filtered in SQL). Compatible sex is used to prune
    but a missing sex on either side never excludes a candidate.
    """
    from va_explorer.va_data_management.models import Death

    from .scoring import va_dod

    geo = _va_geo(va)
    va_dod_day = N.parse_day(va_dod(va))
    va_sex = N.normalize_sex(getattr(va, "Id10019", None))
    va_ea = N.normalize_ea(getattr(va, "ea", None))

    # Narrowest non-empty block wins; broaden only if empty. EA is finest: the
    # Death EA string (``EA_0<digits>``) contains the VA's bare EA digits.
    base = Death.objects.all()
    blocks = []
    if va_ea:
        blocks.append(base.filter(ea__icontains=va_ea))
    if geo["ward"]:
        blocks.append(base.filter(ward__iexact=geo["ward"]))
    if geo["district"]:
        blocks.append(base.filter(district__iexact=geo["district"]))
    if geo["province"]:
        blocks.append(base.filter(province__iexact=geo["province"]))

    candidates = []
    seen = set()
    for block in blocks:
        # Bound the scan; ward/district blocks are small, province is the cap.
        for death in block.only(
            "id",
            "death_id",
            "DE_03",
            "DE_04",
            "DE_05",
            "DE_06",
            "DE_07",
            "ward",
            "district",
            "province",
            "ea",
        )[: max_candidates * 5]:
            if death.pk in seen:
                continue
            # Date-of-death window filter (missing DOD on either side = keep).
            if va_dod_day is not None:
                d_day = N.parse_day(getattr(death, "DE_06", None))
                if d_day is not None and abs((d_day - va_dod_day).days) > window_days:
                    continue
            # Sex prune (only when both known and different).
            d_sex = N.normalize_sex(getattr(death, "DE_05", None))
            if va_sex and d_sex and va_sex != d_sex:
                continue
            seen.add(death.pk)
            candidates.append(death)
            if len(candidates) >= max_candidates:
                return candidates
        if candidates:
            # Stop at the narrowest block that produced anything.
            break
    return candidates
