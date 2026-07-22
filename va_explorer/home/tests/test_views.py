import csv
import datetime as dt
import io
import json
from datetime import date, datetime

import pytest
import time_machine
from dateutil.relativedelta import relativedelta
from dateutil.tz import gettz
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from va_explorer.tests.factories import (
    CauseCodingIssueFactory,
    CauseOfDeathFactory,
    GroupFactory,
    LocationFactory,
    UserFactory,
    VerbalAutopsyFactory,
)
from va_explorer.users.models import User
from va_explorer.va_data_management.models import (
    CSADailyTracker,
    CSASupervisorTargetMap,
    Death,
    Household,
    HouseholdMember,
    ODKFormChoice,
    Pregnancy,
    PregnancyOutcome,
    SRSClusterLocation,
)
from va_explorer.vacms.cmsmodels.events import Event

pytestmark = pytest.mark.django_db
eastern_tz = gettz("US/Eastern")


# Hit the trends json endpoint and make sure the counts are correct
# Use time machine to eliminate variation in retrieving today's date
@time_machine.travel(dt.datetime(2021, 10, 26, 1, 24, tzinfo=eastern_tz))
def test_trends(user: User):
    client = Client()
    client.force_login(user=user)

    today = date.today()

    # Other interview dates
    today_minus_one_month = datetime.now() - relativedelta(months=1)
    today_minus_six_months = datetime.now() - relativedelta(months=6)
    today_minus_one_year = datetime.now() - relativedelta(months=12)

    # VAs collected today = 2
    coded_va = VerbalAutopsyFactory.create(Id10012=today, Id10023=today)
    uncoded_va = VerbalAutopsyFactory.create(Id10012=today, Id10023=today)
    # VAs collected at other points in time = 3
    VerbalAutopsyFactory.create(
        Id10012=today_minus_one_month, Id10023=today_minus_one_month
    )
    VerbalAutopsyFactory.create(
        Id10012=today_minus_six_months, Id10023=today_minus_six_months
    )
    VerbalAutopsyFactory.create(
        Id10012=today_minus_one_year, Id10023=today_minus_one_year
    )

    CauseOfDeathFactory.create(cause="Indeterminate", verbalautopsy=coded_va)
    CauseCodingIssueFactory.create(verbalautopsy=coded_va, severity="error")

    response = client.get("/trends", follow=True)
    assert response.status_code == 200

    json_data = json.loads(response.content)
    va_table_data = json_data["vaTable"]

    # Check that trends counts are correct
    assert va_table_data["collected"]["24"] == 2
    assert va_table_data["collected"]["1 week"] == 2
    assert va_table_data["collected"]["1 month"] == 3
    assert va_table_data["collected"]["Overall"] == 5

    assert va_table_data["coded"]["24"] == 1
    assert va_table_data["coded"]["1 week"] == 1
    assert va_table_data["coded"]["1 month"] == 1
    assert va_table_data["coded"]["Overall"] == 1

    assert va_table_data["uncoded"]["24"] == 1
    assert va_table_data["uncoded"]["1 week"] == 1
    assert va_table_data["uncoded"]["1 month"] == 2
    assert va_table_data["uncoded"]["Overall"] == 4

    # Chart series are cut off at 2025-01-01, while tabular totals above still
    # include the frozen 2021 data.
    assert json_data["graphs"]["collected"]["x"] == []
    assert json_data["graphs"]["collected"]["y"] == []
    assert json_data["graphs"]["coded"]["x"] == []
    assert json_data["graphs"]["coded"]["y"] == []
    assert json_data["graphs"]["uncoded"]["x"] == []
    assert json_data["graphs"]["uncoded"]["y"] == []

    # VAs in issue list includes VAs with no cause in Cause of Death
    assert len(json_data["issueList"]) == 4
    assert uncoded_va.Id10017 in json_data["issueList"][0]["deceased"]
    assert json_data["additionalIssues"] == 0
    # VAs in indeterminate COD list includes VAs with indeterminate COD
    assert len(json_data["indeterminateCodList"]) == 1
    assert json_data["indeterminateCodList"][0]["id"] == coded_va.id
    assert json_data["additionalIndeterminateCods"] == 0

    assert json_data["isFieldWorker"] is False


# Hit the trends json endpoint and make sure the counts are correct
def test_trends_no_data(user: User):
    client = Client()
    client.force_login(user=user)

    response = client.get("/trends", follow=True)
    assert response.status_code == 200

    json_data = json.loads(response.content)
    va_table_data = json_data["vaTable"]

    # Check that trends counts are correct
    assert va_table_data["collected"]["24"] == 0
    assert va_table_data["collected"]["1 week"] == 0
    assert va_table_data["collected"]["1 month"] == 0
    assert va_table_data["collected"]["Overall"] == 0

    assert va_table_data["coded"]["24"] == 0
    assert va_table_data["coded"]["1 week"] == 0
    assert va_table_data["coded"]["1 month"] == 0
    assert va_table_data["coded"]["Overall"] == 0

    assert va_table_data["uncoded"]["24"] == 0
    assert va_table_data["uncoded"]["1 week"] == 0
    assert va_table_data["uncoded"]["1 month"] == 0
    assert va_table_data["uncoded"]["Overall"] == 0

    # Check that the underlying graph data are correct
    # Graphs do not show VAs collected in the current month
    # Thus, the collected VAs were in: October, April, and September
    assert json_data["graphs"]["collected"]["y"] == [
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    ]
    # Graphs do not show VAs coded in the current month
    # Thus, there are no coded VAs in the time period in question
    assert json_data["graphs"]["coded"]["y"] == [
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    ]
    # Graphs do not show VAs uncoded in the current month
    # Thus, the uncoded VAs were in: October, April, and September
    assert json_data["graphs"]["uncoded"]["y"] == [
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    ]

    assert len(json_data["issueList"]) == 0
    assert json_data["additionalIssues"] == 0
    assert len(json_data["indeterminateCodList"]) == 0
    assert json_data["additionalIndeterminateCods"] == 0

    assert json_data["isFieldWorker"] is False


def test_trends_section_model_only(user: User):
    client = Client()
    client.force_login(user=user)

    response = client.get("/trends?section=model", follow=True)
    assert response.status_code == 200
    payload = json.loads(response.content)

    assert isinstance(payload.get("modelTrends"), dict)
    assert payload.get("vaTable") == {}
    assert payload.get("graphs") == {}
    assert payload.get("issueList") == []
    assert payload.get("indeterminateCodList") == []


def _seed_home_dashboard_scope_data():
    southern = LocationFactory.create(
        name="Southern", location_type="province", path="0001"
    )
    southern_district = southern.add_child(name="Choma", location_type="district")
    southern_facility = southern_district.add_child(
        name="Choma Facility", location_type="facility"
    )

    lusaka = LocationFactory.create(
        name="Lusaka", location_type="province", path="0002"
    )
    lusaka_district = lusaka.add_child(name="Lusaka", location_type="district")
    lusaka_facility = lusaka_district.add_child(
        name="Lusaka Facility", location_type="facility"
    )

    today = "2026-02-10"

    Household.objects.create(
        key="hh-southern",
        submissiondate=today,
        today=today,
        start=today,
        province="Southern",
        district="Choma",
        ea="EA-S-1",
    )
    Household.objects.create(
        key="hh-lusaka",
        submissiondate=today,
        today=today,
        start=today,
        province="Lusaka",
        district="Lusaka",
        ea="EA-L-1",
    )

    Pregnancy.objects.create(
        key="preg-southern",
        submissiondate=today,
        today=today,
        start=today,
        province="Southern",
        district="Choma",
        PE_09A=today,
    )
    Pregnancy.objects.create(
        key="preg-lusaka",
        submissiondate=today,
        today=today,
        start=today,
        province="Lusaka",
        district="Lusaka",
        PE_09A=today,
    )

    PregnancyOutcome.objects.create(
        key="po-southern",
        submissiondate=today,
        today=today,
        start=today,
        province="Southern",
        district="Choma",
        PO_41=today,
    )
    PregnancyOutcome.objects.create(
        key="po-lusaka",
        submissiondate=today,
        today=today,
        start=today,
        province="Lusaka",
        district="Lusaka",
        PO_41=today,
    )

    Death.objects.bulk_create(
        [
            Death(
                key="death-southern",
                submissiondate=today,
                today=today,
                start=today,
                province="Southern",
                district="Choma",
                DE_06=today,
            ),
            Death(
                key="death-lusaka",
                submissiondate=today,
                today=today,
                start=today,
                province="Lusaka",
                district="Lusaka",
                DE_06=today,
            ),
        ]
    )

    VerbalAutopsyFactory.create(
        instanceid="va-southern",
        Id10012=today,
        Id10023=today,
        submissiondate=today,
        location=southern_facility,
    )
    VerbalAutopsyFactory.create(
        instanceid="va-lusaka",
        Id10012=today,
        Id10023=today,
        submissiondate=today,
        location=lusaka_facility,
    )

    return southern


def _scoped_dashboard_user(scope_location):
    permission = Permission.objects.filter(codename="view_dashboard").first()
    group = GroupFactory.create(permissions=[permission] if permission else [])
    return UserFactory.create(groups=[group], location_restrictions=[scope_location])


def test_home_dashboard_kpis_are_scoped_to_user_province():
    cache.clear()
    southern = _seed_home_dashboard_scope_data()
    user = _scoped_dashboard_user(southern)
    client = Client()
    client.force_login(user=user)

    response = client.get(reverse("va_analytics:home-dashboard-kpis-api"))
    assert response.status_code == 200
    kpis = response.json()["kpis"]

    assert kpis["eas"]["total"] == 1
    assert kpis["households"]["total"] == 1
    assert kpis["pregnancies"]["total"] == 1
    assert kpis["preg_outcomes"]["total"] == 1
    assert kpis["deaths"]["total"] == 1
    assert kpis["vas"]["total"] == 1


@time_machine.travel(dt.datetime(2026, 4, 27, 12, 0, tzinfo=gettz("Africa/Lusaka")))
def test_home_dashboard_kpis_include_last_30_days_counts():
    cache.clear()
    permission = Permission.objects.filter(codename="view_dashboard").first()
    group = GroupFactory.create(permissions=[permission] if permission else [])
    user = UserFactory.create(groups=[group])
    client = Client()
    client.force_login(user=user)

    recent_dates = ("2026-04-27", "2026-04-17")
    old_date = "2026-03-27"

    for index, recorded_on in enumerate((*recent_dates, old_date), start=1):
        household = Household.objects.create(
            key=f"household-{index}",
            ea=f"cluster-{index}",
            submissiondate=recorded_on,
            today=recorded_on,
            start=recorded_on,
            # Self-reported (detected) counts parsed from the census text fields.
            HH_46A="2",
            HH_47A="3",
            death_size="4",
        )
        HouseholdMember.objects.create(
            household=household,
            first_name=f"Person {index}",
        )
        Pregnancy.objects.create(
            key=f"pregnancy-{index}",
            submissiondate=recorded_on,
            today=recorded_on,
            start=recorded_on,
        )
        PregnancyOutcome.objects.create(
            key=f"pregnancy-outcome-{index}",
            submissiondate=recorded_on,
            today=recorded_on,
            start=recorded_on,
        )
        Death.objects.bulk_create(
            [
                Death(
                    key=f"death-{index}",
                    submissiondate=recorded_on,
                    today=recorded_on,
                    start=recorded_on,
                )
            ]
        )
        va = VerbalAutopsyFactory.create(
            instanceid=f"va-{index}",
            Id10012=recorded_on,
            Id10023=recorded_on,
            submissiondate=recorded_on,
        )
        type(va).objects.filter(pk=va.pk).update(
            created=dt.datetime.fromisoformat(f"{recorded_on}T00:00:00+02:00")
        )

    response = client.get(reverse("va_analytics:home-dashboard-kpis-api"))
    assert response.status_code == 200
    kpis = response.json()["kpis"]

    for key in (
        "eas",
        "households",
        "people",
        "pregnancies",
        "preg_outcomes",
        "deaths",
        "vas",
    ):
        assert kpis[key]["today"] == 1
        assert kpis[key]["week"] == 1
        assert kpis[key]["last_30_days"] == 2
        assert kpis[key]["total"] == 3

    # Detected = sum of household self-reported text fields, windowed the same
    # way (today=1 hh, week=1 hh, last_30_days=2 hh, total=3 hh).
    detected_per_field = {
        "pregnancies_detected": 2,  # HH_46A
        "preg_outcomes_detected": 3,  # HH_47A
        "deaths_detected": 4,  # death_size
    }
    for key, per_household in detected_per_field.items():
        assert kpis[key]["today"] == per_household
        assert kpis[key]["week"] == per_household
        assert kpis[key]["last_30_days"] == per_household * 2
        assert kpis[key]["total"] == per_household * 3


def test_home_dashboard_overview_chart_is_scoped_to_user_province():
    cache.clear()
    southern = _seed_home_dashboard_scope_data()
    southern_facility = southern.get_descendants().get(name="Choma Facility")
    legacy_date = "2024-12-31"
    Pregnancy.objects.create(
        key="preg-southern-legacy",
        submissiondate=legacy_date,
        today=legacy_date,
        start=legacy_date,
        province="Southern",
        district="Choma",
        PE_09A=legacy_date,
    )
    PregnancyOutcome.objects.create(
        key="po-southern-legacy",
        submissiondate=legacy_date,
        today=legacy_date,
        start=legacy_date,
        province="Southern",
        district="Choma",
        PO_41=legacy_date,
    )
    Death.objects.bulk_create(
        [
            Death(
                key="death-southern-legacy",
                submissiondate=legacy_date,
                today=legacy_date,
                start=legacy_date,
                province="Southern",
                district="Choma",
                DE_06=legacy_date,
            )
        ]
    )
    legacy_va = VerbalAutopsyFactory.create(
        instanceid="va-southern-legacy",
        Id10012=legacy_date,
        Id10023=legacy_date,
        submissiondate=legacy_date,
        location=southern_facility,
    )
    type(legacy_va).objects.filter(pk=legacy_va.pk).update(
        created=dt.datetime.fromisoformat(f"{legacy_date}T00:00:00+02:00")
    )
    user = _scoped_dashboard_user(southern)
    client = Client()
    client.force_login(user=user)

    response = client.get(
        reverse(
            "va_analytics:home-dashboard-tab-chart-api",
            kwargs={"tab": "overview", "chart": "events"},
        )
    )
    assert response.status_code == 200
    payload = response.json()

    assert sum(payload["pregnancy"]) == 1
    assert sum(payload["pregnancy_outcome"]) == 1
    assert sum(payload["death"]) == 1
    assert sum(payload["va"]) == 1


def test_home_overview_map_va_ea_counts_use_resolved_va_geography(user: User):
    cache.clear()
    client = Client()
    client.force_login(user=user)

    srs_province = SRSClusterLocation.add_root(
        name="Southern", location_type="province", code="P1", status="Active"
    )
    srs_district = srs_province.add_child(
        name="District 1", location_type="district", code="D1", status="Active"
    )
    srs_constituency = srs_district.add_child(
        name="Constituency 1",
        location_type="constituency",
        code="C1",
        status="Active",
    )
    srs_ward = srs_constituency.add_child(
        name="Ward 10", location_type="ward", code="W10", status="Active"
    )
    srs_ea = srs_ward.add_child(
        name="EA 2001", location_type="ea", code="EA2001", status="Active"
    )

    VerbalAutopsyFactory.create(
        instanceid="home-map-community-va-1",
        location=None,
        cluster=srs_ea,
        province="",
        district="",
        constituency="",
        ward="",
        ea="",
        community_va="yes",
        Id10017="Community",
        Id10018="One",
        Id10019="Female",
        Id10012="2021-03-21",
        Id10023="2021-03-21",
        submissiondate="2021-03-21",
    )
    VerbalAutopsyFactory.create(
        instanceid="home-map-direct-va-1",
        province="Southern",
        district="District 1",
        constituency="Constituency 1",
        ward="Ward 10",
        ea="EA 3001",
        community_va="yes",
        Id10017="Direct",
        Id10018="Two",
        Id10019="Male",
        Id10012="2021-03-21",
        Id10023="2021-03-21",
        submissiondate="2021-03-21",
    )
    VerbalAutopsyFactory.create(
        instanceid="home-map-other-ward-va-1",
        province="Southern",
        district="District 1",
        constituency="Constituency 1",
        ward="Ward 99",
        ea="EA 4001",
        community_va="yes",
        Id10017="Other",
        Id10018="Ward",
        Id10019="Female",
        Id10012="2021-03-21",
        Id10023="2021-03-21",
        submissiondate="2021-03-21",
    )

    response = client.get("/national-operational/map-data/", {"map_view": "EA"})

    assert response.status_code == 200
    rows_by_ea = {row["ea_name"]: row for row in response.json()["map_ea_sums"]}
    assert rows_by_ea["EA 2001"]["verbal_autopsies_count"] == 1
    assert rows_by_ea["EA 2001"]["count"] == 1
    assert rows_by_ea["EA 3001"]["verbal_autopsies_count"] == 1
    assert rows_by_ea["EA 3001"]["count"] == 1
    assert rows_by_ea["EA 4001"]["verbal_autopsies_count"] == 1

    ward_response = client.get(
        "/national-operational/map-data/",
        {
            "map_view": "EA",
            "geography_level": "ward",
            "geography_value": "Ward 10",
        },
    )
    ward_rows_by_ea = {
        row["ea_name"]: row for row in ward_response.json()["map_ea_sums"]
    }
    assert ward_rows_by_ea["EA 2001"]["verbal_autopsies_count"] == 1
    assert ward_rows_by_ea["EA 3001"]["verbal_autopsies_count"] == 1
    assert "EA 4001" not in ward_rows_by_ea


def test_regional_operations_page_loads(user: User):
    client = Client()
    client.force_login(user=user)

    response = client.get("/regional-operations/", follow=True)
    assert response.status_code == 200
    assert b'id="regionalFiltersComponent"' in response.content
    assert b'id="regionalCsaComponent"' in response.content
    assert b'id="regionalMsoComponent"' in response.content


def test_operational_dashboard_includes_rumor_surveillance_tab(user: User):
    client = Client()
    client.force_login(user=user)

    response = client.get("/", {"tab": "rumor_details"}, follow=True)

    assert response.status_code == 200
    assert b"Rumor Surveillance" in response.content
    assert b'data-tab="rumor_surveillance"' in response.content
    assert b'data-tab="rumor_details"' not in response.content
    assert b'data-tab="rumor_overview"' not in response.content
    assert b'data-rumor-surveillance-tab="overview"' in response.content
    assert b'data-rumor-surveillance-tab="details"' in response.content
    assert b'id="rumor-surveillance-overview-panel"' in response.content
    assert b'id="rumor-surveillance-details-panel"' in response.content
    assert b'id="rumorDetailsDashboardApp"' in response.content
    assert b'id="rumorOverviewDashboardApp"' in response.content
    # Shared Rumor Surveillance header: KPI cards and filter controls.
    assert b'id="rumorOverviewSummaryCards"' in response.content
    assert b'id="rumorOverviewFiltersForm"' in response.content
    assert b'id="rumorDetailsFilterGeoLevel"' in response.content
    assert b'id="rumorDetailsFiltersForm"' not in response.content
    # Details sub-tab: has enumerator section, description table
    assert b'id="rumorSummaryCards"' not in response.content
    assert b'id="rumorTrendChart"' not in response.content
    assert b'id="rumorGpsCoverageChart"' not in response.content
    assert b'id="rumorDetailsEnumeratorTable"' in response.content
    assert b'id="rumorDetailsDescTable"' in response.content
    # Overview sub-tab: has map, no legacy overview word cloud.
    assert b'id="rumorOverviewMapContainer"' in response.content
    assert b'id="rumorOverviewWordCloud"' not in response.content
    # Both tabs reference their own JS
    assert b"rumor_details_dashboard.js" in response.content
    assert b"rumor_overview_dashboard.js" in response.content
    # New endpoints present in templates
    assert b"rumor-overview/source-by-province/" in response.content
    assert b"rumor-overview/affected-population/" in response.content
    assert b"rumor-details/enumerator-stats/" in response.content
    assert b"rumor-details/description-table/" in response.content
    assert b"pregnancyDashboardApp" not in response.content

    default_response = client.get("/", {"tab": "rumor_surveillance"}, follow=True)

    assert default_response.status_code == 200
    assert (
        b'id="rumor-surveillance-overview-tab" '
        b'class="rumor-surveillance-subtab rumor-surveillance-subtab--active"'
        in default_response.content
    )
    assert (
        b'id="rumor-surveillance-details-tab" '
        b'class="rumor-surveillance-subtab "' in default_response.content
    )
    assert (
        b'id="rumor-surveillance-overview-panel" '
        b'class="rumor-surveillance-panel rumor-surveillance-panel--active"'
        in default_response.content
    )


def test_rumor_details_api_endpoints_return_dashboard_payloads(user: User):
    client = Client()
    client.force_login(user=user)

    summary_response = client.get(reverse("home:rumor_overview_summary_api"))
    trend_response = client.get(reverse("home:rumor_overview_trend_api"))
    top_rumors_trend_response = client.get(
        reverse("home:rumor_overview_top_rumors_trend_api")
    )
    map_response = client.get(reverse("home:rumor_overview_map_api"))
    source_prov_response = client.get(
        reverse("home:rumor_overview_source_by_province_api")
    )
    affected_pop_response = client.get(
        reverse("home:rumor_overview_affected_population_api")
    )
    enumerator_response = client.get(reverse("home:rumor_details_enumerator_stats_api"))
    gps_coverage_response = client.get(reverse("home:rumor_overview_gps_coverage_api"))
    desc_table_response = client.get(
        reverse("home:rumor_details_description_table_api")
    )

    for resp in [
        summary_response,
        trend_response,
        top_rumors_trend_response,
        map_response,
        source_prov_response,
        affected_pop_response,
        enumerator_response,
        gps_coverage_response,
        desc_table_response,
    ]:
        assert resp.status_code == 200

    summary = summary_response.json()
    assert summary["card_last_data_update"] == "N/A"
    assert summary["card_number_of_events"] == 0
    assert "card_provinces_reporting" in summary
    assert "card_eas_reporting" in summary
    assert "card_enumerators" in summary
    assert "card_top_source" in summary
    assert "card_gps_coverage_pct" in summary
    assert "card_total_affected" in summary
    assert "card_days_since_last" in summary
    assert "card_mean_age" not in summary

    assert set(trend_response.json()) == {"labels", "data"}
    assert set(top_rumors_trend_response.json()) == {
        "has_rumors",
        "labels",
        "periods",
        "series",
    }

    map_payload = map_response.json()
    assert map_payload["map_view"] == "Province"
    assert "map_province_sums" in map_payload

    assert set(source_prov_response.json()) == {"provinces", "series"}
    assert set(affected_pop_response.json()) == {
        "labels",
        "event_counts",
        "affected_counts",
    }
    assert "rows" in enumerator_response.json()
    assert set(gps_coverage_response.json()) == {"provinces", "with_gps", "missing_gps"}

    desc_table = desc_table_response.json()
    assert set(desc_table) >= {"rows", "total_rows", "page", "page_size", "total_pages"}
    assert desc_table["page_size"] == 5
    assert desc_table["rows"] == []
    assert desc_table["total_rows"] == 0

    sentiment_response = client.get(reverse("home:rumor_details_sentiment_api"))
    assert sentiment_response.status_code == 200
    sentiment_payload = sentiment_response.json()
    assert set(sentiment_payload) == {
        "points",
        "total",
        "has_data",
        "counts",
        "monthly_means",
        "range",
    }
    assert sentiment_payload["points"] == []
    assert sentiment_payload["total"] == 0
    assert sentiment_payload["has_data"] is False
    assert sentiment_payload["counts"] == {"positive": 0, "neutral": 0, "negative": 0}
    assert sentiment_payload["monthly_means"] == []
    assert sentiment_payload["range"] == {"start_month": None, "end_month": None}


def test_rumor_source_by_province_resolves_coded_province_labels(user: User):
    client = Client()
    client.force_login(user=user)

    ODKFormChoice.objects.bulk_create(
        [
            ODKFormChoice(
                form_name="csa_daily_tracker",
                field_name="province",
                value="5",
                label="Lusaka Province",
            ),
            ODKFormChoice(
                form_name="csa_daily_tracker",
                field_name="province",
                value="9",
                label="Southern Province",
            ),
        ]
    )
    CSADailyTracker.objects.create(
        key="rumor-source-coded-province-1",
        rum="1",
        desci="Social rumor one",
        sorce_rum="01",
        province="05",
        today="2026-03-01",
    )
    CSADailyTracker.objects.create(
        key="rumor-source-coded-province-2",
        rum="1",
        desci="Social rumor two",
        sorce_rum="01",
        province="5.0",
        today="2026-03-01",
    )
    CSADailyTracker.objects.create(
        key="rumor-source-coded-province-3",
        rum="1",
        desci="Community rumor",
        sorce_rum="02",
        province="09",
        today="2026-03-01",
    )

    response = client.get(reverse("home:rumor_overview_source_by_province_api"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["provinces"] == ["Lusaka Province", "Southern Province"]
    series_by_label = {series["label"]: series["data"] for series in payload["series"]}
    assert series_by_label["Social Media"] == [2, 0]
    assert series_by_label["Community Group"] == [0, 1]


def test_rumor_sentiment_endpoint_weights_by_population(user: User):
    import math

    client = Client()
    client.force_login(user=user)

    CSADailyTracker.objects.create(
        key="sentiment-row-positive",
        rum="1",
        desci="The community is grateful for the rapid and excellent help.",
        ru_num="10",
        sorce_rum="01",
        province="Lusaka Province",
        district="Lusaka District",
        today="2026-03-15",
    )
    CSADailyTracker.objects.create(
        key="sentiment-row-negative",
        rum="1",
        desci="A terrible cholera outbreak is killing people; this is a disaster.",
        ru_num="50",
        sorce_rum="04",
        other_rum="Field worker",
        province="Lusaka Province",
        district="Lusaka District",
        today="2026-03-15",
    )
    # Excluded: rum is empty (negative report)
    CSADailyTracker.objects.create(
        key="sentiment-row-not-rumor",
        rum="",
        desci="Should not appear.",
        ru_num="999",
        today="2026-03-15",
    )
    # Excluded: empty desci
    CSADailyTracker.objects.create(
        key="sentiment-row-empty-desci",
        rum="1",
        desci="",
        ru_num="3",
        today="2026-03-15",
    )
    # Excluded from chart payloads by the dashboard-wide 2025 cutoff.
    CSADailyTracker.objects.create(
        key="sentiment-row-pre-cutoff",
        rum="1",
        desci="Old fear before the chart cutoff.",
        ru_num="100",
        today="2024-12-31",
    )

    response = client.get(reverse("home:rumor_details_sentiment_api"))
    assert response.status_code == 200
    payload = response.json()

    assert payload["has_data"] is True
    assert payload["total"] == 2
    points = {p["id"]: p for p in payload["points"]}
    assert len(points) == 2

    by_key = {p["description"]: p for p in payload["points"]}
    assert "Old fear before the chart cutoff." not in by_key

    pos = by_key["The community is grateful for the rapid and excellent help."]
    neg = by_key["A terrible cholera outbreak is killing people; this is a disaster."]

    assert pos["sentiment"] > 0
    assert neg["sentiment"] < 0
    assert pos["weighted"] > 0
    assert neg["weighted"] < 0

    assert pos["ru_num"] == 10
    assert neg["ru_num"] == 50
    assert math.isclose(
        pos["weighted"], pos["sentiment"] * math.log1p(10), rel_tol=1e-3, abs_tol=1e-3
    )
    assert math.isclose(
        neg["weighted"], neg["sentiment"] * math.log1p(50), rel_tol=1e-3, abs_tol=1e-3
    )

    for p in (pos, neg):
        assert p["month"] == "2026-03"
        assert p["date"] == "2026-03-15"
        assert p["province"] == "Lusaka Province"
        assert p["district"] == "Lusaka District"

    assert pos["source"] == "Social Media"
    assert neg["source"].startswith("Other")
    assert "Field worker" in neg["source"]

    # Each point carries category and is_srs_domain (used by tooltip/colors).
    for p in (pos, neg):
        assert "category" in p
        assert "is_srs_domain" in p
        assert p["is_srs_domain"] is False  # neither sample mentions SRS terms

    # Counts: pos > 0 → positive, neg < 0 → negative
    assert payload["counts"] == {"positive": 1, "neutral": 0, "negative": 1}

    # Monthly means: one entry for 2026-03 covering both points.
    assert len(payload["monthly_means"]) == 1
    mm = payload["monthly_means"][0]
    assert mm["month"] == "2026-03"
    assert mm["count"] == 2
    expected_mean = (pos["weighted"] + neg["weighted"]) / 2
    assert math.isclose(mm["mean"], expected_mean, rel_tol=1e-3, abs_tol=1e-3)

    # Range falls back to observed months when filter is all_time.
    assert payload["range"]["start_month"] == "2026-03"
    assert payload["range"]["end_month"] == "2026-03"

    # Domain-neutral assertions: SRS programme language should score 0 unless
    # the writing carries a clear connotation beyond the threshold.
    # Domain-neutral: factual SRS programme language with no emotional content.
    # VADER would normally score "verbal autopsy" context as negative; the
    # domain rule pins it to 0 when the compound stays within the threshold.
    CSADailyTracker.objects.create(
        key="sentiment-domain-neutral",
        rum="1",
        desci="A verbal autopsy was conducted in the local community.",
        ru_num="1",
        today="2026-03-15",
    )
    # Domain-positive: SRS language with clearly positive connotation (VADER
    # compound >> 0.25), so the domain rule lets the positive score through.
    CSADailyTracker.objects.create(
        key="sentiment-domain-positive",
        rum="1",
        desci="The verbal autopsy team did a wonderful and outstanding job helping families.",
        ru_num="1",
        today="2026-03-15",
    )

    response2 = client.get(reverse("home:rumor_details_sentiment_api"))
    payload2 = response2.json()
    by_key2 = {p["description"]: p for p in payload2["points"]}

    neutral_domain = by_key2["A verbal autopsy was conducted in the local community."]
    positive_domain = by_key2[
        "The verbal autopsy team did a wonderful and outstanding job helping families."
    ]

    # Factual SRS description → pinned to neutral (VADER compound == 0.0)
    assert neutral_domain["sentiment"] == 0.0
    assert neutral_domain["weighted"] == 0.0

    # Clearly positive SRS description → domain rule lets it through (compound > 0.25)
    assert positive_domain["sentiment"] > 0


def test_rumor_sentiment_endpoint_recognises_extended_domain_terms(user: User):
    """birth, death, certificate, pregnancy, autopsy, health, maternal are all
    treated as SRS programme subject matter — flagged is_srs_domain=True. When
    the surrounding text is neutral, the score is pinned to 0; when VADER
    detects strong polarity, the score still passes through."""
    client = Client()
    client.force_login(user=user)

    # Each of these uses one of the newly-added domain terms with otherwise
    # neutral wording — VADER returns 0 and the domain rule keeps it at 0.
    neutral_cases = {
        "domain-birth": "A birth was registered at the local clinic.",
        "domain-pregnancy": "A pregnancy was reported in the area.",
        "domain-maternal": "Maternal health discussion in the community.",
        "domain-certificate": "The certificate was collected by the family.",
        "domain-autopsy": "An autopsy was scheduled.",
    }
    for key, desci in neutral_cases.items():
        CSADailyTracker.objects.create(
            key=key, rum="1", desci=desci, ru_num="1", today="2026-03-15"
        )

    response = client.get(reverse("home:rumor_details_sentiment_api"))
    payload = response.json()
    by_desc = {p["description"]: p for p in payload["points"]}

    for desci in neutral_cases.values():
        p = by_desc[desci]
        assert p["is_srs_domain"] is True, desci
        assert p["sentiment"] == 0.0, desci
        assert p["weighted"] == 0.0, desci

    # All five rolled into the neutral count.
    assert payload["counts"]["neutral"] >= 5
    assert payload["counts"]["positive"] == 0
    assert payload["counts"]["negative"] == 0


@time_machine.travel(dt.datetime(2026, 3, 20, 12, 0, tzinfo=eastern_tz))
def test_rumor_sentiment_endpoint_filters_by_date_range(user: User):
    client = Client()
    client.force_login(user=user)

    CSADailyTracker.objects.create(
        key="sentiment-recent",
        rum="1",
        desci="Outbreak panic spreading rapidly.",
        ru_num="20",
        today="2026-03-18",
    )
    CSADailyTracker.objects.create(
        key="sentiment-old",
        rum="1",
        desci="Old fear about water.",
        ru_num="5",
        today="2025-12-01",
    )

    response = client.get(
        reverse("home:rumor_details_sentiment_api"),
        {"time_preset": "last_7_days"},
    )
    assert response.status_code == 200
    payload = response.json()
    descriptions = [p["description"] for p in payload["points"]]
    assert "Outbreak panic spreading rapidly." in descriptions
    assert "Old fear about water." not in descriptions
    assert payload["total"] == 1

    # Range comes from the filter window (last_7_days), not from observed
    # points — ends at the frozen "now" month, starts 7 days earlier.
    assert payload["range"]["end_month"] == "2026-03"
    assert payload["range"]["start_month"] == "2026-03"


def test_rumor_details_tables_show_top_rumors_and_locations(user: User):
    client = Client()
    client.force_login(user=user)

    district_labels = {
        1: "Lusaka District",
        2: "Kafue District",
        3: "Choma District",
        4: "Monze District",
        5: "Mazabuka District",
        6: "Livingstone District",
    }
    ward_labels = {
        1: "Ward 1",
        2: "Ward 2",
        3: "Ward 3",
        4: "Ward 4",
        5: "Ward 5",
        6: "Ward 6",
    }
    constituency_labels = {
        1: "Lusaka Central",
        2: "Kafue Central",
        3: "Choma Central",
        4: "Monze Central",
        5: "Mazabuka Central",
        6: "Livingstone Central",
    }
    ODKFormChoice.objects.bulk_create(
        [
            ODKFormChoice(
                form_name="csa_daily_tracker",
                field_name="district",
                value=str(code),
                label=label,
            )
            for code, label in district_labels.items()
        ]
        + [
            ODKFormChoice(
                form_name="csa_daily_tracker",
                field_name="constituency",
                value=str(code),
                label=label,
            )
            for code, label in constituency_labels.items()
        ]
        + [
            ODKFormChoice(
                form_name="csa_daily_tracker",
                field_name="ward",
                value=str(code),
                label=label,
            )
            for code, label in ward_labels.items()
        ]
    )

    total_events = 0
    for idx in range(12):
        count = 12 - idx
        total_events += count
        location_code = (idx % 6) + 1
        raw_district_code = (
            f" {location_code:02d} " if idx < 6 else f"{location_code}.0"
        )
        raw_constituency_code = (
            f" {location_code:02d}.0 " if idx < 6 else f"{location_code:02d}"
        )
        raw_ward_code = f" {location_code}.0 " if idx < 6 else f"{location_code:02d}"
        for event_idx in range(count):
            CSADailyTracker.objects.create(
                key=f"rumor-detail-{idx}-{event_idx}",
                rum="1",
                desci=f"Rumor {idx}",
                sorce_rum="01",
                district=raw_district_code,
                constituency=raw_constituency_code,
                ward=raw_ward_code,
                today="2026-03-01",
            )

    desc_response = client.get(reverse("home:rumor_details_description_table_api"))
    location_response = client.get(reverse("home:rumor_details_enumerator_stats_api"))

    assert desc_response.status_code == 200
    desc_payload = desc_response.json()
    assert desc_payload["page_size"] == 5
    assert desc_payload["total_rows"] == 5
    assert desc_payload["total_pages"] == 1
    assert len(desc_payload["rows"]) == 5
    assert [row["description"] for row in desc_payload["rows"]] == [
        f"Rumor {idx}" for idx in range(5)
    ]
    assert desc_payload["rows"][0]["count"] == 12
    assert desc_payload["rows"][0]["pct"] == round(12 * 100 / total_events, 1)

    assert location_response.status_code == 200
    location_payload = location_response.json()
    assert [row["name"] for row in location_payload["rows"]] == [
        "Lusaka District - Lusaka Central - Ward 1",
        "Kafue District - Kafue Central - Ward 2",
        "Choma District - Choma Central - Ward 3",
        "Monze District - Monze Central - Ward 4",
        "Mazabuka District - Mazabuka Central - Ward 5",
    ]
    assert [row["district"] for row in location_payload["rows"]] == [
        "Lusaka District",
        "Kafue District",
        "Choma District",
        "Monze District",
        "Mazabuka District",
    ]
    assert [row["constituency"] for row in location_payload["rows"]] == [
        "Lusaka Central",
        "Kafue Central",
        "Choma Central",
        "Monze Central",
        "Mazabuka Central",
    ]
    assert [row["ward"] for row in location_payload["rows"]] == [
        "Ward 1",
        "Ward 2",
        "Ward 3",
        "Ward 4",
        "Ward 5",
    ]
    assert [row["count"] for row in location_payload["rows"]] == [18, 16, 14, 12, 10]


def test_rumor_details_top_locations_resolves_separate_location_fields(user: User):
    client = Client()
    client.force_login(user=user)

    ODKFormChoice.objects.bulk_create(
        [
            ODKFormChoice(
                form_name="csa_daily_tracker",
                field_name="district",
                value="1",
                label="Lusaka District",
            ),
            ODKFormChoice(
                form_name="csa_daily_tracker",
                field_name="ward",
                value="1",
                label="Ward 1",
            ),
            ODKFormChoice(
                form_name="csa_daily_tracker",
                field_name="constituency",
                value="1",
                label="Lusaka Central",
            ),
        ]
    )

    # Numeric variants resolve and aggregate across separate location fields.
    CSADailyTracker.objects.create(
        key="rumor-shape-leading-zeroes",
        rum="1",
        desci="Leading zero codes",
        district="01",
        constituency="01",
        ward="1.0",
        today="2026-03-01",
    )
    CSADailyTracker.objects.create(
        key="rumor-shape-float-codes",
        rum="1",
        desci="Float-like codes",
        district="1.0",
        constituency="1.0",
        ward="01",
        today="2026-03-02",
    )
    # Already-resolved labels with no matching ODKFormChoice rows must
    #    pass through unchanged (no `01_1` leak from the old fallback).
    CSADailyTracker.objects.create(
        key="rumor-shape-labels",
        rum="1",
        desci="Pre-resolved labels",
        district="Kafue District",
        constituency="Kafue_Constituency",
        ward="Ward 7",
        today="2026-03-04",
    )

    response = client.get(reverse("home:rumor_details_enumerator_stats_api"))
    assert response.status_code == 200
    rows = response.json()["rows"]
    names = {row["name"] for row in rows}

    assert "Lusaka District - Lusaka Central - Ward 1" in names
    assert "Kafue District - Kafue Constituency - Ward 7" in names
    assert {(row["district"], row["constituency"], row["ward"]) for row in rows} >= {
        ("Lusaka District", "Lusaka Central", "Ward 1"),
        ("Kafue District", "Kafue Constituency", "Ward 7"),
    }
    # No coded leakage should appear in any rendered name.
    for name in names:
        assert "01" not in name
        assert "1.0" not in name
        assert "_" not in name


def test_rumor_overview_trend_tracks_top_five_rumors(user: User):
    client = Client()
    client.force_login(user=user)

    rumor_counts = {
        "Cholera rumor": 6,
        "Measles rumor": 5,
        "Vaccine concern": 4,
        "Clinic closed": 3,
        "Water contamination": 2,
        "Not top five": 1,
    }
    month_by_index = ["2026-01-10", "2026-02-10"]
    for rumor, count in rumor_counts.items():
        for index in range(count):
            CSADailyTracker.objects.create(
                key=f"rumor-trend-{rumor}-{index}",
                rum="1",
                desci=rumor,
                today=month_by_index[index % len(month_by_index)],
            )
    CSADailyTracker.objects.create(
        key="rumor-trend-legacy",
        rum="1",
        desci="Cholera rumor",
        today="2024-12-31",
    )

    response = client.get(reverse("home:rumor_overview_top_rumors_trend_api"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_rumors"] is True
    assert payload["labels"] == ["Jan 2026", "Feb 2026"]
    assert [series["name"] for series in payload["series"]] == [
        "Cholera rumor",
        "Measles rumor",
        "Vaccine concern",
        "Clinic closed",
        "Water contamination",
    ]
    assert payload["series"][0]["values"] == [3, 3]


def test_rumor_overview_trend_excludes_pre_2025_dates(user: User):
    client = Client()
    client.force_login(user=user)

    CSADailyTracker.objects.create(
        key="rumor-overview-legacy",
        rum="1",
        desci="Legacy rumor",
        today="2024-12-31",
    )
    CSADailyTracker.objects.create(
        key="rumor-overview-current",
        rum="1",
        desci="Current rumor",
        today="2025-01-01",
    )

    response = client.get(reverse("home:rumor_overview_trend_api"))

    assert response.status_code == 200
    assert response.json() == {"labels": ["Jan 2025"], "data": [1]}


def test_regional_operations_components_refresh_independently(user: User):
    client = Client()
    client.force_login(user=user)

    filters_response = client.get(
        "/regional-operations/components/filters/",
        {"geography": "lusaka", "time_preset": "time7"},
        follow=True,
    )
    assert filters_response.status_code == 200
    assert b'id="geographyFilterSelect"' in filters_response.content
    assert b'value="lusaka" selected' in filters_response.content

    csa_response = client.get(
        "/regional-operations/components/csa/",
        {"geography": "lusaka", "csa_sort": "deaths", "csa_dir": "asc"},
        follow=True,
    )
    assert csa_response.status_code == 200
    assert b"CSA Name" in csa_response.content
    assert b'id="csaSupervisorSearch"' in csa_response.content
    assert b"regional-sort-link" in csa_response.content
    assert b"regional-page-link" in csa_response.content

    mso_response = client.get(
        "/regional-operations/components/mso/",
        {"source": "facility", "mso_sort": "error", "mso_dir": "asc"},
        follow=True,
    )
    assert mso_response.status_code == 200
    assert b'id="msoSourceSelect"' in mso_response.content
    assert b'value="facility" selected' in mso_response.content
    assert b"regional-sort-link" in mso_response.content
    assert b"regional-page-link" in mso_response.content


def test_regional_operations_mso_populated_when_events_unlinked(user: User):
    client = Client()
    client.force_login(user=user)

    VerbalAutopsyFactory.create(
        Id10010="MSO Alpha",
        Id10012="2024-02-01",
        Id10011="09:00:00",
        Id10481="2024-02-01 10:00:00",
        province="Lusaka",
    )
    VerbalAutopsyFactory.create(
        Id10010="MSO Alpha",
        Id10012="2024-02-02",
        Id10011="09:00:00",
        Id10481="2024-02-02 09:20:00",
        province="Lusaka",
    )

    # Unlinked events: no VA and no Death link.
    Event.objects.create(
        event_type=Event.EventType.DEATH,
        event_status=Event.EventStatus.VA_INTERVIEW_SCHEDULED,
        va_interview_status=Event.VAInterviewStatus.SCHEDULED,
        interview_scheduled_date=date(2024, 2, 3),
        province="Lusaka",
        district="Lusaka",
        ward="Ward 1",
        ea="EA 1",
        supervisor="Unlinked Supervisor",
    )
    Event.objects.create(
        event_type=Event.EventType.DEATH,
        va_interview_status=Event.VAInterviewStatus.SCHEDULED,
        interview_complete_date=None,
        province="Lusaka",
        district="Lusaka",
        ward="Ward 1",
        ea="EA 1",
        supervisor="Unlinked Supervisor",
    )

    response = client.get("/regional-operations/components/mso/", follow=True)
    assert response.status_code == 200

    mso_stats = response.context["mso_stats"]
    assert len(mso_stats) > 0
    assert any(row.get("va_total", 0) > 0 for row in mso_stats)


def test_mso_dedups_casing_sums_metrics_and_drops_junk(user: User):
    client = Client()
    client.force_login(user=user)

    # Same person recorded in two casings -> collapses to one canonical row
    # (mixed-case wins) whose VA total is the sum of every casing variant.
    VerbalAutopsyFactory.create(
        Id10010="Lweendo Namitondo", Id10012="2024-02-01", province="Lusaka"
    )
    VerbalAutopsyFactory.create(
        Id10010="LWEENDO NAMITONDO", Id10012="2024-02-02", province="Lusaka"
    )
    VerbalAutopsyFactory.create(
        Id10010="LWEENDO NAMITONDO", Id10012="2024-02-03", province="Lusaka"
    )

    # Junk/placeholder names that must never appear in the table.
    for junk in ("Unassigned MSO", "Facility", "Facility Z", "Facility 1", "Unknown"):
        VerbalAutopsyFactory.create(
            Id10010=junk, Id10012="2024-02-04", province="Lusaka"
        )

    response = client.get(
        "/regional-operations/components/mso/",
        {"time_preset": "timeAll"},
        follow=True,
    )
    assert response.status_code == 200

    rows = response.context["mso_stats"]
    names = [row["name"] for row in rows]

    # Exactly one canonical, mixed-case row with merged totals.
    assert names.count("Lweendo Namitondo") == 1
    assert "LWEENDO NAMITONDO" not in names
    lweendo = next(row for row in rows if row["name"] == "Lweendo Namitondo")
    assert lweendo["va_total"] == 3

    # No junk rows survive.
    for junk in ("Unassigned MSO", "Facility", "Facility Z", "Facility 1", "Unknown"):
        assert junk not in names


def test_mso_uses_only_va_base_names_and_fuzzy_maps_event_aggregates(user: User):
    client = Client()
    client.force_login(user=user)

    # In-scope VA base row.
    VerbalAutopsyFactory.create(
        Id10010="Jane Doe",
        Id10012="2024-02-02",
        province="Lusaka",
    )
    # Out-of-scope VA for base (outside manual date range), but linked events fall in range.
    out_of_scope_va = VerbalAutopsyFactory.create(
        Id10010="Jane-Doe",
        Id10012="2023-01-01",
        province="Lusaka",
    )

    death = Death(
        DE_06="2024-02-03",
        province="Lusaka",
        enumerator="CSA_One_101",
    )
    Death.objects.bulk_create([death])
    Event.objects.create(
        va=out_of_scope_va,
        death=death,
        event_type=Event.EventType.DEATH,
        event_status=Event.EventStatus.VA_INTERVIEW_SCHEDULED,
        va_interview_status=Event.VAInterviewStatus.SCHEDULED,
        interview_scheduled_date=date(2024, 2, 4),
        interview_complete_date=date(2024, 2, 5),
        province="Lusaka",
        district="Lusaka",
        ward="Ward 1",
        ea="EA 1",
        supervisor="CSA Supervisor",
        enumerator="CSA Enumerator",
    )

    response = client.get(
        "/regional-operations/components/mso/",
        {
            "start_date": "2024-02-01",
            "end_date": "2024-02-10",
        },
        follow=True,
    )
    assert response.status_code == 200
    mso_stats = response.context["mso_stats"]
    assert mso_stats

    names = [row["name"] for row in mso_stats]
    assert "Jane Doe" in names
    assert "CSA Supervisor" not in names
    assert "CSA Enumerator" not in names
    assert "CSA_One_101" not in names

    jane_row = next(row for row in mso_stats if row["name"] == "Jane Doe")
    assert jane_row["death_events"] > 0
    assert jane_row["va_scheduled"] > 0


def test_mso_component_filters_by_province_search(user: User):
    client = Client()
    client.force_login(user=user)

    VerbalAutopsyFactory.create(
        Id10010="MSO Lusaka",
        Id10012="2024-02-02",
        province="Lusaka",
    )
    VerbalAutopsyFactory.create(
        Id10010="MSO Copperbelt",
        Id10012="2024-02-02",
        province="Copperbelt",
    )

    response = client.get(
        "/regional-operations/components/mso/",
        {"mso_province_search": "lusa"},
        follow=True,
    )

    assert response.status_code == 200
    assert response.context["mso_province_search"] == "lusa"
    assert [row["province"] for row in response.context["mso_stats"]] == ["Lusaka"]


def test_regional_operations_filter_defaults_per_tab(user: User):
    client = Client()
    client.force_login(user=user)

    # CSA filter (no scope) defaults to the last 7 days.
    csa_response = client.get("/regional-operations/components/filters/", follow=True)
    assert csa_response.status_code == 200
    assert csa_response.context["selected_time_preset"] == "time7"

    # MSO filter keeps the all-time default.
    mso_response = client.get(
        "/regional-operations/components/filters/",
        {"filter_scope": "mso"},
        follow=True,
    )
    assert mso_response.status_code == 200
    assert mso_response.context["selected_time_preset"] == "timeAll"


# Get the about page and make sure it returns successfully
def test_about(user: User):
    client = Client()
    client.force_login(user=user)
    response = client.get("/about/")
    assert response.status_code == 200


@time_machine.travel(dt.datetime(2026, 3, 29, 12, 0, tzinfo=eastern_tz))
def test_csa_component_enriches_supervisor_target_and_coverage(user: User):
    client = Client()
    client.force_login(user=user)

    CSASupervisorTargetMap.objects.create(
        csa_name_raw="Alice_1",
        csa_display_name="Alice",
        csa_name_normalized="alice",
        supervisor_name="Supervisor Alpha",
        csa_target=10,
        status=CSASupervisorTargetMap.STATUS_ACTIVE,
    )

    # Explicit rolling 7-day CSA supervision requests exclude older tracker rows.
    CSADailyTracker.objects.create(
        key="k1",
        enumerator="Alice_1",
        province="Lusaka",
        district="Lusaka",
        ward="Ward 1",
        today="2026-03-28",
        num_death="1",
        num_preg="0",
        num_outcome="0",
    )
    CSADailyTracker.objects.create(
        key="k2",
        enumerator="Alice_1",
        province="Lusaka",
        district="Lusaka",
        ward="Ward 1",
        today="2026-03-10",
        num_death="0",
        num_preg="1",
        num_outcome="0",
    )

    response = client.get(
        "/regional-operations/components/csa/",
        {"time_preset": "time7"},
        follow=True,
    )
    assert response.status_code == 200
    assert b"Supervisor" in response.content
    assert b"CSA Target" in response.content
    assert b"Coverage %" in response.content

    rows = response.context["csa_stats"]
    alice = next(row for row in rows if row["name"] == "Alice")
    assert alice["supervisor"] == "Supervisor Alpha"
    assert alice["status"] == CSASupervisorTargetMap.STATUS_ACTIVE
    assert alice["csa_target"] == 10
    assert alice["visits"] == 1
    assert alice["coverage_pct"] == 10.0


@time_machine.travel(dt.datetime(2026, 3, 29, 12, 0, tzinfo=eastern_tz))
def test_csa_component_scales_displayed_target_by_window(user: User):
    client = Client()
    client.force_login(user=user)

    CSASupervisorTargetMap.objects.create(
        csa_name_raw="Alice_1",
        csa_display_name="Alice",
        csa_name_normalized="alice",
        supervisor_name="Supervisor Alpha",
        csa_target=10,
        tracker_first_date=date(2026, 3, 10),
        status=CSASupervisorTargetMap.STATUS_ACTIVE,
    )
    for key, tracker_date in (("k1", "2026-03-28"), ("k2", "2026-03-10")):
        CSADailyTracker.objects.create(
            key=key,
            enumerator="Alice_1",
            province="Lusaka",
            district="Lusaka",
            ward="Ward 1",
            today=tracker_date,
            num_death="0",
            num_preg="0",
            num_outcome="0",
        )

    response_30 = client.get(
        "/regional-operations/components/csa/",
        {"time_preset": "time30"},
        follow=True,
    )
    alice_30 = next(
        row for row in response_30.context["csa_stats"] if row["name"] == "Alice"
    )
    assert alice_30["csa_target"] == 43
    assert alice_30["coverage_pct"] == 4.7

    response_24 = client.get(
        "/regional-operations/components/csa/",
        {"time_preset": "time24"},
        follow=True,
    )
    alice_24 = next(
        row for row in response_24.context["csa_stats"] if row["name"] == "Alice"
    )
    assert alice_24["csa_target"] == 1
    assert alice_24["coverage_pct"] == 70.0

    response_custom = client.get(
        "/regional-operations/components/csa/",
        {"start_date": "2026-03-20", "end_date": "2026-03-29"},
        follow=True,
    )
    alice_custom = next(
        row for row in response_custom.context["csa_stats"] if row["name"] == "Alice"
    )
    assert alice_custom["csa_target"] == 14
    assert alice_custom["coverage_pct"] == 7.0

    response_all = client.get(
        "/regional-operations/components/csa/",
        {"time_preset": "timeAll"},
        follow=True,
    )
    alice_all = next(
        row for row in response_all.context["csa_stats"] if row["name"] == "Alice"
    )
    assert alice_all["csa_target"] == 29
    assert alice_all["coverage_pct"] == 7.0


def test_csa_component_handles_unmatched_mapping_safely(user: User):
    client = Client()
    client.force_login(user=user)

    CSADailyTracker.objects.create(
        key="k3",
        enumerator="No_Map_CSA_9",
        province="Lusaka",
        district="Lusaka",
        ward="Ward 2",
        today="2026-03-28",
        num_death="0",
        num_preg="1",
        num_outcome="1",
    )

    response = client.get(
        "/regional-operations/components/csa/",
        {"time_preset": "timeAll"},
        follow=True,
    )
    assert response.status_code == 200

    rows = response.context["csa_stats"]
    row = next(r for r in rows if r["name"] == "No Map CSA")
    assert row["supervisor"] == "—"
    assert row["csa_target"] == 0
    assert row["status"] == CSASupervisorTargetMap.STATUS_INACTIVE
    assert row["coverage_pct"] is None


def test_csa_component_renders_totals_row_spanning_all_filtered_rows(user: User):
    client = Client()
    client.force_login(user=user)

    # Twelve CSAs force a second page (the table caps at 10 rows).
    for idx in range(12):
        name = f"CSA{idx:02d}"
        CSASupervisorTargetMap.objects.create(
            csa_name_raw=f"{name}_{idx}",
            csa_display_name=name,
            csa_name_normalized=name.lower(),
            supervisor_name="Supervisor",
            csa_target=10,
            status=CSASupervisorTargetMap.STATUS_ACTIVE,
        )
        CSADailyTracker.objects.create(
            key=f"csa-total-{idx}",
            enumerator=f"{name}_{idx}",
            province="Lusaka",
            district="Lusaka",
            ward="Ward 1",
            today="2026-03-15",
            num_death="1",
            num_preg="0",
            num_outcome="0",
        )

    response = client.get(
        "/regional-operations/components/csa/",
        {"time_preset": "timeAll"},
        follow=True,
    )
    assert response.status_code == 200

    # The visible page is capped at 10 rows; the totals span all 12 CSAs.
    assert len(response.context["csa_stats"]) == 10
    csa_total = response.context["csa_total"]
    assert csa_total is not None
    assert csa_total["visits"] == 12
    assert csa_total["deaths"] == 12
    assert csa_total["events"] == 12
    # Coverage is recomputed from the summed visits / target (12 / 120), not an
    # average of the per-row percentages.
    assert csa_total["csa_target"] == 120
    assert csa_total["coverage_pct"] == 10.0
    assert csa_total["coverage_band"] == "red"
    assert b"csa-total-row" in response.content
    assert b"Total / Average" in response.content


def test_csa_totals_reflect_active_filters(user: User):
    client = Client()
    client.force_login(user=user)

    specs = (("AlphaCsa", "Lusaka"), ("BetaCsa", "Lusaka"), ("GammaCsa", "Southern"))
    for idx, (name, province) in enumerate(specs):
        CSASupervisorTargetMap.objects.create(
            csa_name_raw=f"{name}_{idx}",
            csa_display_name=name,
            csa_name_normalized=name.lower(),
            supervisor_name="Supervisor",
            csa_target=10,
            status=CSASupervisorTargetMap.STATUS_ACTIVE,
        )
        CSADailyTracker.objects.create(
            key=f"geo-{idx}",
            enumerator=f"{name}_{idx}",
            province=province,
            district="Lusaka",
            ward="Ward 1",
            today="2026-03-15",
            num_death="1",
            num_preg="0",
            num_outcome="0",
        )

    national = client.get(
        "/regional-operations/components/csa/",
        {"time_preset": "timeAll"},
        follow=True,
    )
    assert national.context["csa_total"]["deaths"] == 3

    # Narrowing the geography filter shrinks the totals to the Lusaka CSAs.
    lusaka = client.get(
        "/regional-operations/components/csa/",
        {"time_preset": "timeAll", "geography": "lusaka"},
        follow=True,
    )
    assert lusaka.context["csa_total"]["deaths"] == 2


@time_machine.travel(dt.datetime(2026, 4, 26, 12, 0, tzinfo=eastern_tz))
def test_csa_component_defaults_to_last_seven_days(user: User):
    client = Client()
    client.force_login(user=user)

    # Within the last 7 days (2026-04-20..04-26): shown by the default window.
    CSADailyTracker.objects.create(
        key="recent-default-row",
        enumerator="Recent_CSA_1",
        province="Lusaka",
        district="Lusaka",
        ward="Ward 1",
        today="2026-04-22",
    )
    # Older than 7 days: hidden by the default 7-day window.
    CSADailyTracker.objects.create(
        key="older-default-row",
        enumerator="Older_CSA_1",
        province="Lusaka",
        district="Lusaka",
        ward="Ward 1",
        today="2026-03-28",
    )

    response = client.get("/regional-operations/components/csa/", follow=True)

    assert response.status_code == 200
    names = [row["name"] for row in response.context["csa_stats"]]
    assert "Recent CSA" in names
    assert "Older CSA" not in names


@time_machine.travel(dt.datetime(2026, 3, 29, 12, 0, tzinfo=eastern_tz))
def test_csa_component_matches_mapping_by_display_name_when_normalized_differs(
    user: User,
):
    client = Client()
    client.force_login(user=user)

    CSASupervisorTargetMap.objects.create(
        csa_name_raw="Alice A",
        csa_display_name="Alice A",
        csa_name_normalized="alice-a-custom",
        supervisor_name="Supervisor Beta",
        csa_target=8,
    )

    CSADailyTracker.objects.create(
        key="k4",
        enumerator="Alice_A_1",
        province="Lusaka",
        district="Lusaka",
        ward="Ward 1",
        today="2026-03-28",
        num_death="0",
        num_preg="1",
        num_outcome="0",
    )

    response = client.get(
        "/regional-operations/components/csa/",
        {"time_preset": "time7"},
        follow=True,
    )
    assert response.status_code == 200

    rows = response.context["csa_stats"]
    alice = next(row for row in rows if row["name"] == "Alice A")
    assert alice["supervisor"] == "Supervisor Beta"
    assert alice["csa_target"] == 8
    assert alice["coverage_pct"] == 12.5


def test_csa_component_filters_by_supervisor_district_and_ward(user: User):
    client = Client()
    client.force_login(user=user)

    CSASupervisorTargetMap.objects.create(
        csa_name_raw="Alice_1",
        csa_display_name="Alice",
        csa_name_normalized="alice",
        supervisor_name="Supervisor Alpha",
        csa_target=10,
    )
    CSASupervisorTargetMap.objects.create(
        csa_name_raw="Bob_1",
        csa_display_name="Bob",
        csa_name_normalized="bob",
        supervisor_name="Supervisor Beta",
        csa_target=10,
    )
    CSADailyTracker.objects.create(
        key="filter-a",
        enumerator="Alice_1",
        province="Lusaka",
        district="Lusaka",
        ward="Ward 1",
        today=date.today().isoformat(),
    )
    CSADailyTracker.objects.create(
        key="filter-b",
        enumerator="Bob_1",
        province="Lusaka",
        district="Kafue",
        ward="Ward 2",
        today=date.today().isoformat(),
    )

    response = client.get(
        "/regional-operations/components/csa/",
        {
            "csa_supervisor_search": "beta",
            "csa_district_search": "kaf",
            "csa_ward_search": "2",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert response.context["csa_supervisor_search"] == "beta"
    assert response.context["csa_district_search"] == "kaf"
    assert response.context["csa_ward_search"] == "2"
    assert [row["name"] for row in response.context["csa_stats"]] == ["Bob"]


def test_csa_component_paginates_ten_rows_per_page(user: User):
    client = Client()
    client.force_login(user=user)

    for idx in range(11):
        CSADailyTracker.objects.create(
            key=f"page-{idx}",
            enumerator=f"CSA_{idx}_Name",
            province="Lusaka",
            district="Lusaka",
            ward="Ward 1",
            today=date.today().isoformat(),
        )

    response = client.get("/regional-operations/components/csa/", follow=True)

    assert response.status_code == 200
    assert response.context["csa_visible_count"] == 10
    assert response.context["csa_total_count"] == 11
    assert response.context["csa_total_pages"] == 2


def _csv_response_rows(response):
    return list(csv.DictReader(io.StringIO(response.content.decode())))


def test_csa_component_renders_download_button(user: User):
    client = Client()
    client.force_login(user=user)

    response = client.get("/regional-operations/components/csa/", follow=True)

    assert response.status_code == 200
    assert b"Download CSV" in response.content
    assert (
        reverse("home:regional_operations_csa_csv_export").encode() in response.content
    )


def test_csa_csv_export_returns_full_unfiltered_dataset(user: User):
    client = Client()
    client.force_login(user=user)

    for name, supervisor, district in (
        ("Alice", "Supervisor Alpha", "Lusaka"),
        ("Bob", "Supervisor Beta", "Kafue"),
    ):
        CSASupervisorTargetMap.objects.create(
            csa_name_raw=f"{name}_1",
            csa_display_name=name,
            csa_name_normalized=name.lower(),
            supervisor_name=supervisor,
            csa_target=10,
            status=CSASupervisorTargetMap.STATUS_ACTIVE,
        )
        CSADailyTracker.objects.create(
            key=f"export-{name}",
            enumerator=f"{name}_1",
            province="Lusaka",
            district=district,
            ward="Ward 1",
            today=date.today().isoformat(),
            num_death="1",
            num_preg="1",
            num_outcome="0",
        )

    response = client.get(
        reverse("home:regional_operations_csa_csv_export"),
        {"csa_search": "Alice", "csa_supervisor_search": "Alpha"},
    )

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert "attachment;" in response["Content-Disposition"]
    rows = _csv_response_rows(response)
    names = {row["CSA Name"] for row in rows}
    assert {"Alice", "Bob", "Total / Average"}.issubset(names)
    alice = next(row for row in rows if row["CSA Name"] == "Alice")
    assert alice["Supervisor"] == "Supervisor Alpha"
    assert alice["Deaths"] == "1"
    assert alice["Pregnancies"] == "1"


def test_csa_csv_export_empty_dataset_has_headers(user: User):
    client = Client()
    client.force_login(user=user)

    response = client.get(reverse("home:regional_operations_csa_csv_export"))

    assert response.status_code == 200
    assert response.content.decode().splitlines()[0].startswith("CSA Name,Province")
    assert _csv_response_rows(response) == []


def test_mso_component_paginates_ten_rows_per_page(user: User):
    client = Client()
    client.force_login(user=user)

    for idx in range(11):
        VerbalAutopsyFactory.create(
            Id10010=f"MSO {idx} Name",
            Id10012="2024-02-01",
            Id10011="09:00:00",
            Id10481="2024-02-01 09:30:00",
            province="Lusaka",
        )

    response = client.get("/regional-operations/components/mso/", follow=True)

    assert response.status_code == 200
    assert response.context["mso_visible_count"] == 10
    assert response.context["mso_total_count"] == 11
    assert response.context["mso_total_pages"] == 2


@time_machine.travel(dt.datetime(2026, 3, 29, 12, 0, tzinfo=eastern_tz))
def test_refresh_csa_first_dates_sets_status_from_recent_tracker_dates(user: User):
    CSASupervisorTargetMap.objects.create(
        csa_name_raw="Active_1",
        csa_display_name="Active",
        csa_name_normalized="active",
        supervisor_name="Supervisor Active",
        csa_target=10,
    )
    CSASupervisorTargetMap.objects.create(
        csa_name_raw="Inactive_1",
        csa_display_name="Inactive",
        csa_name_normalized="inactive",
        supervisor_name="Supervisor Inactive",
        csa_target=10,
        status=CSASupervisorTargetMap.STATUS_ACTIVE,
    )
    CSADailyTracker.objects.create(
        key="recent",
        enumerator="Active_1",
        today="2026-03-16",
    )
    CSADailyTracker.objects.create(
        key="stale",
        enumerator="Inactive_1",
        today="2026-02-27",
    )

    call_command("refresh_csa_first_dates")

    active = CSASupervisorTargetMap.objects.get(csa_name_normalized="active")
    inactive = CSASupervisorTargetMap.objects.get(csa_name_normalized="inactive")
    assert active.tracker_first_date == date(2026, 3, 16)
    assert active.status == CSASupervisorTargetMap.STATUS_ACTIVE
    assert inactive.tracker_first_date == date(2026, 2, 27)
    assert inactive.status == CSASupervisorTargetMap.STATUS_INACTIVE
