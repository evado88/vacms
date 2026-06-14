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

        # Standardise KEY and PARENT_KEY column names
        if "PARENT_KEY" in df.columns:
            df.rename(columns={"PARENT_KEY": "parent_key"}, inplace=True)
        if "KEY" in df.columns:
            df.rename(columns={"KEY": "key"}, inplace=True)

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
            odk_map.setdefault(field, {})[value] = choice.label

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

        # Preserve link columns before column normalization strips them
        parent_keys = df["parent_key"].copy()
        member_keys = (
            df["key"].copy()
            if "key" in df.columns
            else pd.Series([None] * len(df), index=df.index)
        )

        # Normalize remaining columns to model field names
        df = normalize_dataframe_columns(df, HouseholdMember)

        # Restore link columns (normalization may rename or drop them)
        df["parent_key"] = parent_keys
        df["key"] = member_keys

        # Deduplicate: skip members whose key is already in the database
        existing_keys: set = set()
        csv_keys = [
            str(k).strip() for k in member_keys if not pd.isnull(k) and str(k).strip()
        ]
        if csv_keys:
            existing_keys = set(
                HouseholdMember.objects.filter(key__in=csv_keys).values_list(
                    "key", flat=True
                )
            )

        # Build household lookup keyed by trimmed key
        hh_map = {str(h.key).strip(): h for h in Household.objects.all()}

        objects = []
        skipped_rows = []
        skip_reasons = {
            "blank parent_key": 0,
            "household key not found": 0,
            "member key already exists": 0,
        }

        for _, row in df.iterrows():
            # Dedup by member key when one is present
            member_key = row.get("key")
            if not pd.isnull(member_key) and str(member_key).strip():
                member_key_str = str(member_key).strip()
                if member_key_str in existing_keys:
                    skip_reasons["member key already exists"] += 1
                    skipped_rows.append(row)
                    continue

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

            data = row.drop(["parent_key"]).to_dict()
            # Ensure key field gets the normalised value
            if "key" in data and not pd.isnull(data.get("key")):
                data["key"] = str(data["key"]).strip() or None
            else:
                data.pop("key", None)

            obj = HouseholdMember(**data)
            obj.household = household
            objects.append(obj)

        HouseholdMember.objects.bulk_create(objects, batch_size=2000)

        total_rows = len(df)
        imported = len(objects)
        skipped = len(skipped_rows)

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
