"""VASA coded-answer -> WHO-canonical value decoding.

Pure, unit-testable decode layer over the generated ``VALUE_CROSSWALK``
(:mod:`va_explorer.va_data_management.vasa.field_map`). The loader adopts the
decoded WHO-canonical names in place on the canonical ``Idxxxxx`` fields — the
same values ``build_pycrossva_records()`` sends to coding — so decoded value ==
stored value == coding input.

Every decode is field-specific via the field's own crosswalk entry; there is no
global ``1=yes`` rule. ``value_rule`` classifies each result:

* ``crosswalk``         code matched a WHO-label-matched crosswalk entry
* ``fallback``          code matched a generation-time fallback entry (present
                        but possibly unrecognised by pyCrossVA)
* ``unknown_code``      code absent from the field's crosswalk; raw retained
* ``blank``             empty/missing answer; canonical field stays blank
* ``already_canonical`` value is already a valid WHO name for the field
* ``passthrough``       field is not a crosswalked select (dates, free text,
                        ``cal_*`` helpers, …); value untouched. Not in the task
                        spec's enum but required so ``decode_row`` is total.

``sa_display_value`` is a separate, display-only helper for Social-Autopsy /
Care-Seeking repeat columns (raw codes stay stored; the UI decodes at render
time only).
"""

import re
from dataclasses import dataclass

from .field_map import FALLBACK_CODES, SELECT_TYPES, VALUE_CROSSWALK
from .model_fields import SA_VALUE_LABELS

# field_rule
SELECT_ONE = "select_one"
SELECT_MULTIPLE = "select_multiple"
NOT_A_SELECT = "not_a_select"

# value_rule
CROSSWALK = "crosswalk"
FALLBACK = "fallback"
UNKNOWN_CODE = "unknown_code"
BLANK = "blank"
ALREADY_CANONICAL = "already_canonical"
PASSTHROUGH = "passthrough"

# Aggregate severity for multi-select results: worst token wins.
_RULE_PRECEDENCE = (UNKNOWN_CODE, FALLBACK, CROSSWALK, ALREADY_CANONICAL)

_CANONICAL = {pseudo: frozenset(cw.values()) for pseudo, cw in VALUE_CROSSWALK.items()}
_FALLBACK = {pseudo: frozenset(codes) for pseudo, codes in FALLBACK_CODES.items()}

# Raw ODK multi-selects are space-separated; stored canonical multi values are
# comma-joined. Splitting on both keeps re-decoding stored values idempotent
# (raw codes never contain commas, WHO names never contain whitespace).
_MULTI_SPLIT = re.compile(r"[\s,]+")

# Mirrors the loader's ``_has_meaningful_value`` (pandas NaN stringifies to
# "nan") without importing pandas.
_BLANKS = frozenset({"", "nan", "none", "null"})


@dataclass(frozen=True)
class TokenResult:
    raw: str
    decoded: str
    value_rule: str


@dataclass(frozen=True)
class DecodeResult:
    field_name: str
    raw: str
    decoded: str
    field_rule: str  # select_one | select_multiple | not_a_select
    value_rule: (
        str  # crosswalk|fallback|unknown_code|blank|already_canonical|passthrough
    )
    tokens: tuple = ()  # per-token results; populated only for crosswalked selects


def _is_blank(raw_value) -> bool:
    return raw_value is None or str(raw_value).strip().lower() in _BLANKS


def _decode_token(field_name, token, crosswalk) -> TokenResult:
    if token in crosswalk:
        rule = FALLBACK if token in _FALLBACK.get(field_name, ()) else CROSSWALK
        return TokenResult(token, crosswalk[token], rule)
    if token in _CANONICAL[field_name]:
        return TokenResult(token, token, ALREADY_CANONICAL)
    return TokenResult(token, token, UNKNOWN_CODE)  # retain raw, never fabricate


def _aggregate_rule(tokens) -> str:
    rules = {t.value_rule for t in tokens}
    for rule in _RULE_PRECEDENCE:
        if rule in rules:
            return rule
    return CROSSWALK


def decode_value(field_name, raw_value) -> DecodeResult:
    """Decode one answer to its WHO-canonical name (field-specific crosswalk)."""
    field_rule = SELECT_TYPES.get(field_name, NOT_A_SELECT)
    if _is_blank(raw_value):
        return DecodeResult(field_name, "", "", field_rule, BLANK)

    raw = str(raw_value)
    crosswalk = VALUE_CROSSWALK.get(field_name)
    if crosswalk is None:
        return DecodeResult(field_name, raw, raw, NOT_A_SELECT, PASSTHROUGH)

    if field_rule == SELECT_MULTIPLE:
        parts = [t for t in _MULTI_SPLIT.split(raw.strip()) if t]
        tokens = tuple(_decode_token(field_name, t, crosswalk) for t in parts)
        decoded = ",".join(t.decoded for t in tokens)
        return DecodeResult(
            field_name, raw, decoded, field_rule, _aggregate_rule(tokens), tokens
        )

    token = _decode_token(field_name, raw.strip(), crosswalk)
    return DecodeResult(
        field_name, raw, token.decoded, field_rule, token.value_rule, (token,)
    )


def decode_row(row: dict) -> dict:
    """Decode every answer in a source row; total over all keys."""
    return {field: decode_value(field, value) for field, value in row.items()}


def sa_display_value(field_name, value) -> str:
    """Display-only label for a stored Social-Autopsy / repeat select value.

    Stored values stay raw codes; unknown tokens and non-select fields pass
    through unchanged. Never used for coding input.
    """
    entry = SA_VALUE_LABELS.get(field_name)
    if entry is None or _is_blank(value):
        return value
    labels = entry["labels"]
    text = str(value).strip()
    if entry["kind"] != SELECT_MULTIPLE:
        return labels.get(text, value)
    parts = [t for t in _MULTI_SPLIT.split(text) if t]
    return ", ".join(labels.get(t, t) for t in parts)
