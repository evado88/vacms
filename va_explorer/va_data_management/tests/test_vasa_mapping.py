"""Tests for the VASA XLSForm mapping generator and its variant rule table.

Covers every non-standard ``WHO_name`` variant class documented in
``vasa/variant_rules.py`` plus the shape/idempotency of the generated artifacts.
"""

import keyword

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from va_explorer.va_data_management.models import VerbalAutopsy
from va_explorer.va_data_management.vasa import field_map, model_fields, variant_rules


@pytest.fixture(scope="module")
def va_lookup():
    return {f.name.lower(): f.name for f in VerbalAutopsy._meta.fields}


def _resolve(who, va_lookup):
    return variant_rules.resolve_who_name(who, va_lookup)


# --------------------------------------------------------------- variant rules
def test_exact_id_resolves_to_va(va_lookup):
    r = _resolve("Id10019", va_lookup)
    assert r.classification == "va"
    assert r.targets == ["Id10019"]


def test_exact_id_missing_from_model_goes_to_sa(va_lookup):
    # Id10078 is a valid-looking WHO Id but has no VerbalAutopsy column.
    r = _resolve("Id10078", va_lookup)
    assert r.classification == "sa"
    assert r.kind == "exact_missing_from_model"


@pytest.mark.parametrize(
    ("who", "expected"),
    [
        ("Id10017/18", ["Id10017", "Id10018"]),
        ("Id10358/9", ["Id10358", "Id10359"]),
    ],
)
def test_combined_names_split_into_components(who, expected, va_lookup):
    r = _resolve(who, va_lookup)
    assert r.classification == "va"
    assert r.kind == "combined"
    assert r.targets == expected


@pytest.mark.parametrize(
    ("who", "expected"),
    [
        ("Id10120_unit", "id10120_unit"),  # WHO helper is lowercase in the model
        ("Id10148_units", "Id10148_units"),
        ("Id10196_units", "id10196_unit"),  # plural -> singular fallback
        ("Id10216_uints", "Id10216_units"),  # XLSForm typo tolerated
    ],
)
def test_unit_variants_map_to_helper(who, expected, va_lookup):
    r = _resolve(who, va_lookup)
    assert r.classification == "va"
    assert r.kind == "unit"
    assert r.targets == [expected]


def test_unit_variant_without_helper_goes_to_sa(va_lookup):
    r = _resolve("Id10285_units", va_lookup)
    assert r.classification == "sa"
    assert r.kind == "unit_unresolved"


@pytest.mark.parametrize("who", ["Id10071_check", "Id10439_check", "Id10440_check"])
def test_check_variants_map_to_helper(who, va_lookup):
    r = _resolve(who, va_lookup)
    assert r.classification == "va"
    assert r.kind == "check"
    assert r.targets == [who]


@pytest.mark.parametrize("who", ["Id10120z", "Id10148z", "Id10359z"])
def test_z_sentinels_go_to_sa(who, va_lookup):
    r = _resolve(who, va_lookup)
    assert r.classification == "sa"
    assert r.kind == "z_sentinel"


@pytest.mark.parametrize(
    "who", ["Id10023_a", "Id10023_b", "Id10167_a", "Id10173_a", "Id10069_a"]
)
def test_resolvable_suffix_splits_map_to_va(who, va_lookup):
    r = _resolve(who, va_lookup)
    assert r.classification == "va"
    assert r.targets == [who]


@pytest.mark.parametrize(
    "who", ["Id10308_1", "Id10308_2", "Id10173_nc1", "Id10336_1", "Id10322_a"]
)
def test_unresolvable_suffix_splits_go_to_sa(who, va_lookup):
    r = _resolve(who, va_lookup)
    assert r.classification == "sa"
    assert r.kind == "suffix_unresolved"


@pytest.mark.parametrize("who", ["audit", "b", "language"])
def test_junk_names_are_admin_meta(who, va_lookup):
    assert _resolve(who, va_lookup).classification == "admin_meta"


def test_empty_who_input_question_is_sa(va_lookup):
    r = variant_rules.classify_field(
        name="Q1203", base_type="select_one", who_name="", va_field_lookup=va_lookup
    )
    assert r.classification == "sa"
    assert r.kind == "social_autopsy"


def test_empty_who_calculate_is_admin_meta(va_lookup):
    r = variant_rules.classify_field(
        name="cal_5a", base_type="calculate", who_name="", va_field_lookup=va_lookup
    )
    assert r.classification == "admin_meta"


def test_empty_who_location_preset_is_admin_meta(va_lookup):
    r = variant_rules.classify_field(
        name="constituency",
        base_type="select_one",
        who_name="",
        va_field_lookup=va_lookup,
    )
    assert r.classification == "admin_meta"


# ------------------------------------------------------------ generated artifacts
def test_generated_mapping_is_up_to_date():
    """The committed artifacts must match a fresh regeneration (idempotency)."""
    try:
        call_command("generate_vasa_mapping", "--check", "--quiet")
    except CommandError as exc:  # pragma: no cover - failure path
        pytest.fail(str(exc))


def test_known_clinical_fields_are_mapped():
    assert field_map.PSEUDO_TO_WHO["Q1601"] == "Id10019"  # sex
    assert field_map.PSEUDO_TO_WHO["Q1603"] == "Id10021"  # date of birth
    assert field_map.PSEUDO_TO_WHO["A4001"] == "Id10017"


def test_combined_who_map():
    assert field_map.COMBINED_WHO == {
        "Q1202": ["Id10017", "Id10018"],
        "N2005": ["Id10358", "Id10359"],
    }


def test_repeat_groups_present_with_expected_membership():
    assert set(field_map.REPEAT_GROUPS) == {"br_N2213", "AD_C3253_1", "ar_A4252"}
    counts = {k: len(v) for k, v in field_map.REPEAT_GROUPS.items()}
    assert counts == {"br_N2213": 7, "AD_C3253_1": 8, "ar_A4252": 7}


def test_mapped_targets_are_real_va_fields():
    va_fields = {f.name for f in VerbalAutopsy._meta.fields}
    for who in field_map.PSEUDO_TO_WHO.values():
        assert who in va_fields
    assert "Id10078" not in field_map.PSEUDO_TO_WHO.values()


def test_sa_field_names_are_valid_and_unique():
    reserved = {"id", "pk", "vasa", "key", "parent_key", "created", "updated"}
    seen = set()
    for column, model_field, _label in model_fields.SA_FIELD_DEFS:
        assert column
        assert model_field.isidentifier()
        assert not keyword.iskeyword(model_field)
        assert model_field.lower() not in reserved
        assert model_field not in seen
        seen.add(model_field)


def test_repeat_field_defs_cover_all_repeat_groups():
    assert set(model_fields.REPEAT_FIELD_DEFS) == set(field_map.REPEAT_GROUPS)
    for group, defs in model_fields.REPEAT_FIELD_DEFS.items():
        assert len(defs) == len(field_map.REPEAT_GROUPS[group])
