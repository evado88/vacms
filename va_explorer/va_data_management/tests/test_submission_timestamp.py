from datetime import date, datetime
from datetime import timezone as datetime_timezone

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings

from va_explorer.tests.factories import VASAFactory, VerbalAutopsyFactory
from va_explorer.va_data_management.utils.submission_timestamp import (
    parse_submission_timestamp,
)


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (
            "2026-05-15T16:25:49.168Z",
            datetime(2026, 5, 15, 16, 25, 49, 168000, tzinfo=datetime_timezone.utc),
        ),
        (
            "2026-05-15T16:25:49+02:00",
            datetime(2026, 5, 15, 14, 25, 49, tzinfo=datetime_timezone.utc),
        ),
        (
            "2026-05-15T16:25:49-04:00",
            datetime(2026, 5, 15, 20, 25, 49, tzinfo=datetime_timezone.utc),
        ),
        (
            datetime(
                2026,
                5,
                15,
                16,
                25,
                tzinfo=datetime_timezone.utc,
            ),
            datetime(
                2026,
                5,
                15,
                16,
                25,
                tzinfo=datetime_timezone.utc,
            ),
        ),
    ],
)
def test_parse_submission_timestamp_normalizes_aware_values_to_utc(raw_value, expected):
    assert parse_submission_timestamp(raw_value) == expected


@override_settings(VA_EXPORT_TIME_ZONE="Africa/Lusaka")
@pytest.mark.parametrize("raw_value", ["2026-05-15", date(2026, 5, 15)])
def test_parse_submission_timestamp_interprets_date_only_in_export_timezone(
    raw_value,
):
    assert parse_submission_timestamp(raw_value) == datetime(
        2026, 5, 14, 22, tzinfo=datetime_timezone.utc
    )


@override_settings(VA_EXPORT_TIME_ZONE="UTC")
def test_parse_submission_timestamp_date_only_follows_the_export_timezone_setting():
    # The date-only reading must track VA_EXPORT_TIME_ZONE, not TIME_ZONE, so a
    # bare date and a date picked in the export UI mean the same local day.
    assert parse_submission_timestamp("2026-05-15") == datetime(
        2026, 5, 15, tzinfo=datetime_timezone.utc
    )


@pytest.mark.parametrize("raw_value", [None, "", "   ", float("nan")])
def test_parse_submission_timestamp_accepts_blank_values(raw_value):
    assert parse_submission_timestamp(raw_value) is None


@pytest.mark.parametrize(
    "raw_value",
    [
        "2026-05-15T16:25:49",
        datetime(2026, 5, 15, 16, 25, 49),
        "15/05/2026",
        "2026-02-30",
        "not-a-date",
    ],
)
def test_parse_submission_timestamp_rejects_naive_or_malformed_values(raw_value):
    with pytest.raises(ValidationError):
        parse_submission_timestamp(raw_value)


@pytest.mark.django_db
@pytest.mark.parametrize("factory", [VerbalAutopsyFactory, VASAFactory])
def test_direct_model_save_synchronizes_submission_timestamp(factory):
    record = factory.create(submissiondate="2026-05-15T16:25:49+02:00")
    assert record.submitted_at == datetime(
        2026, 5, 15, 14, 25, 49, tzinfo=datetime_timezone.utc
    )

    record.submissiondate = "2026-05-16T01:30:00-04:00"
    record.save(update_fields=["submissiondate"])
    record.refresh_from_db()

    assert record.submitted_at == datetime(
        2026, 5, 16, 5, 30, tzinfo=datetime_timezone.utc
    )


@pytest.mark.django_db
@pytest.mark.parametrize("factory", [VerbalAutopsyFactory, VASAFactory])
def test_direct_model_save_clears_canonical_value_for_invalid_raw_value(factory):
    # save() must not raise: soft deletion is implemented as save(), so a strict
    # save would let one bad legacy value block deletes and edits forever.
    # submitted_at is purely derived, so an unparseable source means "unknown".
    record = factory.create()
    assert record.submitted_at is not None

    record.submissiondate = "2026-05-15T16:25:49"
    record.save(update_fields=["submissiondate"])
    record.refresh_from_db()

    assert record.submissiondate == "2026-05-15T16:25:49"
    assert record.submitted_at is None


@pytest.mark.django_db
@pytest.mark.parametrize("factory", [VerbalAutopsyFactory, VASAFactory])
def test_unrelated_save_leaves_canonical_value_untouched(factory):
    record = factory.create()
    expected = record.submitted_at

    # A raw value that predates the canonical field, written behind save().
    type(record).all_objects.filter(pk=record.pk).update(submissiondate="15/05/2026")
    record.refresh_from_db()

    record.hospital = "Somewhere Else"
    record.save(update_fields=["hospital"])
    record.refresh_from_db()

    assert record.submitted_at == expected


@pytest.mark.django_db
@pytest.mark.parametrize("factory", [VerbalAutopsyFactory, VASAFactory])
def test_soft_delete_succeeds_for_an_unparseable_legacy_raw_value(factory):
    record = factory.create()
    type(record).all_objects.filter(pk=record.pk).update(submissiondate="15/05/2026")
    record.refresh_from_db()

    record.delete()

    assert type(record).objects.filter(pk=record.pk).count() == 0
    assert type(record).all_objects.get(pk=record.pk).deleted_at is not None
