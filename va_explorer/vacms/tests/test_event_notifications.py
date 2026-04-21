import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from va_explorer.tests.factories import UserFactory
from va_explorer.users.models import UserMessage
from va_explorer.va_data_management.models import Death
from va_explorer.vacms.cmsmodels.events import Event
from va_explorer.vacms.constants import VA_SCHEDULE_MESSAGE_SUBJECT
from va_explorer.vacms.notifications import ensure_va_schedule_message

pytestmark = pytest.mark.django_db


def test_assigning_va_creates_mailbox_message(client):
    mso_group, _ = Group.objects.get_or_create(name="Mortality Surveillance Officer")

    assigned_user = UserFactory()
    assigned_user.groups.add(mso_group)

    scheduler = UserFactory()

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
    mso_group, _ = Group.objects.get_or_create(name="Mortality Surveillance Officer")
    assigned_user = UserFactory()
    assigned_user.groups.add(mso_group)
    scheduler = UserFactory()
    death = Death.objects.create(
        DE_03="Test Person",
        DE_04="1980-01-01",
        DE_05="Male",
        DE_06="2023-01-01",
    )
    return scheduler, assigned_user, death


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
