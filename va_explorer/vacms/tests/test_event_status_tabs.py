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


def _viewer():
    user = UserFactory()
    group, _ = Group.objects.get_or_create(name="Data Managers")
    user.groups.add(group)
    return user


def _event(status, name, province="Lusaka", ward="Central Ward", ea="EA 1"):
    # Province filtering matches death__province, so set it on the Death record.
    death = Death.objects.create(
        DE_03=name,
        DE_04="1980-01-01",
        DE_05="Male",
        DE_06="2023-01-01",
        province=province,
        ward=ward,
        ea=ea,
    )
    return Event.objects.create(
        event_type=Event.EventType.DEATH,
        event_status=Event.EventStatus.VA_INTERVIEW_SCHEDULED,
        va_interview_status=status,
        death=death,
        province=province,
    )


def _make_all():
    return {
        "scheduled": _event(Event.VAInterviewStatus.SCHEDULED, "Sched Person"),
        "completed": _event(Event.VAInterviewStatus.COMPLETED, "Done Person"),
        "not_done": _event(Event.VAInterviewStatus.NOT_DONE, "NotDone Person"),
        "postponed": _event(Event.VAInterviewStatus.POSTPONED, "Postponed Person"),
    }


def _component_ids(client, **params):
    params["component"] = "1"
    response = client.get(reverse("cms-event-list"), params)
    assert response.status_code == 200
    return response, {e.id for e in response.context["object_list"]}


def test_default_tab_shows_only_scheduled(client):
    client.force_login(_viewer())
    events = _make_all()

    _, ids = _component_ids(client)

    assert events["scheduled"].id in ids
    assert events["completed"].id not in ids
    assert events["not_done"].id not in ids
    assert events["postponed"].id not in ids


def test_completed_tab_shows_only_completed_and_marks_active(client):
    client.force_login(_viewer())
    events = _make_all()

    response, ids = _component_ids(client, interview_status="completed")

    assert ids == {events["completed"].id}
    active = {t["value"]: t["active"] for t in response.context["status_tabs"]}
    assert active[Event.VAInterviewStatus.COMPLETED] is True
    assert active[Event.VAInterviewStatus.SCHEDULED] is False


def test_tab_counts(client):
    client.force_login(_viewer())
    _make_all()
    # one extra scheduled event in a different province
    _event(Event.VAInterviewStatus.SCHEDULED, "Sched Two", province="Copperbelt")

    response, _ = _component_ids(client)
    counts = {t["value"]: t["count"] for t in response.context["status_tabs"]}
    assert counts[Event.VAInterviewStatus.SCHEDULED] == 2
    assert counts[Event.VAInterviewStatus.COMPLETED] == 1
    assert counts[Event.VAInterviewStatus.NOT_DONE] == 1
    assert counts[Event.VAInterviewStatus.POSTPONED] == 1

    # province filter narrows the counts (status-agnostic)
    response2, _ = _component_ids(client, province="Copperbelt")
    counts2 = {t["value"]: t["count"] for t in response2.context["status_tabs"]}
    assert counts2[Event.VAInterviewStatus.SCHEDULED] == 1
    assert counts2[Event.VAInterviewStatus.COMPLETED] == 0


def test_ward_filter_limits_events_and_tab_counts(client):
    client.force_login(_viewer())
    matching = _event(
        Event.VAInterviewStatus.SCHEDULED,
        "Matching Person",
        ward="Chawama Ward",
    )
    _event(
        Event.VAInterviewStatus.SCHEDULED,
        "Other Person",
        ward="Kanyama Ward",
    )
    _event(
        Event.VAInterviewStatus.COMPLETED,
        "Completed Person",
        ward="Chawama Ward",
    )

    response, ids = _component_ids(client, ward="chawama")

    assert ids == {matching.id}
    assert response.context["filters"]["ward"] == "chawama"
    counts = {tab["value"]: tab["count"] for tab in response.context["status_tabs"]}
    assert counts[Event.VAInterviewStatus.SCHEDULED] == 1
    assert counts[Event.VAInterviewStatus.COMPLETED] == 1


def test_full_page_renders_tabs_and_no_status_dropdown(client):
    client.force_login(_viewer())
    _make_all()

    response = client.get(reverse("cms-event-list"))
    content = response.content.decode()

    for label in ("Scheduled", "Completed", "Not Done", "Postponed"):
        assert label in content
    assert 'name="interview_status"' not in content  # dropdown removed
    assert "Apply Filters" not in content


def test_list_displays_death_record_death_id(client):
    client.force_login(_viewer())
    event = _event(Event.VAInterviewStatus.SCHEDULED, "Identified Person")
    event.death.death_id = "1321BY0062"
    event.death.save(update_fields=["death_id"])

    response, ids = _component_ids(client)

    assert event.id in ids
    assert "Death ID" in response.content.decode()
    assert "1321BY0062" in response.content.decode()


def test_list_replaces_status_column_with_death_ward_and_ea(client):
    client.force_login(_viewer())
    _event(
        Event.VAInterviewStatus.SCHEDULED,
        "Located Person",
        ward="Chawama Ward",
        ea="EA 12",
    )

    response, _ = _component_ids(client)
    content = response.content.decode()

    assert "<th>Ward-EA</th>" in content
    assert "Chawama Ward-EA 12" in content
    assert ">Status</a>" not in content
