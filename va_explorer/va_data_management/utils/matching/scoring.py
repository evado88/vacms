"""Explainable weighted scoring of a Death candidate against a Verbal Autopsy.

v2: compares two :class:`LinkageProfile` objects with config-table-driven
weights (``MatchingConfig``). The score is a percentage of agreement computed
only over fields present on both records — missing values contribute zero,
never negative. Serious contradictions (sex differs, day-precise dates of
death > 90 days apart, names clearly different) set ``hard_disqualified`` so
the service refuses an automatic link regardless of the numeric score.
"""

from fuzzywuzzy import fuzz

from . import normalization as N
from .profiles import ADULT, CHILD, NEONATE, build_death_profile, build_va_profile
from .results import (
    COMPATIBLE,
    CONTRADICTORY,
    EXACT,
    FUZZY,
    MISSING,
    NORMALIZED,
    CandidateScore,
    FieldComparison,
)

# Similarity thresholds.
NAME_STRONG = 90
NAME_FUZZY_FLOOR = 60
NAME_CONTRADICT = 40
GEO_STRONG = 85
GEO_FUZZY_FLOOR = 60

# Date-of-death distance bands (days) with awarded fraction of the weight.
DOD_BANDS = [(0, EXACT, 1.0), (7, COMPATIBLE, 0.85), (30, FUZZY, 0.6), (90, FUZZY, 0.3)]
# Year-only precision on either side awards at most this fraction.
DOD_YEAR_ONLY_FRACTION = 0.4

GPS_BANDS_KM = [(1.0, EXACT, 1.0), (5.0, COMPATIBLE, 0.6), (15.0, FUZZY, 0.3)]


def _name_similarity(a_raw, b_raw):
    """Blend of token-set ratio and Jaro-Winkler best-token alignment (0-100)."""
    sims = [
        s
        for s in (
            N.name_similarity(a_raw, b_raw),
            N.best_token_alignment_similarity(a_raw, b_raw),
        )
        if s is not None
    ]
    return max(sims) if sims else None


def _cmp_name(va_p, d_p):
    sim = _name_similarity(va_p.name_raw, d_p.name_raw)
    # Nicknames (when the Death form carries them) count as name evidence.
    if d_p.nickname_tokens:
        nick_sim = _name_similarity(
            va_p.name_raw, " ".join(sorted(d_p.nickname_tokens))
        )
        if nick_sim is not None:
            sim = max(sim or 0.0, nick_sim)
    if sim is None:
        return MISSING, 0.0, None, False
    if sim >= NAME_STRONG:
        return NORMALIZED, 1.0, sim, False
    if sim >= NAME_FUZZY_FLOOR:
        return FUZZY, sim / 100.0, sim, False
    return CONTRADICTORY, 0.0, sim, sim < NAME_CONTRADICT


def _cmp_mother_name(va_p, d_p):
    """Mother's name vs "Baby of <mother>" (neonate/child evidence)."""
    applicable = (
        va_p.age_group in {NEONATE, CHILD}
        or d_p.baby_of is not None
        or va_p.baby_of is not None
    )
    if not applicable:
        return MISSING, 0.0, None, False
    pairs = [
        (va_p.mother_name, d_p.baby_of),
        (va_p.baby_of, d_p.baby_of),
        (va_p.baby_of, d_p.mother_name),
    ]
    sims = [_name_similarity(a, b) for a, b in pairs if a and b]
    sims = [s for s in sims if s is not None]
    if not sims:
        return MISSING, 0.0, None, False
    sim = max(sims)
    if sim >= NAME_STRONG:
        return NORMALIZED, 1.0, sim, False
    if sim >= NAME_FUZZY_FLOOR:
        return FUZZY, sim / 100.0, sim, False
    return CONTRADICTORY, 0.0, sim, False


def _cmp_support_name(a, b):
    """Low-weight supporting name comparator (household head, informant)."""
    sim = _name_similarity(a, b)
    if sim is None:
        return MISSING, 0.0, None, False
    if sim >= NAME_STRONG:
        return NORMALIZED, 1.0, sim, False
    if sim >= NAME_FUZZY_FLOOR:
        return FUZZY, sim / 100.0, sim, False
    return CONTRADICTORY, 0.0, sim, False


def _cmp_sex(va_p, d_p):
    if va_p.sex is None or d_p.sex is None:
        return MISSING, 0.0, None, False
    if va_p.sex == d_p.sex:
        return EXACT, 1.0, None, False
    return CONTRADICTORY, 0.0, None, True  # different sex => hard


def _cmp_dod(va_p, d_p, tol_days):
    if va_p.dod is None or d_p.dod is None:
        return MISSING, 0.0, None, False
    year_only = "year" in {va_p.dod_precision, d_p.dod_precision}
    if year_only:
        if va_p.dod.year == d_p.dod.year:
            return COMPATIBLE, DOD_YEAR_ONLY_FRACTION, None, False
        return CONTRADICTORY, 0.0, None, False  # soft: precision too low for hard
    diff = abs((va_p.dod - d_p.dod).days)
    if diff <= tol_days:
        return (
            EXACT if diff == 0 else COMPATIBLE,
            1.0 if diff == 0 else 0.85,
            None,
            False,
        )
    for band_days, classification, fraction in DOD_BANDS[2:]:
        if diff <= band_days:
            return classification, fraction, None, False
    return CONTRADICTORY, 0.0, None, True  # months apart => hard


def _cmp_dob_or_age(va_p, d_p, age_tol_years):
    """DOB when both known; age-in-days otherwise; age-group as last resort."""
    if va_p.dob is not None and d_p.dob is not None:
        year_only = "year" in {va_p.dob_precision, d_p.dob_precision}
        if year_only:
            if va_p.dob.year == d_p.dob.year:
                return COMPATIBLE, 0.4, None, False
            return CONTRADICTORY, 0.0, None, False
        diff = abs((va_p.dob - d_p.dob).days)
        if diff == 0:
            return EXACT, 1.0, None, False
        if diff <= 1:
            return COMPATIBLE, 0.8, None, False
        if diff <= 366:
            return FUZZY, 0.3, None, False
        return CONTRADICTORY, 0.0, None, False  # soft, never hard on its own
    a, b = va_p.age_days, d_p.age_days
    if a is not None and b is not None:
        diff = abs(a - b)
        tol_days = age_tol_years * 365.25
        rel = diff / max(a, b, 1)
        if diff <= 30 or rel <= 0.05:
            return COMPATIBLE, 0.7, None, False
        if diff <= tol_days or rel <= 0.15:
            return FUZZY, 0.4, None, False
        return CONTRADICTORY, 0.0, None, False
    if va_p.age_group and d_p.age_group:
        if va_p.age_group == d_p.age_group:
            return COMPATIBLE, 0.3, None, False
        return CONTRADICTORY, 0.0, None, False
    return MISSING, 0.0, None, False


def _cmp_category(a, b):
    if a is None or b is None:
        return MISSING, 0.0, None, False
    if a == b:
        return EXACT, 1.0, None, False
    return CONTRADICTORY, 0.0, None, False


def _cmp_geo(a, b):
    ka, kb = N.norm_key(a), N.norm_key(b)
    if ka is None or kb is None:
        return MISSING, 0.0, None, False
    if ka == kb:
        return EXACT, 1.0, 100.0, False
    sim = float(fuzz.ratio(ka, kb))
    if sim >= GEO_STRONG:
        return NORMALIZED, 1.0, sim, False
    if sim >= GEO_FUZZY_FLOOR:
        return FUZZY, sim / 100.0, sim, False
    return CONTRADICTORY, 0.0, sim, False


def _cmp_ea(a, b):
    # EA is a precise code: exact match or soft contradiction, never fuzzy.
    if a is None or b is None:
        return MISSING, 0.0, None, False
    if a == b:
        return EXACT, 1.0, 100.0, False
    return CONTRADICTORY, 0.0, 0.0, False


def _cmp_gps(va_p, d_p):
    km = N.gps_distance_km(va_p.gps, d_p.gps)
    if km is None:
        return MISSING, 0.0, None, False
    for band_km, classification, fraction in GPS_BANDS_KM:
        if km <= band_km:
            return classification, fraction, round(km, 2), False
    return CONTRADICTORY, 0.0, round(km, 2), False


def _cmp_occupation(a, b):
    ka, kb = N.norm_key(a), N.norm_key(b)
    if not ka or not kb:
        return MISSING, 0.0, None, False
    sim = float(fuzz.token_set_ratio(ka, kb))
    if sim >= 80:
        return NORMALIZED, 1.0, sim, False
    if sim >= 60:
        return FUZZY, sim / 100.0, sim, False
    return CONTRADICTORY, 0.0, sim, False


def _cmp_pregnancy(va_p, d_p):
    """Consistency of the three pregnancy flags — female adult deaths only."""
    if not (va_p.sex == "female" and d_p.sex == "female"):
        return MISSING, 0.0, None, False
    if va_p.age_group not in {None, ADULT} or d_p.age_group not in {None, ADULT}:
        return MISSING, 0.0, None, False
    agreements, conflicts, compared = 0, 0, 0
    for key in ("pregnant", "childbirth", "postpartum42"):
        a = va_p.pregnancy_flags.get(key)
        b = d_p.pregnancy_flags.get(key)
        if a is None or b is None:
            continue
        compared += 1
        if a == b:
            agreements += 1
        else:
            conflicts += 1
    if compared == 0:
        return MISSING, 0.0, None, False
    if conflicts and not agreements:
        return CONTRADICTORY, 0.0, None, False
    fraction = agreements / compared
    return (EXACT if fraction == 1.0 else COMPATIBLE), fraction, None, False


def score_candidate(
    va,
    death,
    *,
    method,
    config,
    va_profile=None,
    death_profile=None,
    block_sources=None,
):
    """Compare one VA against one Death under ``config``; return a CandidateScore.

    ``config`` is the merged dict from ``MatchingConfig.active()``. Profiles
    may be passed in (batch path builds them once via the DeathIndex).
    """
    weights = config["weights"]
    thresholds = config["thresholds"]
    va_p = va_profile if va_profile is not None else build_va_profile(va)
    d_p = death_profile if death_profile is not None else build_death_profile(death)

    comps = []
    hard = False

    def add(fieldname, weight, outcome, va_value, death_value):
        nonlocal hard
        classification, fraction, similarity, h = outcome
        comps.append(
            FieldComparison(
                field=fieldname,
                va_value=va_value,
                death_value=death_value,
                classification=classification,
                weight=weight,
                awarded=weight * fraction,
                similarity=similarity,
            )
        )
        hard = hard or h

    add("name", weights["name"], _cmp_name(va_p, d_p), va_p.name_raw, d_p.name_raw)
    add(
        "dod",
        weights["dod"],
        _cmp_dod(va_p, d_p, thresholds["tol_dod"]),
        str(va_p.dod or ""),
        str(d_p.dod or ""),
    )
    add(
        "dob_or_age",
        weights["dob_or_age"],
        _cmp_dob_or_age(va_p, d_p, thresholds.get("age_tol_years", 3)),
        str(va_p.dob or va_p.age_days or ""),
        str(d_p.dob or d_p.age_days or ""),
    )
    add("sex", weights["sex"], _cmp_sex(va_p, d_p), va_p.sex, d_p.sex)
    add(
        "mother_name",
        weights["mother_name"],
        _cmp_mother_name(va_p, d_p),
        va_p.mother_name or va_p.baby_of,
        d_p.baby_of or d_p.mother_name,
    )
    add("ea", weights["ea"], _cmp_ea(va_p.ea, d_p.ea), va_p.ea, d_p.ea)
    add("ward", weights["ward"], _cmp_geo(va_p.ward, d_p.ward), va_p.ward, d_p.ward)
    add(
        "gps",
        weights["gps"],
        _cmp_gps(va_p, d_p),
        str(va_p.gps or ""),
        str(d_p.gps or ""),
    )
    add(
        "place_of_death",
        weights["place_of_death"],
        _cmp_category(va_p.place_category, d_p.place_category),
        va_p.place_category,
        d_p.place_category,
    )
    add(
        "district",
        weights["district"],
        _cmp_geo(va_p.district, d_p.district),
        va_p.district,
        d_p.district,
    )
    add(
        "pregnancy",
        weights["pregnancy"],
        _cmp_pregnancy(va_p, d_p),
        str({k: v for k, v in va_p.pregnancy_flags.items() if v} or ""),
        str({k: v for k, v in d_p.pregnancy_flags.items() if v} or ""),
    )
    add(
        "household_head",
        weights["household_head"],
        _cmp_support_name(va_p.household_head, d_p.household_head),
        va_p.household_head,
        d_p.household_head,
    )
    add(
        "informant",
        weights["informant"],
        _cmp_support_name(va_p.informant_name, d_p.informant_name),
        va_p.informant_name,
        d_p.informant_name,
    )
    add(
        "marital_status",
        weights["marital_status"],
        _cmp_category(va_p.marital_category, d_p.marital_category),
        va_p.marital_category,
        d_p.marital_category,
    )
    add(
        "education",
        weights["education"],
        _cmp_category(va_p.education_category, d_p.education_category),
        va_p.education_category,
        d_p.education_category,
    )
    add(
        "occupation",
        weights["occupation"],
        _cmp_occupation(va_p.occupation, d_p.occupation),
        va_p.occupation,
        d_p.occupation,
    )
    add(
        "nationality",
        weights["nationality"],
        _cmp_category(va_p.nationality_category, d_p.nationality_category),
        va_p.nationality_category,
        d_p.nationality_category,
    )

    # Score = agreement over comparable (non-missing) fields only; a missing
    # field contributes zero and is excluded from the denominator.
    denom = sum(c.weight for c in comps if c.classification != MISSING)
    num = sum(c.awarded for c in comps)
    raw = (100.0 * num / denom) if denom > 0 else 0.0

    review_threshold = float(thresholds["review"])
    # Coverage guard: a single strong field is not enough to auto-link. Require
    # the name plus at least one other strong identifier to agree before a
    # score can exceed the review band.
    strong_present = {
        c.field
        for c in comps
        if c.field in {"name", "dod", "dob_or_age", "sex"}
        and c.classification not in {MISSING, CONTRADICTORY}
        and c.awarded > 0
    }
    name_ok = "name" in strong_present
    if not (name_ok and len(strong_present) >= 2):
        raw = min(raw, review_threshold)

    if hard:
        # Never let a serious contradiction reach the review band on score alone.
        raw = min(raw, review_threshold - 1.0)

    return CandidateScore(
        death_pk=death.pk,
        death_id=N.norm_text(getattr(death, "death_id", None)),
        method=method,
        score=round(raw, 2),
        comparisons=comps,
        hard_disqualified=hard,
        block_sources=list(block_sources or []),
    )


def dod_year_conflict(va_profile, death_profile) -> bool:
    """Deterministic-rule guard: both DODs known but the years differ."""
    if va_profile.dod is None or death_profile.dod is None:
        return False
    return va_profile.dod.year != death_profile.dod.year
