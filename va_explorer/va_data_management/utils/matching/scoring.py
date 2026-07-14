"""Explainable weighted scoring of a Death candidate against a Verbal Autopsy.

The score is a percentage of agreement computed only over fields present on both
records (missing values never count as positive evidence). Contradictory fields
are comparable-but-disagreeing and actively pull the score down. Serious
contradictions (sex differs, date-of-death materially different, names clearly
different) set ``hard_disqualified`` so the service can refuse an automatic link
regardless of the numeric score.
"""

from fuzzywuzzy import fuzz

from . import normalization as N
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

# Field weights. Stronger identifiers carry more. (The score is a percentage of
# agreement over comparable fields, so weights need not sum to any fixed total.)
W_NAME = 30
W_DOD = 27
W_DOB = 18
W_SEX = 12
W_EA = 10
W_WARD = 8
W_DISTRICT = 3
W_PLACE = 2

# Similarity thresholds.
NAME_STRONG = 90
NAME_FUZZY_FLOOR = 60
NAME_CONTRADICT = 40
GEO_STRONG = 85
GEO_FUZZY_FLOOR = 60

# Date-of-death: same-day exact; within tolerance compatible; within this many
# days a weak fuzzy signal; beyond it a hard contradiction.
DOD_FUZZY_MAX_DAYS = 30


# --- VA field accessors (work for both VerbalAutopsy and VASA: shared base) ---
def va_full_name(va):
    first = N.norm_text(getattr(va, "Id10017", None)) or ""
    surname = N.norm_text(getattr(va, "Id10018", None)) or ""
    combined = f"{first} {surname}".strip()
    return combined or None


def va_dod(va):
    for attr in ("Id10023", "Id10023_a", "Id10023_b"):
        val = N.norm_text(getattr(va, attr, None))
        if val:
            return val
    return None


_VA_ACCESSORS = {
    "name": va_full_name,
    "sex": lambda va: getattr(va, "Id10019", None),
    "dob": lambda va: getattr(va, "Id10021", None),
    "dod": va_dod,
    "place": lambda va: getattr(va, "Id10058", None),
    # VerbalAutopsy wards are coded; resolve to the name the Death register uses.
    "ward": lambda va: N.resolve_ward_name(getattr(va, "ward", None)),
    "district": lambda va: getattr(va, "district", None),
    "ea": lambda va: getattr(va, "ea", None),
}
_DEATH_ACCESSORS = {
    "name": lambda d: getattr(d, "DE_03", None),
    "sex": lambda d: getattr(d, "DE_05", None),
    "dob": lambda d: getattr(d, "DE_04", None),
    "dod": lambda d: getattr(d, "DE_06", None),
    "place": lambda d: getattr(d, "DE_07", None),
    "ward": lambda d: getattr(d, "ward", None),
    "district": lambda d: getattr(d, "district", None),
    "ea": lambda d: getattr(d, "ea", None),
}


def _cmp_name(va_v, d_v):
    sim = N.name_similarity(va_v, d_v)
    if sim is None:
        return MISSING, 0.0, None, False
    if sim >= NAME_STRONG:
        return NORMALIZED, W_NAME, sim, False
    if sim >= NAME_FUZZY_FLOOR:
        return FUZZY, W_NAME * sim / 100.0, sim, False
    # Names clearly disagree.
    return CONTRADICTORY, 0.0, sim, sim < NAME_CONTRADICT


def _cmp_sex(va_v, d_v):
    a, b = N.normalize_sex(va_v), N.normalize_sex(d_v)
    if a is None or b is None:
        return MISSING, 0.0, None, False
    if a == b:
        return EXACT, W_SEX, None, False
    return CONTRADICTORY, 0.0, None, True  # different sex => hard


def _cmp_dod(va_v, d_v, tol_days):
    a, b = N.parse_day(va_v), N.parse_day(d_v)
    diff = N.days_between(a, b)
    if diff is None:
        return MISSING, 0.0, None, False
    if diff == 0:
        return EXACT, W_DOD, None, False
    if diff <= tol_days:
        return COMPATIBLE, W_DOD * 0.8, None, False
    if diff <= DOD_FUZZY_MAX_DAYS:
        return FUZZY, W_DOD * 0.3, None, False
    return CONTRADICTORY, 0.0, None, True  # months apart => hard


def _cmp_dob(va_v, d_v):
    a, b = N.parse_day(va_v), N.parse_day(d_v)
    diff = N.days_between(a, b)
    if diff is None:
        return MISSING, 0.0, None, False
    if diff == 0:
        return EXACT, W_DOB, None, False
    if diff <= 1:
        return COMPATIBLE, W_DOB * 0.8, None, False
    # DOB disagreement is a soft contradiction (not on its own disqualifying).
    return CONTRADICTORY, 0.0, None, False


def _cmp_geo(va_v, d_v, weight):
    a, b = N.norm_key(va_v), N.norm_key(d_v)
    if a is None or b is None:
        return MISSING, 0.0, None, False
    if a == b:
        return EXACT, weight, 100.0, False
    sim = float(fuzz.ratio(a, b))
    if sim >= GEO_STRONG:
        return NORMALIZED, weight, sim, False
    if sim >= GEO_FUZZY_FLOOR:
        return FUZZY, weight * sim / 100.0, sim, False
    # Geographic naming can legitimately vary; treat as soft contradiction.
    return CONTRADICTORY, 0.0, sim, False


def _cmp_ea(va_v, d_v, weight):
    # EA is a precise code (compared after digit-normalisation), so it is an
    # exact match or a soft contradiction — never fuzzy.
    a, b = N.normalize_ea(va_v), N.normalize_ea(d_v)
    if a is None or b is None:
        return MISSING, 0.0, None, False
    if a == b:
        return EXACT, weight, 100.0, False
    return CONTRADICTORY, 0.0, 0.0, False


def score_candidate(va, death, *, method, tol_dod_days, review_threshold):
    """Compare one VA against one Death; return a CandidateScore.

    ``method`` is the provisional method (fuzzy/exact/event); the service may
    keep it or downgrade based on ``hard_disqualified``.
    """
    comps = []
    hard = False

    def add(fieldname, classification, awarded, weight, similarity):
        nonlocal hard
        comps.append(
            FieldComparison(
                field=fieldname,
                va_value=N.norm_text(_VA_ACCESSORS[fieldname](va)),
                death_value=N.norm_text(_DEATH_ACCESSORS[fieldname](death)),
                classification=classification,
                weight=weight,
                awarded=awarded,
                similarity=similarity,
            )
        )

    cls, awarded, sim, h = _cmp_name(
        _VA_ACCESSORS["name"](va), _DEATH_ACCESSORS["name"](death)
    )
    add("name", cls, awarded, W_NAME, sim)
    hard = hard or h

    cls, awarded, sim, h = _cmp_dod(
        _VA_ACCESSORS["dod"](va), _DEATH_ACCESSORS["dod"](death), tol_dod_days
    )
    add("dod", cls, awarded, W_DOD, sim)
    hard = hard or h

    cls, awarded, sim, h = _cmp_dob(
        _VA_ACCESSORS["dob"](va), _DEATH_ACCESSORS["dob"](death)
    )
    add("dob", cls, awarded, W_DOB, sim)
    hard = hard or h

    cls, awarded, sim, h = _cmp_sex(
        _VA_ACCESSORS["sex"](va), _DEATH_ACCESSORS["sex"](death)
    )
    add("sex", cls, awarded, W_SEX, sim)
    hard = hard or h

    cls, awarded, sim, h = _cmp_ea(
        _VA_ACCESSORS["ea"](va), _DEATH_ACCESSORS["ea"](death), W_EA
    )
    add("ea", cls, awarded, W_EA, sim)

    cls, awarded, sim, h = _cmp_geo(
        _VA_ACCESSORS["ward"](va), _DEATH_ACCESSORS["ward"](death), W_WARD
    )
    add("ward", cls, awarded, W_WARD, sim)

    cls, awarded, sim, h = _cmp_geo(
        _VA_ACCESSORS["district"](va), _DEATH_ACCESSORS["district"](death), W_DISTRICT
    )
    add("district", cls, awarded, W_DISTRICT, sim)

    cls, awarded, sim, h = _cmp_geo(
        _VA_ACCESSORS["place"](va), _DEATH_ACCESSORS["place"](death), W_PLACE
    )
    add("place", cls, awarded, W_PLACE, sim)

    # Score = agreement over comparable (non-missing) fields only.
    denom = sum(c.weight for c in comps if c.classification != MISSING)
    num = sum(c.awarded for c in comps)
    raw = (100.0 * num / denom) if denom > 0 else 0.0

    # Coverage guard: a single strong field is not enough to auto-link. Require
    # the name plus at least one other strong identifier to agree before a score
    # can exceed the review band.
    strong_present = {
        c.field
        for c in comps
        if c.field in {"name", "dod", "dob", "sex"}
        and c.classification not in {MISSING, CONTRADICTORY}
        and c.awarded > 0
    }
    name_ok = "name" in strong_present
    if not (name_ok and len(strong_present) >= 2):
        raw = min(raw, float(review_threshold))

    if hard:
        # Never let a serious contradiction reach the review band on score alone.
        raw = min(raw, float(review_threshold) - 1.0)

    return CandidateScore(
        death_pk=death.pk,
        death_id=N.norm_text(getattr(death, "death_id", None)),
        method=method,
        score=round(raw, 2),
        comparisons=comps,
        hard_disqualified=hard,
    )
