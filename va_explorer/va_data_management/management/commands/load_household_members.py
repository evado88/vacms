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

        # Build household map, keyed by a trimmed key so trailing whitespace or
        # quoting differences in the CSV's parent_key do not cause silent skips.
        hh_map = {str(h.key).strip(): h for h in Household.objects.all()}
        objects = []
        skipped_rows = []
        skip_reasons = {"blank parent_key": 0, "household key not found": 0}

        for _, row in df.iterrows():
            parent_key = row.get("parent_key")
            if pd.isnull(parent_key) or not str(parent_key).strip():
                skip_reasons["blank parent_key"] += 1
                skipped_rows.append(row)
                continue

            household = hh_map.get(str(parent_key).strip())
            if household is None:
                skip_reasons["household key not found"] += 1
                skipped_rows.append(row)
                continue

            data = row.drop("parent_key").to_dict()
            obj = HouseholdMember(**data)
            obj.household = household
            objects.append(obj)

        HouseholdMember.objects.bulk_create(objects, batch_size=2000)

        total_rows = len(df)
        imported = len(objects)
        skipped = len(skipped_rows)

        # Save skipped rows before reporting so the summary can point at the
        # log that explains every skip.
        log_file = None
        if skipped_rows:
            os.makedirs(log_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = os.path.join(
                log_dir, f"skipped_household_members_{timestamp}.csv"
            )
            pd.DataFrame(skipped_rows).to_csv(log_file, index=False)

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {imported} of {total_rows} household members."
            )
        )
        if skipped:
            self.stdout.write(self.style.WARNING(f"Skipped {skipped} row(s):"))
            for reason, count in skip_reasons.items():
                if count:
                    self.stdout.write(self.style.WARNING(f"  - {reason}: {count}"))
            if log_file:
                self.stdout.write(
                    self.style.WARNING(f"Logged skipped rows to {log_file}")
                )

        # Make a failed or mostly-failed run impossible to mistake for success.
        if imported == 0:
            self.stderr.write(
                self.style.ERROR(
                    "No household members were imported — every row was skipped. "
                    "Check that the matching Household records are loaded and that "
                    "the CSV's parent_key values match Household.key."
                )
            )
        elif total_rows and skipped / total_rows > 0.5:
            self.stderr.write(
                self.style.ERROR(
                    f"More than half the rows were skipped ({skipped}/{total_rows}). "
                    "This usually means the CSV's parent_key values do not match "
                    "the loaded Household records — review the skipped-rows log."
                )
            )
