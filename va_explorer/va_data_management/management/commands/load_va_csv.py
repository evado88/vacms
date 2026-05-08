import argparse

import pandas as pd
from django.core.management.base import BaseCommand

from va_explorer.va_data_management.utils.loading import load_records_from_dataframe


class Command(BaseCommand):
    help = "Loads a verbal autopsy CSV file into the database"

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=argparse.FileType("r"))
        parser.add_argument("--random_locations", type=str, nargs="?", default=False)
        parser.add_argument(
            "--upsert",
            action="store_true",
            help="Also update GPS on records that already exist (matched by instanceid).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute the load/upsert plan and print the summary without writing.",
        )

    def _write_upsert_summary(self, stats, dry_run):
        prefix = "[DRY RUN] " if dry_run else ""
        verb = "would update" if dry_run else "updated"
        self.stdout.write(
            f"{prefix}Upsert: {verb} {stats['updated']} existing GPS values "
            f"({stats['skipped_no_change']} unchanged, "
            f"{stats['skipped_no_value']} incoming had no GPS value)."
        )
        if dry_run and stats["sample"]:
            self.stdout.write("Sample of changes that would be applied:")
            for item in stats["sample"]:
                existing = item["existing"] or "(empty)"
                self.stdout.write(
                    f"  key={item['key']}  {existing}  ->  {item['incoming']}"
                )

    def handle(self, *args, **options):
        csv_data = pd.read_csv(options["csv_file"], low_memory=False)
        random_locations = options.get("random_locations", False)
        upsert = options["upsert"]
        dry_run = options["dry_run"]

        results = load_records_from_dataframe(
            csv_data,
            random_locations,
            upsert_gps=upsert,
            dry_run=dry_run,
        )

        num_created = len(results["created"])
        num_ignored = len(results["ignored"])
        num_outdated = len(results["outdated"])
        num_enriched = len(results["enriched"])
        created_community = sum(
            1 for va in results["created"] if va.community_va_normalized == "yes"
        )
        created_facility = sum(
            1 for va in results["created"] if va.community_va_normalized == "no"
        )
        created_unknown = num_created - created_community - created_facility
        created_with_cluster = sum(
            1
            for va in results["created"]
            if va.community_va_normalized == "yes" and va.cluster_id is not None
        )
        created_with_location = sum(
            1
            for va in results["created"]
            if va.community_va_normalized != "yes" and va.location_id is not None
        )

        prefix = "[DRY RUN] " if dry_run else ""
        action = "Would load" if dry_run else "Loaded"
        outdated_action = (
            "would remove as outdated" if dry_run else "removed as outdated"
        )
        self.stdout.write(
            f"{prefix}{action} {num_created} verbal autopsies from CSV "
            f"({num_ignored} ignored, {num_enriched} enriched, "
            f"{num_outdated} {outdated_action})"
        )
        self.stdout.write(
            "Created mix: "
            f"community={created_community} (with cluster={created_with_cluster}), "
            f"facility={created_facility} (with location={created_with_location}), "
            f"unknown={created_unknown}"
        )
        if upsert:
            self._write_upsert_summary(results["gps_upsert"], dry_run)
