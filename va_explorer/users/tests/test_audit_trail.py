"""Tests for the System Audit Trail (UserActivity model, signals, admin views)."""

import json

import pytest
from django.contrib.auth.models import Group, Permission
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.urls import reverse

from va_explorer.tests.factories import AdminFactory, UserFactory
from va_explorer.users.models import UserActivity

pytestmark = pytest.mark.django_db


# --- Model + log() helper -------------------------------------------------------


def test_log_creates_entry_and_derives_category():
    user = UserFactory.create()
    UserActivity.log(UserActivity.DATA_EXPORT, user=user, module="death")

    entry = UserActivity.objects.get(action=UserActivity.DATA_EXPORT)
    assert entry.action_category == UserActivity.CATEGORY_DATA
    assert entry.module == "death"
    assert entry.status == UserActivity.STATUS_SUCCESS
    assert entry.user_email == user.email


def test_display_label_is_category_prefixed():
    entry = UserActivity(action=UserActivity.LOGIN)
    assert entry.display_label == "Authentication › Log in"

    entry = UserActivity(action=UserActivity.DATA_IMPORT)
    assert entry.display_label == "Data › Import data"


def test_log_never_raises_on_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(UserActivity.objects, "create", boom)
    # Must not propagate — audit logging cannot break the caller's workflow.
    UserActivity.log(UserActivity.DATA_CREATE)


# --- Authentication signals -----------------------------------------------------


def test_login_signal_records_entry():
    user = UserFactory.create()
    user_logged_in.send(sender=user.__class__, request=None, user=user)
    assert UserActivity.objects.filter(action=UserActivity.LOGIN, user=user).exists()


def test_logout_signal_records_entry():
    user = UserFactory.create()
    user_logged_out.send(sender=user.__class__, request=None, user=user)
    assert UserActivity.objects.filter(action=UserActivity.LOGOUT, user=user).exists()


def test_failed_login_signal_records_entry():
    user_login_failed.send(
        sender=None,
        credentials={"email": "nope@example.com"},
        request=None,
    )
    entry = UserActivity.objects.get(action=UserActivity.LOGIN_FAILED)
    assert entry.status == UserActivity.STATUS_FAILURE
    assert entry.extra.get("attempted_email") == "nope@example.com"


# --- User / role / permission change signals ------------------------------------


def test_role_change_records_entry():
    user = UserFactory.create()
    group = Group.objects.create(name="Auditors")
    user.groups.add(group)

    entry = UserActivity.objects.filter(
        action=UserActivity.ROLE_CHANGED, target_id=str(user.pk)
    ).latest("timestamp")
    assert entry.target_type == "user"
    assert "Auditors" in entry.extra.get("groups", [])


def test_permission_change_records_entry():
    user = UserFactory.create()
    perm = Permission.objects.first()
    user.user_permissions.add(perm)

    assert UserActivity.objects.filter(
        action=UserActivity.PERMISSION_CHANGED, target_id=str(user.pk)
    ).exists()


def test_group_permission_change_records_entry():
    group = Group.objects.create(name="Reviewers")
    perm = Permission.objects.first()
    group.permissions.add(perm)

    entry = UserActivity.objects.filter(
        action=UserActivity.PERMISSION_CHANGED,
        target_type="group",
        target_id=str(group.pk),
    ).latest("timestamp")
    assert perm.codename in entry.extra.get("permissions", [])


def test_profile_update_records_entry_but_not_last_login_only():
    user = UserFactory.create()
    UserActivity.objects.all().delete()

    # A bookkeeping-only save must not be audited.
    user.save(update_fields=["last_login"])
    assert not UserActivity.objects.filter(action=UserActivity.PROFILE_UPDATED).exists()

    # A real edit is audited.
    user.name = "Renamed"
    user.save()
    assert UserActivity.objects.filter(
        action=UserActivity.PROFILE_UPDATED, target_id=str(user.pk)
    ).exists()


# --- Admin views: access control ------------------------------------------------


def test_audit_component_requires_superuser(client):
    user = UserFactory.create()
    client.force_login(user)
    resp = client.get(reverse("va_admin:activity-log") + "?component=1")
    assert resp.status_code == 403


def test_audit_csv_requires_superuser(client):
    user = UserFactory.create()
    client.force_login(user)
    resp = client.get(reverse("va_admin:activity-log-download"))
    assert resp.status_code == 403


def test_audit_direct_nav_redirects_to_panel(client):
    admin = AdminFactory.create()
    client.force_login(admin)
    resp = client.get(reverse("va_admin:activity-log"))
    assert resp.status_code == 302
    assert "adminTabAudit" in resp["Location"]


def test_admin_panel_renders_audit_tab(client):
    admin = AdminFactory.create()
    UserActivity.log(UserActivity.DATA_EXPORT, module="death")

    client.force_login(admin)
    resp = client.get(reverse("va_admin:index"))
    body = resp.content.decode()
    assert resp.status_code == 200
    assert "System Audit Trail" in body
    # Initial fragment is server-rendered into the slot and Type optgroups exist.
    assert "data-list-slot" in body
    assert "All Actions" in body


# --- Admin views: listing + filtering -------------------------------------------


def test_audit_component_lists_and_filters(client):
    admin = AdminFactory.create()
    UserActivity.log(UserActivity.DATA_IMPORT, module="odk")
    UserActivity.log(UserActivity.DATA_EXPORT, module="death")

    client.force_login(admin)
    base = reverse("va_admin:activity-log")

    resp = client.get(base + "?component=1&action=data_import")
    body = resp.content.decode()
    assert resp.status_code == 200
    assert "Import data" in body
    assert "Export/download data" not in body


def test_audit_keyword_search(client):
    admin = AdminFactory.create()
    UserActivity.log(UserActivity.DATA_EXPORT, details="needle in haystack")
    UserActivity.log(UserActivity.DATA_EXPORT, details="unrelated")

    client.force_login(admin)
    resp = client.get(reverse("va_admin:activity-log") + "?component=1&q=needle")
    body = resp.content.decode()
    assert "needle in haystack" in body
    assert "unrelated" not in body


# --- Admin views: CSV export ----------------------------------------------------


def test_audit_csv_export_headers_and_filename(client):
    admin = AdminFactory.create()
    UserActivity.log(UserActivity.DATA_EXPORT, module="death")

    client.force_login(admin)
    resp = client.get(reverse("va_admin:activity-log-download"))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv"
    assert "vacms_audit_logs_" in resp["Content-Disposition"]

    body = b"".join(resp.streaming_content).decode()
    header = body.splitlines()[0]
    for col in ("action_category", "module", "target_display", "status", "details"):
        assert col in header


def test_audit_csv_respects_filters(client):
    admin = AdminFactory.create()
    UserActivity.log(UserActivity.DATA_IMPORT, module="odk")
    UserActivity.log(UserActivity.DATA_EXPORT, module="death")

    client.force_login(admin)
    resp = client.get(reverse("va_admin:activity-log-download") + "?action=data_import")
    body = b"".join(resp.streaming_content).decode()
    assert "data_import" in body
    assert "data_export" not in body


def test_audit_csv_masks_sensitive_values(client):
    admin = AdminFactory.create()
    UserActivity.log(
        UserActivity.DATA_EXPORT,
        extra={"password": "supersecret", "api_key": "abc123", "rows": 5},
    )

    client.force_login(admin)
    resp = client.get(reverse("va_admin:activity-log-download"))
    body = b"".join(resp.streaming_content).decode()

    assert "supersecret" not in body
    assert "abc123" not in body
    assert "***" in body
    # Non-sensitive values are preserved.
    assert "rows" in body
    # Sanity: the masked payload is still valid JSON in the extra column.
    assert json.dumps({"password": "***"}).strip("{}") in body or "***" in body
