import datetime as dt
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

    # Check that underlying graph data are cumulative and non-decreasing.
    # The 12-month VA chart excludes current month; records in range are:
    # October (previous year), April, and September.
    assert json_data["graphs"]["collected"]["y"] == [
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
        2.0,
        2.0,
        2.0,
        2.0,
        2.0,
        3.0,
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
    # Uncoded trend is also cumulative.
    assert json_data["graphs"]["uncoded"]["y"] == [
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
        2.0,
        2.0,
        2.0,
        2.0,
        2.0,
        3.0,
    ]

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
    southern = LocationFactory.create(name="Southern", location_type="province")
    southern_district = southern.add_child(name="Choma", location_type="district")
    southern_facility = southern_district.add_child(
        name="Choma Facility", location_type="facility"
    )

    lusaka = LocationFactory.create(name="Lusaka", location_type="province")
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

    Death.objects.create(
        key="death-southern",
        submissiondate=today,
        today=today,
        start=today,
        province="Southern",
        district="Choma",
        DE_06=today,
    )
    Death.objects.create(
        key="death-lusaka",
        submissiondate=today,
        today=today,
        start=today,
        province="Lusaka",
        district="Lusaka",
        DE_06=today,
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
        Death.objects.create(
            key=f"death-{index}",
            submissiondate=recorded_on,
            today=recorded_on,
            start=recorded_on,
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


def test_home_dashboard_overview_chart_is_scoped_to_user_province():
    cache.clear()
    southern = _seed_home_dashboard_scope_data()
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
    )
    VerbalAutopsyFactory.create(
        Id10010="MSO Alpha",
        Id10012="2024-02-02",
        Id10011="09:00:00",
        Id10481="2024-02-02 09:20:00",
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

    death = Death.objects.create(
        DE_06="2024-02-03",
        province="Lusaka",
        enumerator="CSA_One_101",
    )
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


def test_regional_operations_filters_default_to_all_time(user: User):
    client = Client()
    client.force_login(user=user)

    response = client.get("/regional-operations/components/filters/", follow=True)

    assert response.status_code == 200
    assert response.context["selected_time_preset"] == "timeAll"


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

    response = client.get("/regional-operations/components/csa/", follow=True)
    assert response.status_code == 200

    rows = response.context["csa_stats"]
    row = next(r for r in rows if r["name"] == "No Map CSA")
    assert row["supervisor"] == "—"
    assert row["csa_target"] == 0
    assert row["status"] == CSASupervisorTargetMap.STATUS_INACTIVE
    assert row["coverage_pct"] is None


@time_machine.travel(dt.datetime(2026, 4, 26, 12, 0, tzinfo=eastern_tz))
def test_csa_component_default_all_time_shows_available_tracker_rows(user: User):
    client = Client()
    client.force_login(user=user)

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
    assert any(row["name"] == "Older CSA" for row in response.context["csa_stats"])


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
            enumerator=f"CSA_{idx}",
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


def test_mso_component_paginates_ten_rows_per_page(user: User):
    client = Client()
    client.force_login(user=user)

    for idx in range(11):
        VerbalAutopsyFactory.create(
            Id10010=f"MSO {idx}",
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
