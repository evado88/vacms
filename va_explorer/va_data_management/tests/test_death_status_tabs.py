import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from va_explorer.tests.factories import GroupFactory, LocationFactory, UserFactory
from va_explorer.va_data_management.models import Death
from va_explorer.vacms.cmsmodels.events import Event

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def disable_death_id_task(monkeypatch):
    from va_explorer.va_data_management.tasks import assign_death_ids_task

    monkeypatch.setattr(assign_death_ids_task, "delay", lambda *args, **kwargs: None)


def _viewer():
    permissions = Permission.objects.filter(codename__in=("view_death", "view_pii"))
    return UserFactory.create(groups=[GroupFactory.create(permissions=permissions)])


def _death(
    name,
    *,
    status=None,
    legacy_eventid=None,
    province="Lusaka",
    district="Lusaka",
):
    death = Death.objects.create(
        key=f"death-{name}",
        enumerator="Enumerator One",
        province=province,
        district=district,
        ward="Test Ward",
        DE_03=name,
        DE_04="1980-01-01",
        DE_05="Male",
        DE_06="2023-01-01",
        eventid=legacy_eventid,
    )
    if status is not None:
        event = Event.objects.create(
            death=death,
            event_type=Event.EventType.DEATH,
            event_status=Event.EventStatus.VA_INTERVIEW_SCHEDULED,
            va_interview_status=status,
        )
        death.eventid = str(event.id)
        death.save(update_fields=["eventid"])
    return death


def _seed_statuses():
    return {
        "unscheduled": _death("Unscheduled Person"),
        "scheduled": _death(
            "Scheduled Person", status=Event.VAInterviewStatus.SCHEDULED
        ),
        "completed": _death(
            "Completed Person", status=Event.VAInterviewStatus.COMPLETED
        ),
        "not_done": _death("Not Done Person", status=Event.VAInterviewStatus.NOT_DONE),
        "postponed": _death(
            "Postponed Person", status=Event.VAInterviewStatus.POSTPONED
        ),
        "legacy": _death("Legacy Person", legacy_eventid="legacy-event"),
    }


def _ids(response):
    return {death.id for death in response.context["object_list"]}


def test_default_and_explicit_all_include_every_death(client):
    client.force_login(_viewer())
    deaths = _seed_statuses()
    expected = {death.id for death in deaths.values()}

    default = client.get(reverse("va_data_management:deaths"))
    explicit = client.get(reverse("va_data_management:deaths"), {"death_status": "all"})

    assert _ids(default) == expected
    assert _ids(explicit) == expected
    assert default.context["active_status"] == "all"


@pytest.mark.parametrize(
    ("status", "expected_key"),
    [("scheduled", "scheduled"), ("completed", "completed")],
)
def test_event_status_tabs_match_va_interview_status(client, status, expected_key):
    client.force_login(_viewer())
    deaths = _seed_statuses()

    response = client.get(
        reverse("va_data_management:deaths"), {"death_status": status}
    )

    assert _ids(response) == {deaths[expected_key].id}


def test_unscheduled_uses_null_eventid_and_other_statuses_are_all_only(client):
    client.force_login(_viewer())
    deaths = _seed_statuses()

    response = client.get(
        reverse("va_data_management:deaths"), {"death_status": "unscheduled"}
    )

    assert _ids(response) == {deaths["unscheduled"].id}
    assert deaths["not_done"].id not in _ids(response)
    assert deaths["postponed"].id not in _ids(response)
    assert deaths["legacy"].id not in _ids(response)


def test_tab_counts_respect_death_filters(client):
    client.force_login(_viewer())
    _seed_statuses()
    _death(
        "Ndola Scheduled",
        status=Event.VAInterviewStatus.SCHEDULED,
        district="Ndola",
    )

    response = client.get(reverse("va_data_management:deaths"), {"district": "Ndola"})
    counts = {tab["value"]: tab["count"] for tab in response.context["status_tabs"]}

    assert counts == {"all": 1, "scheduled": 1, "unscheduled": 0, "completed": 0}


def test_tabs_and_counts_respect_location_restrictions(client):
    location = LocationFactory.create(
        name="Lusaka", location_type="province", depth=1, path="0001"
    )
    permissions = Permission.objects.filter(codename__in=("view_death", "view_pii"))
    user = UserFactory.create(
        groups=[GroupFactory.create(permissions=permissions)],
        location_restrictions=[location],
    )
    client.force_login(user)
    allowed = _death("Allowed Scheduled", status=Event.VAInterviewStatus.SCHEDULED)
    _death(
        "Blocked Completed",
        status=Event.VAInterviewStatus.COMPLETED,
        province="Copperbelt",
        district="Kitwe",
    )

    response = client.get(reverse("va_data_management:deaths"))
    counts = {tab["value"]: tab["count"] for tab in response.context["status_tabs"]}

    assert _ids(response) == {allowed.id}
    assert counts == {"all": 1, "scheduled": 1, "unscheduled": 0, "completed": 0}


def test_component_renders_tabs_and_all_rows_link_to_death_detail(client):
    client.force_login(_viewer())
    deaths = _seed_statuses()

    response = client.get(
        reverse("va_data_management:deaths"),
        {"component": "1", "death_status": "completed"},
    )
    content = response.content.decode()

    assert "<html" not in content.lower()
    for label in ("All", "Scheduled", "Unscheduled", "Completed"):
        assert label in content
    assert (
        reverse(
            "va_data_management:death_detail", kwargs={"id": deaths["completed"].id}
        )
        in content
    )
    assert "btn-secondary disabled" not in content


def test_invalid_status_defaults_to_all(client):
    client.force_login(_viewer())
    deaths = _seed_statuses()

    response = client.get(
        reverse("va_data_management:deaths"), {"death_status": "unknown"}
    )

    assert response.context["active_status"] == "all"
    assert _ids(response) == {death.id for death in deaths.values()}
