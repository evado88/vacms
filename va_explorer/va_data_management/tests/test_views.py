import pytest
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import Client
from django.urls import reverse

from va_explorer.tests.factories import (
    FieldWorkerFactory,
    FieldWorkerGroupFactory,
    GroupFactory,
    LocationFactory,
    UserFactory,
    VerbalAutopsyFactory,
)
from va_explorer.users.management.commands.initialize_groups import GROUPS_PERMISSIONS
from va_explorer.users.models import User
from va_explorer.va_data_management.constants import REDACTED_STRING
from va_explorer.va_data_management.models import Death, Household, Pregnancy, PregnancyOutcome, VerbalAutopsy

pytestmark = pytest.mark.django_db


def seeded_group(name):
    group, _ = Group.objects.get_or_create(name=name)
    group.permissions.clear()
    for model_class, codenames in GROUPS_PERMISSIONS[name].items():
        content_type = ContentType.objects.get_for_model(model_class)
        permissions = Permission.objects.filter(
            content_type=content_type,
            codename__in=codenames,
        )
        group.permissions.add(*permissions)
    return group


@pytest.fixture
def delete_verbalautopsy_group():
    can_delete_verbalautopsy = Permission.objects.filter(
        codename="delete_verbalautopsy"
    ).first()
    return GroupFactory.create(permissions=[can_delete_verbalautopsy])


@pytest.fixture
def bulk_delete_verbalautopsy_group():
    can_bulk_delete_verbalautopsy = Permission.objects.filter(
        codename="bulk_delete"
    ).first()
    return GroupFactory.create(permissions=[can_bulk_delete_verbalautopsy])


@pytest.fixture
def view_datacleanup_group():
    can_view_datacleanup = Permission.objects.filter(
        codename="view_datacleanup"
    ).first()
    return GroupFactory.create(permissions=[can_view_datacleanup])


@pytest.fixture
def questions_to_autodetect_duplicates():
    return "Id10017, Id10010"


# Get the index and make sure the VA in the system is listed
def test_index_with_valid_permission(user: User):
    can_view_record = Permission.objects.filter(codename="view_verbalautopsy").first()
    can_view_pii = Permission.objects.filter(codename="view_pii").first()
    can_view_record_group = GroupFactory.create(
        permissions=[can_view_record, can_view_pii]
    )
    user = UserFactory.create(groups=[can_view_record_group])
    client = Client()
    client.force_login(user=user)
    va = VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")
    response = client.get("/va_data_management/")
    assert response.status_code == 200
    assert bytes(va.Id10010, "utf-8") in response.content


# Get the index and make sure the VA in the system is listed
def test_index_redacted(user: User):
    can_view_record = Permission.objects.filter(codename="view_verbalautopsy").first()
    can_view_record_group = GroupFactory.create(permissions=[can_view_record])
    user = UserFactory.create(groups=[can_view_record_group])

    client = Client()
    client.force_login(user=user)
    va = VerbalAutopsyFactory.create(Id10017="Victim name")
    response = client.get("/va_data_management/")
    assert response.status_code == 200
    assert bytes(REDACTED_STRING, "utf-8") in response.content
    assert bytes(va.Id10017, "utf-8") not in response.content


# Request the index without permissions and make sure its forbidden
def test_index_without_valid_permission(user: User):
    client = Client()
    client.force_login(user=user)

    response = client.get("/va_data_management/")
    assert response.status_code == 403


# Filter VAs by deceased using a search term without spaces
def test_index_deceased_filter_no_space(user: User):
    can_view_record = Permission.objects.filter(codename="view_verbalautopsy").first()
    can_view_pii = Permission.objects.filter(codename="view_pii").first()
    can_view_record_group = GroupFactory.create(
        permissions=[can_view_record, can_view_pii]
    )
    user = UserFactory.create(groups=[can_view_record_group])
    client = Client()
    client.force_login(user=user)
    va = VerbalAutopsyFactory.create(Id10017="Victim", Id10018="Name")
    response = client.get("/va_data_management/", {"deceased": "Victim"})
    assert response.status_code == 200
    assert bytes(va.Id10017, "utf-8") in response.content


# Filter VAs by deceased using a search term with spaces
def test_index_deceased_filter_with_space(user: User):
    can_view_record = Permission.objects.filter(codename="view_verbalautopsy").first()
    can_view_pii = Permission.objects.filter(codename="view_pii").first()
    can_view_record_group = GroupFactory.create(
        permissions=[can_view_record, can_view_pii]
    )
    user = UserFactory.create(groups=[can_view_record_group])
    client = Client()
    client.force_login(user=user)
    va = VerbalAutopsyFactory.create(Id10017="Victim", Id10018="Name")
    response = client.get("/va_data_management/", {"deceased": "Victim Name"})
    assert response.status_code == 200
    assert bytes(va.Id10017, "utf-8") in response.content
    assert bytes(va.Id10018, "utf-8") in response.content


# Show a VA and make sure the data is as expected
def test_show(user: User):
    can_view_record = Permission.objects.filter(codename="view_verbalautopsy").first()
    can_view_pii = Permission.objects.filter(codename="view_pii").first()
    can_view_record_group = GroupFactory.create(
        permissions=[can_view_record, can_view_pii]
    )
    user = UserFactory.create(groups=[can_view_record_group])

    client = Client()
    client.force_login(user=user)
    va = VerbalAutopsyFactory.create(Id10017="Victim name")
    response = client.get(f"/va_data_management/show/{va.id}")
    assert response.status_code == 200
    assert bytes(va.Id10017, "utf-8") in response.content


# Show a VA and make sure the data is as expected (with redacted)
def test_show_redacted(user: User):
    can_view_record = Permission.objects.filter(codename="view_verbalautopsy").first()
    can_view_record_group = GroupFactory.create(permissions=[can_view_record])
    user = UserFactory.create(groups=[can_view_record_group])

    client = Client()
    client.force_login(user=user)
    va = VerbalAutopsyFactory.create(Id10017="Victim name")
    response = client.get(f"/va_data_management/show/{va.id}")
    assert response.status_code == 200
    assert bytes(REDACTED_STRING, "utf-8") in response.content
    assert bytes(va.Id10017, "utf-8") not in response.content


# Request the show page for VA without permissions and make sure it's forbidden
def test_show_without_valid_permissions(user: User):
    client = Client()
    client.force_login(user=user)
    va = VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")
    response = client.get(f"/va_data_management/show/{va.id}")
    assert response.status_code == 403


# Request the edit form of a VA and make sure the data is as expected
def test_edit_with_valid_permissions(user: User):
    can_edit_record = Permission.objects.filter(codename="change_verbalautopsy").first()
    can_view_pii = Permission.objects.filter(codename="view_pii").first()
    can_edit_record_group = GroupFactory.create(
        permissions=[can_edit_record, can_view_pii]
    )
    user = UserFactory.create(groups=[can_edit_record_group])

    client = Client()
    client.force_login(user=user)

    va = VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")
    response = client.get(f"/va_data_management/edit/{va.id}")
    assert response.status_code == 200
    assert bytes(va.Id10010, "utf-8") in response.content
    assert bytes(va.Id10017, "utf-8") in response.content


# Request the edit form of a VA without permissions and make sure its forbidden
def test_edit_without_valid_permissions(user: User):
    client = Client()
    client.force_login(user=user)

    va = VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")
    response = client.get(f"/va_data_management/edit/{va.id}")
    assert response.status_code == 403


def test_delete_with_valid_permission_and_valid_va(
    user: User,
    settings,
    delete_verbalautopsy_group,
    view_datacleanup_group,
    questions_to_autodetect_duplicates,
):
    settings.QUESTIONS_TO_AUTODETECT_DUPLICATES = questions_to_autodetect_duplicates

    user = UserFactory.create(
        groups=[delete_verbalautopsy_group, view_datacleanup_group]
    )

    client = Client()
    client.force_login(user=user)

    VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")
    va2 = VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")

    response = client.post(f"/va_data_management/delete/{va2.id}", follow=True)

    assert response.status_code == 200
    assert (
        bytes(f"Verbal Autopsy {va2.id} was deleted successfully", "utf-8")
        in response.content
    )
    # Confirm that the VA has been deleted
    assert not VerbalAutopsy.objects.filter(pk=va2.id).first()


# A VA must be marked as duplicate for it to be deleted
def test_delete_with_valid_permission_and_non_duplicate_va(
    user: User, delete_verbalautopsy_group, view_datacleanup_group
):
    user = UserFactory.create(
        groups=[delete_verbalautopsy_group, view_datacleanup_group]
    )

    client = Client()
    client.force_login(user=user)

    va = VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")
    response = client.post(f"/va_data_management/delete/{va.id}", follow=True)

    assert response.status_code == 200
    assert (
        bytes(f"Verbal Autopsy {va.id} could not be deleted.", "utf-8")
        in response.content
    )
    # Confirm that the VA has not been deleted
    assert VerbalAutopsy.objects.filter(pk=va.id).first()


# A user must have the delete_verbalautopsy permission to delete a VA
def test_delete_with_invalid_permission(
    user: User, settings, view_datacleanup_group, questions_to_autodetect_duplicates
):
    settings.QUESTIONS_TO_AUTODETECT_DUPLICATES = questions_to_autodetect_duplicates

    user = UserFactory.create(groups=[view_datacleanup_group])

    client = Client()
    client.force_login(user=user)

    VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")
    va2 = VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")

    response = client.post(f"/va_data_management/delete/{va2.id}", follow=True)

    client = Client()
    client.force_login(user=user)

    assert response.status_code == 403
    # Confirm that the VA has not been deleted
    assert VerbalAutopsy.objects.filter(pk=va2.id).first()


def test_bulk_delete_with_valid_permission(
    user: User,
    settings,
    bulk_delete_verbalautopsy_group,
    view_datacleanup_group,
    questions_to_autodetect_duplicates,
):
    settings.QUESTIONS_TO_AUTODETECT_DUPLICATES = questions_to_autodetect_duplicates

    user = UserFactory.create(
        groups=[bulk_delete_verbalautopsy_group, view_datacleanup_group]
    )

    client = Client()
    client.force_login(user=user)

    # Create 3 Verbal Autopsies, 2 which will be duplicate
    for _ in range(0, 3):
        VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")
    # Confirm that there are 2 duplicate VAs in the system
    assert VerbalAutopsy.objects.filter(duplicate=True).count() == 2

    response = client.post("/va_data_management/delete_all", follow=True)

    assert response.status_code == 200
    assert (
        bytes("Duplicate Verbal Autopsies successfully deleted!", "utf-8")
        in response.content
    )
    # Confirm that all duplicates have been deleted
    assert VerbalAutopsy.objects.filter(duplicate=True).count() == 0


def test_bulk_delete_with_invalid_permission(
    user: User, settings, view_datacleanup_group, questions_to_autodetect_duplicates
):
    settings.QUESTIONS_TO_AUTODETECT_DUPLICATES = questions_to_autodetect_duplicates

    user = UserFactory.create(groups=[view_datacleanup_group])

    client = Client()
    client.force_login(user=user)

    # Create 3 Verbal Autopsies, 2 which will be duplicate
    for _ in range(0, 3):
        VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")
    # Confirm that there are 2 duplicate VAs in the system
    assert VerbalAutopsy.objects.filter(duplicate=True).count() == 2

    response = client.post("/va_data_management/delete_all", follow=True)

    assert response.status_code == 403
    # Confirm that all duplicates have been deleted
    assert VerbalAutopsy.objects.filter(duplicate=True).count() == 2


# Update a VA and make sure 1) the data is changed and 2) the history is tracked
def test_save_with_valid_permissions(user: User):
    can_edit_record = Permission.objects.filter(codename="change_verbalautopsy").first()
    can_view_record = Permission.objects.filter(codename="view_verbalautopsy").first()
    can_view_pii = Permission.objects.filter(codename="view_pii").first()
    can_edit_view_record_group = GroupFactory.create(
        permissions=[can_edit_record, can_view_record, can_view_pii]
    )
    user = UserFactory.create(groups=[can_edit_view_record_group])

    client = Client()
    client.force_login(user=user)
    va = VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")
    assert va.history.count() == 1
    new_name = "Updated Example Name"
    response = client.post(
        f"/va_data_management/edit/{va.id}",
        {"Id10010": new_name, "Id10023": "2021-03-01", "Id10058": va.location.name},
    )
    assert response.status_code == 302
    assert response["Location"] == f"/va_data_management/show/{va.id}"
    va = VerbalAutopsy.objects.get(id=va.id)
    assert va.Id10010 == new_name
    assert va.Id10023 == "2021-03-01"
    assert va.history.count() == 2
    assert va.history.first().history_user == user
    response = client.get(f"/va_data_management/show/{va.id}")
    # TODO: We need to handle timezones correctly
    assert (
        bytes(va.history.first().history_date.strftime("%Y-%m-%d %H:%M"), "utf-8")
        in response.content
    )
    assert bytes(va.history.first().history_user.name, "utf-8") in response.content


# Update a VA and make sure there is no redirect
# TODO check form.errors or create separate form test to make this more accurate
def test_save_with_invalid_date_format(user: User):
    can_edit_record = Permission.objects.filter(codename="change_verbalautopsy").first()
    can_view_record = Permission.objects.filter(codename="view_verbalautopsy").first()
    can_view_pii = Permission.objects.filter(codename="view_pii").first()
    can_edit_view_record_group = GroupFactory.create(
        permissions=[can_edit_record, can_view_record, can_view_pii]
    )
    user = UserFactory.create(groups=[can_edit_view_record_group])

    client = Client()
    client.force_login(user=user)
    va = VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")
    assert va.history.count() == 1
    new_name = "Updated Example Name"
    response = client.post(
        f"/va_data_management/edit/{va.id}",
        {"Id10010": new_name, "Id10023": "this is not a date 1234"},
    )
    assert response.status_code == 200


# Verify save access is restricted
def test_save_without_valid_permissions(user: User):
    client = Client()
    client.force_login(user=user)
    va = VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")
    assert va.history.count() == 1
    new_name = "Updated Example Name"
    response = client.post(f"/va_data_management/edit/{va.id}", {"Id10010": new_name})
    assert response.status_code == 403


# Reset an updated VA and make sure
# 1) the data is reset to original values
# 2) the history is tracked
def test_reset_with_valid_permissions(user: User):
    can_edit_record = Permission.objects.filter(codename="change_verbalautopsy").first()
    can_view_pii = Permission.objects.filter(codename="view_pii").first()
    can_edit_record_group = GroupFactory.create(
        permissions=[can_edit_record, can_view_pii]
    )
    user = UserFactory.create(groups=[can_edit_record_group])

    client = Client()
    client.force_login(user=user)
    va = VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")
    original_name = va.Id10010
    original_dod = va.Id10023
    new_name = "Updated Name"
    client.post(
        f"/va_data_management/edit/{va.id}",
        {"Id10010": new_name, "Id10023": "2021-03-01", "Id10058": va.location.name},
    )
    va = VerbalAutopsy.objects.get(id=va.id)
    assert va.Id10010 == new_name
    assert va.history.count() == 2
    # TODO: Switch the buttons to forms in show.html and make this a POST.
    response = client.get(f"/va_data_management/reset/{va.id}")
    assert response.status_code == 302
    assert response["Location"] == f"/va_data_management/show/{va.id}"
    va = VerbalAutopsy.objects.get(id=va.id)
    assert va.Id10010 == original_name
    assert va.Id10023 == original_dod
    assert va.history.count() == 3
    assert va.history.first().history_user == user


# Verify reset access is restricted
def test_reset_without_valid_permissions(user: User):
    client = Client()
    client.force_login(user=user)
    va = VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")
    response = client.get(f"/va_data_management/reset/{va.id}")
    assert response.status_code == 403


# Revert an updated VA and make sure
# 1) the data is reset to previous version
# 2) the history is tracked
def test_revert_latest_with_valid_permissions(user: User):
    can_edit_record = Permission.objects.filter(codename="change_verbalautopsy").first()
    can_view_pii = Permission.objects.filter(codename="view_pii").first()
    can_edit_record_group = GroupFactory.create(
        permissions=[can_edit_record, can_view_pii]
    )
    user = UserFactory.create(groups=[can_edit_record_group])

    client = Client()
    client.force_login(user=user)
    va = VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")
    second_name = "Second Name"
    third_name = "Third Name"
    client.post(
        f"/va_data_management/edit/{va.id}",
        {"Id10010": second_name, "Id10023": "2021-03-01", "Id10058": va.location.name},
    )
    va = VerbalAutopsy.objects.get(id=va.id)
    assert va.Id10010 == second_name
    assert va.Id10023 == "2021-03-01"
    assert va.history.count() == 2
    client.post(
        f"/va_data_management/edit/{va.id}",
        {"Id10010": third_name, "Id10023": "2021-03-02", "Id10058": va.location.name},
    )
    va = VerbalAutopsy.objects.get(id=va.id)
    assert va.Id10010 == third_name
    assert va.Id10023 == "2021-03-02"
    assert va.history.count() == 3
    # TODO: Switch the buttons to forms in show.html and make this a POST.
    response = client.get(f"/va_data_management/revert_latest/{va.id}")
    assert response.status_code == 302
    assert response["Location"] == f"/va_data_management/show/{va.id}"
    va = VerbalAutopsy.objects.get(id=va.id)
    assert va.Id10010 == second_name
    assert va.Id10023 == "2021-03-01"
    assert va.history.count() == 4
    assert va.history.first().history_user == user


# Verify revert access is restricted
def test_revert_latest_without_valid_permissions(user: User):
    client = Client()
    client.force_login(user=user)
    va = VerbalAutopsyFactory.create(Id10017="Victim name", Id10010="Interviewer name")
    response = client.get(f"/va_data_management/revert_latest/{va.id}")
    assert response.status_code == 403


# Test all methods for access control restrictions
def test_access_control(user: User):
    # Set up a location tree so the user can be scoped without access to a VA
    can_edit_record = Permission.objects.filter(codename="change_verbalautopsy").first()
    can_edit_record_group = GroupFactory.create(permissions=[can_edit_record])
    can_view_record = Permission.objects.filter(codename="view_verbalautopsy").first()
    can_view_record_group = GroupFactory.create(permissions=[can_view_record])
    user = UserFactory.create(groups=[can_edit_record_group, can_view_record_group])

    province = LocationFactory.create()
    district1 = province.add_child(name="District1", location_type="district")
    district2 = province.add_child(name="District2", location_type="district")
    facility = district1.add_child(name="Facility", location_type="facility")
    va = VerbalAutopsyFactory.create(location=facility, Id10017="Victim name")
    user.location_restrictions.set([district2])  # Should not have access to VA
    client = Client()
    client.force_login(user=user)
    response = client.get("/va_data_management/")
    assert response.status_code == 200
    assert bytes(va.Id10017, "utf-8") not in response.content
    response = client.get(f"/va_data_management/show/{va.id}")
    assert response.status_code == 404
    response = client.get(f"/va_data_management/edit/{va.id}")
    assert response.status_code == 404
    response = client.post(
        f"/va_data_management/edit/{va.id}", {"10023": "new death date"}
    )
    assert response.status_code == 404
    response = client.get(f"/va_data_management/reset/{va.id}")
    assert response.status_code == 404
    response = client.get(f"/va_data_management/revert_latest/{va.id}")
    assert response.status_code == 404


# A Field Worker can access only the Verbal Autopsies from their own facility
def test_field_worker_access_control():
    can_view_record = Permission.objects.filter(codename="view_verbalautopsy").first()
    can_view_pii = Permission.objects.filter(codename="view_pii").first()
    field_worker_group = FieldWorkerGroupFactory.create(
        permissions=[can_view_record, can_view_pii]
    )
    field_worker = FieldWorkerFactory.create(groups=[field_worker_group])

    province = LocationFactory.create()
    district1 = province.add_child(name="District1", location_type="district")
    district2 = province.add_child(name="District2", location_type="district")
    facility1 = district1.add_child(name="Facility1", location_type="facility")
    facility2 = district2.add_child(name="Facility2", location_type="facility")
    field_worker.location_restrictions.add(*[facility1])

    field_worker.save()

    va = VerbalAutopsyFactory.create(
        Id10017="deceased_name_1",
        Id10010="Role specific value",
        location=facility1,
    )
    va2 = VerbalAutopsyFactory.create(Id10017="deceased_name_2", location=facility1)
    va3 = VerbalAutopsyFactory.create(Id10017="deceased_name_3", location=facility2)

    client = Client()
    client.force_login(user=field_worker)

    response = client.get("/va_data_management/")
    assert response.status_code == 200
    assert str(va.Id10017).encode("utf_8") in response.content
    # field workers see deceased, not interviewer
    assert str(va.Id10010).encode("utf_8") not in response.content
    # field workers see their own facility VAs
    assert str(va2.Id10017).encode("utf_8") in response.content
    # field workers cannot see other facility VAs
    assert str(va3.Id10017).encode("utf_8") not in response.content

    response = client.get(f"/va_data_management/show/{va.id}")
    assert response.status_code == 200

    response = client.get(f"/va_data_management/show/{va2.id}")
    assert response.status_code == 200

    response = client.get(f"/va_data_management/show/{va3.id}")
    assert response.status_code == 404


def test_data_manager_can_access_non_user_data_views():
    user = UserFactory.create(groups=[seeded_group("Data Managers")])
    client = Client()
    client.force_login(user=user)

    urls = [
        reverse("va_data_management:households"),
        reverse("va_data_management:pregnancies"),
        reverse("va_data_management:pregnancy_outcomes"),
        reverse("va_data_management:deaths"),
        reverse("va_data_management:index"),
    ]

    for url in urls:
        response = client.get(url)
        assert response.status_code == 200


def test_data_viewer_can_access_default_data_views():
    user = UserFactory.create(groups=[seeded_group("Data Viewers")])
    client = Client()
    client.force_login(user=user)

    urls = [
        reverse("va_data_management:households"),
        reverse("va_data_management:pregnancies"),
        reverse("va_data_management:pregnancy_outcomes"),
        reverse("va_data_management:deaths"),
        reverse("va_data_management:index"),
    ]

    for url in urls:
        response = client.get(url)
        assert response.status_code == 200


@pytest.mark.parametrize(
    "url_name",
    [
        "va_data_management:households",
        "va_data_management:pregnancies",
        "va_data_management:pregnancy_outcomes",
        "va_data_management:deaths",
    ],
)
def test_non_privileged_user_cannot_access_restricted_data_views(url_name):
    user = UserFactory.create(groups=[GroupFactory.create(permissions=[])])
    client = Client()
    client.force_login(user=user)

    response = client.get(reverse(url_name))

    assert response.status_code == 403


def test_data_manager_verbal_autopsies_are_scoped_to_assigned_location():
    province = LocationFactory.create(name="Scoped Province")
    allowed_district = province.add_child(name="Allowed District", location_type="district")
    blocked_district = province.add_child(name="Blocked District", location_type="district")
    allowed_facility = allowed_district.add_child(name="Allowed Facility", location_type="facility")
    blocked_facility = blocked_district.add_child(name="Blocked Facility", location_type="facility")

    user = UserFactory.create(
        groups=[seeded_group("Data Managers")],
        location_restrictions=[allowed_district],
    )
    client = Client()
    client.force_login(user=user)

    allowed_va = VerbalAutopsyFactory.create(
        location=allowed_facility,
        Id10017="Allowed Person",
        Id10010="Allowed Interviewer",
    )
    blocked_va = VerbalAutopsyFactory.create(
        location=blocked_facility,
        Id10017="Blocked Person",
        Id10010="Blocked Interviewer",
    )

    response = client.get(reverse("va_data_management:index"))

    assert response.status_code == 200
    assert bytes(allowed_va.Id10010, "utf-8") in response.content
    assert bytes(blocked_va.Id10010, "utf-8") not in response.content


def test_data_manager_households_are_scoped_to_assigned_location():
    province = LocationFactory.create(name="Province Scope")
    allowed_district = province.add_child(name="Allowed Household District", location_type="district")
    province.add_child(name="Blocked Household District", location_type="district")

    user = UserFactory.create(
        groups=[seeded_group("Data Managers")],
        location_restrictions=[allowed_district],
    )
    client = Client()
    client.force_login(user=user)

    Household.objects.create(
        key="allowed-household",
        province="Province Scope",
        district="Allowed Household District",
        respondent="Allowed Respondent",
        HH_22="Piped water",
    )
    Household.objects.create(
        key="blocked-household",
        province="Province Scope",
        district="Blocked Household District",
        respondent="Blocked Respondent",
        HH_22="Piped water",
    )

    response = client.get(reverse("va_data_management:households"))

    assert response.status_code == 200
    assert b"Allowed Respondent" in response.content
    assert b"Blocked Respondent" not in response.content
