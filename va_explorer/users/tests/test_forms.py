import pytest
from django.contrib.auth.models import Permission
from django.test import RequestFactory

from va_explorer.tests.factories import (
    FacilityFactory,
    FieldWorkerFactory,
    FieldWorkerGroupFactory,
    GroupFactory,
    LocationFactory,
    NewUserFactory,
    UserFactory,
)
from va_explorer.va_data_management.models import SRSClusterLocation
from va_explorer.users.forms import (
    ExtendedUserCreationForm,
    UserChangePasswordForm,
    UserSetPasswordForm,
    UserUpdateForm,
    process_user_data,
)

pytestmark = pytest.mark.django_db


def create_matching_srs_location(name="Test Location", location_type="province"):
    location = LocationFactory.create(name=name, location_type=location_type)
    srs_location = SRSClusterLocation.add_root(
        name=name, location_type=location_type
    )
    return location, srs_location


class TestUserCreationForm:
    def test_location_restrictions_queryset_only_province_and_district(self):
        province = SRSClusterLocation.add_root(
            name="Province A", location_type="province"
        )
        district = province.add_child(name="District A", location_type="district")
        _ = district.add_child(name="Constituency A", location_type="constituency")

        form = ExtendedUserCreationForm()
        queryset = form.fields["location_restrictions"].queryset
        selected_types = set(queryset.values_list("location_type", flat=True))

        assert selected_types == {"province", "district"}
        assert queryset.filter(location_type="constituency").count() == 0

    def test_valid_form_with_national_access(self, rf: RequestFactory):
        # A user with proto_user params does not exist yet.
        proto_user = NewUserFactory.build()
        group = GroupFactory.create()

        form = ExtendedUserCreationForm(
            {
                "name": proto_user.name,
                "email": proto_user.email,
                "group": group,
                "geographic_access": "national",
                "location_restrictions": [],
            }
        )

        # Note: The form expects a request object to be set in order to save it
        request = rf.get("/fake-url/")
        form.request = request

        assert form.is_valid()

    def test_valid_form_with_location_specific_access(self, rf: RequestFactory):
        # A user with proto_user params does not exist yet.
        proto_user = NewUserFactory.build()
        group = GroupFactory.create()
        _, srs_location = create_matching_srs_location()

        form = ExtendedUserCreationForm(
            {
                "name": proto_user.name,
                "email": proto_user.email,
                "group": group,
                "geographic_access": "location-specific",
                "location_restrictions": [srs_location],
            }
        )

        # Note: The form expects a request object to be set in order to save it
        request = rf.get("/fake-url/")
        form.request = request

        assert form.is_valid()

    def test_valid_form_with_facility_specific_access(self, rf: RequestFactory):
        proto_user = NewUserFactory.build()
        group = FieldWorkerGroupFactory.create()
        location = FacilityFactory.create()

        form = ExtendedUserCreationForm(
            {
                "name": proto_user.name,
                "email": proto_user.email,
                "group": group,
                "geographic_access": "location-specific",
                "location_restrictions": [],
                "facility_restrictions": [location],
            }
        )

        # Note: The form expects a request object to be set in order to save it
        request = rf.get("/fake-url/")
        form.request = request

        assert form.is_valid()

    def test_email_uniqueness(self):
        # A user with existing_user params exists already.
        existing_user = NewUserFactory.create()
        group = GroupFactory.create()
        _, srs_location = create_matching_srs_location()

        form = ExtendedUserCreationForm(
            {
                "name": existing_user.name,
                "email": existing_user.email,
                "group": group,
                "geographic_access": "location-specific",
                "location_restrictions": [srs_location],
            }
        )

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "email" in form.errors

    def test_basic_form_field_requirements(self):
        form = ExtendedUserCreationForm(
            {
                "name": "",
                "email": "",
                "group": "",
                "geographic_access": "",
                "location_restrictions": "",
                "facility_restrictions": "",
            }
        )

        assert not form.is_valid()
        assert len(form.errors) == 4

        assert "email" in form.errors
        assert "name" in form.errors
        assert "group" in form.errors
        assert "geographic_access" in form.errors

    def test_location_required(self):
        # A user with proto_user params does not exist yet.
        proto_user = NewUserFactory.build()
        group = GroupFactory.create()

        form = ExtendedUserCreationForm(
            {
                "name": proto_user.name,
                "email": proto_user.email,
                "group": group,
                "geographic_access": "location-specific",
                "location_restrictions": [],
            }
        )

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "location_restrictions" in form.errors

    def test_location_not_required(self):
        # A user with proto_user params does not exist yet.
        proto_user = NewUserFactory.build()
        group = GroupFactory.create()
        _, srs_location = create_matching_srs_location()

        form = ExtendedUserCreationForm(
            {
                "name": proto_user.name,
                "email": proto_user.email,
                "group": group,
                "geographic_access": "national",
                "location_restrictions": [srs_location],
            }
        )

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "location_restrictions" in form.errors

    def test_facility_required(self):
        # A user with proto_user params does not exist yet.
        proto_user = NewUserFactory.build()
        group = FieldWorkerGroupFactory.create()

        form = ExtendedUserCreationForm(
            {
                "name": proto_user.name,
                "email": proto_user.email,
                "group": group,
                "geographic_access": "location-specific",
                "location_restrictions": [],
                "facility_restrictions": [],
            }
        )

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "facility_restrictions" in form.errors


class TestUserUpdateForm:
    def test_valid_form(self, rf: RequestFactory):
        new_group = GroupFactory.create()
        _, srs_location = create_matching_srs_location()

        form = UserUpdateForm(
            {
                "name": "A new name",
                "email": "updatedemail@example.com",
                "group": new_group,
                "is_active": False,
                "geographic_access": "location-specific",
                "location_restrictions": [srs_location],
            }
        )

        assert form.is_valid()

    def test_invalid_form(self, rf: RequestFactory):
        new_group = GroupFactory.create()

        form = UserUpdateForm(
            {
                "name": "A new name",
                "email": "updatedemail@example.com",
                "group": new_group,
                "is_active": True,
                "geographic_access": "location-specific",
                "location_restrictions": [],
            }
        )

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "location_restrictions" in form.errors

    def test_valid_field_worker_form(self, rf: RequestFactory):
        field_worker_group = FieldWorkerGroupFactory.create()
        facility = FacilityFactory.create()

        form = UserUpdateForm(
            {
                "name": "A name",
                "email": "updatedemail@example.com",
                "group": field_worker_group,
                "is_active": True,
                "geographic_access": "location-specific",
                "location_restrictions": [facility],
            }
        )

        assert form.is_valid()

    def test_invalid_field_worker_form(self, rf: RequestFactory):
        field_worker_group = FieldWorkerGroupFactory.create()
        FieldWorkerFactory.create()

        form = UserUpdateForm(
            {
                "name": "A name",
                "email": "updatedemail@example.com",
                "group": field_worker_group,
                "is_active": True,
                "geographic_access": "national",
                "location_restrictions": [],
            }
        )

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "facility_restrictions" in form.errors

    def test_group_required(self):
        # A user with proto_user params does not exist yet.
        proto_user = NewUserFactory.build()
        _, srs_location = create_matching_srs_location()

        form = UserUpdateForm(
            {
                "name": proto_user.name,
                "email": proto_user.email,
                "group": "",
                "geographic_access": "location-specific",
                "location_restrictions": [srs_location],
            }
        )

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "group" in form.errors


class TestPerUserPermissionOverrides:
    def test_can_revoke_role_default_permission_for_a_user(self):
        view_dashboard = Permission.objects.get(
            content_type__app_label="va_analytics",
            codename="view_dashboard",
        )
        group = GroupFactory.create(permissions=[view_dashboard])
        user = UserFactory.create(groups=[group])

        process_user_data(
            user,
            {
                "group": group,
                "location_restrictions": [],
                "facility_restrictions": [],
                "perm_view_dashboard": False,
            },
        )

        user.refresh_from_db()
        assert "va_analytics.view_dashboard" in user.denied_permissions
        assert not user.has_perm("va_analytics.view_dashboard")

    def test_can_grant_permission_not_in_role(self):
        group = GroupFactory.create(permissions=[])
        user = UserFactory.create(groups=[group])
        change_death = Permission.objects.get(
            content_type__app_label="va_data_management",
            codename="change_death",
        )

        process_user_data(
            user,
            {
                "group": group,
                "location_restrictions": [],
                "facility_restrictions": [],
                "perm_change_death": True,
            },
        )

        user.refresh_from_db()
        assert "va_data_management.change_death" not in user.denied_permissions
        assert user.user_permissions.filter(pk=change_death.pk).exists()
        assert user.has_perm("va_data_management.change_death")

    def test_rechecking_permission_clears_user_deny(self):
        view_dashboard = Permission.objects.get(
            content_type__app_label="va_analytics",
            codename="view_dashboard",
        )
        group = GroupFactory.create(permissions=[view_dashboard])
        user = UserFactory.create(
            groups=[group],
            denied_permissions=["va_analytics.view_dashboard"],
        )

        process_user_data(
            user,
            {
                "group": group,
                "location_restrictions": [],
                "facility_restrictions": [],
                "perm_view_dashboard": True,
            },
        )

        user.refresh_from_db()
        assert "va_analytics.view_dashboard" not in user.denied_permissions
        assert user.has_perm("va_analytics.view_dashboard")


class TestUserSetPasswordForm:
    def test_valid_form(self, rf: RequestFactory):
        form = UserSetPasswordForm(
            {
                "password1": "AReallyGreatPassword1!",
                "password2": "AReallyGreatPassword1!",
            }
        )

        assert form.is_valid()

    def test_invalid_form(self, rf: RequestFactory):
        form = UserSetPasswordForm(
            {
                "password1": "AReallyGreatPassword1!",
                "password2": "ACompletelyDifferentPassword1!",
            }
        )

        assert not form.is_valid()
        assert "You must type the same password each time." in form.errors["password2"]
        assert len(form.errors) == 1


class TestUserChangePasswordForm:
    def test_valid_form(self, rf: RequestFactory):
        existing_user = UserFactory.create()
        existing_user.set_password("Password124!")
        existing_user.save()

        form = UserChangePasswordForm(
            data={
                "current_password": "Password124!",
                "password1": "MyNewPassword123!",
                "password2": "MyNewPassword123!",
            },
            user=existing_user,
        )

        assert form.is_valid()

    def test_invalid_form(self, rf: RequestFactory):
        form = UserChangePasswordForm(
            {
                "current_password": "",
                "password1": "MyNewPassword123!",
                "password2": "MyNewPassword456!",
            }
        )

        assert not form.is_valid()
        assert "You must type the same password each time." in form.errors["password2"]
        assert "This field is required" in form.errors["current_password"][0]
        assert len(form.errors) == 2
