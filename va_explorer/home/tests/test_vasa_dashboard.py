"""Operations/Overview dashboard folds VASA into the verbal-autopsy figures.

Covers the national-operational KPIs + chart series (nov), the overview map
payload, and the VA-Statistics trends bundle.
"""

import pytest
from django.test import RequestFactory

from va_explorer.home.services.map import build_home_combined_map_payload
from va_explorer.home.services.nov import get_national_operational_view_data
from va_explorer.home.va_trends import get_trends_data
from va_explorer.tests.factories import (
    AdminFactory,
    CauseOfDeathFactory,
    LocationFactory,
    UserFactory,
    VASAFactory,
    VerbalAutopsyFactory,
)

pytestmark = pytest.mark.django_db


def _request(user):
    request = RequestFactory().get("/")
    request.user = user
    return request


def test_national_operational_view_folds_vasa_into_va_kpi_and_chart():
    province = LocationFactory.create(name="Southern", location_type="province")
    district = province.add_child(name="District 1", location_type="district")
    facility = district.add_child(name="Facility 1", location_type="facility")

    VerbalAutopsyFactory.create(
        instanceid="nov-va-1", location=facility, duplicate=False
    )
    VASAFactory.create(instanceid="nov-vasa-1", location=facility)
    VASAFactory.create(instanceid="nov-vasa-2", location=facility)

    user = UserFactory.create(location_restrictions=[province])
    data = get_national_operational_view_data(request=_request(user))

    # 1 VA + 2 VASA counted as one unified verbal-autopsy KPI.
    assert data["kpis"]["vas"]["total"] == 3
    # The VA chart series counts VASA submissions too.
    assert sum(data["va_values"]) == 3


def test_overview_map_counts_vasa_on_the_verbal_autopsy_layer():
    facility = LocationFactory.create(name="Facility 1", location_type="facility")
    VASAFactory.create(instanceid="map-vasa-1", location=facility)

    user = AdminFactory.create()
    payload = build_home_combined_map_payload(_request(user))

    # The only event in the system is a VASA record; it must be counted.
    assert payload["map_total_events"] == 1


def test_va_statistics_trends_fold_in_vasa():
    facility = LocationFactory.create(name="Facility 1", location_type="facility")
    va = VerbalAutopsyFactory.create(
        instanceid="trend-va-1", location=facility, duplicate=False
    )
    vasa = VASAFactory.create(instanceid="trend-vasa-1", location=facility)
    # Code one of each so "coded" also reflects VASA.
    CauseOfDeathFactory.create(verbalautopsy=va)
    CauseOfDeathFactory.create(verbalautopsy=None, vasa=vasa)

    user = AdminFactory.create()
    va_table, _graphs, _issues, _indet, _add_issues, _add_indet = get_trends_data(user)

    # Collected/coded overall reflect VA + VASA.
    assert va_table["collected"]["Overall"] == 2
    assert va_table["coded"]["Overall"] == 2
