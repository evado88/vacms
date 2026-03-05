import datetime as dt
import json
from datetime import date, datetime

import pytest
import time_machine
from dateutil.relativedelta import relativedelta
from dateutil.tz import gettz
from django.contrib.auth.models import Permission
from django.core.cache import cache
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
from va_explorer.va_data_management.models import (
    Death,
    Household,
    Pregnancy,
    PregnancyOutcome,
)
from va_explorer.users.models import User
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


def test_regional_operations_page_loads(user: User):
    client = Client()
    client.force_login(user=user)

    response = client.get("/regional-operations/", follow=True)
    assert response.status_code == 200
    assert b"id=\"regionalFiltersComponent\"" in response.content
    assert b"id=\"regionalCsaComponent\"" in response.content
    assert b"id=\"regionalMsoComponent\"" in response.content


def test_regional_operations_components_refresh_independently(user: User):
    client = Client()
    client.force_login(user=user)

    filters_response = client.get(
        "/regional-operations/components/filters/",
        {"geography": "lusaka", "time_preset": "time7"},
        follow=True,
    )
    assert filters_response.status_code == 200
    assert b"id=\"geographyFilterSelect\"" in filters_response.content
    assert b"value=\"lusaka\" selected" in filters_response.content

    csa_response = client.get(
        "/regional-operations/components/csa/",
        {"geography": "lusaka", "csa_sort": "deaths", "csa_dir": "asc"},
        follow=True,
    )
    assert csa_response.status_code == 200
    assert b"CSA Name (Search)" in csa_response.content
    assert b"regional-sort-link" in csa_response.content
    assert b"regional-page-link" in csa_response.content

    mso_response = client.get(
        "/regional-operations/components/mso/",
        {"source": "facility", "mso_sort": "error", "mso_dir": "asc"},
        follow=True,
    )
    assert mso_response.status_code == 200
    assert b"id=\"msoSourceSelect\"" in mso_response.content
    assert b"value=\"facility\" selected" in mso_response.content
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
    in_scope_va = VerbalAutopsyFactory.create(
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


# Get the about page and make sure it returns successfully
def test_about(user: User):
    client = Client()
    client.force_login(user=user)
    response = client.get("/about/")
    assert response.status_code == 200
