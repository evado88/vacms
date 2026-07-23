import json

import pytest
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import Client
from django.urls import reverse

from va_explorer.tests.factories import (
    AdminFactory,
    GroupFactory,
    UserFactory,
    VerbalAutopsyFactory,
)
from va_explorer.va_admin.command_registry import data_dir, resolve_data_file
from va_explorer.va_admin.forms import LinkageConfigurationForm
from va_explorer.va_admin.views import _build_call_command_args
from va_explorer.va_data_management.models import Death, MatchingConfig, VADeathMatch
from va_explorer.va_data_management.utils.matching import (
    auto_link_verbal_autopsy_to_death,
)
from va_explorer.va_data_management.utils.matching.config import resolve_config
from va_explorer.vacms.models import AdminCommandRun

pytestmark = pytest.mark.django_db

RUN_URL = "/admin-panel/run/"
UPLOAD_URL = "/admin-panel/upload-file/"


def _run_perm():
    return Permission.objects.get(
        content_type__app_label="vacms", codename="can_run_admin_commands"
    )


def _linkage_perm():
    return Permission.objects.get(
        content_type__app_label="va_data_management", codename="view_linkagereview"
    )


def _matching_config_perm():
    return Permission.objects.get(
        content_type__app_label="va_data_management", codename="add_matchingconfig"
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


def test_user_with_linkage_permission_can_open_panel():
    group = GroupFactory.create(permissions=[_linkage_perm()])
    client = _login(UserFactory.create(groups=[group]))
    response = client.get(reverse("va_admin:index"))
    assert response.status_code == 200
    assert b"Death-VA Linkage" in response.content
    assert b'href="/va_data_management/linkage-review"' in response.content
    assert b"Automatic Linkage Configuration" in response.content
    assert b"Auto-link score threshold" in response.content
    assert b"Records are treated as a possible death match" in response.content
    assert b"Create and Activate Revision" not in response.content
    assert b"Run Linkage Now" not in response.content


def test_linkage_configuration_creates_and_activates_revision():
    group = GroupFactory.create(permissions=[_linkage_perm(), _matching_config_perm()])
    client = _login(UserFactory.create(groups=[group]))
    previous = MatchingConfig.objects.get(is_active=True)
    previous.weights = {"name": 31}
    previous.save(update_fields=["weights"])
    config = resolve_config()
    payload = {
        name: config["thresholds"][name]
        for name in LinkageConfigurationForm.threshold_fields
    }
    payload.update(
        {
            "revision_name": "linkage-test-revision",
            "notes": "Tuned after local review.",
            "auto": 91,
            "min_sep": 7,
        }
    )

    response = client.post(reverse("va_admin:linkage-config"), payload)

    assert response.status_code == 302
    active = MatchingConfig.objects.get(is_active=True)
    assert active.name == "linkage-test-revision"
    assert active.thresholds["auto"] == 91
    assert active.thresholds["min_sep"] == 7
    assert "max_candidates" not in active.thresholds
    assert active.weights == {"name": 31}
    assert active.updated_by_id is not None


def test_admin_revision_is_used_and_snapshotted_by_linkage_evaluation():
    group = GroupFactory.create(permissions=[_linkage_perm(), _matching_config_perm()])
    client = _login(UserFactory.create(groups=[group]))
    config = resolve_config()
    payload = {
        name: config["thresholds"][name]
        for name in LinkageConfigurationForm.threshold_fields
    }
    payload.update(
        {
            "revision_name": "linkage-evaluation-revision",
            "notes": "Integration test revision.",
            "auto": 0,
            "review": 0,
            "min_sep": 0,
            "tie_margin": 0,
        }
    )
    response = client.post(reverse("va_admin:linkage-config"), payload)
    assert response.status_code == 302
    effective_config = resolve_config()

    death = Death(
        DE_03="Jon Banda",
        DE_04="1980-05-01",
        DE_05="male",
        DE_06="2024-01-10",
        DE_07="Home",
        province="Lusaka",
        district="Lusaka",
        ward="Chaisa",
    )
    Death.objects.bulk_create([death])
    va = VerbalAutopsyFactory.create(
        Id10017="John",
        Id10018="Banda",
        Id10019="male",
        Id10021="1980-05-01",
        Id10023="2024-01-10",
        province="Lusaka",
        district="Lusaka",
        ward="Chaisa",
    )

    auto_link_verbal_autopsy_to_death(va, save=True, source="admin-config-test")

    match = VADeathMatch.objects.get(verbal_autopsy=va)
    assert match.config_version == "linkage-evaluation-revision"
    assert match.config_snapshot == effective_config
    assert match.config_snapshot["thresholds"]["auto"] == 0
    assert "max_candidates" in match.config_snapshot["thresholds"]


def test_historical_legacy_candidate_limit_remains_resolvable():
    config = MatchingConfig.objects.get(is_active=True)
    config.thresholds = {**config.thresholds, "max_candidates": 17}
    config.save()

    resolved = resolve_config()

    assert resolved["thresholds"]["max_candidates"] == 17
    assert "max_candidates" not in LinkageConfigurationForm.threshold_fields


def test_linkage_manager_can_run_linkage_from_admin_panel(monkeypatch):
    recorder = {}
    _stub_call_command(monkeypatch, recorder)
    group = GroupFactory.create(permissions=[_linkage_perm(), _matching_config_perm()])
    client = _login(UserFactory.create(groups=[group]))

    panel = client.get(reverse("va_admin:index"))
    response = client.post(reverse("va_admin:linkage-run"))

    assert b"Run Linkage Now" in panel.content
    assert response.status_code == 302
    assert response.url.endswith("#adminTabLinkage")
    assert recorder["name"] == "link_deaths_to_vas"
    assert recorder["args"] == (
        "--apply",
        "--source",
        "admin_panel_manual",
    )
    run = AdminCommandRun.objects.get(command_id="death_va_linkage")
    assert run.management_command == "link_deaths_to_vas"
    assert run.inputs == {"apply": True, "source": "admin_panel_manual"}
    assert run.ok is True


def test_linkage_run_requires_configuration_permission(monkeypatch):
    _stub_call_command(monkeypatch)
    group = GroupFactory.create(permissions=[_linkage_perm()])
    client = _login(UserFactory.create(groups=[group]))

    response = client.post(reverse("va_admin:linkage-run"))

    assert response.status_code == 403
    assert not AdminCommandRun.objects.filter(command_id="death_va_linkage").exists()


def test_failed_linkage_run_is_audited(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("linkage failed")

    monkeypatch.setattr("va_explorer.va_admin.views.call_command", fail)
    group = GroupFactory.create(permissions=[_linkage_perm(), _matching_config_perm()])
    client = _login(UserFactory.create(groups=[group]))

    response = client.post(reverse("va_admin:linkage-run"))

    assert response.status_code == 302
    run = AdminCommandRun.objects.get(command_id="death_va_linkage")
    assert run.ok is False
    assert "linkage failed" in run.output_full


def test_linkage_configuration_requires_configuration_permission():
    client = _login(
        UserFactory.create(groups=[GroupFactory.create(permissions=[_linkage_perm()])])
    )
    assert client.post(reverse("va_admin:linkage-config"), {}).status_code == 403


def test_linkage_configuration_integrity_error_keeps_active_revision(monkeypatch):
    group = GroupFactory.create(permissions=[_linkage_perm(), _matching_config_perm()])
    client = _login(UserFactory.create(groups=[group]))
    original_active = MatchingConfig.objects.get(is_active=True)
    config = resolve_config()
    payload = {
        name: config["thresholds"][name]
        for name in LinkageConfigurationForm.threshold_fields
    }
    payload.update({"revision_name": "concurrent-revision", "notes": "test"})

    def duplicate_name(**kwargs):
        raise IntegrityError("duplicate configuration name")

    monkeypatch.setattr(MatchingConfig.objects, "create", duplicate_name)

    response = client.post(reverse("va_admin:linkage-config"), payload)

    assert response.status_code == 302
    original_active.refresh_from_db()
    assert original_active.is_active is True


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


def test_vasa_repeat_files_are_resolved_with_group_prefix(settings, tmp_path):
    settings.ADMIN_PANEL_UPLOAD_DIR = str(tmp_path)
    for filename in ("main.csv", "neonate.csv", "child.csv"):
        (tmp_path / filename).write_text("id\n1\n")

    args, safe_inputs, filenames = _build_call_command_args(
        "load_vasa_csv",
        {
            "csv_file": "main.csv",
            "neonate_repeat": "neonate.csv",
            "child_repeat": "child.csv",
        },
    )

    assert args == [
        str(resolve_data_file("main.csv")),
        "--repeat",
        f"br_N2213={resolve_data_file('neonate.csv')}",
        "--repeat",
        f"AD_C3253_1={resolve_data_file('child.csv')}",
    ]
    assert safe_inputs == {
        "csv_file": "main.csv",
        "neonate_repeat": "neonate.csv",
        "child_repeat": "child.csv",
        "dry_run": False,
    }
    assert filenames == ["main.csv", "neonate.csv", "child.csv"]


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
