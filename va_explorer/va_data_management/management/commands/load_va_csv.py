import argparse

import pandas as pd
from django.core.management.base import BaseCommand

from va_explorer.va_data_management.utils.loading import load_records_from_dataframe


class Command(BaseCommand):
    help = "Loads a verbal autopsy CSV file into the database"

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=argparse.FileType("r"))
        parser.add_argument("--random_locations", type=str, nargs="?", default=False)

    def handle(self, *args, **options):
        csv_data = pd.read_csv(options["csv_file"], low_memory=False)
        random_locations = options.get("random_locations", False)

        results = load_records_from_dataframe(csv_data, random_locations)

        num_created = len(results["created"])
        num_ignored = len(results["ignored"])
        num_outdated = len(results["outdated"])
        num_enriched = len(results["enriched"])
        created_community = sum(
            1 for va in results["created"] if va.community_va_normalized == "yes"
        )
        created_facility = sum(
            1 for va in results["created"] if va.community_va_normalized == "no"
        )
        created_unknown = num_created - created_community - created_facility
        created_with_cluster = sum(
            1
            for va in results["created"]
            if va.community_va_normalized == "yes" and va.cluster_id is not None
        )
        created_with_location = sum(
            1
            for va in results["created"]
            if va.community_va_normalized != "yes" and va.location_id is not None
        )

        self.stdout.write(
            f"Loaded {num_created} verbal autopsies from CSV "
            f"({num_ignored} ignored, {num_enriched} enriched, "
            f"{num_outdated} removed as outdated)"
        )
        self.stdout.write(
            "Created mix: "
            f"community={created_community} (with cluster={created_with_cluster}), "
            f"facility={created_facility} (with location={created_with_location}), "
            f"unknown={created_unknown}"
        )
