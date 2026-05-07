import argparse
import subprocess
import sys
from pathlib import Path
from typing import List, Set

import numpy as np
import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Q

from va_explorer.va_data_management.models import ClusterLocationCodes


def _build_cluster_id_df(n_groups: int) -> pd.DataFrame:
    """Generate human readable cluster IDs for Wards.

    Returns a DataFrame with a single column 'cluster_id'.
    """
    # checksum list
    checksum_list = pd.DataFrame(
        {
            "check_chr": [
                "A",
                "B",
                "C",
                "D",
                "E",
                "F",
                "G",
                "H",
                "J",
                "K",
                "L",
                "M",
                "N",
                "P",
                "R",
                "S",
                "T",
                "Y",
                "Z",
            ],
            "check_num": [
                2,
                3,
                7,
                12,
                13,
                16,
                1,
                10,
                14,
                4,
                8,
                18,
                6,
                15,
                0,
                9,
                17,
                5,
                11,
            ],
        }
    )

    # initial dataset
    df = pd.DataFrame({"id": [1], "n_groups": [n_groups]})

    # expand n_groups (replicate rows)
    df = df.loc[df.index.repeat(df["n_groups"])].reset_index(drop=True)

    # generate idc = row number (1-based)
    df["idc"] = np.arange(1, len(df) + 1)

    # convert to string
    df["s"] = df["idc"].astype(str)

    # keep only 4-digit strings
    df = df[df["s"].str.len() == 4].copy()

    # extract digits
    df["p1"] = df["s"].str[0]
    df["p2"] = df["s"].str[1]
    df["p3"] = df["s"].str[2]
    df["p4"] = df["s"].str[3]

    # drop numbers that match certain patterns
    mask = ~(
        (df["p1"] == df["p2"]) & (df["p2"] == df["p3"]) & (df["p3"] == df["p4"])
        | (df["p2"] == df["p3"]) & (df["p3"] == df["p4"])
        | (df["p1"] == df["p4"]) & (df["p3"] == df["p2"])
        | (df["p2"] == df["p3"])
        | (df["p3"] == df["p4"])
        | (df["p1"] < df["p2"]) & (df["p2"] < df["p3"]) & (df["p3"] < df["p4"])
        | (df["p1"] == df["p3"]) & (df["p2"] == df["p4"])
        | (df["p1"] == df["p2"])
        | (df["p3"] == df["p4"])
        | (df["p1"] == "0")
        | (df["p2"] == "0")
        | (df["p3"] == "0")
        | (df["p4"] == "0")
    )

    df = df[mask].copy()

    # drop unused columns
    df = df.drop(columns=["id", "n_groups"])

    # rename s -> idc (string version)
    df["idc"] = df["s"]
    df = df.drop(columns=["s"])

    # convert digit columns to numeric
    for col in ["p1", "p2", "p3", "p4"]:
        df[col] = df[col].astype(int)

    # checksum calculations
    df["L11"] = ((df["p1"] * 9) + (df["p2"] * 7) + (df["p3"] * 5) + (df["p4"] * 3)) % 19

    df["L12"] = ((df["p1"] * 3) + (df["p2"] * 5) + (df["p3"] * 7) + (df["p4"] * 9)) % 19

    # keep only where L11 != L12
    df = df[df["L11"] != df["L12"]].copy()

    # merge for first checksum letter (L11 -> lett1)
    merged_temp3 = df[["idc", "L11"]].merge(
        checksum_list, left_on="L11", right_on="check_num", how="left"
    )
    temp3 = (
        merged_temp3[["idc", "check_chr"]]
        .rename(columns={"check_chr": "lett1"})
        .drop_duplicates(subset=["idc"])
    )

    # merge for second checksum letter (L12 -> lett2)
    merged_temp4 = df[["idc", "L12"]].merge(
        checksum_list, left_on="L12", right_on="check_num", how="left"
    )
    temp4 = (
        merged_temp4[["idc", "check_chr"]]
        .rename(columns={"check_chr": "lett2"})
        .drop_duplicates(subset=["idc"])
    )

    # combine results
    cluster_ids = temp3.merge(temp4, on="idc", how="left")

    # create final cluster_id
    cluster_ids["cluster_id"] = (
        cluster_ids["idc"].astype(str) + cluster_ids["lett2"] + cluster_ids["lett1"]
    )

    # keep only cluster_id column
    cluster_ids = cluster_ids[["cluster_id"]]
    return cluster_ids


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

    def _table_exists(self) -> bool:
        """Check if ClusterLocationCodes table exists."""
        table_name = ClusterLocationCodes._meta.db_table
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = %s",
                [table_name],
            )
            return cursor.fetchone()[0] > 0

    def _run_migrations(self) -> bool:
        """Attempt to make and run migrations for ClusterLocationCodes."""
        try:
            self.stdout.write("Table not found. Running makemigrations...")
            result = subprocess.run(
                [sys.executable, "manage.py", "makemigrations", "va_data_management"],
                check=True,
                capture_output=True,
                text=True,
            )
            self.stdout.write(result.stdout)

            self.stdout.write("Running migrate...")
            result = subprocess.run(
                [sys.executable, "manage.py", "migrate"],
                check=True,
                capture_output=True,
                text=True,
            )
            self.stdout.write(result.stdout)
            return True
        except subprocess.CalledProcessError as e:
            self.stdout.write(self.style.ERROR(f"Migration failed: {e.stderr}"))
            self.stdout.write(
                self.style.WARNING(
                    "Please run migrations manually:\n"
                    f"  {sys.executable} manage.py makemigrations va_data_management\n"
                    f"  {sys.executable} manage.py migrate"
                )
            )
            return False

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

        # Check table existence for --initial
        if do_initial and not self._table_exists():
            self.stdout.write(
                self.style.WARNING("ClusterLocationCodes table does not exist.")
            )
            if not self._run_migrations():
                return

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
