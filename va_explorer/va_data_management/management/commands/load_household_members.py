import argparse
import os
from datetime import datetime

import pandas as pd
from django.core.management.base import BaseCommand, CommandError

from va_explorer.va_data_management.models import (
    Household,
    HouseholdMember,
    ODKFormChoice,
)
from va_explorer.va_data_management.utils.loading import (
    normalize_dataframe_columns,
    normalize_string,
    normalize_value,
)


class Command(BaseCommand):
    """Load household members CSV and log skipped rows to a file."""

    help = "Load household members CSV data and log skipped rows."

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=argparse.FileType("r"))
        parser.add_argument(
            "--log_dir",
            type=str,
            default="logs",
            help="Directory to store failed rows (default: logs/)",
        )

    def handle(self, *args, **options):
        form_name = "household"
        csv_file = options["csv_file"]
        log_dir = options["log_dir"]
        norm_form_name = normalize_string(form_name)

        if not ODKFormChoice.objects.filter(form_name=norm_form_name).exists():
            raise CommandError("Definition for form 'household' has not been loaded")

        df = pd.read_csv(csv_file)

        # Rename and check parent_key column
        if "PARENT_KEY" in df.columns:
            df.rename(columns={"PARENT_KEY": "parent_key"}, inplace=True)

        if "parent_key" not in df.columns:
            raise CommandError(
                "CSV must contain a 'parent_key' column to relate to Household"
            )

        # Map ODK values to labels
        odk_choices = ODKFormChoice.objects.filter(form_name=norm_form_name)
        odk_map = {}
        for choice in odk_choices:
            field = normalize_string(choice.field_name)
            value = normalize_value(choice.value)
            if field not in odk_map:
                odk_map[field] = {}
            odk_map[field][value] = choice.label

        odk_map_columns = [
            "HH_04",
            "HH_05",
            "HH_06",
            "HH_09",
            "HH_11",
            "HH_12",
            "HH_13",
            "HH_14",
        ]

        def apply_odk_map(col, val):
            v_norm = normalize_value(val)
            return odk_map.get(col, {}).get(v_norm, v_norm)

        for col in odk_map_columns:
            if col in df.columns:
                df[col] = df[col].apply(lambda v, c=col: apply_odk_map(c, v))

        # Backup parent_key
        parent_keys = df["parent_key"].copy()

        # Normalize model-compatible columns
        df = normalize_dataframe_columns(df, HouseholdMember)

        # Restore parent_key
        df["parent_key"] = parent_keys

        # Build household map
        hh_map = {h.key: h for h in Household.objects.all()}
        objects = []
        skipped_rows = []

        for i, row in df.iterrows():
            parent_key = row.get("parent_key")
            if parent_key not in hh_map:
                self.stderr.write(
                    self.style.WARNING(
                        f"Skipping row {i}: household key '{parent_key}' not found"
                    )
                )
                skipped_rows.append(row)
                continue

            household = hh_map[parent_key]
            data = row.drop("parent_key").to_dict()
            obj = HouseholdMember(**data)
            obj.household = household
            objects.append(obj)

        HouseholdMember.objects.bulk_create(objects)

        self.stdout.write(
            self.style.SUCCESS(f"Imported {len(objects)} household members.")
        )
        self.stdout.write(self.style.WARNING(f"Skipped {len(skipped_rows)} row(s)."))

        # Save skipped rows to file
        if skipped_rows:
            os.makedirs(log_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = os.path.join(
                log_dir, f"skipped_household_members_{timestamp}.csv"
            )
            pd.DataFrame(skipped_rows).to_csv(log_file, index=False)
            self.stdout.write(self.style.WARNING(f"Logged skipped rows to {log_file}"))
