import json

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from va_explorer.va_data_management.models import Death, ODKFormChoice

pytestmark = pytest.mark.django_db


def _make_death(**kwargs):
    Death.objects.bulk_create([Death(**kwargs)])
    return Death.objects.get(key=kwargs["key"])


def _write_keys(tmp_path, *rows):
    path = tmp_path / "keys.csv"
    path.write_text(
        "id,key,death_id\n"
        + "".join(
            f"{row.get('id', '')},{row.get('key', '')},{row.get('death_id', '')}\n"
            for row in rows
        )
    )
    return path


def _audit(tmp_path, keys, source=None):
    manifest = tmp_path / "manifest.json"
    args = ["--keys-file", str(keys), "--manifest", str(manifest)]
    if source:
        args.extend(["--source-file", str(source)])
    call_command("reconcile_death_nan", *args)
    return manifest, json.loads(manifest.read_text())


def test_audit_is_bounded_reports_unmatched_and_does_not_write(tmp_path):
    death = _make_death(key="uuid:one", death_id="D-1", DE_15="nan")
    _make_death(key="uuid:outside", death_id="D-2", DE_15="nan")
    keys = _write_keys(
        tmp_path,
        {"key": death.key, "death_id": death.death_id},
        {"key": "uuid:missing", "death_id": "D-404"},
    )

    _, manifest = _audit(tmp_path, keys)

    death.refresh_from_db()
    assert death.DE_15 == "nan"
    assert manifest["summary"]["matched_deaths"] == 1
    assert manifest["summary"]["unmatched_inputs"] == 1
    assert manifest["records"][0]["action"] == "normalize_unrecovered"
    assert manifest["records"][0]["proposed_values"] == {"DE_15": None}


def test_audit_accepts_exported_database_id_as_fallback(tmp_path):
    death = _make_death(key=None, death_id=None, DE_15="nan")
    keys = _write_keys(tmp_path, {"id": death.id})

    _, manifest = _audit(tmp_path, keys)

    assert manifest["summary"]["matched_deaths"] == 1
    assert manifest["records"][0]["identity"]["id"] == death.id


def test_stable_identifiers_take_precedence_over_stale_database_id(tmp_path):
    target = _make_death(key="uuid:target", death_id="D-TARGET", DE_15="nan")
    unrelated = _make_death(key="uuid:unrelated", death_id="D-OTHER")
    keys = _write_keys(
        tmp_path,
        {"id": unrelated.id, "key": target.key, "death_id": unrelated.death_id},
    )

    _, manifest = _audit(tmp_path, keys)

    assert manifest["summary"]["matched_deaths"] == 1
    assert manifest["summary"]["ambiguous_inputs"] == 0
    assert manifest["records"][0]["identity"]["id"] == target.id


def test_apply_normalizes_no_consent_and_appends_history(tmp_path):
    death = _make_death(
        key="uuid:declined",
        death_id="D-10",
        eventid="event-7",
        consent="No",
        DE_03="nan",
        DE_15="NAN",
        DE_08="999",
    )
    history_model = Death.history.model
    history_model.objects.create(
        id=death.id,
        key=death.key,
        death_id=death.death_id,
        eventid=death.eventid,
        DE_15="nan",
        history_type="~",
        history_date=timezone.now(),
    )
    old_history_id = history_model.objects.get().history_id
    keys = _write_keys(tmp_path, {"key": death.key})
    manifest_path, _ = _audit(tmp_path, keys)

    call_command("reconcile_death_nan", "--manifest", str(manifest_path), "--apply")

    death.refresh_from_db()
    assert death.DE_03 is None
    assert death.DE_15 is None
    assert death.DE_08 == "999"
    assert death.death_id == "D-10"
    assert death.eventid == "event-7"
    assert history_model.objects.filter(history_id=old_history_id, DE_15="nan").exists()
    assert history_model.objects.count() == 2
    assert death.history.first().history_change_reason == (
        "Death nan/source reconciliation"
    )

    call_command("reconcile_death_nan", "--manifest", str(manifest_path), "--apply")
    assert history_model.objects.count() == 2


def test_source_match_restores_form_fields_but_not_system_fields(tmp_path):
    death = _make_death(
        key="uuid:source",
        death_id="D-20",
        eventid="event-20",
        consent="Yes",
        DE_03="nan",
        DE_05=None,
        DE_06=None,
    )
    for field, value, label in (
        ("consent", "1", "Yes"),
        ("DE_05", "2", "Female"),
    ):
        ODKFormChoice.objects.create(
            form_name="death", field_name=field, value=value, label=label
        )
    source = tmp_path / "source.csv"
    source.write_text(
        "key,death_id,eventid,consent,DE-03,DE-05,DE-06,DE-08\n"
        "uuid:source,SOURCE-ID,SOURCE-EVENT,1,Alice Banda,2,2026-06-01,001234\n"
    )
    keys = _write_keys(tmp_path, {"death_id": death.death_id})

    manifest_path, manifest = _audit(tmp_path, keys, source)
    record = manifest["records"][0]
    assert record["source_status"] == "matched"
    assert record["action"] == "restore_from_source"
    assert "death_id" not in record["proposed_values"]
    assert "eventid" not in record["proposed_values"]

    call_command("reconcile_death_nan", "--manifest", str(manifest_path), "--apply")

    death.refresh_from_db()
    assert death.DE_03 == "Alice Banda"
    assert death.DE_05 == "Female"
    assert death.DE_06 == "2026-06-01"
    assert death.DE_08 == "001234"
    assert death.death_id == "D-20"
    assert death.eventid == "event-20"


def test_ambiguous_source_is_reported_and_not_used_for_restore(tmp_path):
    death = _make_death(key="uuid:duplicate-source", consent="Yes", DE_03="nan")
    source = tmp_path / "source.csv"
    source.write_text(
        "key,consent,DE-03\n"
        "uuid:duplicate-source,Yes,First\n"
        "uuid:duplicate-source,Yes,Second\n"
    )
    keys = _write_keys(tmp_path, {"key": death.key})

    _, manifest = _audit(tmp_path, keys, source)

    record = manifest["records"][0]
    assert record["source_status"] == "ambiguous"
    assert record["action"] == "normalize_unrecovered"
    assert record["proposed_values"] == {"DE_03": None}


def test_apply_rejects_stale_or_tampered_protected_values(tmp_path):
    death = _make_death(
        key="uuid:stale", death_id="D-30", eventid="event-30", DE_15="nan"
    )
    keys = _write_keys(tmp_path, {"key": death.key})
    manifest_path, _ = _audit(tmp_path, keys)
    Death.objects.filter(id=death.id).update(eventid="event-changed")

    with pytest.raises(CommandError, match="Protected field eventid changed"):
        call_command("reconcile_death_nan", "--manifest", str(manifest_path), "--apply")

    assert Death.objects.get(id=death.id).DE_15 == "nan"


def test_apply_requires_a_manifest_and_audit_requires_keys(tmp_path):
    with pytest.raises(CommandError, match="Audit mode requires"):
        call_command(
            "reconcile_death_nan", "--manifest", str(tmp_path / "manifest.json")
        )

    keys = _write_keys(tmp_path, {"key": "uuid:none"})
    with pytest.raises(CommandError, match="accepts only --manifest"):
        call_command(
            "reconcile_death_nan",
            "--keys-file",
            str(keys),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--apply",
        )
