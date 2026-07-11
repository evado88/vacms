import argparse

from django.core.management.base import BaseCommand

from va_explorer.va_data_management.utils.death_ids import assign_death_ids


class Command(BaseCommand):
    help = (
        "Assign sequential Death IDs to Death records without them. "
        "Format: NNNNCCSSSS — 4-digit cluster base, 2 checksum letters, "
        "4-digit per-cluster sequence (e.g. 1213SM0001). "
        "Shares its implementation with the Celery task and the import hooks."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be assigned without saving to database",
        )

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))

        summary = assign_death_ids(dry_run=dry_run)

        if summary["total_missing"] == 0:
            self.stdout.write("No deaths without death_ids found.")
            return

        self.stdout.write(f"Found {summary['total_missing']} deaths without death_ids.")

        if summary["assignments"]:
            self.stdout.write("\n" + "=" * 80)
            self.stdout.write(f"{'Ward':<20} {'Death ID':<15} {'Deceased Name':<40}")
            self.stdout.write("=" * 80)
            for ward, death_id, name in summary["assignments"][:20]:
                self.stdout.write(f"{ward:<20} {death_id:<15} {name:<40}")
            if len(summary["assignments"]) > 20:
                self.stdout.write(f"... and {len(summary['assignments']) - 20} more")
            self.stdout.write("=" * 80 + "\n")

        for ward, info in summary["skipped_wards"].items():
            self.stdout.write(
                self.style.WARNING(
                    f"Skipped {info['count']} death(s) in ward '{ward}' "
                    f"({info['reason']})."
                )
            )
        if summary["malformed_existing"]:
            sample = ", ".join(summary["malformed_existing"][:5])
            self.stdout.write(
                self.style.WARNING(
                    f"Ignored {len(summary['malformed_existing'])} malformed existing "
                    f"death_id(s) for sequence continuation (never overwritten): "
                    f"{sample}"
                    f"{'...' if len(summary['malformed_existing']) > 5 else ''}"
                )
            )
        for overflow in summary["overflow_wards"]:
            self.stdout.write(
                self.style.ERROR(
                    f"Sequence exhausted for ward '{overflow['ward']}' "
                    f"(cluster {overflow['cluster_code']}) — records left "
                    "unassigned. The 4-digit sequence supports 0001-0999."
                )
            )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"DRY RUN: Would assign death_ids to "
                    f"{summary['assigned']} death(s)."
                )
            )
        elif summary["assigned"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Successfully assigned death_ids to "
                    f"{summary['assigned']} death(s)."
                )
            )
        else:
            self.stdout.write("No deaths to update (see warnings above).")
