"""Canonical parsing for ODK VA/VASA submission timestamps."""

import math
import re
from datetime import date, datetime, time
from datetime import timezone as datetime_timezone

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from zoneinfo import ZoneInfo

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def export_timezone():
    """The calendar timezone VA/VASA submission dates are bucketed into.

    Shared by this parser and the export's date bounds so "a bare date" and "a
    date picked in the export UI" always mean the same local day.
    """
    return ZoneInfo(settings.VA_EXPORT_TIME_ZONE)


def _is_blank(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, float):
        return math.isnan(value)
    # Covers pandas.NaT without importing pandas into the model layer.
    try:
        return bool(value != value)
    except (TypeError, ValueError):
        return False


def parse_submission_timestamp(value):
    """Return a UTC-aware timestamp for a raw ODK submission value.

    A date-only legacy value is interpreted as midnight in the export timezone
    (:func:`export_timezone`). Timestamp values must include an explicit UTC
    marker or offset; accepting naive timestamps would make the exported
    calendar date dependent on an undocumented assumption.
    """
    if _is_blank(value):
        return None

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = timezone.make_aware(
            datetime.combine(value, time.min), export_timezone()
        )
    else:
        raw_value = str(value).strip()
        if _ISO_DATE_RE.fullmatch(raw_value):
            try:
                parsed_date = date.fromisoformat(raw_value)
            except ValueError as exc:
                raise ValidationError(
                    "Submission date must be a valid ISO-8601 date or "
                    "timezone-aware timestamp."
                ) from exc
            parsed = timezone.make_aware(
                datetime.combine(parsed_date, time.min),
                export_timezone(),
            )
        else:
            try:
                parsed = parse_datetime(raw_value)
            except ValueError as exc:
                raise ValidationError(
                    "Submission date must be a valid ISO-8601 date or "
                    "timezone-aware timestamp."
                ) from exc
            if parsed is None:
                raise ValidationError(
                    "Submission date must be a valid ISO-8601 date or "
                    "timezone-aware timestamp."
                )

    if timezone.is_naive(parsed):
        raise ValidationError(
            "Submission timestamps must include a UTC marker or timezone offset."
        )
    return parsed.astimezone(datetime_timezone.utc)
