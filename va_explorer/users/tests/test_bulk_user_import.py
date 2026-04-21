import io

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from va_explorer.users.utils.user_form_backend import (
    parse_users_from_file,
    save_users_from_data,
)

User = get_user_model()


@pytest.fixture
def create_groups(db):
    Group.objects.create(name="Data Viewers")
    Group.objects.create(name="Admins")


@pytest.fixture
def csv_file_content():
    return """email,name,group,password
test1@example.com,Test User 1,Data Viewers,pass1
test2@example.com,Test User 2,Data Viewers,
test3@example.com,Test User 3,,pass3
"""


@pytest.mark.django_db
def test_parse_users_from_file(create_groups, csv_file_content):
    # Setup mock file
    f = io.StringIO(csv_file_content)

    # Test without default group
    # Row 1: valid
    # Row 2: valid (password generated)
    # Row 3: valid (defaults to Data Viewers)
    valid, _, invalid = parse_users_from_file(f)

    assert len(valid) == 3
    assert len(invalid) == 0

    # Verify parsing details
    assert valid[0]["email"] == "test1@example.com"
    assert valid[1]["email"] == "test2@example.com"
    assert len(valid[1]["password"]) == 12  # Randomly generated

    # Verify default group assignment
    data_viewers = Group.objects.get(name="Data Viewers")
    assert valid[2]["group"] == data_viewers.pk


@pytest.mark.django_db
def test_parse_users_with_default_group(create_groups, csv_file_content):
    f = io.StringIO(csv_file_content)

    # Override group
    valid, _, invalid = parse_users_from_file(f, default_group="Admins")

    # All 3 should be valid now because row 3 gets a group
    assert len(valid) == 3
    assert len(invalid) == 0

    # Check that group override worked (Group ID should be used)
    admin_group = Group.objects.get(name="Admins")
    assert valid[0]["group"] == admin_group.pk
    assert valid[2]["group"] == admin_group.pk


@pytest.mark.django_db
def test_save_users_from_data(create_groups):
    data = [
        {
            "email": "save1@example.com",
            "name": "Save One",
            "group": Group.objects.get(name="Data Viewers").pk,
            "password": "password123",
            "mobile1": "1234567890",
            "location_restrictions": "",
            "facility_restrictions": "",
            "geographic_access": "national",
            "view_pii": False,
            "download_data": False,
        }
    ]

    users = save_users_from_data(data)
    assert len(users) == 1

    user = User.objects.get(email="save1@example.com")
    assert user.name == "Save One"
    assert user.groups.first().name == "Data Viewers"
    assert user.check_password("password123")
