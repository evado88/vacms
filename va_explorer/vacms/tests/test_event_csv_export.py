import csv
import io

import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from va_explorer.tests.factories import UserFactory
from va_explorer.va_data_management.models import Death
from va_explorer.vacms.cmsmodels.events import Event

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def disable_death_id_task(monkeypatch):
    from va_explorer.va_data_management.tasks import assign_death_ids_task

    monkeypatch.setattr(assign_death_ids_task, "delay", lambda *args, **kwargs: None)


def _group(name):
    group, _ = Group.objects.get_or_create(name=name)
    return group


def _viewer():
    user = UserFactory()
    user.groups.add(_group("Data Managers"))
    return user


def _make_death(name):
    return Death.objects.create(
        DE_03=name,
        DE_04="1980-01-01",
        DE_05="Male",
        DE_06="2023-01-01",
    )


def _parse_csv(response):
    return list(csv.reader(io.StringIO(response.content.decode())))


def test_csv_export_headers_and_content_type(client):
    client.force_login(_viewer())

    response = client.get(reverse("cms-event-list-csv"))

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    disposition = response["Content-Disposition"]
    assert "attachment" in disposition
    assert ".csv" in disposition

    rows = _parse_csv(response)
    assert rows[0] == [
        "ID",
        "Deceased Name",
        "Death ID",
        "Key",
        "Interviewer",
        "Province",
        "District",
        "Event Type",
        "Event Status",
        "VA Interview Status",
        "VA Interview Scheduled Date",
        "VA Interview Complete Date",
        "Supervisor",
        "Respondent Name",
        "Respondent Phone",
        "Not Done Reason",
    ]


def test_csv_export_respects_status_filter(client):
    client.force_login(_viewer())

    completed = Event.objects.create(
        event_type=Event.EventType.DEATH,
        event_status=Event.EventStatus.VA_INTERVIEW_COMPLETED,
        va_interview_status=Event.VAInterviewStatus.COMPLETED,
        death=_make_death("Completed Person"),
        province="Lusaka",
    )
    scheduled = Event.objects.create(
        event_type=Event.EventType.DEATH,
        event_status=Event.EventStatus.VA_INTERVIEW_SCHEDULED,
        va_interview_status=Event.VAInterviewStatus.SCHEDULED,
        death=_make_death("Scheduled Person"),
        province="Lusaka",
    )

    response = client.get(
        reverse("cms-event-list-csv"), {"interview_status": "completed"}
    )

    rows = _parse_csv(response)
    ids = {row[0] for row in rows[1:]}
    assert str(completed.id) in ids
    assert str(scheduled.id) not in ids


def test_unscheduled_filter_matches_list_and_csv(client):
    client.force_login(_viewer())

    unscheduled = Event.objects.create(
        event_type=Event.EventType.DEATH,
        event_status=Event.EventStatus.NEW_EVENT,
        death=_make_death("Pipeline Person"),
        province="Lusaka",
    )
    va_stage = Event.objects.create(
        event_type=Event.EventType.DEATH,
        event_status=Event.EventStatus.VA_INTERVIEW_SCHEDULED,
        va_interview_status=Event.VAInterviewStatus.SCHEDULED,
        death=_make_death("VA Stage Person"),
        province="Lusaka",
    )

    csv_response = client.get(
        reverse("cms-event-list-csv"), {"interview_status": "unscheduled"}
    )
    csv_ids = {row[0] for row in _parse_csv(csv_response)[1:]}
    assert str(unscheduled.id) in csv_ids
    assert str(va_stage.id) not in csv_ids

    list_response = client.get(
        reverse("cms-event-list"), {"interview_status": "unscheduled"}
    )
    list_ids = {event.id for event in list_response.context["object_list"]}
    assert list_ids == {unscheduled.id}


def _csv_dict_rows(response):
    return list(csv.DictReader(io.StringIO(response.content.decode())))


def test_csv_includes_death_fields_and_fallbacks(client):
    client.force_login(_viewer())

    death = Death.objects.create(
        DE_03="Jane Deceased",
        DE_04="1980-01-01",
        DE_05="Female",
        DE_06="2023-01-01",
        key="DKEY-1",
        supervisor="Sup From Death",
        DE_19="Informant Jane",
        DE_26="0977000111",
    )
    # Event with blank supervisor/respondent -> falls back to the death record.
    fallback_event = Event.objects.create(
        event_type=Event.EventType.DEATH,
        event_status=Event.EventStatus.VA_INTERVIEW_SCHEDULED,
        va_interview_status=Event.VAInterviewStatus.SCHEDULED,
        death=death,
        province="Lusaka",
    )
    # Event with its own values -> event values win over the death record.
    death2 = Death.objects.create(
        DE_03="John Deceased",
        DE_04="1975-01-01",
        DE_05="Male",
        DE_06="2023-02-01",
        key="DKEY-2",
        supervisor="Sup From Death 2",
        DE_19="Informant John",
        DE_26="0966000222",
    )
    event_wins = Event.objects.create(
        event_type=Event.EventType.DEATH,
        event_status=Event.EventStatus.VA_INTERVIEW_SCHEDULED,
        va_interview_status=Event.VAInterviewStatus.SCHEDULED,
        death=death2,
        province="Lusaka",
        supervisor="Sup From Event",
        respondent_name="Event Respondent",
        respondent_phone="0955000333",
    )

    rows = {
        row["ID"]: row
        for row in _csv_dict_rows(client.get(reverse("cms-event-list-csv")))
    }

    fb = rows[str(fallback_event.id)]
    assert fb["Deceased Name"] == "Jane Deceased"
    assert fb["Key"] == "DKEY-1"
    assert fb["Death ID"] == str(death.id)
    assert fb["Supervisor"] == "Sup From Death"
    assert fb["Respondent Name"] == "Informant Jane"
    assert fb["Respondent Phone"] == "0977000111"

    ew = rows[str(event_wins.id)]
    assert ew["Deceased Name"] == "John Deceased"
    assert ew["Death ID"] == str(death2.id)
    assert ew["Supervisor"] == "Sup From Event"
    assert ew["Respondent Name"] == "Event Respondent"
    assert ew["Respondent Phone"] == "0955000333"


def test_csv_export_forbidden_without_view_rights(client):
    user = UserFactory()
    user.denied_permissions = ["vacms.view_event", "vacms.change_event"]
    user.save()
    client.force_login(user)

    response = client.get(reverse("cms-event-list-csv"))

    assert response.status_code == 403
