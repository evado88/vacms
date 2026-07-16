"""MSO supervision source filter includes VASA and folds VASA into the table."""

import pytest
from django.test import Client

from va_explorer.tests.factories import (
    UserFactory,
    VASAFactory,
    VerbalAutopsyFactory,
)

pytestmark = pytest.mark.django_db

MSO_URL = "/regional-operations/components/mso/"


def _seed():
    VerbalAutopsyFactory.create(
        Id10010="MSO Vaonly", Id10012="2024-02-01", province="Lusaka"
    )
    VASAFactory.create(Id10010="MSO Vasaonly", Id10012="2024-02-02", province="Lusaka")


def _names(response):
    return {row["name"] for row in response.context["mso_stats"]}


def test_mso_source_options_include_vasa():
    user = UserFactory.create()
    client = Client()
    client.force_login(user=user)
    _seed()

    response = client.get(MSO_URL, {"time_preset": "timeAll"}, follow=True)
    assert response.status_code == 200
    values = {opt["value"] for opt in response.context["mso_source_options"]}
    assert "vasa" in values


def test_mso_default_folds_in_va_and_vasa():
    user = UserFactory.create()
    client = Client()
    client.force_login(user=user)
    _seed()

    response = client.get(MSO_URL, {"time_preset": "timeAll"}, follow=True)
    assert response.status_code == 200
    names = _names(response)
    assert "MSO Vaonly" in names
    assert "MSO Vasaonly" in names


def test_mso_source_vasa_filters_to_vasa_records():
    user = UserFactory.create()
    client = Client()
    client.force_login(user=user)
    _seed()

    response = client.get(
        MSO_URL, {"time_preset": "timeAll", "source": "vasa"}, follow=True
    )
    assert response.status_code == 200
    names = _names(response)
    assert "MSO Vasaonly" in names
    # A VA-only MSO must not appear when filtering to VASA.
    assert "MSO Vaonly" not in names
    vasa_row = next(
        row for row in response.context["mso_stats"] if row["name"] == "MSO Vasaonly"
    )
    assert vasa_row["va_total"] == 1
