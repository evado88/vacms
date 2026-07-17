"""Tests for the v2 normalisation helpers and linkage profiles (Stage 0)."""

from datetime import date

import pytest

from va_explorer.tests.factories import VerbalAutopsyFactory
from va_explorer.va_data_management.utils.matching import normalization as N
from va_explorer.va_data_management.utils.matching.profiles import (
    build_death_profile,
    build_va_profile,
)

from .test_va_death_matching import _make_death


# --- names ------------------------------------------------------------------
def test_parse_baby_of_variants():
    assert N.parse_baby_of("Baby of Mary Banda") == "mary banda"
    assert N.parse_baby_of("BABY OF MARY") == "mary"
    assert N.parse_baby_of("B/O Mary") == "mary"
    assert N.parse_baby_of("Infant of Jane Phiri") == "jane phiri"
    assert N.parse_baby_of("John Phiri") is None
    assert N.parse_baby_of("") is None


def test_name_token_helpers():
    assert N.name_token_set("Mary  BANDA") == frozenset({"mary", "banda"})
    assert N.name_initials("Mary Banda") == "bm"  # order-insensitive
    assert N.name_initials("Banda Mary") == "bm"
    assert N.surname_phonetic("Mary Banda") == N.surname_phonetic("Mary Bandah")
    sim = N.best_token_alignment_similarity("Mary Banda", "Banda Marry")
    assert sim is not None
    assert sim > 90


def test_nrc_name_evidence_aligns_components_without_reusing_tokens():
    evidence = N.nrc_name_evidence("Jhon", "Banda", "Banda John")
    assert evidence["exact"] is False
    assert evidence["surname_min_similarity"] == 100
    assert evidence["given_name_min_similarity"] > 90
    assert evidence["unpaired_tokens"] == {"va": [], "death": []}


def test_nrc_name_evidence_does_not_let_surname_mask_given_name_conflict():
    evidence = N.nrc_name_evidence("Peter", "Banda", "John Banda")
    assert evidence["surname_min_similarity"] == 100
    assert evidence["given_name_min_similarity"] < 90


# --- dates ------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected_day", "expected_precision"),
    [
        ("2023-04-15", date(2023, 4, 15), "day"),
        ("2023", date(2023, 1, 1), "year"),
        ("03/2023", date(2023, 3, 1), "month"),
        ("2023-03", date(2023, 3, 1), "month"),
        ("March 2023", date(2023, 3, 1), "month"),
        ("not a date", None, None),
        ("", None, None),
    ],
)
def test_parse_partial_date(value, expected_day, expected_precision):
    day, precision = N.parse_partial_date(value)
    assert day == expected_day
    assert precision == expected_precision


@pytest.mark.django_db
def test_month_precision_is_not_scored_as_day_exact():
    from va_explorer.va_data_management.utils.matching import resolve_config
    from va_explorer.va_data_management.utils.matching.scoring import score_candidate

    death = _make_death(DE_06="2024-03")
    va = VerbalAutopsyFactory.create(Id10017="John", Id10018="Banda", Id10023="2024-03")
    candidate = score_candidate(va, death, method=None, config=resolve_config())
    dod = next(c for c in candidate.comparisons if c.field == "dod")
    assert dod.classification == "compatible"
    assert dod.awarded < dod.weight


# --- identifiers / GPS ------------------------------------------------------
def test_normalize_nrc():
    assert N.normalize_nrc(" 123456/78/9 ") == "123456789"
    assert N.normalize_nrc("123456789") == "123456789"
    assert N.normalize_nrc("000000") is None  # degenerate placeholder
    assert N.normalize_nrc("12345") is None  # too short
    assert N.normalize_nrc("not applicable") is None
    assert N.normalize_nrc("") is None


def test_parse_gps_and_distance():
    assert N.parse_gps("-15.4 28.3 1200 5") == (-15.4, 28.3)
    assert N.parse_gps("-15.4,28.3") == (-15.4, 28.3)
    assert N.parse_gps("0 0") is None  # null island = missing
    assert N.parse_gps("banana") is None
    assert N.gps_distance_km((-15.4, 28.3), (-15.4, 28.3)) == 0.0
    assert 0.5 < N.gps_distance_km((-15.4, 28.3), (-15.41, 28.3)) < 2.0


# --- choice-code maps -------------------------------------------------------
def test_category_for_codes_and_labels():
    assert N.category_for("sex", "death", "01") == "male"
    assert N.category_for("sex", "death", "1") == "male"  # unpadded code
    assert N.category_for("sex", "death", "MALE") == "male"  # stored label
    assert N.category_for("sex", "who", "male") == "male"
    assert N.category_for("place_of_death", "death", "01") == "facility"
    assert N.category_for("place_of_death", "who", "home") == "community"
    assert N.category_for("place_of_death", "death", "99") is None  # DK
    assert N.category_for("marital_status", "death", "05") == "divorced"


@pytest.mark.django_db
def test_resolve_geo_name_via_xlsform_maps():
    # No ClusterLocationCodes rows: falls through to the generated geo maps.
    assert N.resolve_geo_name("district", "0102") == "CHISAMBA"
    assert N.resolve_geo_name("ward", "0102101") == "CHAMUKA"
    assert N.resolve_geo_name("district", "CHISAMBA") == "CHISAMBA"  # passthrough


# --- profiles ----------------------------------------------------------------
@pytest.mark.django_db
def test_death_profile_pulls_crosswalked_fields():
    death = _make_death(
        DE_03="Baby of Mary Banda",
        DE_05="02",
        DE_08="123456/78/9",
        DE_16="01",
        ea="EA_01021011002",
    )
    p = build_death_profile(death)
    assert p.tool == "death"
    assert p.baby_of == "mary banda"
    assert p.sex == "female"
    assert p.nrc == "123456789"
    assert p.pregnancy_flags["pregnant"] == "yes"
    assert p.ea == "1021011002"
    assert p.dod == date(2024, 1, 10)
    assert p.dod_precision == "day"


@pytest.mark.django_db
def test_va_profile_age_and_year_only_dod():
    va = VerbalAutopsyFactory.create(
        Id10017="John",
        Id10018="Banda",
        Id10019="male",
        Id10023="",
        Id10023_a="",
        Id10023_b="",
        Id10024="2023",
        ageInDays="12190",
    )
    p = build_va_profile(va)
    assert p.tool == "unknown"
    assert p.dod == date(2023, 1, 1)
    assert p.dod_precision == "year"
    assert p.age_days == 12190
    assert p.age_group == "adult"
    assert p.surname_phonetic == N.surname_phonetic("Banda")
