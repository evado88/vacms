import datetime as dt

import pytest
import time_machine
from dateutil.tz import gettz

from va_explorer.home.model_trends import build_trend_for_queryset
from va_explorer.va_data_management.models import Pregnancy

pytestmark = pytest.mark.django_db
eastern_tz = gettz("US/Eastern")


@time_machine.travel(dt.datetime(2026, 3, 5, 9, 0, tzinfo=eastern_tz))
def test_build_trend_for_queryset_is_cumulative_and_non_decreasing():
    Pregnancy.objects.create(key="p1", submissiondate="2025-05-10")
    Pregnancy.objects.create(key="p2", submissiondate="2025-10-10")
    Pregnancy.objects.create(key="p3", submissiondate="2026-03-01")

    payload = build_trend_for_queryset(Pregnancy.objects.all())
    y = payload["graphs"]["recorded"]["y"]

    assert len(y) == 12
    assert y[-1] == 3.0
    assert y == sorted(y)
