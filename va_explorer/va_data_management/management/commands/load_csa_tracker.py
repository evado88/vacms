import argparse

import pandas as pd
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from va_explorer.va_data_management.models import CSADailyTracker
from va_explorer.va_data_management.utils.loading import (
    normalize_dataframe_columns,
    read_form_csv,
)


class Command(BaseCommand):
    """Load CSA daily tracker CSV data into CSADailyTracker."""

    help = "Load CSA daily tracker CSV data (upsert by key)"

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=argparse.FileType("r"))
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Batch size for bulk create/update operations (default: 1000)",
        )

    @staticmethod
    def _clean_value(value):
        if pd.isna(value):
            return None
        if isinstance(value, str):
            value = value.strip()
            return value if value != "" else None
        return value

    def handle(self, *args, **options):
        csv_file = options["csv_file"]
        batch_size = options["batch_size"]

        if batch_size <= 0:
            raise CommandError("--batch-size must be greater than zero")

        df = read_form_csv(csv_file)
        df = normalize_dataframe_columns(df, CSADailyTracker)
        df = df.loc[:, ~df.columns.duplicated()]

        if "key" not in df.columns:
            raise CommandError(
                "The CSV must include a 'key' column to enforce uniqueness."
            )

        before_nonnull = len(df)
        df["key"] = df["key"].map(lambda v: v.strip() if isinstance(v, str) else v)
        df = df[df["key"].notna() & (df["key"].astype(str).str.strip() != "")]
        dropped_blank = before_nonnull - len(df)

        before_dupes = len(df)
        df = df.sort_values("key").drop_duplicates(subset=["key"], keep="last")
        intrafile_dupes = before_dupes - len(df)

        if df.empty:
            self.stdout.write(
                "No rows to import after cleaning "
                f"(dropped {dropped_blank} blank-key rows, {intrafile_dupes} intra-file duplicates)."
            )
            return

        model_fields = [f.name for f in CSADailyTracker._meta.fields if f.name != "id"]
        update_fields = [f for f in model_fields if f != "key"]

        records = df.to_dict(orient="records")
        keys = [record["key"] for record in records]

        existing_by_key = {
            obj.key: obj for obj in CSADailyTracker.objects.filter(key__in=keys)
        }

        to_create = []
        to_update = []

        for record in records:
            key = record["key"]
            values = {
                field: self._clean_value(record.get(field)) for field in model_fields
            }

            existing = existing_by_key.get(key)
            if existing:
                for field in update_fields:
                    setattr(existing, field, values.get(field))
                to_update.append(existing)
            else:
                to_create.append(CSADailyTracker(**values))

        with transaction.atomic():
            if to_create:
                CSADailyTracker.objects.bulk_create(to_create, batch_size=batch_size)
            if to_update:
                CSADailyTracker.objects.bulk_update(
                    to_update,
                    fields=update_fields,
                    batch_size=batch_size,
                )

        self.stdout.write(
            "Imported CSA daily tracker records: "
            f"created={len(to_create)}, updated={len(to_update)}, "
            f"dropped_blank_key={dropped_blank}, intrafile_duplicates={intrafile_dupes}."
        )

        # Keep tracker_first_date current so the dashboard "all time" coverage
        # calculation does not need to hit the tracker table on every request.
        call_command("refresh_csa_first_dates")
