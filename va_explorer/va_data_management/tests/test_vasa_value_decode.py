"""Unit tests for the pure VASA value decoder (``vasa/value_decode.py``).

No database: the decoder is a pure function over the generated crosswalk.
"""

import pytest

from va_explorer.va_data_management.vasa.field_map import (
    FALLBACK_CODES,
    PSEUDO_TO_WHO,
    SELECT_TYPES,
    VALUE_CROSSWALK,
)
from va_explorer.va_data_management.vasa.model_fields import SA_VALUE_LABELS
from va_explorer.va_data_management.vasa.value_decode import (
    ALREADY_CANONICAL,
    BLANK,
    CROSSWALK,
    FALLBACK,
    NOT_A_SELECT,
    PASSTHROUGH,
    SELECT_MULTIPLE,
    SELECT_ONE,
    UNKNOWN_CODE,
    decode_row,
    decode_value,
    sa_display_value,
)


# ------------------------------------------------------------- direct decode
def test_sex_codes_decode_to_who_names():
    assert PSEUDO_TO_WHO["Q1601"] == "Id10019"
    male = decode_value("Q1601", "1")
    female = decode_value("Q1601", "2")
    assert (male.decoded, male.value_rule) == ("male", CROSSWALK)
    assert (female.decoded, female.value_rule) == ("female", CROSSWALK)
    assert female.field_rule == SELECT_ONE
    assert female.raw == "2"


@pytest.mark.parametrize(
    ("code", "expected"), [("1", "yes"), ("2", "no"), ("8", "ref"), ("9", "dk")]
)
def test_yes_no_special_codes(code, expected):
    assert PSEUDO_TO_WHO["A4006"] == "Id10064"
    result = decode_value("A4006", code)
    assert result.decoded == expected
    assert result.value_rule == CROSSWALK


@pytest.mark.parametrize("pseudo", ["N2001", "C3001"])
@pytest.mark.parametrize(
    ("code", "expected"), [("1", "yes"), ("2", "no"), ("8", "ref"), ("9", "dk")]
)
def test_cross_section_duplicate_concept_decodes_identically(pseudo, code, expected):
    assert PSEUDO_TO_WHO[pseudo] == "Id10354"
    assert decode_value(pseudo, code).decoded == expected


# ------------------------------------------------------------ select_multiple
def test_select_multiple_joins_who_names_with_commas():
    assert SELECT_TYPES["A4119"] == SELECT_MULTIPLE
    result = decode_value("A4119", "2 3")
    assert result.decoded == "trunk,extremities"  # order preserved
    assert result.field_rule == SELECT_MULTIPLE
    assert [t.raw for t in result.tokens] == ["2", "3"]
    assert [t.decoded for t in result.tokens] == ["trunk", "extremities"]
    assert result.value_rule == CROSSWALK


def test_select_multiple_flags_worst_token():
    result = decode_value("A4119", "2 77")
    assert result.decoded == "trunk,77"  # unknown token retained raw
    assert result.value_rule == UNKNOWN_CODE
    assert [t.value_rule for t in result.tokens] == [CROSSWALK, UNKNOWN_CODE]


# ------------------------------------------------------- unknown and fallback
def test_unknown_code_is_retained_in_place():
    result = decode_value("Q1601", "7")
    assert result.decoded == "7"  # never fabricate a value
    assert result.value_rule == UNKNOWN_CODE


def test_fallback_values_are_flagged():
    # A4002 (marital status) carries generation-time fallback entries.
    code = FALLBACK_CODES["A4002"][0]
    result = decode_value("A4002", code)
    assert result.decoded == VALUE_CROSSWALK["A4002"][code]
    assert result.value_rule == FALLBACK


# ------------------------------------------------------------- blank vs zero
@pytest.mark.parametrize("raw", ["", None, "nan", "  "])
def test_blank_and_missing_values_stay_blank(raw):
    result = decode_value("Q1601", raw)
    assert result.value_rule == BLANK
    assert result.decoded == ""


def test_zero_is_a_code_not_a_blank():
    # C3015 is the one crosswalked field with a genuine "0" choice.
    assert "0" in VALUE_CROSSWALK["C3015"]
    result = decode_value("C3015", "0")
    assert result.value_rule in (CROSSWALK, FALLBACK)
    assert result.decoded == VALUE_CROSSWALK["C3015"]["0"]
    # On a field without a "0" code it is an unknown code — never a blank.
    assert decode_value("Q1601", "0").value_rule == UNKNOWN_CODE


# --------------------------------------------------------------- idempotency
def test_already_canonical_value_passes_through():
    result = decode_value("Q1601", "female")
    assert result.decoded == "female"
    assert result.value_rule == ALREADY_CANONICAL


def test_stored_multi_select_value_redecodes_identically():
    once = decode_value("A4119", "2 3").decoded
    again = decode_value("A4119", once)
    assert again.decoded == once
    assert again.value_rule == ALREADY_CANONICAL


# -------------------------------------------------------------- non-selects
@pytest.mark.parametrize(
    ("field", "raw"),
    [
        ("Cal_Q1601", "5"),
        ("deviceid", "collect:ABC123"),
        ("Q1603", "2000-01-01"),  # date of birth: not a select
        ("Q1202", "Jane Banda"),  # combined name field: free text
    ],
)
def test_non_select_fields_pass_through_untouched(field, raw):
    assert field not in VALUE_CROSSWALK
    result = decode_value(field, raw)
    assert result.decoded == raw
    assert result.field_rule == NOT_A_SELECT
    assert result.value_rule == PASSTHROUGH


def test_decode_row_is_total_over_all_keys():
    row = {"Q1601": "2", "deviceid": "x", "A4006": "", "unknown_col": "y"}
    results = decode_row(row)
    assert set(results) == set(row)
    assert results["Q1601"].decoded == "female"
    assert results["A4006"].value_rule == BLANK
    assert results["unknown_col"].value_rule == PASSTHROUGH


# --------------------------------------------------- SA display-label helper
def test_sa_display_value_maps_select_one_code_to_label():
    labels = SA_VALUE_LABELS["Q1203"]["labels"]
    assert sa_display_value("Q1203", "1") == labels["1"]


def test_sa_display_value_maps_select_multiple_codes():
    entry = SA_VALUE_LABELS["A4193_1"]
    assert entry["kind"] == SELECT_MULTIPLE
    expected = f'{entry["labels"]["1"]}, {entry["labels"]["3"]}'
    assert sa_display_value("A4193_1", "1 3") == expected


def test_sa_display_value_passes_through_unknown_fields_and_tokens():
    assert sa_display_value("not_a_field", "1") == "1"
    assert sa_display_value("Q1203", "77") == "77"
    assert sa_display_value("Q1203", "") == ""
