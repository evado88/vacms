# va_explorer/va_data_management/management/commands/load_household_csv.py

import argparse

import pandas as pd
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from simple_history.utils import bulk_update_with_history

from va_explorer.va_data_management.models import Household, ODKFormChoice
from va_explorer.va_data_management.utils.geopoint import merge_geopoint_into
from va_explorer.va_data_management.utils.loading import (
    _has_meaningful_value,
    load_odk_csv_to_model,
    normalize_dataframe_columns,
    normalize_string,
    normalize_value,
)

# Fields that carry ODK coded values requiring label lookup
ODK_MAP_COLUMNS = [
    c.strip()
    for c in [
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
]

# Text fields eligible for backfill on re-ingestion (excludes non-updatable identifiers)
_SKIP_UPDATE_FIELDS = {"key", "id"}
_UPDATABLE_TEXT_FIELDS = [
    f.name
    for f in Household._meta.get_fields()
    if hasattr(f, "get_internal_type")
    and f.get_internal_type() == "TextField"
    and f.name not in _SKIP_UPDATE_FIELDS
]


def _build_odk_label_map(odk_choices_qs):
    odk_map = {}
    for choice in odk_choices_qs:
        field = normalize_string(choice.field_name)
        value = normalize_value(choice.value)
        odk_map.setdefault(field, {})[value] = choice.label
    return odk_map


def _apply_odk_labels(df, odk_map):
    """Map coded ODK values to human-readable labels in-place on a copy."""
    df = df.copy()
    for col in ODK_MAP_COLUMNS:
        if col not in df.columns or col not in odk_map:
            continue
        col_map = odk_map[col]

        def lookup(v, m=col_map):
            v_norm = normalize_value(v)
            return m.get(v_norm, v_norm) if _has_meaningful_value(v) else v

        df[col] = df[col].apply(lookup)
    return df


def _upsert_existing(existing_df, stdout, style):
    """Backfill blank fields on already-imported Household rows."""
    if existing_df.empty:
        return 0

    hh_map = {
        h.key: h for h in Household.objects.filter(key__in=existing_df["key"].tolist())
    }

    updates = []
    updated_fields_seen = set()

    for _, row in existing_df.iterrows():
        obj = hh_map.get(str(row["key"]).strip())
        if obj is None:
            continue

        changed = False
        for field in _UPDATABLE_TEXT_FIELDS:
            if field not in row:
                continue
            incoming = row[field]
            if not _has_meaningful_value(incoming):
                continue
            if not _has_meaningful_value(getattr(obj, field, None)):
                setattr(obj, field, str(incoming).strip())
                updated_fields_seen.add(field)
                changed = True

        if changed:
            updates.append(obj)

    if updates:
        bulk_update_with_history(
            updates,
            Household,
            fields=list(updated_fields_seen),
            batch_size=500,
        )
        stdout.write(
            style.SUCCESS(
                f"Updated {len(updates)} existing household record(s) with missing field values."
            )
        )
    else:
        stdout.write(style.SUCCESS("No field updates needed for existing records."))

    return len(updates)


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

        # --- Load and prepare CSV ---
        df = pd.read_csv(csv_file, dtype=str)

        # Merge split GPS columns (hh_gps-Latitude/Longitude/Altitude/Accuracy → hh_gps)
        # before column normalization drops the component columns.
        df = merge_geopoint_into(df, source_prefix="hh_gps", target_field="hh_gps")

        df = normalize_dataframe_columns(df, Household)

        if "key" not in df.columns:
            raise CommandError(
                "CSV is missing required 'key' column after normalization."
            )

        # Standardize keys and strip within-CSV duplicates
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

        new_df = df[~df["key"].isin(existing_keys)].copy()
        existing_df = df[df["key"].isin(existing_keys)].copy()

        odk_choices_qs = ODKFormChoice.objects.filter(form_name=norm_form_name)

        # --- Import new records ---
        objects = load_odk_csv_to_model(
            df=new_df,
            model=Household,
            odk_choices_queryset=odk_choices_qs,
            odk_map_columns=ODK_MAP_COLUMNS,
            verbose=True,
        )
        Household.objects.bulk_create(objects, ignore_conflicts=True)
        self.stdout.write(
            self.style.SUCCESS(f"Imported {len(objects)} new household record(s).")
        )

        # --- Backfill missing fields on existing records ---
        if not existing_df.empty:
            odk_label_map = _build_odk_label_map(odk_choices_qs)
            existing_df_mapped = _apply_odk_labels(existing_df, odk_label_map)
            _upsert_existing(existing_df_mapped, self.stdout, self.style)

        # --- Run data quality checks immediately after import ---
        try:
            call_command("dq_households")
            self.stdout.write(
                self.style.SUCCESS("Ran dq_households successfully after import.")
            )
        except Exception as e:
            self.stdout.write(
                self.style.WARNING(
                    f"dq_households encountered an error after import: {e}"
                )
            )
