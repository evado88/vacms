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
        "PregnancyOutcome via PARENT_KEY -> PregnancyOutcome.key."
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

        # Normalize parent-child linkage columns from ODK exports.
        if "PARENT_KEY" in df.columns:
            df = df.rename(columns={"PARENT_KEY": "parent_key"})
        if "KEY" in df.columns:
            df = df.rename(columns={"KEY": "row_key"})

        if "parent_key" not in df.columns:
            raise CommandError(
                "CSV must contain 'PARENT_KEY' (or normalized 'parent_key') to link baby rows."
            )

        # Standardize parent keys and drop blank links.
        df["parent_key"] = df["parent_key"].astype(str).str.strip()
        before_blank_parent = len(df)
        df = df[df["parent_key"].notna() & (df["parent_key"] != "")]
        dropped_blank_parent = before_blank_parent - len(df)

        # Intra-file de-duplication by row KEY if present.
        intrafile_dupes = 0
        if "row_key" in df.columns:
            before_dupes = len(df)
            df["row_key"] = df["row_key"].astype(str).str.strip()
            df = df[df["row_key"].notna() & (df["row_key"] != "")]
            df = df.sort_values("row_key").drop_duplicates(
                subset=["row_key"], keep="last"
            )
            intrafile_dupes = before_dupes - len(df)

        # Map ODK choice values to labels for baby fields.
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

        parent_keys = df["parent_key"].copy()

        # Keep only model fields; restore parent_key for relation handling.
        df = normalize_dataframe_columns(df, PregnancyOutcomeBaby)
        df["parent_key"] = parent_keys

        outcome_map = {
            outcome.key: outcome
            for outcome in PregnancyOutcome.objects.exclude(key__isnull=True).only(
                "key"
            )
        }

        objects = []
        skipped_rows = []

        for idx, row in df.iterrows():
            parent_key = (row.get("parent_key") or "").strip()
            pregnancy_outcome = outcome_map.get(parent_key)
            if not pregnancy_outcome:
                row_data = row.to_dict()
                row_data["skip_reason"] = f"parent key '{parent_key}' not found"
                row_data["csv_row_index"] = idx
                skipped_rows.append(row_data)
                continue

            data = row.drop("parent_key").to_dict()
            obj = PregnancyOutcomeBaby(**data)
            obj.pregnancy_outcome = pregnancy_outcome
            objects.append(obj)

        created = 0
        if objects:
            PregnancyOutcomeBaby.objects.bulk_create(objects)
            created = len(objects)

        self.stdout.write(
            f"Imported {created} pregnancy outcome baby detail records "
            f"(skipped {len(skipped_rows)} missing-parent rows, dropped {dropped_blank_parent} blank-parent rows, "
            f"{intrafile_dupes} intra-file duplicates by KEY)."
        )

        if skipped_rows:
            os.makedirs(log_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = os.path.join(
                log_dir, f"skipped_pregnancy_outcome_baby_details_{timestamp}.csv"
            )
            pd.DataFrame(skipped_rows).to_csv(log_file, index=False)
            self.stdout.write(self.style.WARNING(f"Logged skipped rows to {log_file}"))
