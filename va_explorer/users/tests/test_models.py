import pytest
from django.contrib.auth.models import Group, Permission

from va_explorer.tests.factories import (
    GroupFactory,
    LocationFactory,
    UserFactory,
    VASAFactory,
    VerbalAutopsyFactory,
)
from va_explorer.users.models import User, UserPasswordHistory
from va_explorer.va_data_management.models import SRSClusterLocation

pytestmark = pytest.mark.django_db


def _view_pii_permission():
    return Permission.objects.get(
        content_type__app_label="va_analytics", codename="view_pii"
    )


def test_role_granted_pii_cannot_be_revoked_by_per_user_denial():
    # A role (group) that grants view_pii guarantees PII visibility even if the
    # permission is listed in the user's per-user denials.
    group, _ = Group.objects.get_or_create(name="Data Managers")
    group.permissions.add(_view_pii_permission())
    user = UserFactory.create(groups=[group])
    user.denied_permissions = ["va_analytics.view_pii"]
    user.save()

    assert user.can_view_pii is True


def test_direct_pii_grant_still_works_without_role():
    # A user whose role doesn't grant view_pii can still be granted it directly.
    user = UserFactory.create(groups=[GroupFactory.create()])
    user.user_permissions.add(_view_pii_permission())

    assert User.objects.get(pk=user.pk).can_view_pii is True


def test_no_role_and_no_grant_cannot_view_pii():
    user = UserFactory.create(groups=[GroupFactory.create()])

    assert user.can_view_pii is False


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


def test_user_scoped_vasa_records():
    # VASA records are location-scoped exactly like verbal autopsies.
    province = LocationFactory.create()
    district1 = province.add_child(name="District1", location_type="district")
    facility1 = district1.add_child(name="Facility1", location_type="facility")
    district2 = province.add_child(name="District2", location_type="district")
    facility2 = district2.add_child(name="Facility2", location_type="facility")
    facility3 = district2.add_child(name="Facility3", location_type="facility")
    vasa1 = VASAFactory.create(location=facility1)
    vasa2 = VASAFactory.create(location=facility2)
    vasa3 = VASAFactory.create(location=facility3)

    # No restrictions -> sees all VASA records.
    user = UserFactory.create()
    assert user.vasa_records().count() == 3
    # Province restriction -> sees all.
    user = UserFactory.create(location_restrictions=[province])
    assert user.vasa_records().count() == 3
    # District restriction -> correct subset.
    user = UserFactory.create(location_restrictions=[district1])
    assert list(user.vasa_records()) == [vasa1]
    user = UserFactory.create(location_restrictions=[district2])
    scoped = set(user.vasa_records())
    assert scoped == {vasa2, vasa3}
    # Facility restriction -> single record.
    user = UserFactory.create(location_restrictions=[facility1])
    assert list(user.vasa_records()) == [vasa1]


def test_user_scoped_vasa_records_includes_community_by_admin_geography():
    # Community VASA records are scoped by their persisted admin geography,
    # mirroring the verbal-autopsy community path.
    province = LocationFactory.create(name="Southern", location_type="province")
    district = province.add_child(name="District1", location_type="district")
    facility = district.add_child(name="Facility1", location_type="facility")
    cluster = SRSClusterLocation.add_root(
        name="EA 1001",
        location_type="ea",
        code="EA1001",
        status="Active",
    )

    facility_vasa = VASAFactory.create(
        instanceid="scoped-vasa-facility-1",
        location=facility,
        province="Southern",
        district="District1",
        community_va="no",
        Id10023="2021-03-21",
    )
    community_vasa = VASAFactory.create(
        instanceid="scoped-vasa-community-1",
        location=None,
        cluster=cluster,
        province="Southern",
        district="District1",
        ward="Ward 1",
        ea="EA 1001",
        community_va="yes",
        Id10023="2021-03-21",
    )
    VASAFactory.create(
        instanceid="scoped-vasa-community-outside-1",
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
    scoped = user.vasa_records()
    assert facility_vasa in scoped
    assert community_vasa in scoped
    assert scoped.count() == 2
