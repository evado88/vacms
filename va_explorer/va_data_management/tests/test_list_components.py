import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from va_explorer.tests.factories import GroupFactory, UserFactory
from va_explorer.va_data_management.filters import DeathFilter
from va_explorer.va_data_management.models import Death

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def disable_death_id_task(monkeypatch):
    # Death.save() queues a Celery task to an unreachable broker otherwise.
    from va_explorer.va_data_management.tasks import assign_death_ids_task

    monkeypatch.setattr(assign_death_ids_task, "delay", lambda *args, **kwargs: None)


def _death_viewer():
    can_view = Permission.objects.filter(codename="view_death").first()
    can_pii = Permission.objects.filter(codename="view_pii").first()
    group = GroupFactory.create(permissions=[can_view, can_pii])
    return UserFactory.create(groups=[group])


def _make_death(**kwargs):
    defaults = {
        "DE_03": "Test Person",
        "DE_04": "1980-01-01",
        "DE_05": "Male",
        "DE_06": "2023-01-01",
    }
    defaults.update(kwargs)
    return Death.objects.create(**defaults)


def test_deaths_dod_filter_labels_renamed():
    form = DeathFilter().form
    assert form.fields["start_date"].label == "Earliest DOD"
    assert form.fields["end_date"].label == "Latest DOD"


def test_component_request_returns_fragment_not_full_page():
    client = Client()
    client.force_login(_death_viewer())
    _make_death(DE_03="Fragment Person", eventid=None, district="Lusaka")

    url = reverse("va_data_management:deaths")

    full = client.get(url)
    assert full.status_code == 200
    assert b"<html" in full.content.lower()

    fragment = client.get(url, {"component": "1"})
    assert fragment.status_code == 200
    body = fragment.content.lower()
    assert b"<html" not in body
    assert b"<table" in body
    assert b"fragment person" in body


def test_component_honors_filters():
    client = Client()
    client.force_login(_death_viewer())
    _make_death(DE_03="Lusaka Person", eventid=None, district="Lusaka")
    _make_death(DE_03="Ndola Person", eventid=None, district="Ndola")

    response = client.get(
        reverse("va_data_management:deaths"),
        {"component": "1", "district": "Lusaka"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "Lusaka Person" in content
    assert "Ndola Person" not in content


def test_full_page_has_no_apply_button_and_loads_list_js():
    client = Client()
    client.force_login(_death_viewer())

    response = client.get(reverse("va_data_management:deaths"))
    content = response.content.decode()

    assert "Apply Filters" not in content
    assert "list_table.js" in content
    assert "data-list-slot" in content
