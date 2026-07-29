"""Audit and repair Death records affected by literal ``nan`` values.

The command deliberately separates planning from mutation. Audit mode creates
a reviewable manifest from a bounded key file and an optional raw ODK source
export. Apply mode accepts only that manifest, verifies that the database has
not changed since the audit, and appends simple-history repair records.
"""

import json
from collections import defaultdict
from pathlib import Path

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from simple_history.utils import bulk_update_with_history

from va_explorer.va_data_management.models import Death
from va_explorer.va_data_management.odk.service import (
    _strip_group_prefix,
    prepare_form_dataframe,
)
from va_explorer.va_data_management.utils.loading import read_form_csv

MANIFEST_VERSION = 1
PROTECTED_FIELDS = frozenset({"id", "key", "death_id", "eventid"})
TEXT_FIELD_NAMES = frozenset(
    field.name
    for field in Death._meta.concrete_fields
    if field.get_internal_type() in {"TextField", "CharField"}
)
REPAIRABLE_FIELDS = TEXT_FIELD_NAMES - PROTECTED_FIELDS
AFFIRMATIVE_CONSENT = frozenset({"yes", "y", "true", "1", "01"})
NEGATIVE_CONSENT = frozenset({"no", "n", "false", "0", "00"})


def _is_nan(value):
    return isinstance(value, str) and value.strip().lower() == "nan"


def _is_affirmative(value):
    return str(value or "").strip().lower() in AFFIRMATIVE_CONSENT


def _is_negative(value):
    return str(value or "").strip().lower() in NEGATIVE_CONSENT


def _source_value(value):
    """Normalize an explicit source blank without altering meaningful strings."""
    if pd.isna(value):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() == "nan":
            return None
    return value


def _read_key_rows(path):
    try:
        frame = read_form_csv(path)
    except (OSError, ValueError) as exc:
        raise CommandError(f"Could not read keys file: {exc}") from exc

    frame.columns = [str(column).strip().lower() for column in frame.columns]
    available = {"id", "key", "death_id"} & set(frame.columns)
    if not available:
        raise CommandError(
            "Keys file must contain an 'id', 'key', or 'death_id' column."
        )

    rows = []
    for raw in frame.to_dict(orient="records"):
        identity = {
            name: str(raw.get(name, "")).strip() or None
            for name in ("id", "key", "death_id")
            if name in frame.columns
        }
        if identity.get("id"):
            try:
                identity["id"] = int(identity["id"])
            except ValueError as exc:
                raise CommandError(
                    f"Invalid Death id in keys file: {identity['id']!r}."
                ) from exc
        if any(identity.values()):
            rows.append(identity)
    if not rows:
        raise CommandError("Keys file does not contain any usable identifiers.")
    return rows


def _select_deaths(key_rows):
    ids = {row.get("id") for row in key_rows if row.get("id")}
    keys = {row.get("key") for row in key_rows if row.get("key")}
    death_ids = {row.get("death_id") for row in key_rows if row.get("death_id")}
    query = Q()
    if ids:
        query |= Q(id__in=ids)
    if keys:
        query |= Q(key__in=keys)
    if death_ids:
        query |= Q(death_id__in=death_ids)
    candidates = list(Death.objects.filter(query).order_by("id"))

    selected = {}
    unmatched = []
    ambiguous = []
    for identity in key_rows:
        if identity.get("key"):
            # The ODK submission key is the durable source identity. Death IDs
            # may be re-minted and database PKs may be reused after a restore,
            # so neither is allowed to redirect a row that has an ODK key.
            matches = [death for death in candidates if death.key == identity["key"]]
        elif identity.get("death_id"):
            matches = [
                death for death in candidates if death.death_id == identity["death_id"]
            ]
        else:
            matches = [
                death
                for death in candidates
                if identity.get("id") and death.id == identity["id"]
            ]
        unique_matches = {death.id: death for death in matches}
        if not unique_matches:
            unmatched.append(identity)
        elif len(unique_matches) > 1:
            ambiguous.append(identity)
        else:
            death = next(iter(unique_matches.values()))
            selected[death.id] = death
    return list(selected.values()), unmatched, ambiguous


def _read_source_rows(path):
    if not path:
        return {}, []
    try:
        frame = read_form_csv(path)
    except (OSError, ValueError) as exc:
        raise CommandError(f"Could not read source file: {exc}") from exc

    frame.columns = [_strip_group_prefix(column) for column in frame.columns]
    frame = prepare_form_dataframe("death", frame)
    if "key" not in frame.columns:
        raise CommandError("Source file must contain an ODK submission key.")

    rows_by_key = defaultdict(list)
    for row in frame.to_dict(orient="records"):
        key = str(row.get("key") or "").strip()
        if key:
            rows_by_key[key].append(row)
    return dict(rows_by_key), list(frame.columns)


def _build_record_plan(death, source_rows, source_columns, source_provided):
    nan_fields = sorted(
        field for field in TEXT_FIELD_NAMES if _is_nan(getattr(death, field))
    )
    matches = source_rows.get(str(death.key or "").strip(), [])
    if not source_provided:
        source_status = "not_provided"
    elif not matches:
        source_status = "missing"
    elif len(matches) > 1:
        source_status = "ambiguous"
    else:
        source_status = "matched"

    proposed = {field: None for field in nan_fields if field in REPAIRABLE_FIELDS}
    source_consent = matches[0].get("consent") if len(matches) == 1 else None
    effective_consent = (
        source_consent if source_consent not in {None, ""} else death.consent
    )
    consented = _is_affirmative(effective_consent)

    restored_fields = []
    if source_status == "matched" and consented:
        source = matches[0]
        for field in source_columns:
            if field not in REPAIRABLE_FIELDS:
                continue
            value = _source_value(source.get(field))
            if getattr(death, field) != value:
                proposed[field] = value
                restored_fields.append(field)

    proposed = {
        field: value
        for field, value in proposed.items()
        if getattr(death, field) != value
    }
    original = {field: getattr(death, field) for field in proposed}

    if source_status == "matched" and consented:
        action = "restore_from_source"
    elif _is_negative(effective_consent):
        action = "normalize_no_consent"
    else:
        action = "normalize_unrecovered"
    if not proposed:
        action = "no_change"

    return {
        "identity": {"id": death.id, "key": death.key, "death_id": death.death_id},
        "consent": death.consent,
        "effective_consent": effective_consent,
        "source_status": source_status,
        "action": action,
        "nan_fields": nan_fields,
        "restored_fields": sorted(restored_fields),
        "protected_values": {
            field: getattr(death, field) for field in sorted(PROTECTED_FIELDS)
        },
        "original_values": original,
        "proposed_values": proposed,
    }


def _write_manifest(*, keys_file, source_file, manifest_path):
    key_rows = _read_key_rows(keys_file)
    deaths, unmatched, ambiguous_identifiers = _select_deaths(key_rows)
    source_rows, source_columns = _read_source_rows(source_file)
    records = [
        _build_record_plan(
            death,
            source_rows,
            source_columns,
            source_provided=bool(source_file),
        )
        for death in deaths
    ]

    action_counts = defaultdict(int)
    source_counts = defaultdict(int)
    for record in records:
        action_counts[record["action"]] += 1
        source_counts[record["source_status"]] += 1
    manifest = {
        "version": MANIFEST_VERSION,
        "generated_at": timezone.now().isoformat(),
        "keys_file": str(keys_file),
        "source_file": str(source_file) if source_file else None,
        "summary": {
            "input_rows": len(key_rows),
            "matched_deaths": len(records),
            "unmatched_inputs": len(unmatched),
            "ambiguous_inputs": len(ambiguous_identifiers),
            "changed_deaths": sum(
                bool(record["proposed_values"]) for record in records
            ),
            "actions": dict(sorted(action_counts.items())),
            "source_matches": dict(sorted(source_counts.items())),
        },
        "unmatched_inputs": unmatched,
        "ambiguous_inputs": ambiguous_identifiers,
        "records": records,
    }

    output = Path(manifest_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def _load_manifest(path):
    try:
        manifest = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CommandError(f"Could not read manifest: {exc}") from exc
    if manifest.get("version") != MANIFEST_VERSION:
        raise CommandError(
            f"Unsupported manifest version: {manifest.get('version')!r}."
        )
    if not isinstance(manifest.get("records"), list):
        raise CommandError("Manifest is missing its records list.")
    return manifest


def _apply_manifest(manifest):
    record_plans = manifest["records"]
    ids = [record.get("identity", {}).get("id") for record in record_plans]
    if any(not isinstance(object_id, int) for object_id in ids):
        raise CommandError("Manifest contains an invalid Death id.")

    with transaction.atomic():
        deaths = {
            death.id: death
            for death in Death.objects.select_for_update().filter(id__in=ids)
        }
        updates = []
        update_fields = set()
        already_applied = 0

        for plan in record_plans:
            identity = plan["identity"]
            death = deaths.get(identity["id"])
            if death is None:
                raise CommandError(f"Death id={identity['id']} no longer exists.")

            protected = plan.get("protected_values", {})
            if set(protected) != PROTECTED_FIELDS:
                raise CommandError(
                    f"Manifest has an invalid protected-field set for Death "
                    f"id={death.id}."
                )
            for field, expected in protected.items():
                if getattr(death, field) != expected:
                    raise CommandError(
                        f"Protected field {field} changed for Death id={death.id}; "
                        "generate and review a new manifest."
                    )

            original = plan.get("original_values", {})
            proposed = plan.get("proposed_values", {})
            if set(original) != set(proposed):
                raise CommandError(f"Manifest field mismatch for Death id={death.id}.")
            invalid_fields = set(proposed) - REPAIRABLE_FIELDS
            if invalid_fields:
                raise CommandError(
                    f"Manifest attempts to update protected/unknown field(s): "
                    f"{', '.join(sorted(invalid_fields))}."
                )

            changed = False
            for field, new_value in proposed.items():
                current = getattr(death, field)
                if current == new_value:
                    continue
                if current != original[field]:
                    raise CommandError(
                        f"Field {field} changed for Death id={death.id}; generate "
                        "and review a new manifest."
                    )
                setattr(death, field, new_value)
                update_fields.add(field)
                changed = True
            if changed:
                updates.append(death)
            elif proposed:
                already_applied += 1

        if updates:
            bulk_update_with_history(
                updates,
                Death,
                fields=sorted(update_fields),
                batch_size=500,
                default_change_reason="Death nan/source reconciliation",
            )
    return len(updates), already_applied


class Command(BaseCommand):
    help = "Audit or apply a source-aware repair of Death records containing nan"

    def add_arguments(self, parser):
        parser.add_argument(
            "--keys-file",
            help="CSV containing id, key, and/or death_id values to audit.",
        )
        parser.add_argument(
            "--source-file",
            help="Optional raw ODK Death CSV used to restore form-owned fields.",
        )
        parser.add_argument(
            "--manifest",
            required=True,
            help="Manifest path to write in audit mode or read in apply mode.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply a previously generated and reviewed manifest.",
        )

    def handle(self, *args, **options):
        if options["apply"]:
            if options.get("keys_file") or options.get("source_file"):
                raise CommandError(
                    "--apply accepts only --manifest; generate a new manifest to "
                    "change the audit inputs."
                )
            manifest = _load_manifest(options["manifest"])
            updated, already_applied = _apply_manifest(manifest)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Reconciled {updated} Death record(s); "
                    f"{already_applied} already applied."
                )
            )
            return

        if not options.get("keys_file"):
            raise CommandError("Audit mode requires --keys-file.")
        manifest = _write_manifest(
            keys_file=options["keys_file"],
            source_file=options.get("source_file"),
            manifest_path=options["manifest"],
        )
        summary = manifest["summary"]
        self.stdout.write(
            self.style.WARNING(
                "AUDIT ONLY: wrote manifest for "
                f"{summary['matched_deaths']} Death record(s); "
                f"{summary['changed_deaths']} would change, "
                f"{summary['unmatched_inputs']} input row(s) were unmatched."
            )
        )
