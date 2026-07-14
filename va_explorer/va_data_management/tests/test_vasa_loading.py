"""Ingestion tests for VASA using the four vendored sample-CSV fixtures.

Expected shape of the sample export: 6 main records (2 neonate, 1 child, 3 adult)
and repeat rows br_N2213=3, AD_C3253_1=0, ar_A4252=4.
"""

from collections import Counter
from pathlib import Path

import pandas as pd
import pytest
from django.core.management import call_command

from va_explorer.va_data_management.models import (
    VASA,
    VASACareSeekingAdult,
    VASACareSeekingNeonate,
    VASASocialAutopsy,
)
from va_explorer.va_data_management.models.data_quality import DataQualityIssue
from va_explorer.va_data_management.utils.vasa_loading import (
    load_vasa_from_csv,
    load_vasa_from_dataframes,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "vasa"
MAIN = FIXTURES / "znphifacilityvasa_02.csv"
REPEATS = {
    "br_N2213": FIXTURES / "znphifacilityvasa_02-br_N2213.csv",
    "AD_C3253_1": FIXTURES / "znphifacilityvasa_02-AD_C3253_1.csv",
    "ar_A4252": FIXTURES / "znphifacilityvasa_02-ar_A4252.csv",
}


@pytest.fixture
def loaded():
    return load_vasa_from_csv(str(MAIN), {g: str(p) for g, p in REPEATS.items()})


@pytest.mark.django_db
def test_loads_six_records_with_expected_instrument_split(loaded):
    assert loaded["created"] == 6
    assert loaded["errors"] == 0
    assert VASA.objects.count() == 6
    instruments = Counter(VASA.objects.values_list("instrument", flat=True))
    # 2 neonate, 1 child, 3 adult (some adult may refine to maternal).
    assert instruments["neonate"] == 2
    assert instruments["child"] == 1
    assert instruments["adult"] + instruments["maternal"] == 3


@pytest.mark.django_db
def test_repeat_rows_link_to_parents(loaded):
    assert loaded["repeats"]["br_N2213"]["created"] == 3
    assert loaded["repeats"]["AD_C3253_1"]["created"] == 0
    assert loaded["repeats"]["ar_A4252"]["created"] == 4
    assert VASACareSeekingNeonate.objects.count() == 3
    assert VASACareSeekingAdult.objects.count() == 4
    # Every repeat row resolved a parent VASA record.
    for child in VASACareSeekingAdult.objects.all():
        assert child.vasa_id is not None


@pytest.mark.django_db
def test_each_record_has_a_social_autopsy_row(loaded):
    assert VASASocialAutopsy.objects.count() == 6


@pytest.mark.django_db
def test_select_values_are_decoded_to_who_value_space(loaded):
    # Q1601 sex (code 1/2) must land as WHO "male"/"female" on Id10019.
    sexes = set(VASA.objects.values_list("Id10019", flat=True))
    assert sexes <= {"male", "female", "undetermined", ""}
    assert sexes & {"male", "female"}


@pytest.mark.django_db
def test_legacy_facility_columns_are_tolerated(loaded):
    # The sample export is the legacy facility schema (province/area/hospital,
    # no constituency/ward/ea) — it must load and resolve as facility VAs.
    vasa = VASA.objects.exclude(hospital="").first()
    assert vasa is not None
    assert vasa.area
    assert vasa.community_va_normalized == "no"


@pytest.mark.django_db
def test_geopoint_is_composed_from_gps_columns(loaded):
    # Exactly one sample record carries GPS-* columns.
    assert VASA.objects.exclude(geopoint="").count() == 1


@pytest.mark.django_db
def test_load_vasa_csv_command_loads_main_and_repeats():
    call_command(
        "load_vasa_csv",
        str(MAIN),
        *[f"--repeat={group}={path}" for group, path in REPEATS.items()],
    )
    assert VASA.objects.count() == 6
    assert VASACareSeekingNeonate.objects.count() == 3
    assert VASACareSeekingAdult.objects.count() == 4


@pytest.mark.django_db
def test_ingestion_is_idempotent():
    first = load_vasa_from_csv(str(MAIN), {g: str(p) for g, p in REPEATS.items()})
    assert first["created"] == 6
    second = load_vasa_from_csv(str(MAIN), {g: str(p) for g, p in REPEATS.items()})
    assert second["created"] == 0
    assert second["updated"] == 6
    assert VASA.objects.count() == 6
    assert VASACareSeekingNeonate.objects.count() == 3
    assert VASACareSeekingAdult.objects.count() == 4


# --------------------------------------------------------------------------- #
# Combined-field (Q1202 / N2005) splitting and WHO-Id collision handling
# --------------------------------------------------------------------------- #


def _df(rows):
    """Inline main-form DataFrame; every row needs at least instanceID/KEY."""
    return pd.DataFrame(rows).fillna("")


def _row(instanceid, **columns):
    return {"instanceID": instanceid, "KEY": instanceid, **columns}


def _conflict_issues():
    return DataQualityIssue.objects.filter(
        issue_type=DataQualityIssue.VALIDITY,
        keys__check__in=["name_conflict", "who_id_conflict"],
    )


@pytest.mark.django_db
def test_q1202_alone_splits_given_and_surname():
    summary = load_vasa_from_dataframes(_df([_row("uuid:split", Q1202="Jane Banda")]))
    vasa = VASA.objects.get(instanceid="uuid:split")
    assert vasa.Id10017 == "Jane"
    assert vasa.Id10018 == "Banda"
    assert summary["collisions"] == 0
    assert summary["collisions_unresolved"] == 0
    assert _conflict_issues().count() == 0


@pytest.mark.django_db
def test_identical_duplicate_name_is_consistent():
    # Neonate section name (N2023) identical to the Q1202 full name.
    summary = load_vasa_from_dataframes(
        _df([_row("uuid:same", N2001="1", N2023="Janet Mumena", Q1202="Janet Mumena")])
    )
    vasa = VASA.objects.get(instanceid="uuid:same")
    assert vasa.Id10017 == "Janet"
    assert vasa.Id10018 == "Mumena"
    assert summary["collisions"] == 0  # identical values are not a collision
    assert _conflict_issues().count() == 0


@pytest.mark.django_db
def test_section_given_name_prefix_resolved_by_precedence():
    summary = load_vasa_from_dataframes(
        _df([_row("uuid:prefix", A4001="Kasongo", Q1202="Kasongo Mumena")])
    )
    vasa = VASA.objects.get(instanceid="uuid:prefix")
    assert vasa.Id10017 == "Kasongo"
    assert vasa.Id10018 == "Mumena"
    assert summary["collisions"] == 1
    assert summary["records_with_collisions"] == 1
    assert summary["collisions_resolved"] == 1
    assert summary["collisions_unresolved"] == 0
    assert _conflict_issues().count() == 0


@pytest.mark.django_db
def test_reversed_token_order_resolves_as_component():
    summary = load_vasa_from_dataframes(
        _df([_row("uuid:rev", A4001="Allias Musonda", Q1202="Musonda Allias")])
    )
    vasa = VASA.objects.get(instanceid="uuid:rev")
    assert vasa.Id10017 == "Allias Musonda"
    assert vasa.Id10018 == ""
    assert summary["collisions_resolved"] == 1
    assert summary["collisions_unresolved"] == 0
    assert _conflict_issues().count() == 0


@pytest.mark.django_db
def test_conflicting_names_keep_section_value_and_flag_dq():
    summary = load_vasa_from_dataframes(
        _df([_row("uuid:conflict", A4001="Peter", Q1202="John Phiri")])
    )
    vasa = VASA.objects.get(instanceid="uuid:conflict")
    assert vasa.Id10017 == "Peter"  # section given-name question wins
    assert vasa.Id10018 == "Phiri"  # Q1202 remains the surname source
    assert summary["collisions_unresolved"] == 1
    issue = _conflict_issues().get()
    assert issue.status == DataQualityIssue.OPEN
    assert issue.details["section_value"] == "Peter"
    assert issue.details["combined_value"] == "John Phiri"

    # A corrected re-import self-heals the issue.
    load_vasa_from_dataframes(
        _df([_row("uuid:conflict", A4001="Peter", Q1202="Peter Phiri")])
    )
    issue.refresh_from_db()
    assert issue.status == DataQualityIssue.RESOLVED


@pytest.mark.django_db
def test_reimport_repairs_record_missing_surname():
    # Simulate a record loaded before the combined-field fix: given name only.
    VASA.objects.create(
        instanceid="uuid:repair", key="uuid:repair", Id10017="Kasongo", Id10018=""
    )
    summary = load_vasa_from_dataframes(
        _df([_row("uuid:repair", A4001="Kasongo", Q1202="Kasongo Mumena")])
    )
    vasa = VASA.objects.get(instanceid="uuid:repair")
    assert summary["updated"] == 1
    assert vasa.Id10018 == "Mumena"


@pytest.mark.django_db
def test_n2005_routed_by_units():
    load_vasa_from_dataframes(
        _df(
            [
                _row("uuid:days", N2001="1", N2005="3", N2005_units="1"),
                _row("uuid:months", N2001="1", N2005="2", N2005_units="3"),
            ]
        )
    )
    days = VASA.objects.get(instanceid="uuid:days")
    months = VASA.objects.get(instanceid="uuid:months")
    assert days.Id10359 == "3"
    assert days.Id10358 == ""
    assert months.Id10358 == "2"
    assert months.Id10359 == ""


# --------------------------------------------------------------------------- #
# DeathID reporting and dry-run
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_summary_counts_rows_with_and_without_death_id():
    summary = load_vasa_from_dataframes(
        _df(
            [
                _row("uuid:withid", DeathID="2026TL0001"),
                _row("uuid:noid"),
            ]
        )
    )
    assert summary["rows"] == 2
    assert summary["rows_with_death_id"] == 1
    assert summary["rows_without_death_id"] == 1
    assert summary["death_id_validation"]["checked"] == 1


@pytest.mark.django_db
def test_death_id_header_is_case_insensitive():
    load_vasa_from_dataframes(_df([_row("uuid:lower", deathid="2026TL0001")]))
    sa = VASASocialAutopsy.objects.get(vasa__instanceid="uuid:lower")
    assert sa.DeathID == "2026TL0001"


@pytest.mark.django_db
def test_dry_run_writes_nothing_and_reports_diffs():
    load_vasa_from_dataframes(_df([_row("uuid:dry", Q1202="Jane Banda")]))
    assert VASA.objects.count() == 1

    summary = load_vasa_from_dataframes(
        _df(
            [
                _row("uuid:dry", Q1202="Jane Phiri"),
                _row("uuid:new", Q1202="Mary Zulu"),
            ]
        ),
        dry_run=True,
    )
    assert summary["dry_run"] is True
    assert summary["updated"] == 1
    assert summary["created"] == 1
    assert summary["death_id_validation"] is None
    update = next(c for c in summary["changes"] if c["instanceid"] == "uuid:dry")
    assert update["fields"]["Id10018"] == {"old": "Banda", "new": "Phiri"}
    # Nothing was written.
    assert VASA.objects.count() == 1
    assert VASA.objects.get().Id10018 == "Banda"
    assert _conflict_issues().count() == 0


@pytest.mark.django_db
def test_command_dry_run_creates_nothing():
    call_command("load_vasa_csv", str(MAIN), "--dry-run")
    assert VASA.objects.count() == 0
    assert VASASocialAutopsy.objects.count() == 0


@pytest.mark.django_db
def test_linking_summary_reflects_feature_flag(settings):
    settings.VA_DEATH_MATCH_ENABLED = False
    summary = load_vasa_from_dataframes(_df([_row("uuid:link1")]))
    assert summary["linking"] == {"enabled": False, "scheduled": 0}

    settings.VA_DEATH_MATCH_ENABLED = True
    summary = load_vasa_from_dataframes(_df([_row("uuid:link2")]))
    assert summary["linking"] == {"enabled": True, "scheduled": 1}
