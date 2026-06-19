import pandas as pd
import pytest
from django.core.management import call_command

from va_explorer.va_data_management.models import RumorLog, SocialMobilization
from va_explorer.va_data_management.odk.service import (
    import_rumor_log_records,
    purge_junk_rumor_logs,
)

pytestmark = pytest.mark.django_db

PARENT_KEY = "uuid:parent-sm-001"
RUMOR_KEY = "uuid:parent-sm-001/rumors_log_repeat[1]"
RUMOR_KEY_2 = "uuid:parent-sm-001/rumors_log_repeat[2]"


def _make_parent(**kwargs):
    """Create a SocialMobilization record with a known key."""
    return SocialMobilization.objects.create(
        key=PARENT_KEY,
        province="copperbelt",
        district="ndola",
        health_facility="HP1",
        **kwargs,
    )


def _write_csv(tmp_path, rows=None):
    if rows is None:
        rows = [
            {
                "PARENT_KEY": PARENT_KEY,
                "KEY": RUMOR_KEY,
                "rumor_description": "Salt prevents disease",
                "rumor_source": "in_field",
                "community_group_name": "",
                "person_name": "Alice",
                "offline_channel_name": "",
                "online_page_group_name": "",
            }
        ]
    df = pd.DataFrame(rows)
    csv_path = tmp_path / "RUMORS_LOG_REPEAT.csv"
    df.to_csv(csv_path, index=False)
    return str(csv_path)


# --- load_rumor_log management command ----------------------------------------


def test_load_command_creates_rumor_log_linked_to_parent(tmp_path):
    _make_parent()
    call_command("load_rumor_log", _write_csv(tmp_path))

    assert RumorLog.objects.count() == 1
    rl = RumorLog.objects.get(key=RUMOR_KEY)
    assert rl.social_mobilization.key == PARENT_KEY
    assert rl.rumor_description == "Salt prevents disease"
    assert rl.rumor_source == "in_field"
    assert rl.person_name == "Alice"


def test_load_command_skips_missing_parent(tmp_path):
    # No SocialMobilization exists — row should be skipped.
    call_command("load_rumor_log", _write_csv(tmp_path))

    assert RumorLog.objects.count() == 0


def test_load_command_upserts_by_key(tmp_path):
    """Re-importing the same KEY updates the existing record instead of duplicating it."""
    _make_parent()
    call_command("load_rumor_log", _write_csv(tmp_path))
    assert RumorLog.objects.count() == 1

    updated_rows = [
        {
            "PARENT_KEY": PARENT_KEY,
            "KEY": RUMOR_KEY,
            "rumor_description": "Salt actually helps",
            "rumor_source": "radio",
            "community_group_name": "",
            "person_name": "Bob",
            "offline_channel_name": "",
            "online_page_group_name": "",
        }
    ]
    call_command("load_rumor_log", _write_csv(tmp_path, rows=updated_rows))

    # Same key → still one record, description updated.
    assert RumorLog.objects.count() == 1
    rl = RumorLog.objects.get(key=RUMOR_KEY)
    assert rl.rumor_description == "Salt actually helps"
    assert rl.rumor_source == "radio"


def test_load_command_drops_blank_parent_key(tmp_path):
    _make_parent()
    rows = [
        {
            "PARENT_KEY": "",
            "KEY": RUMOR_KEY,
            "rumor_description": "Blank parent",
            "rumor_source": "",
            "community_group_name": "",
            "person_name": "",
            "offline_channel_name": "",
            "online_page_group_name": "",
        }
    ]
    call_command("load_rumor_log", _write_csv(tmp_path, rows=rows))

    assert RumorLog.objects.count() == 0


def test_load_command_intrafile_dedup(tmp_path):
    _make_parent()
    # Same key appears twice — only the last should be kept.
    rows = [
        {
            "PARENT_KEY": PARENT_KEY,
            "KEY": RUMOR_KEY,
            "rumor_description": "First",
            "rumor_source": "a",
            "community_group_name": "",
            "person_name": "",
            "offline_channel_name": "",
            "online_page_group_name": "",
        },
        {
            "PARENT_KEY": PARENT_KEY,
            "KEY": RUMOR_KEY,
            "rumor_description": "Second",
            "rumor_source": "b",
            "community_group_name": "",
            "person_name": "",
            "offline_channel_name": "",
            "online_page_group_name": "",
        },
    ]
    call_command("load_rumor_log", _write_csv(tmp_path, rows=rows))

    assert RumorLog.objects.count() == 1
    assert RumorLog.objects.first().rumor_description == "Second"


def test_load_command_multiple_rows_per_parent(tmp_path):
    _make_parent()
    rows = [
        {
            "PARENT_KEY": PARENT_KEY,
            "KEY": RUMOR_KEY,
            "rumor_description": "Rumour one",
            "rumor_source": "in_field",
            "community_group_name": "",
            "person_name": "",
            "offline_channel_name": "",
            "online_page_group_name": "",
        },
        {
            "PARENT_KEY": PARENT_KEY,
            "KEY": RUMOR_KEY_2,
            "rumor_description": "Rumour two",
            "rumor_source": "online",
            "community_group_name": "",
            "person_name": "",
            "offline_channel_name": "",
            "online_page_group_name": "",
        },
    ]
    call_command("load_rumor_log", _write_csv(tmp_path, rows=rows))

    assert RumorLog.objects.count() == 2
    assert RumorLog.objects.filter(social_mobilization__key=PARENT_KEY).count() == 2


def test_load_command_skips_junk_description(tmp_path):
    """Rows whose rumor_description is in the junk list are not imported."""
    _make_parent()
    # A sample of junk values — case-insensitive, with a real value mixed in.
    rows = [
        {
            "PARENT_KEY": PARENT_KEY,
            "KEY": "uuid:junk-nil",
            "rumor_description": "Nil",  # junk
            "rumor_source": "in_field",
            "community_group_name": "",
            "person_name": "",
            "offline_channel_name": "",
            "online_page_group_name": "",
        },
        {
            "PARENT_KEY": PARENT_KEY,
            "KEY": "uuid:junk-none",
            "rumor_description": "None",  # junk
            "rumor_source": "in_field",
            "community_group_name": "",
            "person_name": "",
            "offline_channel_name": "",
            "online_page_group_name": "",
        },
        {
            "PARENT_KEY": PARENT_KEY,
            "KEY": "uuid:junk-norumours",
            "rumor_description": "No rumours",  # junk
            "rumor_source": "in_field",
            "community_group_name": "",
            "person_name": "",
            "offline_channel_name": "",
            "online_page_group_name": "",
        },
        {
            "PARENT_KEY": PARENT_KEY,
            "KEY": RUMOR_KEY,
            "rumor_description": "Salt causes fever",  # real rumour
            "rumor_source": "in_field",
            "community_group_name": "",
            "person_name": "",
            "offline_channel_name": "",
            "online_page_group_name": "",
        },
    ]
    call_command("load_rumor_log", _write_csv(tmp_path, rows=rows))

    assert RumorLog.objects.count() == 1
    assert RumorLog.objects.first().rumor_description == "Salt causes fever"


def test_load_command_junk_description_case_insensitive(tmp_path):
    """Junk matching is case-insensitive."""
    _make_parent()
    rows = [
        {
            "PARENT_KEY": PARENT_KEY,
            "KEY": RUMOR_KEY,
            "rumor_description": "NIL",  # junk, uppercase
            "rumor_source": "in_field",
            "community_group_name": "",
            "person_name": "",
            "offline_channel_name": "",
            "online_page_group_name": "",
        },
    ]
    call_command("load_rumor_log", _write_csv(tmp_path, rows=rows))

    assert RumorLog.objects.count() == 0


# --- import_rumor_log_records (pyODK path) ------------------------------------


def test_import_rumor_log_records_csv_path():
    _make_parent()
    df = pd.DataFrame(
        [
            {
                "PARENT_KEY": PARENT_KEY,
                "KEY": RUMOR_KEY,
                "rumor_description": "Salt prevents disease",
                "rumor_source": "in_field",
                "person_name": "Alice",
            }
        ]
    )

    counts = import_rumor_log_records(df)

    assert counts["created"] == 1
    assert counts["updated"] == 0
    assert counts["skipped"] == 0
    rl = RumorLog.objects.get(key=RUMOR_KEY)
    assert rl.rumor_description == "Salt prevents disease"


def test_import_rumor_log_records_upserts_by_key():
    """Re-importing the same KEY via service path updates existing record."""
    parent = _make_parent()
    RumorLog.objects.create(
        social_mobilization=parent,
        key=RUMOR_KEY,
        rumor_description="Old description",
    )
    df = pd.DataFrame(
        [
            {
                "PARENT_KEY": PARENT_KEY,
                "KEY": RUMOR_KEY,
                "rumor_description": "Updated description",
                "rumor_source": "radio",
            }
        ]
    )

    counts = import_rumor_log_records(df)

    assert counts["created"] == 0
    assert counts["updated"] == 1
    assert RumorLog.objects.count() == 1
    assert (
        RumorLog.objects.get(key=RUMOR_KEY).rumor_description == "Updated description"
    )


def test_import_rumor_log_records_odk_api_path():
    """Simulate columns as they arrive after list_submissions column stripping.

    ODK OData: __id stays as __id (no '-'); __Submissions-id → id.
    """
    _make_parent()
    df = pd.DataFrame(
        [
            {
                "__id": RUMOR_KEY,
                "id": PARENT_KEY,  # was __Submissions-id
                "rumor_description": "Salt prevents disease",
                "rumor_source": "in_field",
            }
        ]
    )

    counts = import_rumor_log_records(df)

    assert counts["created"] == 1
    rl = RumorLog.objects.get(key=RUMOR_KEY)
    assert rl.social_mobilization.key == PARENT_KEY


def test_import_rumor_log_records_skips_missing_parent():
    df = pd.DataFrame(
        [
            {
                "PARENT_KEY": "uuid:does-not-exist",
                "KEY": RUMOR_KEY,
                "rumor_description": "Orphan",
                "rumor_source": "unknown",
            }
        ]
    )

    counts = import_rumor_log_records(df)

    assert counts["created"] == 0
    assert counts["skipped"] == 1


def test_import_rumor_log_records_skips_junk_description():
    _make_parent()
    df = pd.DataFrame(
        [
            {
                "PARENT_KEY": PARENT_KEY,
                "KEY": "uuid:junk-nil",
                "rumor_description": "Nil",
                "rumor_source": "in_field",
            },
            {
                "PARENT_KEY": PARENT_KEY,
                "KEY": "uuid:junk-norumour",
                "rumor_description": "No rumour",
                "rumor_source": "in_field",
            },
            {
                "PARENT_KEY": PARENT_KEY,
                "KEY": RUMOR_KEY,
                "rumor_description": "Vaccines cause harm",
                "rumor_source": "community",
            },
        ]
    )

    counts = import_rumor_log_records(df)

    assert counts["created"] == 1
    assert counts["skipped"] == 2
    assert RumorLog.objects.count() == 1
    assert RumorLog.objects.first().rumor_description == "Vaccines cause harm"


# --- purge_junk_rumor_logs / cleanup_rumor_log command -----------------------


def test_purge_junk_rumor_logs_deletes_matching_records():
    parent = _make_parent()
    # Three junk records plus one real record.
    RumorLog.objects.bulk_create(
        [
            RumorLog(social_mobilization=parent, key="k1", rumor_description="Nil"),
            RumorLog(
                social_mobilization=parent, key="k2", rumor_description="No rumours"
            ),
            RumorLog(social_mobilization=parent, key="k3", rumor_description="None"),
            RumorLog(
                social_mobilization=parent,
                key="k4",
                rumor_description="Vaccines cause fever",
            ),
        ]
    )

    deleted = purge_junk_rumor_logs()

    assert deleted == 3
    assert RumorLog.objects.count() == 1
    assert RumorLog.objects.first().rumor_description == "Vaccines cause fever"


def test_purge_junk_rumor_logs_case_insensitive():
    parent = _make_parent()
    RumorLog.objects.create(
        social_mobilization=parent, key="k1", rumor_description="NIL"
    )
    RumorLog.objects.create(
        social_mobilization=parent, key="k2", rumor_description="no rumour"
    )

    deleted = purge_junk_rumor_logs()

    assert deleted == 2
    assert RumorLog.objects.count() == 0


def test_purge_junk_rumor_logs_is_idempotent():
    parent = _make_parent()
    RumorLog.objects.create(
        social_mobilization=parent,
        key="k1",
        rumor_description="Good rumour about vaccines",
    )

    first = purge_junk_rumor_logs()
    second = purge_junk_rumor_logs()

    assert first == 0
    assert second == 0
    assert RumorLog.objects.count() == 1


def test_load_command_purges_previously_ingested_junk(tmp_path):
    """load_rumor_log removes existing junk records as part of each import."""
    parent = _make_parent()
    # Simulate a previously ingested junk record (bypasses the import filter).
    RumorLog.objects.create(
        social_mobilization=parent, key="old-junk", rumor_description="Nil"
    )
    assert RumorLog.objects.count() == 1

    # Run load with a real rumour row — junk should be purged.
    call_command("load_rumor_log", _write_csv(tmp_path))

    assert RumorLog.objects.count() == 1
    assert RumorLog.objects.first().rumor_description == "Salt prevents disease"


def test_cleanup_command_removes_junk():
    parent = _make_parent()
    RumorLog.objects.bulk_create(
        [
            RumorLog(social_mobilization=parent, key="k1", rumor_description="Nil"),
            RumorLog(
                social_mobilization=parent, key="k2", rumor_description="No rumour"
            ),
            RumorLog(
                social_mobilization=parent,
                key="k3",
                rumor_description="Vaccines spread disease",
            ),
        ]
    )

    from io import StringIO

    out = StringIO()
    call_command("cleanup_rumor_log", stdout=out)

    assert RumorLog.objects.count() == 1
    assert "2" in out.getvalue()


def test_cleanup_command_reports_nothing_to_purge():
    parent = _make_parent()
    RumorLog.objects.create(
        social_mobilization=parent, key="k1", rumor_description="Salt prevents disease"
    )

    from io import StringIO

    out = StringIO()
    call_command("cleanup_rumor_log", stdout=out)

    assert RumorLog.objects.count() == 1
    assert "nothing" in out.getvalue().lower()
