import json

import pytest
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from va_explorer.tests.factories import AdminFactory, GroupFactory, UserFactory
from va_explorer.va_admin.command_registry import data_dir, resolve_data_file
from va_explorer.vacms.models import AdminCommandRun

pytestmark = pytest.mark.django_db

RUN_URL = "/admin-panel/run/"
UPLOAD_URL = "/admin-panel/upload-file/"


def _run_perm():
    return Permission.objects.get(
        content_type__app_label="vacms", codename="can_run_admin_commands"
    )


def _login(user, password=None):
    if password:
        user.set_password(password)
        user.save()
    client = Client()
    client.force_login(user)
    return client


def _post_json(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def _stub_call_command(monkeypatch, recorder=None):
    """Replace call_command so tests never execute real management commands."""

    def fake(name, *args, **kwargs):
        if recorder is not None:
            recorder["name"] = name
            recorder["args"] = args
        stdout = kwargs.get("stdout")
        if stdout is not None:
            stdout.write(f"ran {name}")

    monkeypatch.setattr("va_explorer.va_admin.views.call_command", fake)


# ── Access control ───────────────────────────────────────────────────────────


def test_superuser_can_open_panel():
    client = _login(AdminFactory.create())
    assert client.get(reverse("va_admin:index")).status_code == 200


def test_user_with_permission_can_open_panel():
    group = GroupFactory.create(permissions=[_run_perm()])
    client = _login(UserFactory.create(groups=[group]))
    assert client.get(reverse("va_admin:index")).status_code == 200


def test_plain_user_is_forbidden_everywhere(monkeypatch):
    _stub_call_command(monkeypatch)
    client = _login(UserFactory.create())
    assert client.get(reverse("va_admin:index")).status_code == 403
    assert _post_json(client, RUN_URL, {"command_id": "dq_all"}).status_code == 403
    assert client.post(UPLOAD_URL, {}).status_code == 403


# ── Allowlist & execution ────────────────────────────────────────────────────


def test_unknown_command_is_rejected(monkeypatch):
    _stub_call_command(monkeypatch)
    client = _login(AdminFactory.create())
    response = _post_json(client, RUN_URL, {"command_id": "rm_rf_everything"})
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_favorite_command_runs_and_is_logged(monkeypatch):
    recorder = {}
    _stub_call_command(monkeypatch, recorder)
    client = _login(AdminFactory.create())

    response = _post_json(client, RUN_URL, {"command_id": "dq_all", "inputs": {}})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "ran dq_all" in body["output"]
    assert recorder["name"] == "dq_all"

    run = AdminCommandRun.objects.get(command_id="dq_all")
    assert run.ok is True
    assert run.duration_ms >= 0


# ── Password confirmation (non-favorite, mutating) ───────────────────────────


def test_non_favorite_mutating_requires_password(monkeypatch):
    _stub_call_command(monkeypatch)
    client = _login(AdminFactory.create(), password="correct-horse-1")

    # Missing password -> blocked.
    missing = _post_json(client, RUN_URL, {"command_id": "initialize_groups"})
    assert missing.status_code == 403
    assert missing.json().get("password_required") is True

    # Wrong password -> blocked.
    wrong = _post_json(
        client, RUN_URL, {"command_id": "initialize_groups", "password": "nope"}
    )
    assert wrong.status_code == 403

    # No run should have been recorded for the blocked attempts.
    assert not AdminCommandRun.objects.filter(command_id="initialize_groups").exists()

    # Correct password -> runs.
    ok = _post_json(
        client,
        RUN_URL,
        {"command_id": "initialize_groups", "password": "correct-horse-1"},
    )
    assert ok.status_code == 200
    assert ok.json()["ok"] is True
    assert AdminCommandRun.objects.filter(command_id="initialize_groups").exists()


def test_sensitive_inputs_are_masked_in_audit(monkeypatch):
    _stub_call_command(monkeypatch)
    client = _login(AdminFactory.create(), password="confirm-pass-9")

    response = _post_json(
        client,
        RUN_URL,
        {
            "command_id": "seed_admin_user",
            "inputs": {"email": "admin@example.com", "password": "newsecret"},
            "password": "confirm-pass-9",
        },
    )
    assert response.status_code == 200

    run = AdminCommandRun.objects.get(command_id="seed_admin_user")
    assert run.inputs.get("email") == "admin@example.com"
    assert run.inputs.get("password") == "***"
    # The confirmation password must never be stored anywhere on the run.
    assert "confirm-pass-9" not in json.dumps(run.inputs)


# ── File upload ──────────────────────────────────────────────────────────────


def test_upload_saves_to_upload_dir(monkeypatch):
    client = _login(AdminFactory.create())
    upload = SimpleUploadedFile(
        "USERS.csv", b"col_a,col_b\n1,2\n", content_type="text/csv"
    )
    try:
        response = client.post(UPLOAD_URL, {"file": upload, "filename": "USERS.csv"})
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["saved_to"].endswith("USERS.csv")
        saved = resolve_data_file("USERS.csv")
        assert saved.exists()
        assert str(saved).startswith(str(data_dir().resolve()))
    finally:
        target = resolve_data_file("USERS.csv")
        if target.exists():
            target.unlink()


def test_upload_rejects_path_traversal():
    client = _login(AdminFactory.create())
    upload = SimpleUploadedFile("evil.csv", b"x\n", content_type="text/csv")
    response = client.post(UPLOAD_URL, {"file": upload, "filename": "../evil.csv"})
    assert response.status_code == 400
    assert response.json()["ok"] is False
