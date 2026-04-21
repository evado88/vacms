import pandas as pd
import pytest

from va_explorer.va_data_management.models import CSADailyTracker
from va_explorer.va_data_management.odk.config import (
    get_odk_form_configs,
    resolve_odk_form_configs,
)
from va_explorer.va_data_management.odk.normalization import (
    ODKColumnCollision,
    iter_odk_column_aliases,
)
from va_explorer.va_data_management.utils.loading import normalize_dataframe_columns

pytestmark = pytest.mark.django_db


def test_normalize_dataframe_columns_supports_dash_and_dot_aliases():
    df = pd.DataFrame(
        [
            {
                "group.HH-22F": "other",
                "meta.instanceID": "uuid-1",
                "hh_gps-Latitude": "-15.0",
            }
        ]
    )

    normalized = normalize_dataframe_columns(df, CSADailyTracker)

    assert list(normalized.columns) == ["HH_22F", "instanceid", "hh_gps_latitude"]


def test_normalize_dataframe_columns_raises_on_collision():
    df = pd.DataFrame(
        [
            {
                "group.HH-22F": "first",
                "repeat/HH-22F": "second",
            }
        ]
    )

    with pytest.raises(ODKColumnCollision, match="HH_22F"):
        normalize_dataframe_columns(df, CSADailyTracker)


def test_iter_odk_column_aliases_preserves_legacy_dash_tail_behavior():
    aliases = list(iter_odk_column_aliases("respondent-backgr-Id10008"))

    assert "respondent-backgr-Id10008" in aliases
    assert "Id10008" in aliases


def test_get_odk_form_configs_normalizes_defaults(settings):
    settings.ODK_PULL_FORMS = [
        {
            "form_id": "alpha",
            "form_name": "death",
            "enabled": True,
            "schedule_key": "operational",
        },
        {"form_id": "", "form_name": "ignored"},
    ]
    settings.ODK_DEFAULT_PROJECT_ID = 99

    assert get_odk_form_configs() == [
        {
            "form_id": "alpha",
            "form_name": "death",
            "project_id": 99,
            "enabled": True,
            "schedule_key": "operational",
            "fetch_expand": None,
            "watermark_field": None,
        }
    ]


def test_resolve_odk_form_configs_prefers_explicit_form_ids(settings):
    settings.ODK_DEFAULT_PROJECT_ID = 42

    assert resolve_odk_form_configs(form_ids=["one", "two"]) == [
        {
            "form_id": "one",
            "form_name": None,
            "project_id": 42,
            "enabled": True,
            "schedule_key": "manual",
            "fetch_expand": None,
            "watermark_field": None,
        },
        {
            "form_id": "two",
            "form_name": None,
            "project_id": 42,
            "enabled": True,
            "schedule_key": "manual",
            "fetch_expand": None,
            "watermark_field": None,
        },
    ]


def test_get_odk_form_configs_supports_multiple_va_versions(settings):
    settings.ODK_PULL_FORMS = [
        {
            "form_id": "znphi_va_who_v1_5_2_7",
            "form_name": "verbal_autopsy",
            "enabled": True,
            "schedule_key": "va_hourly",
        },
        {
            "form_id": "zm_va_who_v1_5_2_7",
            "form_name": "verbal_autopsy",
            "enabled": True,
            "schedule_key": "va_hourly",
        },
    ]
    settings.ODK_DEFAULT_PROJECT_ID = 7

    configs = get_odk_form_configs()

    assert [cfg["form_id"] for cfg in configs] == [
        "znphi_va_who_v1_5_2_7",
        "zm_va_who_v1_5_2_7",
    ]
    assert {cfg["form_name"] for cfg in configs} == {"verbal_autopsy"}
