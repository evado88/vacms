import argparse
import os
import re

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from va_explorer.va_data_management.models import SRSEAPopulationTarget

# CSV header (upper-cased) -> model field name. The CSV's "s/n" column is a
# spreadsheet serial number and is intentionally not mapped.
COLUMN_MAP = {
    "PROV_NAME": "province_name",
    "PROV_CODE": "province_code",
    "DIST_NAME": "district_name",
    "DIST_CODE": "district_code",
    "CONST_NAME": "constituency_name",
    "CONST_CODE": "constituency_code",
    "WARD_NAME": "ward_name",
    "WARD_CODE": "ward_code",
    "REG": "region_code",
    "URBAN/RURAL": "rural_urban",
    "GEOID": "geoid",
    "POP": "population_target",
    "HH": "household_target",
    "COMMENTS": "comments",
}

REQUIRED_FIELDS = ("geoid", "population_target", "household_target")
GEOID_PATTERN = re.compile(r"^\d{11}$")

# Model fields written by the loader (everything except the surrogate PK and the
# auto-managed `created` timestamp).
TEXT_FIELDS = (
    "province_name",
    "province_code",
    "district_name",
    "district_code",
    "constituency_name",
    "constituency_code",
    "ward_name",
    "ward_code",
    "region_code",
    "rural_urban",
    "comments",
)


class Command(BaseCommand):
    """Load the SRS EA population & household targets CSV into the database."""

    help = "Load SRS EA population & household targets CSV (upsert by GEOID)"

    def add_arguments(self, parser):
        # encoding="utf-8-sig" transparently strips the BOM the source file ships with.
        parser.add_argument(
            "csv_file", type=argparse.FileType("r", encoding="utf-8-sig")
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Batch size for bulk create/update operations (default: 1000)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute the upsert plan and print the summary without writing.",
        )

    @staticmethod
    def _clean_text(value):
        if value is None or pd.isna(value):
            return ""
        return str(value).strip()

    @classmethod
    def _parse_count(cls, value):
        """Return a non-negative int, or None if the value cannot be parsed."""
        text = cls._clean_text(value)
        if not text:
            return None
        try:
            number = int(text)
        except ValueError:
            return None
        return number if number >= 0 else None

    def handle(self, *args, **options):
        csv_file = options["csv_file"]
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]

        if batch_size <= 0:
            raise CommandError("--batch-size must be greater than zero")

        df = pd.read_csv(csv_file, dtype=str, keep_default_na=False)

        # Normalize headers, then remap to model field names; drop unmapped columns.
        df.columns = [str(col).strip().upper() for col in df.columns]
        df = df.rename(columns=COLUMN_MAP)
        df = df.loc[:, ~df.columns.duplicated()]
        df = df[[col for col in df.columns if col in set(COLUMN_MAP.values())]]

        missing = [f for f in REQUIRED_FIELDS if f not in df.columns]
        if missing:
            raise CommandError(
                "CSV is missing required column(s): "
                + ", ".join(sorted(missing))
                + ". Expected headers include GEOID, POP and HH."
            )

        records = df.to_dict(orient="records")
        source_filename = os.path.basename(getattr(csv_file, "name", "") or "")

        skipped_blank_geoid = 0
        skipped_invalid = 0
        malformed_geoids = 0
        cleaned = {}  # geoid -> values dict; later wins (intra-file dedup)

        for record in records:
            geoid = self._clean_text(record.get("geoid"))
            if not geoid:
                skipped_blank_geoid += 1
                continue

            population = self._parse_count(record.get("population_target"))
            household = self._parse_count(record.get("household_target"))
            if population is None or household is None:
                skipped_invalid += 1
                continue

            if not GEOID_PATTERN.match(geoid):
                malformed_geoids += 1

            values = {
                field: self._clean_text(record.get(field)) for field in TEXT_FIELDS
            }
            if values["province_code"]:
                values["province_code"] = values["province_code"].zfill(2)
            values["geoid"] = geoid
            values["population_target"] = population
            values["household_target"] = household
            values["source_filename"] = source_filename

            # Intra-file duplicate GEOIDs: last occurrence wins.
            cleaned[geoid] = values

        intrafile_duplicates = len(records) - skipped_blank_geoid - skipped_invalid
        intrafile_duplicates -= len(cleaned)

        update_fields = [
            *TEXT_FIELDS,
            "population_target",
            "household_target",
            "source_filename",
            "updated",
        ]

        existing_by_geoid = {
            obj.geoid: obj
            for obj in SRSEAPopulationTarget.objects.filter(geoid__in=cleaned.keys())
        }

        to_create = []
        to_update = []
        now = timezone.now()
        for geoid, values in cleaned.items():
            existing = existing_by_geoid.get(geoid)
            if existing:
                for field, value in values.items():
                    setattr(existing, field, value)
                existing.updated = now
                to_update.append(existing)
            else:
                to_create.append(SRSEAPopulationTarget(**values))

        prefix = "[DRY RUN] " if dry_run else ""

        if not dry_run:
            with transaction.atomic():
                if to_create:
                    SRSEAPopulationTarget.objects.bulk_create(
                        to_create, batch_size=batch_size
                    )
                if to_update:
                    SRSEAPopulationTarget.objects.bulk_update(
                        to_update, fields=update_fields, batch_size=batch_size
                    )

        self.stdout.write(
            f"{prefix}SRS EA population targets: "
            f"created={len(to_create)}, updated={len(to_update)}, "
            f"skipped_blank_geoid={skipped_blank_geoid}, "
            f"skipped_invalid={skipped_invalid}, "
            f"intrafile_duplicates={intrafile_duplicates}."
        )
        if malformed_geoids:
            self.stdout.write(
                self.style.WARNING(
                    f"{prefix}Warning: {malformed_geoids} row(s) have a GEOID that is "
                    "not 11 digits; they were imported as-is."
                )
            )
