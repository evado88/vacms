"""Explicit resolution rules for VASA ``WHO_name`` values.

The VASA XLSForm carries ~705 non-empty ``WHO_name`` values. 575 are exact
``Id\\d+`` strings (318 distinct WHO Ids); the remaining ~87 are non-standard
variants that need deterministic, documented handling. These functions are the
single source of truth for that handling and are intentionally free of any
Django dependency so they can be unit-tested in isolation and reused by the
``generate_vasa_mapping`` command.

Resolution target
-----------------
A ``WHO_name`` resolves against the *VerbalAutopsy model field set* (the WHO Ids
and helper columns that the coding pipeline and VA storage already understand),
passed in as ``va_field_lookup`` = ``{field_name.lower(): field_name}``.

Classifications
---------------
``va``          resolvable to one or more VerbalAutopsy fields -> stored on the
                (inherited) VASA WHO columns and fed to coding.
``sa``          empty or unresolvable ``WHO_name`` on a real input question ->
                social-autopsy storage, never fed to coding.
``admin_meta``  ODK/form machinery (device, location presets, timestamps, audit,
                intermediate ``calculate`` fields, junk) -> logged, not coded.

Variant rule table (non-standard ``WHO_name`` classes)
------------------------------------------------------
* exact ``Id\\d+``            -> VA field of the same name; if the WHO Id is not a
                                VA data column (e.g. ``Id10078``) it is reported
                                as missing-from-model and routed to ``sa``.
* combined ``IdX/Y``          -> both component Ids (e.g. ``Id10017/18`` ->
                                ``Id10017`` + ``Id10018``; ``Id10358/9`` ->
                                ``Id10358`` + ``Id10359``). Stored on the first
                                present component (names do not affect coding).
* ``<Id>_unit`` / ``_units`` / ``_uints`` (typo)
                              -> the VA helper column ``<Id>_unit`` or
                                ``<Id>_units`` if one exists, else ``sa``.
* ``<Id>_check``              -> the VA helper column ``<Id>_check`` if it exists,
                                else ``sa``.
* ``<Id>z`` sentinel          -> ``sa``. These are an alternate-instrument raw
                                numeric entry; the WHO form has no ``Idz`` column
                                and the normalised final ``Id`` is absent from
                                VASA by design, so the value is preserved in SA.
* suffixed split ``<Id><suf>`` (``_a``/``_b``/``_c``/``_0``/``_1``/``_2``/``a``/``b`` ...)
                              -> the VA column ``<Id><suf>`` / ``<Id>_<suf>`` if it
                                exists (e.g. ``Id10023_a``, ``Id10167_a``), else
                                ``sa`` (e.g. ``Id10308_1``, ``Id10173_nc1``).
* junk ``audit`` / ``b`` / ``language`` -> ``admin_meta``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# WHO_name values that are ODK machinery / noise rather than clinical data.
JUNK_WHO_NAMES = frozenset({"audit", "b", "language"})

# ``name`` values (case-insensitive) that are admin/location presets or ODK
# machinery even though they carry no WHO_name. Routed to admin_meta; ingestion
# maps the location members onto the VASA record's own location columns.
ADMIN_FIELD_NAMES = frozenset(
    {
        "deviceid",
        "starttime",
        "endtime",
        "start",
        "end",
        "today",
        "gps",
        "instanceid",
        "key",
        "submissiondate",
        "province",
        "district",
        "constituency",
        "ward",
        "ea",
        "area",
        "hospital",
    }
)

# Base ODK types that, when carrying no WHO_name, are ODK machinery (derived or
# structural) rather than social-autopsy input questions.
ADMIN_EMPTY_TYPES = frozenset(
    {
        "calculate",
        "audit",
        "start",
        "end",
        "today",
        "deviceid",
        "geopoint",
    }
)

_EXACT_ID_RE = re.compile(r"^Id\d+$")
_COMBINED_RE = re.compile(r"^Id(\d+)/(\d+)$")
_ID_SUFFIX_RE = re.compile(r"^(Id\d+)(.*)$")
_UNIT_RE = re.compile(r"^_?(units?|uints)$", re.IGNORECASE)
_CHECK_RE = re.compile(r"^_?check$", re.IGNORECASE)
_Z_RE = re.compile(r"^_?z$", re.IGNORECASE)


@dataclass(frozen=True)
class Resolution:
    """Outcome of resolving/classifying a single VASA survey field."""

    classification: str  # "va" | "sa" | "admin_meta"
    kind: str  # granular reason, used for the validation report
    targets: list[str] = field(default_factory=list)  # VA fields (va only)

    @property
    def is_va(self) -> bool:
        return self.classification == "va"


def _match(candidates, va_field_lookup: dict[str, str]) -> list[str]:
    out: list[str] = []
    for cand in candidates:
        actual = va_field_lookup.get(cand.lower())
        if actual and actual not in out:
            out.append(actual)
    return out


def resolve_who_name(who_name: str, va_field_lookup: dict[str, str]) -> Resolution:
    """Resolve a non-empty ``WHO_name`` against the VA field set.

    Callers handle the empty-``WHO_name`` case via :func:`classify_field`.
    """
    who_name = (who_name or "").strip()
    if not who_name:
        return Resolution("sa", "empty")

    if who_name.lower() in JUNK_WHO_NAMES:
        return Resolution("admin_meta", "junk")

    if _EXACT_ID_RE.match(who_name):
        matched = _match([who_name], va_field_lookup)
        if matched:
            return Resolution("va", "exact", matched)
        # Valid-looking WHO Id that is not a VA data column (e.g. Id10078).
        return Resolution("sa", "exact_missing_from_model")

    combined = _COMBINED_RE.match(who_name)
    if combined:
        left = f"Id{combined.group(1)}"
        prefix, num = combined.group(1), combined.group(2)
        # "Id10017/18" -> Id10017 + Id10018 ; "Id10358/9" -> Id10358 + Id10359
        right = "Id" + (prefix[: -len(num)] + num if len(num) < len(prefix) else num)
        matched = _match([left, right], va_field_lookup)
        if matched:
            return Resolution("va", "combined", matched)
        return Resolution("sa", "combined_unresolved")

    parts = _ID_SUFFIX_RE.match(who_name)
    if parts:
        base, suffix = parts.group(1), parts.group(2)
        if _UNIT_RE.match(suffix):
            matched = _match([f"{base}_unit", f"{base}_units"], va_field_lookup)
            return (
                Resolution("va", "unit", matched)
                if matched
                else Resolution("sa", "unit_unresolved")
            )
        if _CHECK_RE.match(suffix):
            matched = _match([f"{base}_check"], va_field_lookup)
            return (
                Resolution("va", "check", matched)
                if matched
                else Resolution("sa", "check_unresolved")
            )
        if _Z_RE.match(suffix):
            return Resolution("sa", "z_sentinel")
        # Generic suffixed split: try "<Id><suf>" and "<Id>_<suf>".
        stripped = suffix.lstrip("_")
        matched = _match([f"{base}{suffix}", f"{base}_{stripped}"], va_field_lookup)
        return (
            Resolution("va", "suffix", matched)
            if matched
            else Resolution("sa", "suffix_unresolved")
        )

    return Resolution("sa", "nonstandard")


def classify_field(
    *,
    name: str,
    base_type: str,
    who_name: str,
    va_field_lookup: dict[str, str],
) -> Resolution:
    """Classify a survey field (already known to be a real, non-note field).

    ``base_type`` is the first token of the ODK ``type`` (e.g. ``select_one`` for
    ``"select_one yndr"``).
    """
    who_name = (who_name or "").strip()
    if who_name:
        return resolve_who_name(who_name, va_field_lookup)

    # Empty WHO_name, but the VASA field *name* is itself a VerbalAutopsy column
    # (e.g. the age calculates ``age_group`` / ``ageInDays`` / ``isNeonatal``).
    # These are the VA helper columns under their own name, so map them to VA;
    # this also avoids a name collision with an inherited VASA column.
    lowered = name.strip().lower()
    if lowered in va_field_lookup and lowered not in ADMIN_FIELD_NAMES:
        return Resolution("va", "name_is_va_field", [va_field_lookup[lowered]])

    # Otherwise: admin/machinery vs a real social-autopsy input question.
    if lowered in ADMIN_FIELD_NAMES or base_type in ADMIN_EMPTY_TYPES:
        return Resolution("admin_meta", f"empty_{base_type or 'unknown'}")
    return Resolution("sa", "social_autopsy")
