import csv
import io

import pytest
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from va_explorer.tests.factories import UserFactory
from va_explorer.users.management.commands.initialize_groups import GROUPS_PERMISSIONS
from va_explorer.va_data_management.constants import DEATH_PII_FIELDS, REDACTED_STRING
from va_explorer.va_data_management.models import Death
from va_explorer.vacms.cmsmodels.events import Event

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def disable_death_id_task(monkeypatch):
    # Death.save() fires a post_save signal that queues a Celery task to an
    # unreachable broker, which otherwise stalls these tests on retries.
    from va_explorer.va_data_management.tasks import assign_death_ids_task

    monkeypatch.setattr(assign_death_ids_task, "delay", lambda *args, **kwargs: None)


def _seeded_group(name):
    group, _ = Group.objects.get_or_create(name=name)
    group.permissions.clear()
    for model_class, codenames in GROUPS_PERMISSIONS[name].items():
        content_type = ContentType.objects.get_for_model(model_class)
        permissions = Permission.objects.filter(
            content_type=content_type, codename__in=codenames
        )
        group.permissions.add(*permissions)
    return group


def _data_manager():
    """Full Data Manager (includes view_death + view_pii)."""
    return UserFactory.create(groups=[_seeded_group("Data Managers")])


def _data_viewer():
    """Can view deaths but is NOT a Data Manager/Admin -> no CSV download."""
    return UserFactory.create(groups=[_seeded_group("Data Viewers")])


def _make_death(**kwargs):
    defaults = {
        "enumerator": "Enumerator One",
        "province": "Southern",
        "district": "Choma",
        "ward": "Macha Ward",
        "DE_03": "Test Person",
        "DE_04": "1980-01-01",
        "DE_05": "Male",
        "DE_06": "2023-01-01",
    }
    defaults.update(kwargs)
    return Death.objects.create(**defaults)


def _parse_csv(response):
    return list(csv.reader(io.StringIO(response.content.decode())))


def test_export_defaults_to_all_deaths(client):
    client.force_login(_data_manager())

    unscheduled = _make_death(DE_03="No Event Person", eventid=None, district="Lusaka")
    scheduled = _make_death(DE_03="Has Event Person", eventid="E1", district="Lusaka")

    response = client.get(reverse("va_data_management:deaths_csv"))

    assert response.status_code == 200
    assert response["Content-Type"] == "text/csv"
    assert "attachment" in response["Content-Disposition"]

    rows = _parse_csv(response)
    header = rows[0]
    assert header == [field.name for field in Death._meta.fields]

    id_col = header.index("id")
    ids = {row[id_col] for row in rows[1:]}
    assert str(unscheduled.id) in ids
    assert str(scheduled.id) in ids


def test_export_honors_unscheduled_tab(client):
    client.force_login(_data_manager())

    unscheduled = _make_death(DE_03="No Event Person", eventid=None)
    scheduled = _make_death(DE_03="Has Event Person", eventid="E1")

    response = client.get(
        reverse("va_data_management:deaths_csv"),
        {"death_status": "unscheduled"},
    )

    rows = _parse_csv(response)
    id_col = rows[0].index("id")
    ids = {row[id_col] for row in rows[1:]}
    assert str(unscheduled.id) in ids
    assert str(scheduled.id) not in ids
    assert "unscheduled_deaths_" in response["Content-Disposition"]


def test_export_honors_completed_tab(client):
    client.force_login(_data_manager())

    completed = _make_death(DE_03="Completed Person")
    completed_event = Event.objects.create(
        death=completed,
        event_type=Event.EventType.DEATH,
        event_status=Event.EventStatus.VA_INTERVIEW_COMPLETED,
        va_interview_status=Event.VAInterviewStatus.COMPLETED,
    )
    completed.eventid = str(completed_event.id)
    completed.save(update_fields=["eventid"])
    _make_death(DE_03="Unscheduled Person")

    response = client.get(
        reverse("va_data_management:deaths_csv"),
        {"death_status": "completed"},
    )

    rows = _parse_csv(response)
    id_col = rows[0].index("id")
    assert {row[id_col] for row in rows[1:]} == {str(completed.id)}
    assert "completed_deaths_" in response["Content-Disposition"]


def test_export_honors_filters(client):
    client.force_login(_data_manager())

    keep = _make_death(DE_03="Lusaka Person", eventid=None, district="Lusaka")
    drop = _make_death(DE_03="Ndola Person", eventid=None, district="Ndola")

    response = client.get(
        reverse("va_data_management:deaths_csv"), {"district": "Lusaka"}
    )

    rows = _parse_csv(response)
    id_col = rows[0].index("id")
    ids = {row[id_col] for row in rows[1:]}
    assert str(keep.id) in ids
    assert str(drop.id) not in ids


def test_literal_nan_is_excluded_from_list_and_export(client):
    client.force_login(_data_manager())
    hidden = _make_death(DE_03="NaN", eventid=None)
    visible = _make_death(DE_03="Visible Person", eventid=None)

    list_response = client.get(reverse("va_data_management:deaths"))
    assert str(hidden.id).encode() not in list_response.content
    assert b"Visible Person" in list_response.content

    rows = _parse_csv(client.get(reverse("va_data_management:deaths_csv")))
    id_col = rows[0].index("id")
    ids = {row[id_col] for row in rows[1:]}
    assert str(hidden.id) not in ids
    assert str(visible.id) in ids


def test_export_serializes_optional_nan_as_blank(client):
    client.force_login(_data_manager())
    death = _make_death(DE_15="NaN")

    rows = _parse_csv(client.get(reverse("va_data_management:deaths_csv")))
    header = rows[0]
    row = next(item for item in rows[1:] if item[header.index("id")] == str(death.id))
    assert row[header.index("DE_15")] == ""


def test_export_redacts_pii_for_non_pii_user(client):
    # Data Manager (CSV allowed) but WITHOUT view_pii -> PII columns redacted.
    dm_group, _ = Group.objects.get_or_create(name="Data Managers")
    dm_group.permissions.clear()
    view_death = Permission.objects.filter(codename="view_death").first()
    dm_group.permissions.add(view_death)
    user = UserFactory.create(groups=[dm_group])
    client.force_login(user)

    _make_death(DE_03="Secret Name", eventid=None)

    response = client.get(reverse("va_data_management:deaths_csv"))
    rows = _parse_csv(response)
    header = rows[0]
    de03_col = header.index("DE_03")
    assert rows[1][de03_col] == REDACTED_STRING
    assert "Secret Name" not in response.content.decode()
    # Sanity: DE_03 is in the PII list we redact.
    assert "DE_03" in DEATH_PII_FIELDS


def test_export_forbidden_for_non_data_manager(client):
    client.force_login(_data_viewer())

    response = client.get(reverse("va_data_management:deaths_csv"))

    assert response.status_code == 403


def test_download_button_visibility(client):
    unscheduled_only_url = reverse("va_data_management:deaths")

    client.force_login(_data_manager())
    dm_response = client.get(unscheduled_only_url)
    assert b'aria-label="Download CSV"' in dm_response.content
    assert b'title="Download CSV"' in dm_response.content
    assert b"fa-download" in dm_response.content

    client.force_login(_data_viewer())
    viewer_response = client.get(unscheduled_only_url)
    assert b'aria-label="Download CSV"' not in viewer_response.content
