"""Tests for the linkage crosswalk generator and committed artifacts."""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from va_explorer.va_data_management.linkage import active_concepts, linkage_map
from va_explorer.va_data_management.management.commands.generate_linkage_crosswalk import (
    DEFAULT_E_DEATH,
    DEFAULT_FACILITY_CSV,
    DEFAULT_VASA,
    DEFAULT_WHO,
    Command,
)


@pytest.fixture(scope="module")
def artifacts():
    return Command().build_artifacts(
        DEFAULT_E_DEATH, DEFAULT_WHO, DEFAULT_VASA, DEFAULT_FACILITY_CSV
    )


def test_household_head_chain_q1209_id10061_de02(artifacts):
    # The spec's canonical chain: VASA Q1209 -> WHO Id10061 -> E_DEATH DE-02.
    spec = artifacts["linkage_map"]["CONCEPTS"]["household_head_name"]
    assert spec["de_field"] == "DE_02"
    assert spec["who_ids"] == ["Id10061"]
    assert "Q1209" in spec["vasa_names"]
    assert spec["status"] == "confirmed"


def test_combined_name_case_id10017_18(artifacts):
    # Q1202 carries the combined Id10017/18 name; resolved via COMBINED_WHO.
    spec = artifacts["linkage_map"]["CONCEPTS"]["deceased_name"]
    assert spec["de_field"] == "DE_03"
    assert spec["who_ids"] == ["Id10017", "Id10018"]
    assert "Q1202" in spec["vasa_names"]


def test_nrc_maps_de08_and_excludes_informant_de23(artifacts):
    spec = artifacts["linkage_map"]["CONCEPTS"]["nrc"]
    assert spec["de_field"] == "DE_08"  # deceased NRC — NOT DE_23
    assert spec["who_ids"] == ["Id10073"]
    # DE-23 (informant NRC) never appears as any concept's Death-side field.
    de_fields = {s["de_field"] for s in artifacts["linkage_map"]["CONCEPTS"].values()}
    assert "DE_23" not in de_fields
    # ... and is documented in the reviewable CSV as excluded.
    assert "DE_23" in artifacts["csv"]
    assert "excluded" in artifacts["csv"]


def test_absent_in_form_seeds_are_dormant_not_confirmed(artifacts):
    concepts = artifacts["linkage_map"]["CONCEPTS"]
    assert concepts["deceased_nickname"]["status"] == "absent_in_form"
    assert concepts["household_head_nickname"]["status"] == "absent_in_form"
    # death_reg_no stays confirmed but has no Death-side source (inert).
    assert concepts["death_reg_no"]["status"] == "confirmed"
    assert concepts["death_reg_no"]["de_field"] == ""


def test_proposed_rows_never_drive_linking(artifacts):
    concepts = artifacts["linkage_map"]["CONCEPTS"]
    assert concepts["usual_residence"]["status"] == "proposed"
    # active_concepts() (used by the profile builders) filters them out.
    active = active_concepts()
    assert "usual_residence" not in active
    assert "deceased_nickname" not in active


def test_choice_category_maps_ground_truth(artifacts):
    maps = artifacts["linkage_map"]["CHOICE_CATEGORY_MAPS"]
    assert maps["sex"]["death"]["01"] == "male"
    assert maps["sex"]["death"]["02"] == "female"
    assert maps["sex"]["who"]["male"] == "male"
    assert maps["place_of_death"]["death"]["01"] == "facility"
    assert maps["place_of_death"]["death"]["02"] == "community"
    assert maps["place_of_death"]["who"]["hospital"] == "facility"
    assert (
        maps["place_of_death"]["who"]["on_route_to_hospital_or_facility"] == "transit"
    )
    # DK/unknown codes resolve to no category at all.
    assert "99" not in maps["place_of_death"]["death"]


def test_facility_csv_columns_verified(artifacts):
    # Facility VA has province/area/hospital only — no district/ward/EA.
    cols = artifacts["linkage_map"]["FACILITY_VA_COLUMNS"]
    assert "province" in cols
    assert "hospital" in cols
    assert "area" in cols
    assert "district" not in cols


def test_committed_artifacts_match_forms():
    # The version-controlled artifacts must never drift from the XLSForms.
    call_command("generate_linkage_crosswalk", "--check", "--quiet")


def test_check_detects_drift(monkeypatch):
    monkeypatch.setattr(linkage_map, "CROSSWALK_VERSION", "tampered")
    with pytest.raises(CommandError, match="stale"):
        call_command("generate_linkage_crosswalk", "--check", "--quiet")
