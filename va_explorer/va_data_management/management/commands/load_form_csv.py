import argparse
from collections import defaultdict

import pandas as pd
from django.core.management.base import BaseCommand, CommandError

from va_explorer.va_data_management.models import (
    CSADailyTracker,
    Death,
    Household,
    HouseholdMember,
    ODKFormChoice,
    Pregnancy,
    PregnancyOutcome,
    SocialMobilization,
)
from va_explorer.va_data_management.utils.death_ids import assign_death_ids
from va_explorer.va_data_management.utils.loading import (
    blank_values_for_model,
    clean_import_value,
    normalize_dataframe_columns,
)
from va_explorer.va_data_management.utils.repeat_group_dedup import (
    CHILD_DEDUP_SPECS,
    run_dedup_for_model,
)

FORM_MODEL_MAP = {
    "household": Household,
    "household_member": HouseholdMember,
    "pregnancy": Pregnancy,
    "pregnancy_outcome": PregnancyOutcome,
    "death": Death,
    "csa_daily_tracker": CSADailyTracker,
    "social_mobilization": SocialMobilization,
}


class Command(BaseCommand):
    help = "Load CSV data for a form using ODK definition reference"

    def add_arguments(self, parser):
        parser.add_argument("form_name", choices=FORM_MODEL_MAP.keys())
        parser.add_argument("csv_file", type=argparse.FileType("r"))

    def handle(self, *args, **options):
        form_name = options["form_name"]
        csv_file = options["csv_file"]

        if not ODKFormChoice.objects.filter(form_name=form_name).exists():
            raise CommandError(f"Definition for form '{form_name}' has not been loaded")

        df = pd.read_csv(csv_file)
        model = FORM_MODEL_MAP[form_name]
        df = normalize_dataframe_columns(df, model)

        # Build mapping of field -> value -> label
        lookup = defaultdict(dict)
        for choice in ODKFormChoice.objects.filter(form_name=form_name):
            lookup[choice.field_name][choice.value] = choice.label

        for field, mapping in lookup.items():
            if field in df.columns:
                df[field] = df[field].map(lambda v, _map=mapping: _map.get(str(v), v))

        blanks = blank_values_for_model(model)
        objects = [
            model(
                **{
                    key: clean_import_value(value, blanks.get(key))
                    for key, value in row.items()
                }
            )
            for row in df.to_dict(orient="records")
        ]
        model.objects.bulk_create(objects)

        self.stdout.write(f"Imported {len(objects)} records for {form_name}")

        # Deaths are registered without a Death ID; mint them at ingest so no
        # record waits for the periodic sweeper.
        if model is Death and objects:
            id_summary = assign_death_ids(
                death_ids=[obj.id for obj in objects if obj.id]
            )
            if id_summary["assigned"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Assigned death_ids to {id_summary['assigned']} "
                        "imported death(s)."
                    )
                )
            for ward, info in id_summary["skipped_wards"].items():
                self.stdout.write(
                    self.style.WARNING(
                        f"No death_id for {info['count']} death(s) in ward "
                        f"'{ward}' ({info['reason']})."
                    )
                )

        # For repeat-group child models (e.g. household_member), flag content
        # duplicates immediately so counts only include one row per person.
        spec = next((s for s in CHILD_DEDUP_SPECS.values() if s.model is model), None)
        if spec and objects:
            summary = run_dedup_for_model(
                spec,
                parent_ids={getattr(obj, spec.parent_field) for obj in objects},
            )
            if summary["flagged"]:
                self.stdout.write(
                    self.style.WARNING(
                        f"Flagged {summary['flagged']} content-duplicate "
                        f"{spec.label} record(s); only the most complete record "
                        "per person is counted as unique."
                    )
                )
