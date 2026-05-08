import argparse

import pandas as pd
from django.core.management.base import BaseCommand, CommandError

from va_explorer.va_data_management.models import (
    ODKFormChoice,
    PregnancyOutcome,
    SRSClusterLocation,
)
from va_explorer.va_data_management.utils.geopoint import merge_geopoint_into
from va_explorer.va_data_management.utils.loading import (
    load_odk_csv_to_model,
    normalize_dataframe_columns,
    normalize_string,
    upsert_gps_for_existing,
)


class Command(BaseCommand):
    help = (
        "Load pregnancy outcome CSV data. Adds 'cluster' column to match "
        "EA name (CSV) to SRSClusterLocation.name and inserts SRSClusterLocation.code "
        "into the 'cluster' column before saving."
    )

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=argparse.FileType("r"))
        parser.add_argument(
            "--log_missing_ea",
            type=str,
            default="missing_ea_name_mappings.csv",
            help="CSV file to log unmatched EA names",
        )
        parser.add_argument(
            "--upsert",
            action="store_true",
            help="Also update GPS on records that already exist (matched by key).",
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
        form_name = "pregnancy_outcome"
        csv_file = options["csv_file"]
        missing_log = options["log_missing_ea"]
        upsert = options["upsert"]
        dry_run = options["dry_run"]

        norm_form_name = normalize_string(form_name)
        odk_choices = ODKFormChoice.objects.filter(form_name=norm_form_name)
        if not odk_choices.exists():
            raise CommandError(
                "ODK form definition for 'pregnancy_outcome' not loaded."
            )

        df = pd.read_csv(csv_file)

        df = merge_geopoint_into(df, source_prefix="gps", target_field="gps")

        df = normalize_dataframe_columns(df, PregnancyOutcome)

        # Ensure `key` exists
        if "key" not in df.columns:
            raise CommandError(
                "The CSV must include a 'key' column to enforce uniqueness."
            )

        # Drop rows with missing/blank keys (cannot dedupe reliably)
        before_nonnull = len(df)
        df = df[df["key"].notna() & (df["key"].astype(str).str.strip() != "")]
        dropped_blank = before_nonnull - len(df)

        # Intra-file de-duplication by key
        before_dupes = len(df)
        df = df.sort_values("key").drop_duplicates(subset=["key"], keep="last")
        intrafile_dupes = before_dupes - len(df)

        # Filter out keys that already exist in DB
        existing_keys = set(
            PregnancyOutcome.objects.values_list("key", flat=True).exclude(
                key__isnull=True
            )
        )
        before_existing_filter = len(df)
        upsert_stats = {
            "updated": 0,
            "skipped_no_change": 0,
            "skipped_no_value": 0,
            "sample": [],
        }
        if upsert:
            existing_df = df[df["key"].astype(str).isin(existing_keys)]
            upsert_stats = upsert_gps_for_existing(
                model=PregnancyOutcome,
                df=existing_df,
                key_field="key",
                gps_field="gps",
                dry_run=dry_run,
            )
        df = df[~df["key"].astype(str).isin(existing_keys)]
        skipped_existing = before_existing_filter - len(df)

        # Map EA → SRSClusterLocation.name → code
        ea_to_code_map = {
            loc.name.strip(): loc.code
            for loc in SRSClusterLocation.objects.exclude(name__isnull=True).exclude(
                code__isnull=True
            )
        }

        # Create 'cluster' column in the dataframe
        df["cluster"] = df["ea"].astype(str).str.strip().map(ea_to_code_map)

        # Log rows where EA didn't match
        missing_ea_df = df[df["cluster"].isnull()]
        if not missing_ea_df.empty:
            if not dry_run:
                missing_ea_df[["key", "ea"]].drop_duplicates().to_csv(
                    missing_log, index=False
                )
                log_message = f"Logged to {missing_log}"
            else:
                log_message = f"Would log to {missing_log}"
            self.stdout.write(
                self.style.WARNING(
                    f"{len(missing_ea_df)} records had unmatched EA values. {log_message}"
                )
            )

        odk_map_columns = [
            "province",
            "district",
            "constituency",
            "ward",
            "ea",
            "supervisor",
            "enumerator",
            "consent",
            "PO_07",
            "PO_09",
            "PO_11",
            "PO_11A",
            "PO_15",
            "PO_21",
            "PO_22",
            "PO_26",
            "PO_28",
            "PO_30",
            "PO_31",
            "informant",
            "PO_34",
            "PO_35",
            "PO_37",
            "PO_42",
            "PO_43",
            "PO_44",
            "PO_45",
            "PO_46",
            "PO_47B",
            "PO_48",
            "PO_49C",
            "PO_49E",
            "PO_50",
        ]

        objects = load_odk_csv_to_model(
            df=df,
            model=PregnancyOutcome,
            odk_choices_queryset=odk_choices,
            odk_map_columns=odk_map_columns,
            verbose=True,
        )

        # Bulk create (DB has unique index on key for safety)
        created = 0
        if objects:
            # ignore_conflicts handles any race conditions (same key inserted by another process)
            if not dry_run:
                PregnancyOutcome.objects.bulk_create(objects, ignore_conflicts=True)
            created = len(objects)

        prefix = "[DRY RUN] " if dry_run else ""
        action = "Would import" if dry_run else "Imported"
        self.stdout.write(
            f"{prefix}{action} {created} pregnancy outcomes "
            f"(skipped {skipped_existing} existing, {intrafile_dupes} intra-file duplicates, "
            f"dropped {dropped_blank} blank-key rows)."
        )
        if upsert:
            self._write_upsert_summary(upsert_stats, dry_run)
