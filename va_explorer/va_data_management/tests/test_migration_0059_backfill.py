"""The 0059 data migration that repairs ``submitted_at`` on every form model.

``0057`` added the column to VA/VASA without backfilling it, so every existing
row stayed NULL and silently vanished from dated exports until an operator ran
the management command by hand. These tests pin the behaviour that makes
``migrate`` alone sufficient.
"""

from datetime import datetime
from datetime import timezone as datetime_timezone
from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from va_explorer.tests.factories import (
    HouseholdFactory,
    PregnancyFactory,
    VerbalAutopsyFactory,
)

pytestmark = pytest.mark.django_db

# The module name starts with a digit, so it cannot be imported with `from`.
MIGRATION = (
    "va_explorer.va_data_management.migrations.0059_canonical_submission_timestamp"
)


def _migration_module():
    return import_module(MIGRATION)


def _historical_apps():
    """The app registry as the migration itself sees it.

    Matters because ``apps.get_model()`` yields a plain auto-created manager
    rather than the project's alive-only ``SoftDeletionManager``, which is what
    makes the backfill reach soft-deleted rows.
    """
    executor = MigrationExecutor(connection)
    state = executor.loader.project_state(
        ("va_data_management", "0059_canonical_submission_timestamp")
    )
    return state.apps


def _run_backfill():
    _migration_module().backfill_submitted_at(_historical_apps(), None)


def _clear(model, pk):
    manager = getattr(model, "all_objects", model.objects)
    manager.filter(pk=pk).update(submitted_at=None)


def test_backfill_populates_rows_left_null_by_0057():
    va = VerbalAutopsyFactory.create(submissiondate="2026-05-15T16:25:49Z")
    _clear(type(va), va.pk)

    _run_backfill()

    va.refresh_from_db()
    assert va.submitted_at == datetime(
        2026, 5, 15, 16, 25, 49, tzinfo=datetime_timezone.utc
    )


def test_backfill_reaches_soft_deleted_rows():
    va = VerbalAutopsyFactory.create(submissiondate="2026-05-15T16:25:49Z")
    va.delete()
    _clear(type(va), va.pk)

    _run_backfill()

    restored = type(va).all_objects.get(pk=va.pk)
    assert restored.deleted_at is not None
    assert restored.submitted_at is not None


def test_backfill_honours_the_declared_fallback():
    pregnancy = PregnancyFactory.create(submissiondate="", today="02/09/2025")
    _clear(type(pregnancy), pregnancy.pk)

    _run_backfill()

    pregnancy.refresh_from_db()
    assert pregnancy.submitted_at == datetime(
        2025, 9, 1, 22, tzinfo=datetime_timezone.utc
    )


def test_backfill_leaves_populated_values_alone():
    household = HouseholdFactory.create(submissiondate="2026-01-15T12:00:00Z")
    original = household.submitted_at
    assert original is not None

    _run_backfill()

    household.refresh_from_db()
    assert household.submitted_at == original


def test_backfill_skips_unparseable_values_without_raising():
    # An unattended migrate must not abort a deploy on one bad legacy value.
    household = HouseholdFactory.create(submissiondate="2026-01-15T12:00:00Z")
    type(household).objects.filter(pk=household.pk).update(
        submissiondate="15/05/2026", submitted_at=None
    )

    _run_backfill()

    household.refresh_from_db()
    assert household.submitted_at is None


def test_backfill_is_idempotent():
    va = VerbalAutopsyFactory.create(submissiondate="2026-05-15T16:25:49Z")
    _clear(type(va), va.pk)

    _run_backfill()
    va.refresh_from_db()
    first = va.submitted_at

    _run_backfill()
    va.refresh_from_db()

    assert va.submitted_at == first


def test_backfill_writes_no_history_rows():
    household = HouseholdFactory.create(submissiondate="2026-01-15T12:00:00Z")
    _clear(type(household), household.pk)
    history_count = household.history.count()

    _run_backfill()

    assert household.history.count() == history_count
