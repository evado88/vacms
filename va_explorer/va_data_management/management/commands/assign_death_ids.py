import argparse
import subprocess
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Q

from va_explorer.va_data_management.models import ClusterLocationCodes, Death


class Command(BaseCommand):
    help = (
        "Generate and assign sequential death_ids to Death records without them. "
        "Format: cluster_code + ward sequence (e.g., 1213SM0001, 1213SM0002). "
        "Each ward gets its own sequential counter."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be assigned without saving to database",
        )

    def _column_exists(self) -> bool:
        """Check if death_id column exists on Death table."""
        # First check if the field is defined in the model
        field_names = {f.name for f in Death._meta.get_fields()}
        if "death_id" not in field_names:
            return False

        # Field is in model; check if it exists in the database
        table_name = Death._meta.db_table
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = %s AND COLUMN_NAME = 'death_id'",
                [table_name],
            )
            return cursor.fetchone() is not None

    def _run_migrations(self) -> bool:
        """Attempt to make and run migrations for Death.death_id field."""
        try:
            self.stdout.write("death_id column not found. Running makemigrations...")
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

    def _add_column_manually(self) -> bool:
        """Add death_id column manually using ALTER TABLE."""
        try:
            table_name = Death._meta.db_table
            self.stdout.write(f"Adding death_id column to {table_name}...")

            with connection.cursor() as cursor:
                # PostgreSQL ALTER TABLE to add column
                cursor.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN death_id TEXT NULL"
                )

            self.stdout.write(self.style.SUCCESS("✓ Column added successfully."))
            return True
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to add column: {str(e)}"))
            return False

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))

        # Check if death_id column exists
        if not self._column_exists():
            self.stdout.write(
                self.style.WARNING("death_id column does not exist on Death table.")
            )
            # Try migrations first
            if not self._run_migrations() and not self._add_column_manually():
                return

        # Query deaths without death_id
        try:
            deaths_qs = Death.objects.filter(Q(death_id__isnull=True) | Q(death_id=""))
            total_missing = deaths_qs.count()
        except Exception as e:
            # If column still doesn't exist, try adding it manually
            if "death_id" in str(e):
                self.stdout.write(
                    self.style.WARNING(
                        "Column check failed, attempting manual creation..."
                    )
                )
                if not self._add_column_manually():
                    raise
                deaths_qs = Death.objects.filter(
                    Q(death_id__isnull=True) | Q(death_id="")
                )
                total_missing = deaths_qs.count()
            else:
                raise

        if total_missing == 0:
            self.stdout.write("No deaths without death_ids found.")
            return

        self.stdout.write(f"Found {total_missing} deaths without death_ids.")

        # Group deaths by ward
        deaths_by_ward: Dict[str, List[Death]] = defaultdict(list)
        for death in deaths_qs:
            ward = (death.ward or "").strip()
            if ward:
                deaths_by_ward[ward].append(death)

        if not deaths_by_ward:
            self.stdout.write("No wards found in death records.")
            return

        self.stdout.write(f"Deaths grouped into {len(deaths_by_ward)} ward(s).")

        # Build mapping: ward_code -> cluster_code
        ward_to_cluster = {}
        missing_wards = []

        for ward_name in deaths_by_ward:
            cluster_loc = ClusterLocationCodes.objects.filter(
                ward_code__iexact=ward_name
            ).first()

            if cluster_loc and cluster_loc.cluster_code:
                ward_to_cluster[ward_name] = cluster_loc.cluster_code
            else:
                missing_wards.append(ward_name)

        if missing_wards:
            self.stdout.write(
                self.style.WARNING(
                    f"⚠ No cluster code found for {len(missing_wards)} ward(s): "
                    f"{', '.join(missing_wards[:5])}"
                    f"{'...' if len(missing_wards) > 5 else ''}"
                )
            )

        # Assign death_ids
        deaths_to_update: List[Death] = []
        assignments: List[Tuple[str, str, str]] = []  # (ward, death_id, death_name)

        for ward_name, deaths_in_ward in deaths_by_ward.items():
            cluster_code = ward_to_cluster.get(ward_name)

            if not cluster_code:
                self.stdout.write(
                    self.style.WARNING(
                        f"Skipping {len(deaths_in_ward)} death(s) in ward '{ward_name}' "
                        f"(no cluster code)."
                    )
                )
                continue

            # Get existing deaths in this ward to find next sequence
            existing_deaths = Death.objects.filter(ward__iexact=ward_name).exclude(
                Q(death_id__isnull=True) | Q(death_id="")
            )

            # Extract sequence numbers from existing death_ids
            existing_sequences = []
            for existing_death in existing_deaths:
                if existing_death.death_id and existing_death.death_id.startswith(
                    cluster_code
                ):
                    # Extract the numeric suffix
                    suffix = existing_death.death_id[len(cluster_code) :]
                    try:
                        seq_num = int(suffix)
                        existing_sequences.append(seq_num)
                    except ValueError:
                        pass

            next_seq = max(existing_sequences) + 1 if existing_sequences else 1

            # Assign sequential death_ids
            for death in sorted(deaths_in_ward, key=lambda d: d.id):
                death_id = f"{cluster_code}{next_seq:04d}"
                death.death_id = death_id
                deaths_to_update.append(death)
                assignments.append((ward_name, death_id, death.DE_03 or "Unknown"))
                next_seq += 1

        if not deaths_to_update:
            self.stdout.write("No deaths to update (all missing cluster codes).")
            return

        # Display assignments
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(f"{'Ward':<20} {'Death ID':<15} {'Deceased Name':<40}")
        self.stdout.write("=" * 80)
        for ward, death_id, name in assignments[:20]:  # Show first 20
            self.stdout.write(f"{ward:<20} {death_id:<15} {name:<40}")
        if len(assignments) > 20:
            self.stdout.write(f"... and {len(assignments) - 20} more")
        self.stdout.write("=" * 80 + "\n")

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY RUN: Would assign death_ids to {len(deaths_to_update)} death(s)."
                )
            )
            return

        # Persist changes
        Death.objects.bulk_update(deaths_to_update, ["death_id"], batch_size=1000)

        self.stdout.write(
            self.style.SUCCESS(
                f"✓ Successfully assigned death_ids to {len(deaths_to_update)} death(s)."
            )
        )
