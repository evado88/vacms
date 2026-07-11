import argparse
from pathlib import Path
from typing import List, Set

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from va_explorer.va_data_management.models import ClusterLocationCodes
from va_explorer.va_data_management.utils.death_ids import checksum_letters


def _base_is_acceptable(digits: str) -> bool:
    """Filter rules for readable/distinct 4-digit cluster bases.

    Rejects zeros, repeated/adjacent-equal digits, mirrored pairs, and strictly
    ascending runs (same rules as the original R-derived generator).
    """
    p1, p2, p3, p4 = digits
    return not (
        "0" in digits
        or p1 == p2
        or p2 == p3
        or p3 == p4
        or (p1 == p4 and p3 == p2)
        or (p1 == p3 and p2 == p4)
        or (p1 < p2 < p3 < p4)
    )


def _build_cluster_id_df(n_groups: int) -> pd.DataFrame:
    """Generate human readable cluster IDs for Wards.

    Returns a DataFrame with a single column 'cluster_id'. Checksum letters
    come from the shared Death ID helper so ward codes and Death ID validation
    can never drift.
    """
    ids = []
    for idc in range(1000, min(n_groups, 9999) + 1):
        digits = str(idc)
        if not _base_is_acceptable(digits):
            continue
        letters = checksum_letters(digits)
        if letters[0] == letters[1]:
            continue
        ids.append(digits + letters)
    return pd.DataFrame({"cluster_id": ids})


def _generate_unique_codes(n_needed: int, existing: Set[str]) -> List[str]:
    """Generate at least n_needed unique codes not present in existing set.

    If the first batch is insufficient, increase the search space until enough
    codes are produced.
    """
    produced: List[str] = []
    # Start with ample search space; will expand if not enough
    n_groups = max(9999, n_needed * 2)

    while len(produced) < n_needed:
        df = _build_cluster_id_df(n_groups)
        for code in df["cluster_id"].tolist():
            if code and code not in existing and code not in produced:
                produced.append(code)
                if len(produced) >= n_needed:
                    break
        if len(produced) < n_needed:
            # Expand search space and try again
            n_groups *= 2
            if n_groups > 200000:  # safeguard against runaway loops
                raise CommandError(
                    "Unable to generate enough unique codes; please try again with fewer targets."
                )
    return produced


class Command(BaseCommand):
    help = (
        "Manage ward cluster codes in ClusterLocationCodes table. "
        "Use --initial to load CSV and generate codes; "
        "--update to sync new wards from CSV; "
        "--update-codes to assign codes to wards without them."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--initial",
            action="store_true",
            help="Load wards.csv into ClusterLocationCodes and generate cluster_codes for all wards",
        )
        parser.add_argument(
            "--update",
            action="store_true",
            help="Add new wards from CSV to table and generate codes for them",
        )
        parser.add_argument(
            "--update-codes",
            action="store_true",
            help="Generate cluster_codes for wards that don't have them",
        )

    def _get_csv_path(self) -> Path:
        """Get path to wards.csv in static/data directory."""
        # Navigate from this file: commands/ -> management/ -> va_data_management/ -> va_explorer/ -> project root
        command_dir = Path(__file__).resolve().parent
        project_root = command_dir.parent.parent.parent.parent
        csv_path = project_root / "va_explorer" / "static" / "data" / "wards.csv"

        if not csv_path.exists():
            raise CommandError(f"wards.csv not found at {csv_path}")
        return csv_path

    def _load_csv(self) -> pd.DataFrame:
        """Load and validate wards.csv."""
        csv_path = self._get_csv_path()
        df = pd.read_csv(csv_path, dtype=str)

        # Validate required columns
        required_cols = {"ward", "ward_code"}
        missing = required_cols - set(df.columns)
        if missing:
            raise CommandError(f"CSV missing required columns: {', '.join(missing)}")

        # Clean data
        df["ward"] = df["ward"].str.strip()
        df["ward_code"] = df["ward_code"].str.strip()

        return df

    def handle(self, *args, **options):
        do_initial = bool(options.get("initial"))
        do_update = bool(options.get("update"))
        do_update_codes = bool(options.get("update_codes"))

        # Validate flags
        flag_count = sum([do_initial, do_update, do_update_codes])
        if flag_count == 0:
            raise CommandError(
                "You must specify one of --initial, --update, or --update-codes."
            )
        if flag_count > 1:
            raise CommandError("Specify only one flag at a time.")

        # Load CSV for --initial and --update
        csv_df = None
        if do_initial or do_update:
            csv_df = self._load_csv()

        # Handle --initial: clear table, load CSV, generate codes
        if do_initial:
            self.stdout.write(
                "--initial mode: Loading CSV and generating all cluster codes..."
            )

            # Clear existing data
            deleted_count = ClusterLocationCodes.objects.all().count()
            ClusterLocationCodes.objects.all().delete()
            self.stdout.write(f"Cleared {deleted_count} existing records.")

            # Bulk create from CSV
            ward_objects = [
                ClusterLocationCodes(
                    ward=row["ward"], ward_code=row["ward_code"], cluster_code=None
                )
                for _, row in csv_df.iterrows()
            ]
            ClusterLocationCodes.objects.bulk_create(ward_objects, batch_size=1000)
            self.stdout.write(f"Loaded {len(ward_objects)} wards from CSV.")

            # Generate codes for all
            targets = list(ClusterLocationCodes.objects.all())
            self._assign_codes(targets)

            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Initial load complete: {len(targets)} wards with cluster codes."
                )
            )

        # Handle --update: sync new wards from CSV
        elif do_update:
            self.stdout.write("--update mode: Syncing new wards from CSV...")

            # Get existing ward names
            existing_wards = set(
                ClusterLocationCodes.objects.values_list("ward", flat=True)
            )

            # Find new wards in CSV
            new_ward_rows = csv_df[~csv_df["ward"].isin(existing_wards)]

            if new_ward_rows.empty:
                self.stdout.write("No new wards to add.")
                return

            # Add new wards
            new_objects = [
                ClusterLocationCodes(
                    ward=row["ward"], ward_code=row["ward_code"], cluster_code=None
                )
                for _, row in new_ward_rows.iterrows()
            ]
            ClusterLocationCodes.objects.bulk_create(new_objects, batch_size=1000)
            self.stdout.write(f"Added {len(new_objects)} new wards.")

            # Generate codes for new wards
            self._assign_codes(new_objects)

            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Update complete: {len(new_objects)} new wards with cluster codes."
                )
            )

        # Handle --update-codes: assign codes to wards without them
        elif do_update_codes:
            self.stdout.write(
                "--update-codes mode: Generating codes for wards without them..."
            )

            targets = list(
                ClusterLocationCodes.objects.filter(
                    Q(cluster_code__isnull=True) | Q(cluster_code="")
                )
            )

            if not targets:
                self.stdout.write("No wards missing cluster codes.")
                return

            self._assign_codes(targets)

            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Update codes complete: {len(targets)} wards assigned cluster codes."
                )
            )

    def _assign_codes(self, targets: List[ClusterLocationCodes]) -> None:
        """Generate and assign unique cluster codes to target ward objects."""
        if not targets:
            return

        # Build set of existing codes
        existing_codes_qs = ClusterLocationCodes.objects.exclude(
            Q(cluster_code__isnull=True) | Q(cluster_code="")
        )
        existing: Set[str] = {
            str(c).strip()
            for c in existing_codes_qs.values_list("cluster_code", flat=True)
            if str(c).strip()
        }

        # Generate unique codes
        n_needed = len(targets)
        codes = _generate_unique_codes(n_needed, existing)

        # Assign codes
        for obj, code in zip(targets, codes):
            # Ensure no collision
            while code in existing:
                extra = _generate_unique_codes(1, existing)
                code = extra[0]
            obj.cluster_code = code
            existing.add(code)

        # Bulk update
        ClusterLocationCodes.objects.bulk_update(
            targets, ["cluster_code"], batch_size=1000
        )

        self.stdout.write(f"Assigned {len(targets)} unique cluster codes.")
