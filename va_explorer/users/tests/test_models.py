import pytest

from va_explorer.tests.factories import (
    LocationFactory,
    UserFactory,
    VerbalAutopsyFactory,
)
from va_explorer.users.models import User, UserPasswordHistory
from va_explorer.va_data_management.models import SRSClusterLocation

pytestmark = pytest.mark.django_db


def test_user_get_absolute_url(user: User):
    assert user.get_absolute_url() == f"/users/{user.id}/"


def test_user_password_history_exists():
    user = UserFactory.create()
    user.set_password("test")
    user.save()

    password_history = UserPasswordHistory.objects.filter(user_id=user)
    assert password_history.count() == 1


def test_user_scoped_verbal_autopsies():
    # Set up a small tree of locations;
    # top level province, two districts, three facilities
    province = LocationFactory.create()
    district1 = province.add_child(name="District1", location_type="district")
    facility1 = district1.add_child(name="Facility1", location_type="facility")
    district2 = province.add_child(name="District2", location_type="district")
    facility2 = district2.add_child(name="Facility2", location_type="facility")
    facility3 = district2.add_child(name="Facility3", location_type="facility")
    # Each facility with one VA
    va1 = VerbalAutopsyFactory.create(location=facility1)
    va2 = VerbalAutopsyFactory.create(location=facility2)
    va3 = VerbalAutopsyFactory.create(location=facility3)
    # A user with no location restrictions should see all VAs in the system
    user = UserFactory.create()
    assert user.verbal_autopsies().count() == 3
    # A user restricted to the province should see all VAs
    user = UserFactory.create(location_restrictions=[province])
    assert user.verbal_autopsies().count() == 3
    # A user restricted to a district should see the correct subset of VAs
    user = UserFactory.create(location_restrictions=[district1])
    assert user.verbal_autopsies().count() == 1
    assert va1 in user.verbal_autopsies()
    user = UserFactory.create(location_restrictions=[district2])
    assert user.verbal_autopsies().count() == 2
    assert va2 in user.verbal_autopsies()
    assert va3 in user.verbal_autopsies()
    # A user restricted to a facility should see the correct VA
    user = UserFactory.create(location_restrictions=[facility1])
    assert user.verbal_autopsies().count() == 1
    assert va1 in user.verbal_autopsies()
    user = UserFactory.create(location_restrictions=[facility2])
    assert user.verbal_autopsies().count() == 1
    assert va2 in user.verbal_autopsies()
    user = UserFactory.create(location_restrictions=[facility3])
    assert user.verbal_autopsies().count() == 1
    assert va3 in user.verbal_autopsies()


def test_user_scoped_verbal_autopsies_includes_community_by_admin_geography():
    province = LocationFactory.create(name="Southern", location_type="province")
    district = province.add_child(name="District1", location_type="district")
    facility = district.add_child(name="Facility1", location_type="facility")
    cluster = SRSClusterLocation.add_root(
        name="EA 1001",
        location_type="ea",
        code="EA1001",
        status="Active",
    )

    facility_va = VerbalAutopsyFactory.create(
        instanceid="scoped-facility-1",
        location=facility,
        province="Southern",
        district="District1",
        community_va="no",
        Id10023="2021-03-21",
    )
    community_va = VerbalAutopsyFactory.create(
        instanceid="scoped-community-1",
        location=None,
        cluster=cluster,
        province="Southern",
        district="District1",
        ward="Ward 1",
        ea="EA 1001",
        community_va="yes",
        Id10023="2021-03-21",
    )
    VerbalAutopsyFactory.create(
        instanceid="scoped-community-outside-1",
        location=None,
        cluster=cluster,
        province="Lusaka",
        district="Lusaka",
        ward="Ward X",
        ea="EA X",
        community_va="yes",
        Id10023="2021-03-21",
    )

    user = UserFactory.create(location_restrictions=[province])
    scoped = user.verbal_autopsies()
    assert facility_va in scoped
    assert community_va in scoped
    assert scoped.count() == 2
