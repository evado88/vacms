import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from va_explorer.tests.factories import (
    DeathFactory,
    GroupFactory,
    HouseholdFactory,
    PregnancyFactory,
    PregnancyOutcomeFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def disable_death_id_task(monkeypatch):
    from va_explorer.va_data_management.tasks import assign_death_ids_task

    monkeypatch.setattr(assign_death_ids_task, "delay", lambda *args, **kwargs: None)


def _viewer(permission_codename, include_pii=True):
    permissions = [Permission.objects.get(codename=permission_codename)]
    if include_pii:
        permissions.append(Permission.objects.get(codename="view_pii"))
    group = GroupFactory.create(permissions=permissions)
    return UserFactory.create(groups=[group])


def _list_ids(response):
    return [obj.id for obj in response.context["object_list"]]


def test_households_list_excludes_placeholder_rows_and_reorders_filters():
    client = Client()
    client.force_login(_viewer("view_household"))

    valid = HouseholdFactory.create(
        enumerator="Enumerator One",
        province="Southern",
        district="Choma",
        ward="Macha Ward",
        locality_name="Macha",
        respondent="Jane Doe",
    )
    HouseholdFactory.create(
        enumerator="Enumerator Two",
        province="Southern",
        district="Choma",
        ward="Macha Ward",
        locality_name="Macha",
        respondent="   ",
    )

    response = client.get(reverse("va_data_management:households"))

    assert response.status_code == 200
    assert _list_ids(response) == [valid.id]
    assert list(response.context["filter"].form.fields.keys()) == [
        "id",
        "enumerator",
        "district",
        "ward",
    ]


def test_pregnancies_list_uses_lmp_column_and_excludes_placeholder_rows():
    client = Client()
    client.force_login(_viewer("view_pregnancy"))

    valid = PregnancyFactory.create(
        enumerator="Enumerator One",
        province="Southern",
        district="Choma",
        ward="Macha Ward",
        PE_06="Mary Example",
        PE_09A="2024-02-03",
    )
    PregnancyFactory.create(
        enumerator="Enumerator Two",
        province="Southern",
        district="Choma",
        ward="Macha Ward",
        PE_06="Grace Example",
        PE_09A=" - ",
    )

    response = client.get(reverse("va_data_management:pregnancies"))
    content = response.content.decode()

    assert response.status_code == 200
    assert _list_ids(response) == [valid.id]
    assert "Last Menstrual Period Date" in content
    assert ">DOB<" not in content


def test_pregnancy_outcomes_list_excludes_placeholder_rows_and_reorders_filters():
    client = Client()
    client.force_login(_viewer("view_pregnancyoutcome"))

    valid = PregnancyOutcomeFactory.create(
        enumerator="Enumerator One",
        province="Southern",
        district="Choma",
        ward="Macha Ward",
        PO_41="2024-03-04",
        PO_46="Live birth",
    )
    PregnancyOutcomeFactory.create(
        enumerator="Enumerator Two",
        province="Southern",
        district="Choma",
        ward="Macha Ward",
        PO_41="2024-03-04",
        PO_46="Unknown",
    )

    response = client.get(reverse("va_data_management:pregnancy_outcomes"))

    assert response.status_code == 200
    assert _list_ids(response) == [valid.id]
    assert list(response.context["filter"].form.fields.keys()) == [
        "enumerator",
        "district",
        "ward",
        "outcome_type",
        "start_date",
        "end_date",
    ]


def test_deaths_list_excludes_placeholder_rows_and_reorders_filters():
    client = Client()
    client.force_login(_viewer("view_death"))

    valid = DeathFactory.create(
        enumerator="Enumerator One",
        province="Southern",
        district="Choma",
        ward="Macha Ward",
        DE_03="Joseph Example",
        DE_06="2024-04-05",
        eventid=None,
    )
    DeathFactory.create(
        enumerator="Enumerator Two",
        province="Southern",
        district="Choma",
        ward="Macha Ward",
        DE_03="Joseph Example",
        DE_06="   ",
        eventid=None,
    )

    response = client.get(reverse("va_data_management:deaths"))

    assert response.status_code == 200
    assert _list_ids(response) == [valid.id]
    assert list(response.context["filter"].form.fields.keys()) == [
        "enumerator",
        "district",
        "ward",
        "ea",
        "deceased",
        "start_date",
        "end_date",
    ]
