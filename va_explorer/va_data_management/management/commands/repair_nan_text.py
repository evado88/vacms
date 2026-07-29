"""Repair text fields that hold the literal string "nan".

A blank cell in an ODK CSV export arrives from ``pandas.read_csv`` as
``float('nan')``. The CSV loaders used to pass that straight into
``model(**row)``; because these forms store every answer in a TextField, Django
stringified the float and persisted the four characters "nan" instead of NULL.

The loaders are fixed (``utils.loading.clean_import_value``), so this command is
a one-off repair of records imported before that fix. It is idempotent and safe
to re-run.

Only case-insensitive "nan" is matched. "none"/"null"/"999" are deliberately
left alone -- "999" in particular is a real enumerator convention for
refused/unknown in the free-text NRC and occupation fields.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from va_explorer.va_data_management.models import (
    CSADailyTracker,
    Death,
    Household,
    HouseholdMember,
    Pregnancy,
    PregnancyOutcome,
    SocialMobilization,
)

# Every model reachable from the CSV loaders that shared the unsanitised
# row -> model(**row) path (``utils.loading.load_odk_csv_to_model`` and
# ``load_form_csv``).
REPAIRABLE_MODELS = [
    Death,
    Pregnancy,
    PregnancyOutcome,
    Household,
    HouseholdMember,
    SocialMobilization,
    CSADailyTracker,
]

TEXTUAL_FIELD_TYPES = {"TextField", "CharField"}


def _textual_fields(model):
    """(name, blank_value) for each concrete text column on a model.

    Death is entirely ``null=True``, but Pregnancy, PregnancyOutcome and
    Household each carry a handful of NOT NULL text columns, so the blank value
    has to follow the field rather than being None everywhere.
    """
    return [
        (field.name, None if field.null else "")
        for field in model._meta.fields
        if field.get_internal_type() in TEXTUAL_FIELD_TYPES
    ]


def _repair_target(model, *, dry_run):
    """Count (and optionally blank out) "nan" values on one model.

    Returns ``{field_name: count}`` for fields that had at least one hit.
    """
    counts = {}
    for name, blank_value in _textual_fields(model):
        queryset = model.objects.filter(**{f"{name}__iexact": "nan"})
        hits = queryset.count()
        if not hits:
            continue
        counts[name] = hits
        if not dry_run:
            # .update() bypasses save()/signals on purpose: this is a data
            # correction, not a domain event, and Death.post_save queues a
            # Celery task we do not want to fan out per row.
            queryset.update(**{name: blank_value})
    return counts


def _history_model(model):
    """The simple_history sibling table, if the model tracks history."""
    manager = getattr(model, "history", None)
    return getattr(manager, "model", None)


class Command(BaseCommand):
    help = 'Replace literal "nan" strings in form text fields with NULL'

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )
        parser.add_argument(
            "--model",
            action="append",
            dest="models",
            help=(
                "Limit the repair to one model by name (repeatable). "
                "Defaults to every affected model."
            ),
        )

    def _select_models(self, names):
        if not names:
            return REPAIRABLE_MODELS
        wanted = {name.lower() for name in names}
        selected = [m for m in REPAIRABLE_MODELS if m.__name__.lower() in wanted]
        unknown = wanted - {m.__name__.lower() for m in selected}
        if unknown:
            known = ", ".join(m.__name__ for m in REPAIRABLE_MODELS)
            raise SystemExit(
                f"Unknown model(s): {', '.join(sorted(unknown))}. Known: {known}"
            )
        return selected

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        models = self._select_models(options.get("models"))
        prefix = "[DRY RUN] " if dry_run else ""
        total = 0

        with transaction.atomic():
            for model in models:
                # Repair the history table alongside the live one. Leaving it
                # behind keeps "nan" visible in the record's history tab and
                # lets a revert write the bad value straight back.
                targets = [(model.__name__, model)]
                history_model = _history_model(model)
                if history_model is not None:
                    targets.append((history_model.__name__, history_model))

                for label, target in targets:
                    counts = _repair_target(target, dry_run=dry_run)
                    if not counts:
                        continue
                    subtotal = sum(counts.values())
                    total += subtotal
                    verb = "would repair" if dry_run else "repaired"
                    self.stdout.write(f"{prefix}{label}: {verb} {subtotal} value(s)")
                    for name, hits in sorted(
                        counts.items(), key=lambda item: (-item[1], item[0])
                    ):
                        self.stdout.write(f"    {name}: {hits}")

            if dry_run:
                transaction.set_rollback(True)

        if not total:
            self.stdout.write(
                self.style.SUCCESS('No literal "nan" values found. Nothing to do.')
            )
            return

        verb = "Would repair" if dry_run else "Repaired"
        message = f'{prefix}{verb} {total} literal "nan" value(s) in total.'
        self.stdout.write(
            self.style.WARNING(message) if dry_run else self.style.SUCCESS(message)
        )
