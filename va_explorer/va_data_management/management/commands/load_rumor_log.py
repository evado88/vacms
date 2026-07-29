import argparse
import os
from datetime import datetime

import pandas as pd
from django.core.management.base import BaseCommand, CommandError

from va_explorer.va_data_management.models import RumorLog, SocialMobilization
from va_explorer.va_data_management.odk.service import (
    _RUMOR_UPDATE_FIELDS,
    RUMOR_JUNK_DESCRIPTIONS,
    purge_junk_rumor_logs,
)
from va_explorer.va_data_management.utils.loading import (
    normalize_dataframe_columns,
    read_form_csv,
)


class Command(BaseCommand):
    help = (
        "Load the rumors_log_repeat CSV and link each row to an existing "
        "SocialMobilization record via PARENT_KEY → SocialMobilization.key. "
        "Records are upserted by KEY; rows with junk or meaningless rumour "
        "descriptions are skipped automatically. Any previously ingested junk "
        "records are also purged from the database."
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
        csv_file = options["csv_file"]
        log_dir = options["log_dir"]

        df = read_form_csv(csv_file)

        # Standardise KEY and PARENT_KEY column names
        if "PARENT_KEY" in df.columns:
            df = df.rename(columns={"PARENT_KEY": "parent_key"})
        if "KEY" in df.columns:
            df = df.rename(columns={"KEY": "key"})

        if "parent_key" not in df.columns:
            raise CommandError(
                "CSV must contain 'PARENT_KEY' to link rumour rows to a SocialMobilization."
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

        # Intra-file de-duplication by KEY (keep last occurrence for upsert)
        intrafile_dupes = 0
        if "key" in df.columns:
            df["key"] = df["key"].astype(str).str.strip()
            before_dupes = len(df)
            df = df[df["key"].notna() & (df["key"] != "") & (df["key"] != "nan")]
            df = df.sort_values("key").drop_duplicates(subset=["key"], keep="last")
            intrafile_dupes = before_dupes - len(df)

        # Preserve link columns before normalization strips them
        parent_keys = df["parent_key"].copy()
        rumor_keys = (
            df["key"].copy()
            if "key" in df.columns
            else pd.Series([None] * len(df), index=df.index)
        )

        df = normalize_dataframe_columns(df, RumorLog)

        # Restore link columns
        df["parent_key"] = parent_keys
        df["key"] = rumor_keys

        # Build parent SocialMobilization lookup
        sm_map = {
            sm.key: sm
            for sm in SocialMobilization.objects.exclude(key__isnull=True).only("key")
        }

        # Snapshot existing keys to distinguish creates from updates in reporting
        csv_keys = [
            str(k).strip()
            for k in rumor_keys
            if not pd.isnull(k) and str(k).strip() not in ("", "nan")
        ]
        existing_keys = (
            set(RumorLog.objects.filter(key__in=csv_keys).values_list("key", flat=True))
            if csv_keys
            else set()
        )

        objects = []
        skipped_rows = []
        skip_reasons = {
            "blank parent_key": dropped_blank_parent,
            "parent key not found": 0,
            "junk description": 0,
        }

        for _, row in df.iterrows():
            parent_key = (row.get("parent_key") or "").strip()
            social_mobilization = sm_map.get(parent_key)
            if not social_mobilization:
                skip_reasons["parent key not found"] += 1
                skipped_rows.append(row)
                continue

            description = str(row.get("rumor_description") or "").strip()
            if description.lower() in RUMOR_JUNK_DESCRIPTIONS:
                skip_reasons["junk description"] += 1
                skipped_rows.append(row)
                continue

            data = row.drop(["parent_key"]).to_dict()
            if "key" in data and not pd.isnull(data.get("key")):
                data["key"] = str(data["key"]).strip() or None
            else:
                data.pop("key", None)

            obj = RumorLog(**data)
            obj.social_mobilization = social_mobilization
            objects.append(obj)

        if objects:
            RumorLog.objects.bulk_create(
                objects,
                batch_size=2000,
                update_conflicts=True,
                unique_fields=["key"],
                update_fields=_RUMOR_UPDATE_FIELDS,
            )

        created = sum(1 for obj in objects if (obj.key or "") not in existing_keys)
        updated = len(objects) - created
        skipped = len(skipped_rows)
        total_rows = len(df) + dropped_blank_parent

        log_file = None
        if skipped_rows:
            os.makedirs(log_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = os.path.join(log_dir, f"skipped_rumor_log_{timestamp}.csv")
            pd.DataFrame(skipped_rows).to_csv(log_file, index=False)

        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {created} new and updated {updated} existing rumour log "
                f"record(s) from {total_rows} total row(s) "
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

        # Purge any previously ingested records with junk descriptions
        purged = purge_junk_rumor_logs()
        if purged:
            self.stdout.write(
                self.style.WARNING(
                    f"Purged {purged} previously ingested junk rumour record(s) from the database."
                )
            )

        if created + updated == 0 and purged == 0:
            self.stderr.write(
                self.style.ERROR(
                    "No rumour log records were loaded — every row was skipped. "
                    "Check that the matching SocialMobilization records are loaded "
                    "and that the CSV's PARENT_KEY values match SocialMobilization.key."
                )
            )
        elif total_rows and skipped / total_rows > 0.5:
            self.stderr.write(
                self.style.ERROR(
                    f"More than half the rows were skipped ({skipped}/{total_rows}). "
                    "This usually means the CSV's PARENT_KEY values do not match "
                    "the loaded SocialMobilization records, or most entries have junk "
                    "descriptions — review the skipped-rows log."
                )
            )
