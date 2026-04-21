import pytest

from va_explorer.home.dashboard_metrics import _compute_homepage_metrics
from va_explorer.tests.factories import (
    LocationFactory,
    UserFactory,
    VerbalAutopsyFactory,
)
from va_explorer.va_data_management.models import SRSClusterLocation

pytestmark = pytest.mark.django_db


def test_homepage_metrics_include_facility_and_community_vas_in_scope():
    province = LocationFactory.create(name="Southern", location_type="province")
    district = province.add_child(name="District 1", location_type="district")
    facility = district.add_child(name="Facility 1", location_type="facility")
    cluster = SRSClusterLocation.add_root(
        name="EA 2001",
        location_type="ea",
        code="EA2001",
        status="Active",
    )

    VerbalAutopsyFactory.create(
        instanceid="metrics-facility-1",
        location=facility,
        province="Southern",
        district="District 1",
        community_va="no",
    )
    VerbalAutopsyFactory.create(
        instanceid="metrics-community-1",
        location=None,
        cluster=cluster,
        province="Southern",
        district="District 1",
        ward="Ward 1",
        ea="EA 2001",
        community_va="yes",
    )
    VerbalAutopsyFactory.create(
        instanceid="metrics-community-outside-1",
        location=None,
        cluster=cluster,
        province="Lusaka",
        district="Lusaka",
        ward="Ward X",
        ea="EA X",
        community_va="yes",
    )

    user = UserFactory.create(location_restrictions=[province])
    metrics = _compute_homepage_metrics(user=user)

    assert metrics["total_vas"] == 2
