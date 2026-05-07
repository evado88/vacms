import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from va_explorer.tests.factories import UserFactory
from va_explorer.users.models import UserMessage
from va_explorer.va_data_management.models import Death
from va_explorer.vacms.cmsmodels.events import Event
from va_explorer.vacms.constants import VA_SCHEDULE_MESSAGE_SUBJECT
from va_explorer.vacms.notifications import ensure_va_schedule_message
from va_explorer.vacms.permissions import (
    user_can_manage_va_schedule,
    user_can_view_va_schedule,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def disable_death_id_task(monkeypatch):
    from va_explorer.va_data_management.tasks import assign_death_ids_task

    monkeypatch.setattr(assign_death_ids_task, "delay", lambda *args, **kwargs: None)


def _group(name):
    group, _ = Group.objects.get_or_create(name=name)
    return group


def _user_in_group(name):
    user = UserFactory()
    user.groups.add(_group(name))
    return user


def test_assigning_va_creates_mailbox_message(client):
    assigned_user = UserFactory()
    assigned_user.groups.add(_group("Mortality Surveillance Officer"))

    scheduler = _user_in_group("Data Managers")

    death = Death.objects.create(
        DE_03="Test Person",
        DE_04="1980-01-01",
        DE_05="Male",
        DE_06="2023-01-01",
    )

    client.force_login(scheduler)

    response = client.post(
        reverse("cms-event-death-create", args=[death.id]),
        data={
            "id": str(death.id),
            "name": death.DE_03,
            "dob": death.DE_04,
            "sex": death.DE_05,
            "dod": death.DE_06,
            "interview_scheduled_date": "2023-09-01",
            "va_interview_staff": assigned_user.id,
            "interview_contact_name": "Contact",
            "interview_contact_tel": "123",
            "interview_comments": "Test",
        },
    )

    assert response.status_code == 302

    death.refresh_from_db()
    event = Event.objects.get(pk=death.eventid)

    message = UserMessage.objects.get(user=assigned_user)

    assert message.subject == VA_SCHEDULE_MESSAGE_SUBJECT
    assert "Test Person" in message.body
    assert "2023-09-01" in message.body

    expected_url = reverse("cms-event-detail", kwargs={"pk": event.pk})
    assert expected_url in message.body

    assert message.metadata["event_id"] == event.pk
    assert message.metadata["death_id"] == death.pk


def _schedule_event_for_user(client, scheduler, assigned_user, death):
    client.force_login(scheduler)
    client.post(
        reverse("cms-event-death-create", args=[death.id]),
        data={
            "id": str(death.id),
            "name": death.DE_03,
            "dob": death.DE_04,
            "sex": death.DE_05,
            "dod": death.DE_06,
            "interview_scheduled_date": "2023-09-01",
            "va_interview_staff": assigned_user.id,
            "interview_contact_name": "Contact",
            "interview_contact_tel": "123",
            "interview_comments": "Test",
        },
    )
    death.refresh_from_db()
    return Event.objects.get(pk=death.eventid)


def _create_users_and_death():
    assigned_user = UserFactory()
    assigned_user.groups.add(_group("Mortality Surveillance Officer"))
    scheduler = _user_in_group("Data Managers")
    death = Death.objects.create(
        DE_03="Test Person",
        DE_04="1980-01-01",
        DE_05="Male",
        DE_06="2023-01-01",
    )
    return scheduler, assigned_user, death


@pytest.mark.parametrize(
    "group_name",
    [("Mortality Surveillance Officer",), ("Community Surveillance Officer",)],
)
def test_mso_and_csa_have_va_schedule_rights(group_name):
    user = _user_in_group(group_name)

    assert user_can_manage_va_schedule(user)
    assert user_can_view_va_schedule(user)


def test_user_without_schedule_rights_cannot_manage_va_schedule():
    user = UserFactory()

    assert not user_can_manage_va_schedule(user)


def test_user_without_schedule_rights_cannot_view_va_schedule():
    user = UserFactory()

    assert not user_can_view_va_schedule(user)


def test_schedule_denial_overrides_scheduler_role():
    user = _user_in_group("Mortality Surveillance Officer")
    user.denied_permissions = ["vacms.change_event"]

    assert not user_can_manage_va_schedule(user)
    assert user_can_view_va_schedule(user)

    user.denied_permissions = ["vacms.change_event", "vacms.view_event"]
    assert not user_can_view_va_schedule(user)


def test_va_schedule_message_only_created_once(client, rf):
    scheduler, assigned_user, death = _create_users_and_death()
    event = _schedule_event_for_user(client, scheduler, assigned_user, death)

    ensure_va_schedule_message(event, rf.get("/mailbox"))

    assert (
        UserMessage.objects.filter(
            user=assigned_user,
            metadata__event_id=event.pk,
            subject=VA_SCHEDULE_MESSAGE_SUBJECT,
        ).count()
        == 1
    )


def test_va_schedule_message_removed_on_completion(client):
    scheduler, assigned_user, death = _create_users_and_death()
    event = _schedule_event_for_user(client, scheduler, assigned_user, death)

    client.force_login(assigned_user)
    response = client.post(
        reverse("cms-event-detail", args=[event.pk]),
        data={
            "va_interview_status": Event.VAInterviewStatus.COMPLETED,
            "va_not_done_reason": "",
            "va_not_done_other": "",
        },
    )

    assert response.status_code == 302
    assert not UserMessage.objects.filter(
        user=assigned_user, metadata__event_id=event.pk
    ).exists()


def test_va_schedule_message_removed_on_not_done(client):
    scheduler, assigned_user, death = _create_users_and_death()
    event = _schedule_event_for_user(client, scheduler, assigned_user, death)

    client.force_login(assigned_user)
    response = client.post(
        reverse("cms-event-detail", args=[event.pk]),
        data={
            "va_interview_status": Event.VAInterviewStatus.NOT_DONE,
            "va_not_done_reason": Event.VANotDoneReason.RELOCATED,
            "va_not_done_other": "",
        },
    )

    assert response.status_code == 302
    assert not UserMessage.objects.filter(
        user=assigned_user, metadata__event_id=event.pk
    ).exists()
