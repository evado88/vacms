"""Re-decode VASA canonical answers from stored raw-code provenance.

After a crosswalk change (``generate_vasa_mapping`` regenerated from a revised
XLSForm), replay the value decode over each record's ``raw_answers`` and adopt
the new WHO-canonical values in place on the canonical ``Idxxxxx`` fields.

Scope: plain crosswalked select fields only. Combined-field routing
(``Q1202``/``N2005``) and admin/location columns are loader concerns and are
not replayed — a re-import repairs those. Unknown-code / fallback decodes are
(re-)flagged as ``DataQualityIssue``s exactly like the loader, and issues that
no longer reproduce are auto-resolved.

Usage::

    python manage.py redecode_vasa            # replay + report
    python manage.py redecode_vasa --dry-run  # report only, write nothing
"""

import json

from django.core.management.base import BaseCommand
from django.db import transaction

from va_explorer.va_data_management.models import VASA
from va_explorer.va_data_management.utils.vasa_loading import (
    _DECODE_KEY_CHECKS,
    VASA_FIELD_NAMES,
    _decode_pseudo_answers,
    _flag_decode_issues,
    _resolve_stale_loader_issues,
)
from va_explorer.va_data_management.vasa.field_map import (
    COMBINED_WHO,
    CROSSWALK_VERSION,
)

_CHUNK_SIZE = 500

# WHO targets owned by the loader's combined-field splitting; never replayed.
_COMBINED_TARGETS = frozenset(
    who for targets in COMBINED_WHO.values() for who in targets
)


class Command(BaseCommand):
    help = (
        "Re-decode VASA canonical answers from raw_answers provenance "
        "(after a crosswalk change)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report would-be changes; write nothing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        summary = {
            "crosswalk_version": CROSSWALK_VERSION,
            "records_scanned": 0,
            "records_changed": 0,
            "fields_changed": 0,
            "unknown_codes": 0,
            "fallback_values": 0,
            "dry_run": dry_run,
        }
        flagged_checks = {}

        queryset = VASA.all_objects.exclude(raw_answers={}).exclude(
            raw_answers__values={}
        )
        for vasa in queryset.iterator(chunk_size=_CHUNK_SIZE):
            summary["records_scanned"] += 1
            raw = (vasa.raw_answers or {}).get("values", {})
            who_values, _sources, _collisions, decode_flags, _raws = (
                _decode_pseudo_answers(raw)
            )
            summary["unknown_codes"] += sum(
                1 for f in decode_flags if f["check"] == "unknown_code"
            )
            summary["fallback_values"] += sum(
                1 for f in decode_flags if f["check"] == "fallback_value"
            )

            changed = []
            for who, value in who_values.items():
                if who in _COMBINED_TARGETS or who not in VASA_FIELD_NAMES:
                    continue
                if str(getattr(vasa, who, "") or "") != str(value):
                    changed.append((who, value))

            stored_version = (
                (vasa.raw_answers or {}).get("meta", {}).get("crosswalk_version")
            )
            if changed:
                summary["records_changed"] += 1
                summary["fields_changed"] += len(changed)
            if dry_run:
                continue

            with transaction.atomic():
                if changed or stored_version != CROSSWALK_VERSION:
                    for who, value in changed:
                        setattr(vasa, who, value)
                    vasa.raw_answers = {
                        **vasa.raw_answers,
                        "meta": {
                            **vasa.raw_answers.get("meta", {}),
                            "crosswalk_version": CROSSWALK_VERSION,
                        },
                    }
                    vasa.save()
                if decode_flags:
                    _flag_decode_issues(vasa, vasa.instanceid, decode_flags)
                flagged_checks[vasa.pk] = {f["check"] for f in decode_flags}

        if not dry_run:
            # Only decode checks: redecode never re-evaluates name/WHO-Id
            # conflicts, so their issues must not be touched here.
            _resolve_stale_loader_issues(flagged_checks, checks=_DECODE_KEY_CHECKS)

        self.stdout.write(json.dumps(summary, indent=2))
