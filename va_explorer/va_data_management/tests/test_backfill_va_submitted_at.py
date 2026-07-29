from datetime import datetime
from datetime import timezone as datetime_timezone
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from va_explorer.tests.factories import VASAFactory, VerbalAutopsyFactory
from va_explorer.va_data_management.models import VerbalAutopsy

pytestmark = pytest.mark.django_db


def _make_legacy(factory, raw_value, *, deleted=False):
    record = factory.create()
    if deleted:
        record.delete()
    record.__class__.all_objects.filter(pk=record.pk).update(
        submissiondate=raw_value,
        submitted_at=None,
    )
    return record


def test_backfill_reports_dry_run_without_writing():
    va = _make_legacy(VerbalAutopsyFactory, "2026-05-15T16:25:49Z")
    vasa = _make_legacy(VASAFactory, "2026-05-16")
    stdout = StringIO()

    call_command(
        "backfill_va_submitted_at",
        "--dry-run",
        "--batch-size=1",
        stdout=stdout,
    )

    va.refresh_from_db()
    vasa.refresh_from_db()
    assert va.submitted_at is None
    assert vasa.submitted_at is None
    assert "DRY RUN" in stdout.getvalue()
    assert "VA: parsed=1 blank=0 invalid=0 updated=0" in stdout.getvalue()
    assert "VASA: parsed=1 blank=0 invalid=0 updated=0" in stdout.getvalue()


def test_backfill_handles_both_sources_soft_deletion_and_invalid_values():
    active_va = _make_legacy(VerbalAutopsyFactory, "2026-05-15T16:25:49+02:00")
    deleted_va = _make_legacy(
        VerbalAutopsyFactory,
        "2026-05-16",
        deleted=True,
    )
    blank_va = _make_legacy(VerbalAutopsyFactory, "")
    invalid_va = _make_legacy(VerbalAutopsyFactory, "15/05/2026")
    vasa = _make_legacy(VASAFactory, "2026-05-17T00:00:00Z")

    # Simulate history created before the canonical field was introduced.
    active_va.history.update(submitted_at=None)
    history_count = active_va.history.count()

    stdout = StringIO()
    # Cutover is unsafe while any record still fails to parse, so the command
    # fails rather than letting a deploy script step over a warning. The
    # per-source counts are still reported before it does.
    with pytest.raises(CommandError, match="1 unparseable raw submission"):
        call_command(
            "backfill_va_submitted_at",
            "--batch-size=1",
            stdout=stdout,
        )

    active_va.refresh_from_db()
    deleted_va = VerbalAutopsy.all_objects.get(pk=deleted_va.pk)
    blank_va.refresh_from_db()
    invalid_va.refresh_from_db()
    vasa.refresh_from_db()

    assert active_va.submitted_at == datetime(
        2026, 5, 15, 14, 25, 49, tzinfo=datetime_timezone.utc
    )
    # A date-only legacy value means midnight in the export timezone
    # (Africa/Lusaka, UTC+2), not midnight UTC.
    assert deleted_va.submitted_at == datetime(
        2026, 5, 15, 22, tzinfo=datetime_timezone.utc
    )
    assert vasa.submitted_at == datetime(2026, 5, 17, tzinfo=datetime_timezone.utc)
    assert blank_va.submitted_at is None
    assert invalid_va.submitted_at is None
    assert "VA: parsed=2 blank=1 invalid=1 updated=2" in stdout.getvalue()
    assert "VASA: parsed=1 blank=0 invalid=0 updated=1" in stdout.getvalue()

    assert active_va.history.count() == history_count
    assert active_va.history.filter(submitted_at__isnull=False).count() == 0

    # Re-running never rewrites already-populated rows.
    second_stdout = StringIO()
    with pytest.raises(CommandError):
        call_command("backfill_va_submitted_at", stdout=second_stdout)
    assert "VA: parsed=0 blank=1 invalid=1 updated=0" in second_stdout.getvalue()
    assert "VASA: parsed=0 blank=0 invalid=0 updated=0" in second_stdout.getvalue()


def test_backfill_succeeds_when_every_raw_value_parses():
    va = _make_legacy(VerbalAutopsyFactory, "2026-05-15T16:25:49Z")
    vasa = _make_legacy(VASAFactory, "2026-05-16T00:00:00Z")
    stdout = StringIO()

    call_command("backfill_va_submitted_at", stdout=stdout)

    va.refresh_from_db()
    vasa.refresh_from_db()
    assert va.submitted_at is not None
    assert vasa.submitted_at is not None
    assert "VA: parsed=1 blank=0 invalid=0 updated=1" in stdout.getvalue()
