"""Reusable field-normalisation helpers for VA <-> Death matching.

Deliberately builds on the project's existing normalisers rather than inventing
parallel ones:
- ``dq._norm`` / ``dq.parse_when`` for text + date parsing
- ``death_ids.is_valid_death_id`` for the Death-ID exact key

Only person-matching-specific helpers (name normalisation, sex canonicalisation,
age reconciliation, string similarity) are new here.
"""

import math
import re
import unicodedata
from datetime import date
from typing import Optional

import jellyfish
from fuzzywuzzy import fuzz

from va_explorer.va_data_management.utils import dq

# Titles/prefixes that carry no identifying signal.
_TITLES = {
    "mr",
    "mrs",
    "ms",
    "miss",
    "dr",
    "prof",
    "sir",
    "madam",
    "mister",
    "late",
}
_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def norm_text(value) -> Optional[str]:
    """Strip + treat blanks/nan/none/null/n/a as None (reuses dq._norm)."""
    return dq._norm(value)


def norm_key(value) -> Optional[str]:
    """Case-insensitive normalised text (reuses dq._norm_key)."""
    return dq._norm_key(value)


def normalize_name(value) -> Optional[str]:
    """Lowercase, strip accents/punctuation, drop titles, collapse spaces.

    Kept conservative: it does not drop or reorder name components (that would
    risk false matches), only removes noise. Returns None for blank names.
    """
    text = norm_text(value)
    if text is None:
        return None
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _PUNCT_RE.sub(" ", text).lower()
    tokens = [t for t in _SPACE_RE.split(text) if t and t not in _TITLES]
    if not tokens:
        return None
    return " ".join(tokens)


def name_similarity(a, b) -> Optional[float]:
    """Order-insensitive token similarity (0-100), or None if either is blank."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return None
    # token_set_ratio handles different ordering, missing middle names, initials.
    return float(fuzz.token_set_ratio(na, nb))


def normalize_death_id(value) -> Optional[str]:
    """Normalise a Death ID for exact joins: strip + upper (per death_ids)."""
    text = norm_text(value)
    return text.upper() if text is not None else None


_SEX_MALE = {"m", "male", "1", "01", "man", "boy"}
_SEX_FEMALE = {"f", "female", "2", "02", "woman", "girl"}


def normalize_sex(value) -> Optional[str]:
    text = norm_text(value)
    if text is None:
        return None
    key = text.lower()
    if key in _SEX_MALE:
        return "male"
    if key in _SEX_FEMALE:
        return "female"
    return None


def parse_day(value) -> Optional[date]:
    """Parse a date-of-birth / date-of-death value to a date, or None."""
    dt = dq.parse_when(value)
    return dt.date() if dt is not None else None


def days_between(a: Optional[date], b: Optional[date]) -> Optional[int]:
    if a is None or b is None:
        return None
    return abs((a - b).days)


def to_int_age(value) -> Optional[int]:
    text = norm_text(value)
    if text is None:
        return None
    m = re.match(r"-?\d+", text)
    if not m:
        return None
    try:
        n = int(m.group(0))
    except ValueError:
        return None
    return n if 0 <= n <= 130 else None


# Lazily-built {ward_code: ward_name} map. VerbalAutopsy stores geography as
# numeric codes (ward "502103") while the Death register stores names ("CHINKULI"),
# so blocking/scoring must translate codes to names before comparing.
_WARD_CODE_MAP = None


def _ward_code_map():
    global _WARD_CODE_MAP
    if _WARD_CODE_MAP is None:
        from va_explorer.va_data_management.models import ClusterLocationCodes

        _WARD_CODE_MAP = {
            str(code).strip(): str(name).strip()
            for code, name in ClusterLocationCodes.objects.exclude(
                ward_code__isnull=True
            ).values_list("ward_code", "ward")
            if code and name
        }
    return _WARD_CODE_MAP


def reset_ward_code_map():
    """Clear the cached code->name map (call after loading location codes)."""
    global _WARD_CODE_MAP
    _WARD_CODE_MAP = None


def resolve_ward_name(value) -> Optional[str]:
    """Translate a coded ward to its name; pass names through unchanged.

    Only all-digit values are looked up (they are codes); anything else is
    already a name and is returned as-is.
    """
    text = norm_text(value)
    if text is None:
        return None
    if text.isdigit():
        return _ward_code_map().get(text, text)
    return text


def normalize_ea(value) -> Optional[str]:
    """Canonicalise an Enumeration Area code for comparison.

    VerbalAutopsy stores EA as bare digits (``5021031018``) while Death stores a
    prefixed form (``EA_05021031018``). Reducing both to digits + stripping the
    leading zero aligns them. Returns None for blank/too-short values.
    """
    text = norm_text(value)
    if text is None:
        return None
    digits = re.sub(r"\D", "", text).lstrip("0")
    # EA codes are long; guard against a stray short number matching broadly.
    return digits if len(digits) >= 6 else None


# --- v2 additions: names -------------------------------------------------------


def name_token_set(value) -> frozenset:
    """Normalised name tokens (empty frozenset for blank names)."""
    name = normalize_name(value)
    return frozenset(name.split()) if name else frozenset()


_BABY_OF_LEADS = {"baby", "infant", "newborn"}


def parse_baby_of(value) -> Optional[str]:
    """Extract the mother's name from a "Baby of <mother>" style name.

    Neonates are commonly registered as "Baby of Mary Banda" (also "B/O Mary");
    the remainder is the mother's name for comparison against ``Id10062``.
    Returns None when the name is not of that form.
    """
    name = normalize_name(value)
    if not name:
        return None
    tokens = name.split()
    rest: Optional[list] = None
    if tokens[0] in _BABY_OF_LEADS:
        rest = tokens[1:]
        if rest and rest[0] in {"of", "for"}:
            rest = rest[1:]
    elif tokens[:2] == ["b", "o"]:  # "B/O Mary" after punctuation stripping
        rest = tokens[2:]
    if not rest:
        return None
    return " ".join(rest)


def name_initials(value) -> Optional[str]:
    """Order-insensitive initials string ("banda mary" -> "bm"), for blocking."""
    tokens = name_token_set(value)
    if not tokens:
        return None
    return "".join(sorted(t[0] for t in tokens))


def _phonetic_token(token) -> Optional[str]:
    try:
        code = jellyfish.soundex(token)
    except Exception:
        code = None
    if not code:
        try:
            code = jellyfish.metaphone(token)
        except Exception:
            return None
    return code or None


def name_phonetics(value) -> frozenset:
    """Phonetic codes for every normalized name token, independent of order."""
    return frozenset(
        code for token in name_token_set(value) if (code := _phonetic_token(token))
    )


def surname_phonetic(value) -> Optional[str]:
    """Phonetic encoding of the last name token for VA-side blocking."""
    name = normalize_name(value)
    return _phonetic_token(name.split()[-1]) if name else None


def jaro_winkler(a: str, b: str) -> float:
    """Jaro-Winkler similarity as 0-100."""
    return 100.0 * jellyfish.jaro_winkler_similarity(a, b)


def best_token_alignment_similarity(a, b) -> Optional[float]:
    """Name similarity (0-100) via greedy Jaro-Winkler token alignment.

    Each token of the shorter name is paired with its best-scoring token of the
    other name; the result is the mean of the pair scores. Handles swapped
    order, initials-vs-full tokens and single-token nicknames better than a
    whole-string ratio.
    """
    ta, tb = sorted(name_token_set(a)), sorted(name_token_set(b))
    if not ta or not tb:
        return None
    if len(ta) > len(tb):
        ta, tb = tb, ta
    scores = [max(jaro_winkler(t, other) for other in tb) for t in ta]
    return sum(scores) / len(scores)


def one_to_one_token_alignment(a, b) -> dict:
    """Greedily align name tokens without reusing a token on either side."""
    left = sorted(name_token_set(a))
    right = sorted(name_token_set(b))
    if not left or not right:
        return {
            "similarity": None,
            "pairs": [],
            "unpaired_left": left,
            "unpaired_right": right,
        }
    scored = sorted(
        (
            (jaro_winkler(l_token, r_token), li, ri, l_token, r_token)
            for li, l_token in enumerate(left)
            for ri, r_token in enumerate(right)
        ),
        reverse=True,
    )
    used_left, used_right, pairs = set(), set(), []
    target = min(len(left), len(right))
    for score, li, ri, l_token, r_token in scored:
        if li in used_left or ri in used_right:
            continue
        used_left.add(li)
        used_right.add(ri)
        pairs.append({"left": l_token, "right": r_token, "similarity": round(score, 2)})
        if len(pairs) == target:
            break
    return {
        "similarity": (
            sum(pair["similarity"] for pair in pairs) / len(pairs) if pairs else None
        ),
        "pairs": pairs,
        "unpaired_left": [token for i, token in enumerate(left) if i not in used_left],
        "unpaired_right": [
            token for i, token in enumerate(right) if i not in used_right
        ],
    }


def nrc_name_evidence(given_name, surname, death_name) -> dict:
    """Component-level name evidence for an exact-NRC candidate.

    Death stores one free-text full name while VA/VASA has WHO given/surname
    fields. The surname is aligned to its best Death-name token; given names are
    aligned against the remaining tokens so a matching surname cannot mask a
    materially different first name.
    """
    death_tokens = sorted(name_token_set(death_name))
    surname_tokens = sorted(name_token_set(surname))
    given_tokens = sorted(name_token_set(given_name))
    full_va = " ".join(token for token in (given_name, surname) if norm_text(token))
    overall = one_to_one_token_alignment(full_va, death_name)

    surname_pairs = []
    used_death = set()
    for surname_token in surname_tokens:
        choices = [
            (jaro_winkler(surname_token, token), idx, token)
            for idx, token in enumerate(death_tokens)
            if idx not in used_death
        ]
        if not choices:
            break
        score, idx, token = max(choices)
        used_death.add(idx)
        surname_pairs.append(
            {"left": surname_token, "right": token, "similarity": round(score, 2)}
        )

    remaining_death = [
        token for idx, token in enumerate(death_tokens) if idx not in used_death
    ]
    given = one_to_one_token_alignment(
        " ".join(given_tokens), " ".join(remaining_death)
    )
    surname_similarity = (
        sum(pair["similarity"] for pair in surname_pairs) / len(surname_pairs)
        if surname_pairs
        else None
    )
    return {
        "exact": bool(
            name_token_set(full_va)
            and name_token_set(full_va) == name_token_set(death_name)
        ),
        "overall_name_similarity": overall["similarity"],
        "given_name_similarity": given["similarity"],
        "given_name_min_similarity": (
            min(pair["similarity"] for pair in given["pairs"])
            if given["pairs"]
            else None
        ),
        "surname_similarity": surname_similarity,
        "surname_min_similarity": (
            min(pair["similarity"] for pair in surname_pairs) if surname_pairs else None
        ),
        "token_pairs": {
            "surname": surname_pairs,
            "given": given["pairs"],
            "overall": overall["pairs"],
        },
        "unpaired_tokens": {
            "va": overall["unpaired_left"],
            "death": overall["unpaired_right"],
        },
    }


# --- v2 additions: dates -------------------------------------------------------

_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ]
    )
}
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_MONTH_YEAR_RE = re.compile(r"^(\d{1,2})[/\-.](\d{4})$")
_YEAR_MONTH_RE = re.compile(r"^(\d{4})[/\-.](\d{1,2})$")
_MONTHNAME_YEAR_RE = re.compile(r"^([a-z]+),?\s+((?:19|20)\d{2})$")


def parse_partial_date(value):
    """Parse a possibly-partial date to ``(date, precision)``.

    Precision is ``"day"``, ``"month"`` or ``"year"`` (partial dates resolve to
    the first day of the period); ``(None, None)`` when unparseable. Handles
    the free-text dates in VASA screening fields and Death register columns.
    """
    full = parse_day(value)
    if full is not None:
        return full, "day"
    text = norm_text(value)
    if text is None:
        return None, None
    text = text.strip().lower()
    if _YEAR_RE.match(text):
        return date(int(text), 1, 1), "year"
    m = _MONTH_YEAR_RE.match(text)
    if m and 1 <= int(m.group(1)) <= 12:
        return date(int(m.group(2)), int(m.group(1)), 1), "month"
    m = _YEAR_MONTH_RE.match(text)
    if m and 1 <= int(m.group(2)) <= 12:
        return date(int(m.group(1)), int(m.group(2)), 1), "month"
    m = _MONTHNAME_YEAR_RE.match(text)
    if m:
        month = _MONTHS.get(m.group(1)) or _MONTHS.get(
            next((k for k in _MONTHS if k.startswith(m.group(1)[:3])), "")
        )
        if month:
            return date(int(m.group(2)), month, 1), "month"
    return None, None


# --- v2 additions: identifiers -------------------------------------------------


def normalize_nrc(value) -> Optional[str]:
    """Canonicalise an NRC / national ID for exact comparison.

    Uppercase alphanumeric only; rejects too-short or degenerate values
    (all zeros / single repeated character) that would join spuriously.
    """
    text = norm_text(value)
    if text is None:
        return None
    cleaned = re.sub(r"[^0-9A-Za-z]", "", text).upper()
    if len(cleaned) < 6:
        return None
    if cleaned in {
        "UNKNOWN",
        "NOTKNOWN",
        "NOTAPPLICABLE",
        "NONEPROVIDED",
        "WITHOUTNRC",
    }:
        return None
    if len(set(cleaned)) == 1:  # e.g. "000000" / "999999" placeholders
        return None
    return cleaned


# --- v2 additions: GPS ---------------------------------------------------------


def parse_gps(value):
    """Parse an ODK geopoint string ("lat lon alt acc") to ``(lat, lon)``."""
    text = norm_text(value)
    if text is None:
        return None
    parts = re.split(r"[,\s]+", text.strip())
    if len(parts) < 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    return lat, lon


def gps_distance_km(a, b) -> Optional[float]:
    """Haversine distance in kilometres between two (lat, lon) pairs."""
    if not a or not b:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))


# --- v2 additions: crosswalk-driven categories & geography ---------------------


def category_for(concept: str, side: str, value) -> Optional[str]:
    """Canonical category for a choice value via the generated crosswalk maps.

    ``side`` is ``"death"`` (E_DEATH numeric codes) or ``"who"`` (WHO choice
    names, which VASA values are already decoded to). Falls back to pattern
    matching the raw value against the hand-maintained category rules, so label
    text stored instead of a code still resolves.
    """
    text = norm_text(value)
    if text is None:
        return None
    from va_explorer.va_data_management.linkage import linkage_map

    mapping = linkage_map.CHOICE_CATEGORY_MAPS.get(concept, {}).get(side, {})
    for candidate in (text, text.lower(), text.upper(), text.zfill(2)):
        if candidate in mapping:
            return mapping[candidate]
    # Fallback: the stored value may be a label ("MALE") rather than a code.
    from va_explorer.va_data_management.linkage import crosswalk_rules

    patterns = crosswalk_rules.CATEGORY_MAPS.get(concept)
    if patterns:
        tokens = set(norm_key(text).split()) if norm_key(text) else set()
        for category, pats in patterns.items():
            if any(set(p.split()) <= tokens for p in pats):
                return category
    return None


def resolve_geo_name(level: str, value) -> Optional[str]:
    """Resolve a coded geography value to the name the Death register uses.

    Tries ``ClusterLocationCodes`` for wards (the operational source), then the
    XLSForm-derived ``GEO_CODE_MAPS``; non-digit values pass through as names.
    """
    text = norm_text(value)
    if text is None:
        return None
    if not text.isdigit():
        return text
    if level == "ward":
        resolved = _ward_code_map().get(text)
        if resolved:
            return resolved
    from va_explorer.va_data_management.linkage import linkage_map

    return linkage_map.GEO_CODE_MAPS.get(level, {}).get(text.lstrip("0"), text)
