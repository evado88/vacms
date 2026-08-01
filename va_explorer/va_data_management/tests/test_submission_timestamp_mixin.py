"""Derivation of the canonical ``submitted_at`` column on the ODK form models."""

from datetime import datetime
from datetime import timezone as datetime_timezone

import pytest

from va_explorer.tests.factories import (
    DeathFactory,
    HouseholdFactory,
    PregnancyFactory,
    PregnancyOutcomeFactory,
    VASAFactory,
    VerbalAutopsyFactory,
)
from va_explorer.va_data_management.models import CSADailyTracker

pytestmark = pytest.mark.django_db

# Models that derive only from ``submissiondate``. VA/VASA have no ``today``
# column at all; Household's is unset on every real row and Death always has a
# submissiondate, so neither of those declares a fallback either.
NO_TODAY_COLUMN = [VerbalAutopsyFactory, VASAFactory]
TODAY_IGNORED = [HouseholdFactory, DeathFactory]
STRICT_FACTORIES = NO_TODAY_COLUMN + TODAY_IGNORED

# Models that fall back to the form's own ``today`` column.
FALLBACK_FACTORIES = [PregnancyFactory, PregnancyOutcomeFactory]

ALL_FACTORIES = STRICT_FACTORIES + FALLBACK_FACTORIES


@pytest.fixture(autouse=True)
def _no_death_id_task(monkeypatch):
    # Death's post_save queues a Celery task and the broker is unreachable in
    # tests, so an unpatched Death.save() hangs the suite.
    monkeypatch.setattr(
        "va_explorer.va_data_management.tasks.assign_death_ids_task.delay",
        lambda *args, **kwargs: None,
    )


@pytest.mark.parametrize("factory", ALL_FACTORIES)
def test_submitted_at_is_derived_from_submissiondate(factory):
    record = factory.create(submissiondate="2026-05-15T16:25:49Z")

    assert record.submitted_at == datetime(
        2026, 5, 15, 16, 25, 49, tzinfo=datetime_timezone.utc
    )


@pytest.mark.parametrize("factory", FALLBACK_FACTORIES)
@pytest.mark.parametrize(
    ("today", "expected"),
    [
        # Midnight in the export timezone (Africa/Lusaka, UTC+2), not UTC.
        ("2025-09-02", datetime(2025, 9, 1, 22, tzinfo=datetime_timezone.utc)),
        ("02/09/2025", datetime(2025, 9, 1, 22, tzinfo=datetime_timezone.utc)),
    ],
)
def test_fallback_models_derive_from_today_when_submissiondate_is_blank(
    factory, today, expected
):
    record = factory.create(submissiondate="", today=today)

    assert record.submitted_at == expected


@pytest.mark.parametrize("factory", TODAY_IGNORED)
def test_models_without_a_declared_fallback_ignore_today(factory):
    # Honouring an undeclared fallback would quietly widen what these exports
    # return.
    record = factory.create(submissiondate="", today="2025-09-02")

    assert record.submitted_at is None


@pytest.mark.parametrize("factory", FALLBACK_FACTORIES)
def test_submissiondate_wins_over_today(factory):
    record = factory.create(
        submissiondate="2026-05-15T16:25:49Z",
        today="2020-01-01",
    )

    assert record.submitted_at == datetime(
        2026, 5, 15, 16, 25, 49, tzinfo=datetime_timezone.utc
    )


@pytest.mark.parametrize("factory", FALLBACK_FACTORIES)
def test_fallback_applies_when_submissiondate_is_unparseable(factory):
    # A malformed submissiondate must not shadow a usable fallback, otherwise a
    # single bad value hides the record from every dated export.
    record = factory.create(submissiondate="not a timestamp", today="2025-09-02")

    assert record.submitted_at == datetime(2025, 9, 1, 22, tzinfo=datetime_timezone.utc)


@pytest.mark.parametrize("factory", ALL_FACTORIES)
def test_unparseable_only_source_clears_the_column_without_raising(factory):
    record = factory.create(submissiondate="15/05/2026")

    assert record.submitted_at is None


@pytest.mark.parametrize("factory", ALL_FACTORIES)
def test_blank_sources_leave_the_column_empty(factory):
    record = factory.create(submissiondate="")

    assert record.submitted_at is None


@pytest.mark.parametrize("factory", ALL_FACTORIES)
def test_unrelated_save_does_not_rederive(factory):
    record = factory.create(submissiondate="2026-05-15T16:25:49Z")
    expected = record.submitted_at

    # A raw value that predates the canonical field, written behind save().
    manager = getattr(type(record), "all_objects", type(record).objects)
    manager.filter(pk=record.pk).update(submissiondate="15/05/2026")
    record.refresh_from_db()

    record.province = "Somewhere Else"
    record.save(update_fields=["province"])
    record.refresh_from_db()

    assert record.submitted_at == expected


def test_csa_daily_tracker_uses_the_fallback():
    record = CSADailyTracker.objects.create(
        key="tracker-1", submissiondate="", today="2025-09-02"
    )

    assert record.submitted_at == datetime(2025, 9, 1, 22, tzinfo=datetime_timezone.utc)
