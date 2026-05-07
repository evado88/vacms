import csv
from pathlib import Path
from typing import Dict, List, Tuple

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from va_explorer.va_data_management.models import (
    Death,
    Household,
    Pregnancy,
    PregnancyOutcome,
    VerbalAutopsy,
)
from va_explorer.va_data_management.utils.fake_data import FakeDataGenerator

MODEL_CONFIGS = [
    {
        "model": Household,
        "slug": "household",
        "field_rules": {
            "respondent": "name_full",
            "HH_01": "name_full",
            "hh_gps": "gps",
        },
        "location_fields": ["province", "district", "constituency", "ward"],
    },
    {
        "model": Pregnancy,
        "slug": "pregnancy",
        "field_rules": {
            "supervisor": "name_full",
            "enumerator": "name_full",
            "PE_02": "name_full",
            "PE_05": "name_full",
            "PE_06": "name_full",
            "PE_20": "facility",
            "PE_25": "gps",
        },
        "location_fields": ["province", "district", "constituency", "ward"],
    },
    {
        "model": PregnancyOutcome,
        "slug": "pregnancy_outcome",
        "field_rules": {
            "supervisor": "name_full",
            "enumerator": "name_full",
            "PO_01": "name_full",
            "PO_03": "name_full",
            "PO_04": "name_full",
            "PO_17": "facility",
            "PO_23": "name_full",
            "PO_38": "name_full",
            "PO_49B": "name_full",
            "PO_14": "phone",
            "PO_31A": "phone",
            "PO_06": "identifier",
            "PO_25": "identifier",
            "PO_36": "identifier",
            "PO_51A_02": "identifier",
            "PO_51A_02A": "identifier",
            "PO_51A_03": "identifier",
            "PO_51A_03A": "identifier",
            "PO_51A_04": "identifier",
            "PO_51A_04A": "identifier",
            "PO_51B_03": "identifier",
            "PO_51B_03A": "identifier",
            "PO_51B_04": "identifier",
            "PO_51B_04A": "identifier",
            "PO_51B_05": "identifier",
            "PO_51B_05A": "identifier",
            "gps": "gps",
        },
        "location_fields": ["province", "district", "constituency", "ward"],
    },
    {
        "model": Death,
        "slug": "death",
        "field_rules": {
            "supervisor": "name_full",
            "enumerator": "name_full",
            "DE_02": "name_full",
            "DE_03": "name_full",
            "DE_19": "name_full",
            "DE_28": "name_full",
            "DE_26": "phone",
            "DE_29": "phone",
            "DE_08": "identifier",
            "DE_10": "identifier",
            "DE_23": "identifier",
            "DE_34": "gps",
        },
        "location_fields": ["province", "district", "constituency", "ward"],
    },
    {
        "model": VerbalAutopsy,
        "slug": "verbal_autopsy",
        "field_rules": {
            "deviceid": "identifier",
            "instanceid": "identifier",
            "instancename": "identifier",
            "username": "identifier",
            "simserial": "identifier",
            "bid": "identifier",
            "bid2": "identifier",
            "bid_check": "identifier",
            "phonenumber": "phone",
            "hospital": "facility",
            "Id10007": "name_full",
            "Id10010": "name_full",
            "Id10017": "name_full",
            "Id10018": "name_full",
            "Id10061": "name_full",
            "Id10062": "name_full",
            "Id10070": "identifier",
            "Id10073": "identifier",
            "geopoint": "gps",
        },
        "location_fields": ["province", "district", "constituency", "ward"],
    },
]


class Command(BaseCommand):
    help = (
        "Generate CSV dumps with fake data for sensitive fields. "
        "By default no database changes are persisted; pass --write to update records."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--n",
            type=int,
            required=True,
            dest="record_limit",
            help="Number of records per model to include in each dump.",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=None,
            help="Optional seed for deterministic fake data.",
        )
        parser.add_argument(
            "--outdir",
            type=str,
            default="va_explorer/static/data",
            help=(
                "Output directory for the CSV files "
                "(default: va_explorer/va_explorer/static/data relative to project root)."
            ),
        )
        parser.add_argument(
            "--write",
            action="store_true",
            default=False,
            help="Persist fake values back to the database before exporting.",
        )

    def handle(self, *args, **options):
        record_limit = options["record_limit"]
        if record_limit <= 0:
            raise CommandError("--n must be greater than zero.")

        generator = FakeDataGenerator(seed=options.get("seed"))
        outdir = self._resolve_output_dir(options["outdir"])
        outdir.mkdir(parents=True, exist_ok=True)
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")

        for config in MODEL_CONFIGS:
            self._process_model(
                config, record_limit, generator, outdir, timestamp, options["write"]
            )

    def _resolve_output_dir(self, directory: str) -> Path:
        output_path = Path(directory)

        # Resolve relative paths against the project root (cwd where manage.py is run)
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path

        output_path.mkdir(parents=True, exist_ok=True)
        return output_path

    def _process_model(
        self,
        config: Dict,
        record_limit: int,
        generator: FakeDataGenerator,
        outdir: Path,
        timestamp: str,
        should_write: bool,
    ):
        model = config["model"]
        queryset = self._select_records(model, record_limit)
        field_objs: List[object] = [
            field
            for field in model._meta.get_fields()
            if getattr(field, "concrete", False) and not field.many_to_many
        ]
        field_names = [field.name for field in field_objs]
        field_map = {field.name: field for field in field_objs}

        csv_rows: List[Dict[str, str]] = []
        objects_to_update = []
        changed_fields = set()

        for obj in queryset:
            updates, row = self._sanitize_object(
                obj,
                config,
                generator,
                field_objs,
                field_map,
            )
            csv_rows.append(row)
            if updates:
                for field_name, value in updates.items():
                    setattr(obj, field_name, value)
                objects_to_update.append(obj)
                changed_fields.update(updates.keys())

        filename = f"{config['slug']}_sanitised_{timestamp}.csv"
        file_path = outdir / filename
        self._write_csv(file_path, field_names, csv_rows)

        if should_write and objects_to_update and changed_fields:
            model.objects.bulk_update(objects_to_update, list(changed_fields))
            self.stdout.write(
                self.style.SUCCESS(
                    f"{model.__name__}: updated {len(objects_to_update)} records and wrote {file_path}."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{model.__name__}: processed {len(csv_rows)} records (export-only) -> {file_path}."
                )
            )

    def _select_records(self, model, record_limit: int):
        order_field = (
            "-created"
            if any(f.name == "created" for f in model._meta.get_fields())
            else "-pk"
        )
        return model.objects.order_by(order_field)[:record_limit]

    def _sanitize_object(
        self,
        obj,
        config: Dict,
        generator: FakeDataGenerator,
        field_objs: List[object],
        field_map: Dict[str, object],
    ) -> Tuple[Dict[str, str], Dict[str, object]]:
        updates: Dict[str, str] = {}
        row: Dict[str, object] = {}

        location_fields = [
            field for field in config.get("location_fields", []) if hasattr(obj, field)
        ]
        location_key = (
            tuple(getattr(obj, field, None) for field in location_fields)
            if location_fields
            else ()
        )
        location_values = (
            generator.fake_location(location_key) if location_fields else {}
        )

        for field in field_objs:
            field_name = field.name
            export_value = field.value_from_object(obj)
            original_value = getattr(obj, field.attname)

            if field_name in config.get("field_rules", {}) and field_name in field_map:
                rule = config["field_rules"][field_name]
                fake_value = generator.fake_value(
                    rule,
                    original_value,
                    getattr(field_map[field_name], "max_length", None),
                )
                if fake_value is not None and fake_value != original_value:
                    updates[field_name] = fake_value
                    export_value = fake_value
            elif field_name in location_fields:
                fake_value = location_values.get(field_name)
                if fake_value is not None and fake_value != original_value:
                    updates[field_name] = fake_value
                    export_value = fake_value

            row[field_name] = export_value

        return updates, row

    def _write_csv(
        self, file_path: Path, field_names: List[str], rows: List[Dict[str, object]]
    ):
        if not rows:
            with file_path.open("w", newline="") as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(field_names)
            return

        with file_path.open("w", newline="") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=field_names)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
