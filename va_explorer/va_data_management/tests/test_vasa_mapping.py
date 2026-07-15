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


# ------------------------------------------------- value-decode artifact keys
def test_xlsform_hashes_match_committed_workbooks():
    from va_explorer.va_data_management.management.commands.generate_vasa_mapping import (  # noqa: E501
        DEFAULT_WHO_XLSFORM,
        DEFAULT_XLSFORM,
        _sha256,
    )

    assert {
        "vasa": _sha256(DEFAULT_XLSFORM),
        "who": _sha256(DEFAULT_WHO_XLSFORM),
    } == field_map.XLSFORM_HASHES
    vasa_sha, who_sha = (
        field_map.XLSFORM_HASHES["vasa"],
        field_map.XLSFORM_HASHES["who"],
    )
    assert f"{vasa_sha[:8]}-{who_sha[:8]}" == field_map.CROSSWALK_VERSION


def test_check_fails_on_xlsform_hash_drift(monkeypatch):
    monkeypatch.setattr(
        field_map, "XLSFORM_HASHES", {"vasa": "0" * 64, "who": "0" * 64}
    )
    with pytest.raises(CommandError, match="XLSFORM_HASHES"):
        call_command("generate_vasa_mapping", "--check", "--quiet")


def test_select_types_cover_exactly_the_crosswalked_fields():
    assert set(field_map.SELECT_TYPES) == set(field_map.VALUE_CROSSWALK)
    assert set(field_map.SELECT_TYPES.values()) <= {"select_one", "select_multiple"}
    assert field_map.SELECT_TYPES["Q1601"] == "select_one"
    assert field_map.SELECT_TYPES["A4119"] == "select_multiple"


def test_fallback_codes_are_real_lowercase_crosswalk_entries():
    assert field_map.FALLBACK_CODES  # the current form does produce fallbacks
    for pseudo, codes in field_map.FALLBACK_CODES.items():
        assert pseudo in field_map.VALUE_CROSSWALK
        for code in codes:
            value = field_map.VALUE_CROSSWALK[pseudo][code]
            # Fallbacks derive from _norm_label and are lowercase by
            # construction — the normalisation guarantee. Matched WHO choice
            # names (which may legitimately be capitalised, e.g. "Ref"/"DK")
            # are never marked fallback.
            assert value == value.lower()
    assert "Q1601" not in field_map.FALLBACK_CODES  # sex fully WHO-matched


def test_domain_coverage_against_who_choice_lists():
    """Every non-fallback decoded value must be a name in the target WHO
    field's own choice list (fallbacks are the explicit, marked exceptions)."""
    from va_explorer.va_data_management.management.commands.generate_vasa_mapping import (  # noqa: E501
        DEFAULT_WHO_XLSFORM,
        _choice_maps,
    )

    who_field_list, who_list_choices, _ = _choice_maps(DEFAULT_WHO_XLSFORM)
    violations = []
    for pseudo, crosswalk in field_map.VALUE_CROSSWALK.items():
        who = field_map.PSEUDO_TO_WHO[pseudo]
        list_name = who_field_list.get(who)
        who_names = set(who_list_choices.get(list_name, {})) if list_name else set()
        fallbacks = set(field_map.FALLBACK_CODES.get(pseudo, ()))
        for code, value in crosswalk.items():
            if code in fallbacks:
                continue
            if value not in who_names:
                violations.append((pseudo, code, value, who))
    assert not violations


def test_sa_value_labels_key_real_sa_and_repeat_fields():
    known_fields = {f for _c, f, _l in model_fields.SA_FIELD_DEFS}
    for defs in model_fields.REPEAT_FIELD_DEFS.values():
        known_fields |= {f for _c, f, _l in defs}
    assert model_fields.SA_VALUE_LABELS
    for field, entry in model_fields.SA_VALUE_LABELS.items():
        assert field in known_fields
        assert entry["kind"] in ("select_one", "select_multiple")
        assert entry["labels"]
