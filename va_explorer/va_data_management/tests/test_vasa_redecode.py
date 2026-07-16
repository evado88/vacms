"""Tests for ``manage.py redecode_vasa`` — replaying value decode from
``raw_answers`` provenance after a crosswalk change."""

import json
from io import StringIO

import pandas as pd
import pytest
from django.core.management import call_command

from va_explorer.va_data_management.models import VASA
from va_explorer.va_data_management.models.data_quality import DataQualityIssue
from va_explorer.va_data_management.utils.vasa_loading import (
    load_vasa_from_dataframes,
)
from va_explorer.va_data_management.vasa.field_map import CROSSWALK_VERSION

pytestmark = pytest.mark.django_db


def _load(instanceid, **columns):
    load_vasa_from_dataframes(
        pd.DataFrame([{"instanceID": instanceid, "KEY": instanceid, **columns}]).fillna(
            ""
        )
    )
    return VASA.objects.get(instanceid=instanceid)


def _redecode(*args):
    out = StringIO()
    call_command("redecode_vasa", *args, stdout=out)
    return json.loads(out.getvalue())


def test_redecode_replays_canonical_values_from_raw_answers():
    vasa = _load("uuid:replay", Q1601="2")
    # Simulate a record decoded under a broken/old crosswalk: raw code stored.
    VASA.objects.filter(pk=vasa.pk).update(Id10019="2")
    VASA.objects.filter(pk=vasa.pk).update(
        raw_answers={"values": {"Q1601": "2"}, "meta": {"crosswalk_version": "old"}}
    )

    summary = _redecode()
    vasa.refresh_from_db()
    assert vasa.Id10019 == "female"
    assert vasa.raw_answers["meta"]["crosswalk_version"] == CROSSWALK_VERSION
    assert summary["records_scanned"] == 1
    assert summary["records_changed"] == 1
    assert summary["fields_changed"] == 1


def test_redecode_is_a_noop_on_freshly_loaded_records():
    _load("uuid:noop", Q1601="2", A4006="1")
    summary = _redecode()
    assert summary["records_scanned"] == 1
    assert summary["records_changed"] == 0
    assert summary["fields_changed"] == 0


def test_redecode_dry_run_writes_nothing():
    vasa = _load("uuid:replaydry", Q1601="2")
    VASA.objects.filter(pk=vasa.pk).update(Id10019="2")

    summary = _redecode("--dry-run")
    vasa.refresh_from_db()
    assert summary["dry_run"] is True
    assert summary["records_changed"] == 1
    assert vasa.Id10019 == "2"  # untouched


def test_redecode_never_touches_combined_field_targets():
    # N2005_units raw provenance decodes to Id10358_units, but the routed
    # combined targets (Id10358/Id10359) belong to the loader and stay put.
    vasa = _load("uuid:combined", N2001="1", N2005="2", N2005_units="3")
    assert vasa.Id10358 == "2"
    VASA.objects.filter(pk=vasa.pk).update(Id10358_units="3")  # stale decode

    _redecode()
    vasa.refresh_from_db()
    assert vasa.Id10358_units == "months"  # replayed from raw
    assert vasa.Id10358 == "2"  # combined target untouched
    assert vasa.Id10359 == ""


def test_redecode_flags_and_resolves_decode_issues_only():
    # An unknown code flagged at load time stays open across a redecode ...
    vasa = _load("uuid:flags", Q1601="7", A4001="Peter", Q1202="John Phiri")
    unknown = DataQualityIssue.objects.get(keys__check="unknown_code")
    conflict = DataQualityIssue.objects.get(keys__check="name_conflict")
    assert unknown.status == conflict.status == DataQualityIssue.OPEN

    _redecode()
    unknown.refresh_from_db()
    conflict.refresh_from_db()
    assert unknown.status == DataQualityIssue.OPEN  # still reproduces from raw
    assert conflict.status == DataQualityIssue.OPEN  # not redecode's concern

    # ... and resolves once the raw answer is corrected.
    VASA.objects.filter(pk=vasa.pk).update(
        raw_answers={
            "values": {"Q1601": "2"},
            "meta": {"crosswalk_version": CROSSWALK_VERSION},
        }
    )
    _redecode()
    unknown.refresh_from_db()
    conflict.refresh_from_db()
    assert unknown.status == DataQualityIssue.RESOLVED
    assert conflict.status == DataQualityIssue.OPEN  # conflicts stay untouched
