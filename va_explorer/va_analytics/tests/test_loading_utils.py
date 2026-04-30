import pytest

from va_explorer.tests.factories import (
    CauseOfDeathFactory,
    LocationFactory,
    UserFactory,
    VerbalAutopsyFactory,
)
from va_explorer.va_analytics.utils.loading import load_va_data
from va_explorer.va_data_management.models import SRSClusterLocation

pytestmark = pytest.mark.django_db


def test_load_va_data_counts_coded_community_without_facility_location():
    province = LocationFactory.create(name="Southern", location_type="province")
    district = province.add_child(name="District 1", location_type="district")
    facility = district.add_child(name="Facility 1", location_type="facility")

    srs_province = SRSClusterLocation.add_root(
        name="Southern", location_type="province", code="P1", status="Active"
    )
    srs_district = srs_province.add_child(
        name="District 1", location_type="district", code="D1", status="Active"
    )
    srs_ward = srs_district.add_child(
        name="Ward 10", location_type="ward", code="W10", status="Active"
    )
    srs_ea = srs_ward.add_child(
        name="EA 2001", location_type="ea", code="EA2001", status="Active"
    )

    facility_va = VerbalAutopsyFactory.create(
        instanceid="analytics-facility-1",
        location=facility,
        community_va="no",
        Id10023="2021-03-21",
    )
    CauseOfDeathFactory.create(
        verbalautopsy=facility_va,
        cause="Cause A",
    )

    community_va = VerbalAutopsyFactory.create(
        instanceid="analytics-community-1",
        location=None,
        cluster=srs_ea,
        # Deliberately leave admin text empty to force cluster-derived geo.
        province="",
        district="",
        ward="",
        ea="",
        community_va="yes",
        Id10023="2021-03-21",
    )
    CauseOfDeathFactory.create(
        verbalautopsy=community_va,
        cause="Cause A",
    )

    user = UserFactory.create()
    data = load_va_data(
        user=user,
        cause_of_death=None,
        start_date="1901-01-01",
        end_date="2100-01-01",
        region_of_interest=None,
        age=None,
        sex=None,
    )

    assert data["map_total_coded_vas"] == 2
    assert data["update_stats"]["total_vas"] == 2
    # Coded geo totals should include both facility- and cluster-sourced records.
    assert {"province_name": "Southern", "count": 2} in data["geographic_province_sums"]
    assert {"district_name": "District 1", "count": 2} in data[
        "geographic_district_sums"
    ]
    # Map-level geographic totals should also include both paths.
    assert {"province_name": "Southern", "count": 2} in data["map_province_sums"]
    assert {"district_name": "District 1", "count": 2} in data["map_district_sums"]
    assert {"ea_name": "EA 2001", "count": 1} in data["map_ea_sums"]


def test_load_va_data_source_filter_all_community_facility():
    province = LocationFactory.create(name="Southern", location_type="province")
    district = province.add_child(name="District 1", location_type="district")
    facility = district.add_child(name="Facility 1", location_type="facility")

    srs_province = SRSClusterLocation.add_root(
        name="Southern", location_type="province", code="P1", status="Active"
    )
    srs_district = srs_province.add_child(
        name="District 1", location_type="district", code="D1", status="Active"
    )
    srs_ward = srs_district.add_child(
        name="Ward 10", location_type="ward", code="W10", status="Active"
    )
    srs_ea = srs_ward.add_child(
        name="EA 2001", location_type="ea", code="EA2001", status="Active"
    )

    facility_va = VerbalAutopsyFactory.create(
        instanceid="analytics-source-facility-1",
        location=facility,
        community_va="no",
        Id10023="2021-03-21",
    )
    CauseOfDeathFactory.create(verbalautopsy=facility_va, cause="Cause A")

    community_va = VerbalAutopsyFactory.create(
        instanceid="analytics-source-community-1",
        location=None,
        cluster=srs_ea,
        community_va="yes",
        Id10023="2021-03-21",
    )
    CauseOfDeathFactory.create(verbalautopsy=community_va, cause="Cause A")

    user = UserFactory.create()

    all_data = load_va_data(
        user=user,
        cause_of_death=None,
        start_date="1901-01-01",
        end_date="2100-01-01",
        region_of_interest=None,
        age=None,
        sex=None,
        source="all",
    )
    assert all_data["update_stats"]["total_vas"] == 2
    assert all_data["map_total_vas"] == 2

    community_data = load_va_data(
        user=user,
        cause_of_death=None,
        start_date="1901-01-01",
        end_date="2100-01-01",
        region_of_interest=None,
        age=None,
        sex=None,
        source="community",
    )
    assert community_data["update_stats"]["total_vas"] == 1
    assert community_data["map_total_vas"] == 1

    facility_data = load_va_data(
        user=user,
        cause_of_death=None,
        start_date="1901-01-01",
        end_date="2100-01-01",
        region_of_interest=None,
        age=None,
        sex=None,
        source="facility",
    )
    assert facility_data["update_stats"]["total_vas"] == 1
    assert facility_data["map_total_vas"] == 1


def test_load_va_data_cause_trends_use_date_of_death():
    va_delayed_submission = VerbalAutopsyFactory.create(
        instanceid="analytics-cod-trend-delayed",
        Id10023="2026-03-05",
        submissiondate="2026-02-01",
    )
    CauseOfDeathFactory.create(
        verbalautopsy=va_delayed_submission,
        cause="Cause A",
    )
    va_later_submission = VerbalAutopsyFactory.create(
        instanceid="analytics-cod-trend-later-submission",
        Id10023="2026-01-20",
        submissiondate="2026-03-10",
    )
    CauseOfDeathFactory.create(
        verbalautopsy=va_later_submission,
        cause="Cause B",
    )

    data = load_va_data(
        user=UserFactory.create(),
        cause_of_death=None,
        start_date="2026-01-01",
        end_date="2026-12-31",
        region_of_interest=None,
        age=None,
        sex=None,
    )

    assert list(data["COD_trend"]) == [
        {"month_key": "2026-01", "count": 1},
        {"month_key": "2026-03", "count": 1},
    ]

    cause_a_series = next(
        series
        for series in data["va_cause_trend"]["series"]
        if series["name"] == "Cause A"
    )
    periods = data["va_cause_trend"]["periods"]
    assert cause_a_series["values"][periods.index("Mar 2026")] == 1
    assert cause_a_series["values"][periods.index("Feb 2026")] == 0
