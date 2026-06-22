import argparse
import os
from datetime import datetime

import pandas as pd
from django.core.management.base import BaseCommand, CommandError

from va_explorer.va_data_management.models import (
    ODKFormChoice,
    PregnancyOutcome,
    PregnancyOutcomeBaby,
)
from va_explorer.va_data_management.utils.loading import (
    normalize_dataframe_columns,
    normalize_string,
    normalize_value,
)


class Command(BaseCommand):
    help = (
        "Load pregnancy outcome baby-details CSV and link each row to an existing "
        "PregnancyOutcome via PARENT_KEY → PregnancyOutcome.key."
    )

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=argparse.FileType("r"))
        parser.add_argument(
            "--log_dir",
            type=str,
            default="logs",
            help="Directory to store skipped rows (default: logs/)",
        )

    def handle(self, *args, **options):
        form_name = "pregnancy_outcome"
        csv_file = options["csv_file"]
        log_dir = options["log_dir"]

        norm_form_name = normalize_string(form_name)
        odk_choices = ODKFormChoice.objects.filter(form_name=norm_form_name)
        if not odk_choices.exists():
            raise CommandError(
                "ODK form definition for 'pregnancy_outcome' not loaded."
            )

        df = pd.read_csv(csv_file, dtype=str)

        # Standardise KEY and PARENT_KEY column names
        if "PARENT_KEY" in df.columns:
            df = df.rename(columns={"PARENT_KEY": "parent_key"})
        if "KEY" in df.columns:
            df = df.rename(columns={"KEY": "key"})

        if "parent_key" not in df.columns:
            raise CommandError(
                "CSV must contain 'PARENT_KEY' to link baby rows to a PregnancyOutcome."
            )

        # Drop rows with blank parent links up-front
        df["parent_key"] = df["parent_key"].astype(str).str.strip()
        before_blank_parent = len(df)
        df = df[
            df["parent_key"].notna()
            & (df["parent_key"] != "")
            & (df["parent_key"] != "nan")
        ]
        dropped_blank_parent = before_blank_parent - len(df)

        # Intra-file de-duplication by KEY
        intrafile_dupes = 0
        if "key" in df.columns:
            df["key"] = df["key"].astype(str).str.strip()
            before_dupes = len(df)
            df = df[df["key"].notna() & (df["key"] != "") & (df["key"] != "nan")]
            df = df.sort_values("key").drop_duplicates(subset=["key"], keep="last")
            intrafile_dupes = before_dupes - len(df)

        # Map ODK choice values to human-readable labels
        odk_map = {}
        for choice in odk_choices:
            field = normalize_string(choice.field_name)
            value = normalize_value(choice.value)
            odk_map.setdefault(field, {})[value] = choice.label

        odk_map_columns = ["PO_49C", "PO_49E", "PO_50"]

        def apply_odk_map(col, val):
            v_norm = normalize_value(val)
            return odk_map.get(col, {}).get(v_norm, val)

        for col in odk_map_columns:
            if col in df.columns:
                df[col] = df[col].apply(lambda v, c=col: apply_odk_map(c, v))

        # Preserve link columns before normalization strips them
        parent_keys = df["parent_key"].copy()
        baby_keys = (
            df["key"].copy()
            if "key" in df.columns
            else pd.Series([None] * len(df), index=df.index)
        )

        df = normalize_dataframe_columns(df, PregnancyOutcomeBaby)

        # Restore link columns
        df["parent_key"] = parent_keys
        df["key"] = baby_keys

        # DB-level deduplication: skip babies already imported
        existing_keys: set = set()
        csv_keys = [
            str(k).strip()
            for k in baby_keys
            if not pd.isnull(k) and str(k).strip() not in ("", "nan")
        ]
        if csv_keys:
            existing_keys = set(
                PregnancyOutcomeBaby.objects.filter(key__in=csv_keys).values_list(
                    "key", flat=True
                )
            )

        # Build parent outcome lookup
        outcome_map = {
            outcome.key: outcome
            for outcome in PregnancyOutcome.objects.exclude(key__isnull=True).only(
                "key"
            )
        }

        objects = []
        skipped_rows = []
        skip_reasons = {
            "blank parent_key": dropped_blank_parent,
            "parent key not found": 0,
            "baby key already exists": 0,
        }

        for _, row in df.iterrows():
            # DB-level dedup by key
            baby_key = row.get("key")
            baby_key_str = str(baby_key).strip()
            if (
                not pd.isnull(baby_key)
                and baby_key_str not in ("", "nan")
                and baby_key_str in existing_keys
            ):
                skip_reasons["baby key already exists"] += 1
                skipped_rows.append(row)
                continue

            parent_key = (row.get("parent_key") or "").strip()
            pregnancy_outcome = outcome_map.get(parent_key)
            if not pregnancy_outcome:
                skip_reasons["parent key not found"] += 1
                skipped_rows.append(row)
                continue

            data = row.drop(["parent_key"]).to_dict()
            if "key" in data and not pd.isnull(data.get("key")):
                data["key"] = str(data["key"]).strip() or None
            else:
                data.pop("key", None)

            obj = PregnancyOutcomeBaby(**data)
            obj.pregnancy_outcome = pregnancy_outcome
            objects.append(obj)

        if objects:
            PregnancyOutcomeBaby.objects.bulk_create(objects, batch_size=2000)

        imported = len(objects)
        skipped = len(skipped_rows)
        total_rows = len(df) + dropped_blank_parent

        log_file = None
        if skipped_rows:
            os.makedirs(log_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = os.path.join(
                log_dir, f"skipped_pregnancy_outcome_baby_details_{timestamp}.csv"
            )
            pd.DataFrame(skipped_rows).to_csv(log_file, index=False)

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {imported} of {total_rows} baby detail record(s) "
                f"({intrafile_dupes} intra-file duplicate(s) removed)."
            )
        )
        if dropped_blank_parent or skipped:
            self.stdout.write(self.style.WARNING("Skipped:"))
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
                    "No baby detail records were imported — every row was skipped. "
                    "Check that the matching PregnancyOutcome records are loaded and that "
                    "the CSV's PARENT_KEY values match PregnancyOutcome.key."
                )
            )
        elif total_rows and (skipped + dropped_blank_parent) / total_rows > 0.5:
            self.stderr.write(
                self.style.ERROR(
                    f"More than half the rows were skipped ({skipped + dropped_blank_parent}/{total_rows}). "
                    "This usually means the CSV's PARENT_KEY values do not match "
                    "the loaded PregnancyOutcome records — review the skipped-rows log."
                )
            )
