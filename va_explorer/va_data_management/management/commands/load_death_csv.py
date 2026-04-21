import argparse

import pandas as pd
from django.core.management.base import BaseCommand, CommandError

from va_explorer.va_data_management.models import Death, ODKFormChoice
from va_explorer.va_data_management.utils.loading import (
    load_odk_csv_to_model,
    normalize_dataframe_columns,
    normalize_string,
)


class Command(BaseCommand):
    """Load a death CSV using the previously loaded ODK definition."""

    help = "Load death CSV data"

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=argparse.FileType("r"))

    def handle(self, *args, **options):
        form_name = "death"
        csv_file = options["csv_file"]

        norm_form_name = normalize_string(form_name)

        if not ODKFormChoice.objects.filter(form_name=norm_form_name).exists():
            raise CommandError("Definition for form 'death' has not been loaded")

        df = pd.read_csv(csv_file)
        df = normalize_dataframe_columns(df, Death)

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
            Death.objects.values_list("key", flat=True).exclude(key__isnull=True)
        )
        before_existing_filter = len(df)
        df = df[~df["key"].astype(str).isin(existing_keys)]
        skipped_existing = before_existing_filter - len(df)

        odk_map_columns = [
            "province",
            "district",
            "constituency",
            "ward",
            "ea",
            "supervisor",
            "enumerator",
            "consent",
            "DE_05",
            "DE_07",
            "DE_09",
            "DE_11",
            "DE_13",
            "DE_16",
            "DE_17",
            "DE_18",
            "DE_21",
            "DE_24",
            "DE_30",
            "DE_31",
        ]
        odk_map_columns = [c.strip() for c in odk_map_columns]

        objects = load_odk_csv_to_model(
            df=df,
            model=Death,
            odk_choices_queryset=ODKFormChoice.objects.filter(form_name=norm_form_name),
            odk_map_columns=odk_map_columns,
            verbose=True,  # Set to False to reduce output
        )
        # Bulk create (DB has unique index on key for safety)
        created = 0
        if objects:
            # ignore_conflicts handles any race conditions (same key inserted by another process)
            Death.objects.bulk_create(objects, ignore_conflicts=True)
            created = len(objects)

        self.stdout.write(
            f"Imported {created} death records "
            f"(skipped {skipped_existing} existing, {intrafile_dupes} intra-file duplicates, "
            f"dropped {dropped_blank} blank-key rows)."
        )
