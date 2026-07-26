"""Backfill VASA ``location`` / ``cluster`` FKs from their coded geography.

VASA records arrive with geography as raw census codes and no location FK, so
they drop out of every location-scoped query (exports, ``location_restrictions``,
dashboards). The loader now resolves geography on ingest; this command applies
the same resolution to records already in the database (and is safe to re-run,
e.g. after loading/refreshing the ``SRSEAPopulationTarget`` reference data or the
``SRSClusterLocation`` tree).

Usage::

    python manage.py resolve_vasa_locations            # resolve + report
    python manage.py resolve_vasa_locations --dry-run   # report only, write nothing
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from va_explorer.va_data_management.models import VASA
from va_explorer.va_data_management.utils.vasa_geocoding import VasaGeocoder, geo_codes
from va_explorer.va_data_management.utils.vasa_loading import (
    _GEO_KEY_CHECKS,
    _flag_geo_issue,
    _resolve_stale_loader_issues,
)

_CHUNK_SIZE = 500


class Command(BaseCommand):
    help = (
        "Resolve VASA location/cluster FKs from coded geography so existing "
        "records participate in location scoping."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report would-be changes; write nothing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        geocoder = VasaGeocoder()

        summary = {
            "total": 0,
            "changed": 0,
            "location_resolved": 0,
            "cluster_resolved": 0,
            "unresolved": 0,
        }
        pending = []
        flagged_checks = {}

        # all_objects: soft-deleted records still need correct scoping if restored.
        for vasa in VASA.all_objects.all().iterator(chunk_size=_CHUNK_SIZE):
            summary["total"] += 1
            geo = geocoder.resolve(geo_codes(vasa))
            if geo.location_id is not None:
                summary["location_resolved"] += 1
            if geo.cluster_id is not None:
                summary["cluster_resolved"] += 1
            if geo.unresolved_level is not None:
                summary["unresolved"] += 1

            if vasa.location_id != geo.location_id or vasa.cluster_id != geo.cluster_id:
                summary["changed"] += 1
                vasa.location_id = geo.location_id
                vasa.cluster_id = geo.cluster_id
                pending.append(vasa)

            if not dry_run:
                flagged_checks[vasa.pk] = (
                    {"geography_unresolved"} if geo.unresolved_level else set()
                )
                if geo.unresolved_level is not None:
                    _flag_geo_issue(vasa, vasa.instanceid, geo)

            if not dry_run and len(pending) >= _CHUNK_SIZE:
                self._flush(pending)
                pending = []

        if not dry_run:
            self._flush(pending)
            try:
                _resolve_stale_loader_issues(flagged_checks, checks=_GEO_KEY_CHECKS)
            except Exception:
                self.stderr.write("Failed to auto-resolve stale VASA geography issues")

        mode = " (dry-run)" if dry_run else ""
        self.stdout.write(
            f"VASA geography backfill{mode}: {summary['total']} records, "
            f"{summary['changed']} changed, "
            f"{summary['location_resolved']} location-resolved, "
            f"{summary['cluster_resolved']} cluster-resolved, "
            f"{summary['unresolved']} unresolved."
        )

    @staticmethod
    def _flush(pending):
        if pending:
            with transaction.atomic():
                VASA.all_objects.bulk_update(pending, ["location_id", "cluster_id"])
