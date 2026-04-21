import argparse

import pandas as pd
from django.core.management.base import BaseCommand, CommandError

from va_explorer.va_data_management.models import ODKFormChoice, Pregnancy
from va_explorer.va_data_management.utils.loading import (
    load_odk_csv_to_model,
    normalize_dataframe_columns,
    normalize_string,
)


class Command(BaseCommand):
    """Load a pregnancy CSV using the previously loaded ODK definition."""

    help = "Load pregnancy CSV data"

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=argparse.FileType("r"))

    def handle(self, *args, **options):
        form_name = "pregnancy"
        csv_file = options["csv_file"]

        norm_form_name = normalize_string(form_name)

        if not ODKFormChoice.objects.filter(form_name=norm_form_name).exists():
            raise CommandError("Definition for form 'pregnancy' has not been loaded")

        df = pd.read_csv(csv_file)

        # Normalize columns to model fields (keeps 'key' once added to model)
        df = normalize_dataframe_columns(df, Pregnancy)

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
            Pregnancy.objects.values_list("key", flat=True).exclude(key__isnull=True)
        )
        before_existing_filter = len(df)
        df = df[~df["key"].astype(str).isin(existing_keys)]
        skipped_existing = before_existing_filter - len(df)

        # Map ODK code values -> labels
        odk_map_columns = [
            "province",
            "district",
            "constituency",
            "ward",
            "ea",
            "enumerator",
            "consent",
            "PE_03",
            "PE_08",
            "PE_09",
            "PE_10",
            "PE_11",
            "PE_12",
            "PE_12A",
            "PE_13",
            "PE_14",
            "PE_15",
            "PE_16",
            "PE_17",
            "PE_18",
            "PE_23",
            "PE_24",
        ]
        odk_map_columns = [c.strip() for c in odk_map_columns]

        objects = load_odk_csv_to_model(
            df=df,
            model=Pregnancy,
            odk_choices_queryset=ODKFormChoice.objects.filter(form_name=norm_form_name),
            odk_map_columns=odk_map_columns,
            verbose=True,  # Set to False to reduce output
        )

        # Bulk create (DB has unique index on key for safety)
        created = 0
        if objects:
            # ignore_conflicts handles any race conditions (same key inserted by another process)
            Pregnancy.objects.bulk_create(objects, ignore_conflicts=True)
            created = len(objects)

        self.stdout.write(
            f"Imported {created} pregnancy records "
            f"(skipped {skipped_existing} existing, {intrafile_dupes} intra-file duplicates, "
            f"dropped {dropped_blank} blank-key rows)."
        )
