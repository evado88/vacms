# va_explorer/va_data_management/management/commands/load_household_csv.py

import argparse

import pandas as pd
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from va_explorer.va_data_management.models import Household, ODKFormChoice
from va_explorer.va_data_management.utils.loading import (
    load_odk_csv_to_model,
    normalize_dataframe_columns,
    normalize_string,
)


class Command(BaseCommand):
    """Load a household CSV using the previously loaded ODK definition, then run DQ checks."""

    help = "Load household CSV data and run dq_households"

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=argparse.FileType("r"))

    def handle(self, *args, **options):
        form_name = "household"
        csv_file = options["csv_file"]

        norm_form_name = normalize_string(form_name)

        if not ODKFormChoice.objects.filter(form_name=norm_form_name).exists():
            raise CommandError("Definition for form 'households' has not been loaded")

        # --- Load CSV ---
        df = pd.read_csv(csv_file, dtype=str)
        df = normalize_dataframe_columns(df, Household)

        if "key" not in df.columns:
            raise CommandError(
                "CSV is missing required 'key' column after normalization."
            )

        # Standardize keys so we can deduplicate both against the CSV itself and the DB
        df = df[~df["key"].isna()].copy()
        df["key"] = df["key"].astype(str).str.strip()
        df = df[df["key"] != ""]
        df = df.drop_duplicates(subset="key", keep="first")

        csv_keys = df["key"].unique().tolist()
        existing_keys = set()
        if csv_keys:
            existing_keys = set(
                Household.objects.filter(key__in=csv_keys).values_list("key", flat=True)
            )
            if existing_keys:
                df = df[~df["key"].isin(existing_keys)]

        odk_map_columns = [
            "consent",
            "household",
            "household_type",
            "HH_16",
            "HH_17",
            "HH_18",
            "HH_19",
            "HH_20",
            "HH_22",
            "HH_22A",
            "HH_22B",
            "HH_22C",
            "HH_22D",
            "HH_22E",
            "HH_23",
            "HH_28",
            "HH_29",
            "HH_30",
            "HH_31",
            "HH_32",
            "HH_33",
            "HH_34",
            "HH_35",
            "HH_36_Bed",
            "HH_36_Blanket",
            "HH_36_Table",
            "HH_36_Sofa",
            "HH_36_Stove",
            "HH_36_Fridge",
            "HH_36_Decorder",
            "HH_36_Radio",
            "HH_36_Non_smart_Television",
            "HH_36_Smart_Television",
            "HH_36_Desktop_Laptop_Computer",
            "HH_36_Landline",
            "HH_36_Access_to_internet_away_from_home",
            "HH_36_Access_to_internet_at_Home",
            "HH_36_Generator",
            "HH_36_Wheelbarrow",
            "HH_36_Bicycle",
            "HH_36_Motor_Vehicle",
            "HH_36_Motorcycle",
            "HH_36_Scotch_Cart",
            "HH_36_Motorised_Boat",
            "HH_36_Non-motorised_Boat",
            "HH_36_Fishing_net",
            "HH_36_Grain_Grinder",
            "HH_36_Hoe",
            "HH_36_Plough",
            "HH_36_Tractor",
            "HH_36_Hammer_Mill",
            "HH_36_Agricultural_Land",
            "HH_38",
            "HH_39",
            "HH_40",
            "HH_41",
            "HH_42",
            "HH_43",
            "HH_44",
            "HH_45",
            "HH_46",
            "HH_47",
            "HH_48",
            "visits",
            "result_list",
        ]
        odk_map_columns = [c.strip() for c in odk_map_columns]

        objects = load_odk_csv_to_model(
            df=df,
            model=Household,
            odk_choices_queryset=ODKFormChoice.objects.filter(form_name=norm_form_name),
            odk_map_columns=odk_map_columns,
            verbose=True,  # Set to False to reduce output
        )

        Household.objects.bulk_create(objects, ignore_conflicts=True)
        self.stdout.write(
            self.style.SUCCESS(f"Imported {len(objects)} records for household")
        )

        # --- Run data quality checks immediately after import ---
        try:
            call_command("dq_households")  # uses built-in constants; no args needed
            self.stdout.write(
                self.style.SUCCESS("Ran dq_households successfully after import.")
            )
        except Exception as e:
            # Don't fail the import if DQ fails; surface a clear message
            self.stdout.write(
                self.style.WARNING(
                    f"dq_households encountered an error after import: {e}"
                )
            )
