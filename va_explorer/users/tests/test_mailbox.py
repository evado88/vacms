import pytest
from django.test import Client
from django.urls import reverse

from va_explorer.tests.factories import UserFactory
from va_explorer.users.models import UserMessage
from va_explorer.utils.context_processors import mailbox
from va_explorer.vacms.cmsmodels.events import Event

pytestmark = pytest.mark.django_db


def test_user_message_unread_manager(user):
    message = UserMessage.objects.create(user=user, subject="Hello", body="Test")

    assert user.mailbox_unread_count == 1
    assert list(user.unread_messages) == [message]

    message.mark_read()
    assert user.mailbox_unread_count == 0


def test_mailbox_context_processor_counts_unread(user, rf):
    request = rf.get("/")
    request.user = user
    assert mailbox(request)["MAILBOX_UNREAD_COUNT"] == 0

    UserMessage.objects.create(user=user, body="Pending")
    assert mailbox(request)["MAILBOX_UNREAD_COUNT"] == 1


def test_mailbox_list_view_shows_user_messages(client: Client, user):
    UserMessage.objects.create(user=user, subject="Hello", body="Greetings")
    other_user = UserFactory()
    UserMessage.objects.create(
        user=other_user, subject="Hidden", body="Should not show"
    )

    client.force_login(user)
    response = client.get(reverse("users:mailbox"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Greetings" in content
    assert "Hidden" not in content


def test_mailbox_detail_marks_message_as_read(client: Client, user):
    message = UserMessage.objects.create(user=user, subject="Detail", body="Full body")

    client.force_login(user)
    response = client.get(message.get_absolute_url())

    assert response.status_code == 200
    message.refresh_from_db()
    assert message.read_at is not None


def test_mailbox_detail_prevents_cross_user_access(client: Client, user):
    other_user = UserFactory()
    message = UserMessage.objects.create(user=other_user, body="Secret")

    client.force_login(user)
    response = client.get(message.get_absolute_url())

    assert response.status_code == 404


def test_mailbox_detail_includes_event_button(client: Client, user):
    event = Event.objects.create(
        event_type=Event.EventType.DEATH,
        event_status=Event.EventStatus.VA_INTERVIEW_SCHEDULED,
    )

    event_url = reverse("cms-event-detail", kwargs={"pk": event.pk})

    message = UserMessage.objects.create(
        user=user,
        subject="Detail",
        body=f"Full body\nView the details: http://testserver{event_url}",
        metadata={"event_id": event.pk},
    )

    client.force_login(user)
    response = client.get(message.get_absolute_url())

    assert response.status_code == 200

    content = response.content.decode()
    assert "Full body" in content
    assert "View scheduled VA" in content
    assert f'href="{event_url}"' in content
    assert "http://testserver" not in content
