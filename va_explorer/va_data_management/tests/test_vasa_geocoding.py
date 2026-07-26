"""Unit tests for VASA coded-geography -> location-tree resolution."""

import pandas as pd
import pytest
from django.core.management import call_command

from va_explorer.va_data_management.models import (
    VASA,
    Location,
    SRSClusterLocation,
    SRSEAPopulationTarget,
)
from va_explorer.va_data_management.utils.vasa_geocoding import (
    VasaGeocoder,
    geo_codes,
)
from va_explorer.va_data_management.utils.vasa_loading import (
    load_vasa_from_dataframes,
)

pytestmark = pytest.mark.django_db


def _census(**overrides):
    defaults = {
        "geoid": "01021011002",
        "province_name": "CENTRAL",
        "province_code": "01",
        "district_name": "LUANSHYA",
        "district_code": "0102",
        "constituency_name": "CHISAMBA",
        "constituency_code": "01021",
        "ward_name": "CHAMUKA",
        "ward_code": "0102101",
        "population_target": 1,
        "household_target": 1,
    }
    defaults.update(overrides)
    return SRSEAPopulationTarget.objects.create(**defaults)


def _location_tree():
    """A national-style Location tree: province -> district (as the app has)."""
    central = Location.add_root(name="Central Province", location_type="province")
    central.add_child(name="Luanshya District", location_type="district")
    # A district whose census name has a spacing variant vs the tree.
    central.add_child(name="Kapirimposhi District", location_type="district")
    return central


def test_resolves_to_district_with_name_normalization():
    _census()  # district_code 0102 -> census "LUANSHYA"
    _location_tree()  # tree has "Luanshya District"
    geo = VasaGeocoder().resolve({"province": "01", "district": "0102"})

    resolved = Location.objects.get(pk=geo.location_id)
    assert resolved.name == "Luanshya District"
    assert geo.unresolved_level is None


def test_normalization_handles_spacing_variants():
    _census(district_code="0103", district_name="KAPIRI MPOSHI")
    _location_tree()  # tree has "Kapirimposhi District"
    geo = VasaGeocoder().resolve({"province": "01", "district": "0103"})

    assert Location.objects.get(pk=geo.location_id).name == "Kapirimposhi District"
    assert geo.unresolved_level is None


def test_falls_back_to_province_when_district_absent_from_tree():
    # District decodes, but its name isn't a node in the (shallow) tree.
    _census(district_code="0108", district_name="MKUSHI")
    _location_tree()
    geo = VasaGeocoder().resolve({"province": "01", "district": "0108"})

    resolved = Location.objects.get(pk=geo.location_id)
    assert resolved.location_type == "province"
    assert resolved.name == "Central Province"
    assert geo.unresolved_level is None


def test_cluster_resolves_by_ea_geoid():
    _census()
    _location_tree()
    SRSClusterLocation.add_root(name="01021011002", location_type="ea")
    geo = VasaGeocoder().resolve({"ea": "01021011002"})

    assert SRSClusterLocation.objects.get(pk=geo.cluster_id).name == "01021011002"


def test_unresolvable_codes_flag_the_finest_level_without_raising():
    _census()
    _location_tree()
    # Codes with no census/tree match at any level.
    geo = VasaGeocoder().resolve({"province": "99", "ward": "9999999"})

    assert geo.location_id is None
    assert geo.cluster_id is None
    assert geo.unresolved_level == "ward"


def test_ambiguous_district_name_falls_back_instead_of_mismapping():
    # Same district name under two provinces -> ambiguous, must not guess.
    _census(district_code="0102", district_name="LUANSHYA", province_code="01")
    central = Location.add_root(name="Central Province", location_type="province")
    central.add_child(name="Luanshya District", location_type="district")
    copperbelt = Location.add_root(name="Copperbelt Province", location_type="province")
    copperbelt.add_child(name="Luanshya District", location_type="district")

    geo = VasaGeocoder().resolve({"province": "01", "district": "0102"})

    # District is ambiguous; province "01"/CENTRAL is unambiguous -> province.
    resolved = Location.objects.get(pk=geo.location_id)
    assert resolved == central
    assert geo.unresolved_level is None


def test_geo_codes_reads_dicts_and_instances():
    codes = {"province": "01", "district": "0102", "ea": "x", "extra": "ignored"}
    assert geo_codes(codes) == {
        "province": "01",
        "district": "0102",
        "constituency": None,
        "ward": None,
        "ea": "x",
    }


def test_loader_resolves_geography_to_location_fk():
    """Ingestion sets the location FK from coded geography (the real fix path)."""
    _census()  # district_code 0102 -> "LUANSHYA"
    _location_tree()  # tree has "Luanshya District"
    df = pd.DataFrame(
        [
            {
                "instanceid": "geo-1",
                "key": "geo-1",
                "province": "01",
                "district": "0102",
                "ea": "01021011002",
            }
        ]
    )

    summary = load_vasa_from_dataframes(df)

    vasa = VASA.objects.get(instanceid="geo-1")
    assert vasa.location is not None
    assert vasa.location.name == "Luanshya District"
    assert summary["geo"]["location_resolved"] == 1
    assert summary["geo"]["unresolved"] == 0


def test_backfill_command_populates_location_fk():
    """Existing (pre-fix) VASA records get their FK filled by the backfill."""
    _census()
    _location_tree()
    vasa = VASA.objects.create(
        instanceid="b1", province="01", district="0102", location=None
    )
    assert vasa.location_id is None

    call_command("resolve_vasa_locations")

    vasa.refresh_from_db()
    assert vasa.location is not None
    assert vasa.location.name == "Luanshya District"


def test_backfill_dry_run_writes_nothing():
    _census()
    _location_tree()
    vasa = VASA.objects.create(
        instanceid="b2", province="01", district="0102", location=None
    )

    call_command("resolve_vasa_locations", "--dry-run")

    vasa.refresh_from_db()
    assert vasa.location_id is None
