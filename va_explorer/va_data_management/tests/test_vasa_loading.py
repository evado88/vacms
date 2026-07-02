"""Ingestion tests for VASA using the four vendored sample-CSV fixtures.

Expected shape of the sample export: 6 main records (2 neonate, 1 child, 3 adult)
and repeat rows br_N2213=3, AD_C3253_1=0, ar_A4252=4.
"""

from collections import Counter
from pathlib import Path

import pytest

from va_explorer.va_data_management.models import (
    VASA,
    VASACareSeekingAdult,
    VASACareSeekingNeonate,
    VASASocialAutopsy,
)
from va_explorer.va_data_management.utils.vasa_loading import load_vasa_from_csv

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
def test_ingestion_is_idempotent():
    first = load_vasa_from_csv(str(MAIN), {g: str(p) for g, p in REPEATS.items()})
    assert first["created"] == 6
    second = load_vasa_from_csv(str(MAIN), {g: str(p) for g, p in REPEATS.items()})
    assert second["created"] == 0
    assert second["updated"] == 6
    assert VASA.objects.count() == 6
    assert VASACareSeekingNeonate.objects.count() == 3
    assert VASACareSeekingAdult.objects.count() == 4
