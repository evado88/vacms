"""Reusable field-normalisation helpers for VA <-> Death matching.

Deliberately builds on the project's existing normalisers rather than inventing
parallel ones:
- ``dq._norm`` / ``dq.parse_when`` for text + date parsing
- ``death_ids.is_valid_death_id`` for the Death-ID exact key

Only person-matching-specific helpers (name normalisation, sex canonicalisation,
age reconciliation, string similarity) are new here.
"""

import re
import unicodedata
from datetime import date
from typing import Optional

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
