from datetime import timedelta

import pandas as pd
import pytest
from django.core.management import call_command
from django.utils import timezone

from va_explorer.va_data_management.models import (
    CSADailyTracker,
    Death,
    Household,
    ODKFormChoice,
    ODKPullLock,
    ODKPullState,
)
from va_explorer.va_data_management.odk.service import (
    ODKPullLocked,
    ODKPullService,
    import_dataframe_records,
)

pytestmark = pytest.mark.django_db


def test_pull_form_updates_state_and_counts(monkeypatch):
    service = ODKPullService(default_project_id=1)
    last_seen = timezone.now() - timedelta(days=2)
    state = ODKPullState.objects.create(
        form_id="abc",
        project_id=1,
        last_submission_at=last_seen,
        last_server_submission_at=last_seen + timedelta(hours=1),
    )

    calls = {}

    def fake_list_submissions(
        form_id, project_id, since=None, include_attachments=False, **kwargs
    ):
        calls["since"] = since
        df = pd.DataFrame([{"createdAt": "2024-01-01T00:00:00Z"}])
        return df, {
            "submission_at": timezone.now(),
            "server_submission_at": timezone.now(),
        }

    def fake_upsert(df, form_name=None):
        return {"created": len(df)}

    monkeypatch.setattr(service, "list_submissions", fake_list_submissions)
    monkeypatch.setattr(service, "upsert_into_models", fake_upsert)

    summary = service.pull_form("abc", project_id=1, form_name="test")

    state.refresh_from_db()
    assert calls["since"] == last_seen - timedelta(seconds=300)
    assert state.last_run_status == ODKPullState.STATUS_SUCCESS
    assert summary["created"] == 1
    assert state.last_submission_at is not None
    assert state.last_server_submission_at is not None


def test_lock_prevents_overlap():
    service = ODKPullService(default_project_id=1)
    ODKPullLock.objects.create(
        form_id="locked",
        project_id=1,
        expires_at=timezone.now() + timedelta(hours=1),
    )

    with pytest.raises(ODKPullLocked):
        service.pull_form("locked", project_id=1)


def test_management_command_delegates_to_service(monkeypatch):
    called = {}

    class FakeService(ODKPullService):
        def pull_forms(self, form_configs, **kwargs):
            called["configs"] = list(form_configs)
            called["kwargs"] = kwargs
            return {"abc": {"status": "SUCCESS", "created": 1}}

    monkeypatch.setattr(
        "va_explorer.va_data_management.management.commands.import_from_odk.ODKPullService",
        FakeService,
    )

    call_command("import_from_odk", "--form-id", "abc", "--full-refresh", "--dry-run")

    assert called["configs"][0]["form_id"] == "abc"
    assert called["kwargs"]["full_refresh"] is True
    assert called["kwargs"]["dry_run"] is True


def test_list_submissions_omits_expand_by_default(monkeypatch):
    service = ODKPullService(default_project_id=1)
    captured = {}

    class FakeSubmissions:
        def get_table(self, **kwargs):
            captured.update(kwargs)
            return {"value": []}

    class FakeClientContext:
        submissions = FakeSubmissions()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClientFactory:
        def open(self):
            return FakeClientContext()

    monkeypatch.setattr(service, "get_client", lambda project_id: FakeClientFactory())

    _, watermarks = service.list_submissions("abc", 1)

    assert "expand" not in captured
    assert watermarks == {"submission_at": None, "server_submission_at": None}


def test_list_submissions_passes_configured_expand(monkeypatch):
    service = ODKPullService(default_project_id=1)
    captured = {}

    class FakeSubmissions:
        def get_table(self, **kwargs):
            captured.update(kwargs)
            return {"value": []}

    class FakeClientContext:
        submissions = FakeSubmissions()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClientFactory:
        def open(self):
            return FakeClientContext()

    monkeypatch.setattr(service, "get_client", lambda project_id: FakeClientFactory())

    service.list_submissions("abc", 1, fetch_expand="*")

    assert captured["expand"] == "*"


def test_extract_watermarks_prefers_submission_and_server_submission_dates():
    service = ODKPullService(default_project_id=1)
    df = pd.DataFrame(
        [
            {
                "updatedAt": "2024-01-01T00:00:00Z",
                "__system/submissionDate": "2024-01-01T01:00:00Z",
            },
            {
                "updatedAt": "2024-01-02T00:00:00Z",
                "__system/submissionDate": "2024-01-02T01:30:00Z",
            },
        ]
    )

    watermarks = service._extract_watermarks(df)

    assert watermarks["submission_at"].isoformat() == "2024-01-02T00:00:00+00:00"
    assert watermarks["server_submission_at"].isoformat() == "2024-01-02T01:30:00+00:00"


def test_import_dataframe_records_upserts_non_va_rows():
    Household.objects.create(key="house-1", province="Old Province", HH_01="Initial")
    ODKFormChoice.objects.create(
        form_name="household",
        field_name="province",
        value="1",
        label="Central",
    )

    df = pd.DataFrame(
        [
            {"KEY": "house-1", "province": "1", "HH-01": "Updated"},
            {"KEY": "house-2", "province": "1", "HH-01": "Created"},
        ]
    )

    counts = import_dataframe_records("household", df)

    assert counts == {"created": 1, "updated": 1, "ignored": 0, "duplicate_rows": 0}
    assert Household.objects.count() == 2
    existing = Household.objects.get(key="house-1")
    created = Household.objects.get(key="house-2")
    assert existing.province == "Central"
    assert existing.HH_01 == "Updated"
    assert created.province == "Central"


def test_death_partial_update_preserves_absent_and_system_fields():
    Death.objects.bulk_create(
        [
            Death(
                key="death-1",
                death_id="1321BY0062",
                eventid="event-1",
                DE_03="Existing Name",
                DE_06="2026-01-01",
            )
        ]
    )

    counts = import_dataframe_records(
        "death",
        pd.DataFrame(
            [
                {
                    "key": "death-1",
                    "death_id": "source-id",
                    "eventid": "source-event",
                    "DE-06": "2026-02-02",
                }
            ]
        ),
    )

    death = Death.objects.get(key="death-1")
    assert counts["updated"] == 1
    assert death.DE_03 == "Existing Name"
    assert death.DE_06 == "2026-02-02"
    assert death.death_id == "1321BY0062"
    assert death.eventid == "event-1"


def test_death_hyphenated_choice_field_is_mapped_after_normalization():
    ODKFormChoice.objects.create(
        form_name="death", field_name="DE_05", value="02", label="Female"
    )

    import_dataframe_records(
        "death", pd.DataFrame([{"key": "death-choice", "DE-05": "02"}])
    )

    assert Death.objects.get(key="death-choice").DE_05 == "Female"


def test_consenting_death_with_missing_core_columns_is_diagnosed(caplog):
    with caplog.at_level("WARNING"):
        import_dataframe_records(
            "death", pd.DataFrame([{"key": "death-warning", "consent": "Yes"}])
        )

    assert "missing expected source column(s)" in caplog.text
    assert "DE_03" in caplog.text


def test_import_dataframe_records_drops_blank_keys_and_intrafile_duplicates():
    df = pd.DataFrame(
        [
            {"KEY": "", "HH-01": "Blank"},
            {"KEY": "house-1", "HH-01": "Older"},
            {"KEY": "house-1", "HH-01": "Newest"},
        ]
    )

    counts = import_dataframe_records("household", df)

    assert counts == {"created": 1, "updated": 0, "ignored": 1, "duplicate_rows": 1}
    assert Household.objects.get(key="house-1").HH_01 == "Newest"


def test_import_dataframe_records_routes_to_csa_daily_tracker():
    df = pd.DataFrame(
        [
            {"KEY": "csa-1", "today": "2026-01-01", "HH-22F": "Other"},
        ]
    )

    counts = import_dataframe_records("csa_daily_tracker", df)

    assert counts == {"created": 1, "updated": 0, "ignored": 0, "duplicate_rows": 0}
    created = CSADailyTracker.objects.get(key="csa-1")
    assert created.today == "2026-01-01"
    assert created.HH_22F == "Other"


def test_upsert_into_models_wraps_va_loader_in_atomic_transaction(monkeypatch):
    service = ODKPullService(default_project_id=1)

    def fake_load_records(df, **kwargs):
        Household.objects.create(key="tx-house")
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "va_explorer.va_data_management.odk.service.load_records_from_dataframe",
        fake_load_records,
    )

    with pytest.raises(RuntimeError, match="boom"):
        service.upsert_into_models(
            pd.DataFrame([{"instanceid": "va-1"}]), form_name="verbal_autopsy"
        )

    assert not Household.objects.filter(key="tx-house").exists()
