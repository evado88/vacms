from io import StringIO

import pandas as pd
import pytest
from django.core.management import call_command
from simple_history.utils import bulk_create_with_history

from va_explorer.tests.factories import VerbalAutopsyFactory
from va_explorer.va_data_management.models import (
    Death,
    ODKFormChoice,
    Pregnancy,
    PregnancyOutcome,
    VerbalAutopsy,
)
from va_explorer.va_data_management.odk.service import ODKPullService

pytestmark = pytest.mark.django_db


GPS_LOADERS = [
    ("load_pregnancy_csv", Pregnancy, "PE_25"),
    ("load_death_csv", Death, "DE_34"),
    ("load_pregnancy_outcome_csv", PregnancyOutcome, "gps"),
]


def create_with_history(model, **kwargs):
    created = bulk_create_with_history([model(**kwargs)], model)
    return model.objects.get(pk=created[0].pk)


def seed_definition(form_name):
    ODKFormChoice.objects.get_or_create(
        form_name=form_name,
        field_name="_seed",
        value="_seed",
        defaults={"label": "_seed"},
    )


def form_name_from_command(command_name):
    return command_name.removeprefix("load_").removesuffix("_csv")


def write_model_csv(tmp_path, command_name, gps_field, rows):
    path = tmp_path / f"{command_name}.csv"
    columns = ["key", gps_field, "enumerator"]
    if command_name == "load_pregnancy_outcome_csv":
        columns.append("ea")
    lines = [",".join(columns)]
    for row in rows:
        values = [
            row.get("key", ""),
            row.get(gps_field, ""),
            row.get("enumerator", ""),
        ]
        if command_name == "load_pregnancy_outcome_csv":
            values.append(row.get("ea", ""))
        lines.append(",".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_command(command_name, csv_path, *args):
    output = StringIO()
    command_args = list(args)
    if command_name == "load_pregnancy_outcome_csv" and "--log_missing_ea" not in args:
        command_args.extend(
            ["--log_missing_ea", str(csv_path.parent / "missing_ea.csv")]
        )
    call_command(
        command_name,
        str(csv_path),
        *command_args,
        stdout=output,
        stderr=output,
    )
    return output.getvalue()


@pytest.mark.parametrize(("command_name", "model", "gps_field"), GPS_LOADERS)
def test_no_upsert_keeps_existing_gps_untouched(
    tmp_path, command_name, model, gps_field
):
    seed_definition(form_name_from_command(command_name))
    existing = create_with_history(
        model,
        key="existing-1",
        **{gps_field: "-10 20", "enumerator": "Old Enumerator"},
    )
    history_count = existing.history.count()
    csv_path = write_model_csv(
        tmp_path,
        command_name,
        gps_field,
        [{"key": "existing-1", gps_field: "-14.5 28.3", "enumerator": "New"}],
    )

    output = run_command(command_name, csv_path)

    existing.refresh_from_db()
    assert getattr(existing, gps_field) == "-10 20"
    assert existing.enumerator == "Old Enumerator"
    assert existing.history.count() == history_count
    assert "skipped 1 existing" in output
    assert "Upsert:" not in output


@pytest.mark.parametrize(
    ("existing_gps", "incoming_gps", "expected_gps", "expected_updated"),
    [
        ("", "-14.5 28.3", "-14.5 28.3", 1),
        ("-10 20", "-14.5 28.3", "-14.5 28.3", 1),
        ("-10 20", "", "-10 20", 0),
        ("-10 20", "-10 20", "-10 20", 0),
    ],
)
@pytest.mark.parametrize(("command_name", "model", "gps_field"), GPS_LOADERS)
def test_upsert_gps_matrix(
    tmp_path,
    command_name,
    model,
    gps_field,
    existing_gps,
    incoming_gps,
    expected_gps,
    expected_updated,
):
    seed_definition(form_name_from_command(command_name))
    existing = create_with_history(
        model,
        key="existing-1",
        **{gps_field: existing_gps, "enumerator": "Old Enumerator"},
    )
    history_count = existing.history.count()
    csv_path = write_model_csv(
        tmp_path,
        command_name,
        gps_field,
        [{"key": "existing-1", gps_field: incoming_gps, "enumerator": "New"}],
    )

    output = run_command(command_name, csv_path, "--upsert")

    existing.refresh_from_db()
    assert getattr(existing, gps_field) == expected_gps
    assert existing.enumerator == "Old Enumerator"
    assert existing.history.count() == history_count + expected_updated
    assert f"updated {expected_updated} existing GPS values" in output


@pytest.mark.parametrize(("command_name", "model", "gps_field"), GPS_LOADERS)
def test_upsert_still_creates_new_rows(tmp_path, command_name, model, gps_field):
    seed_definition(form_name_from_command(command_name))
    csv_path = write_model_csv(
        tmp_path,
        command_name,
        gps_field,
        [{"key": "new-1", gps_field: "-14.5 28.3", "enumerator": "New"}],
    )

    output = run_command(command_name, csv_path, "--upsert")

    obj = model.objects.get(key="new-1")
    assert getattr(obj, gps_field) == "-14.5 28.3"
    assert "Imported 1" in output


@pytest.mark.parametrize(("command_name", "model", "gps_field"), GPS_LOADERS)
def test_dry_run_no_writes(tmp_path, command_name, model, gps_field):
    seed_definition(form_name_from_command(command_name))
    existing = create_with_history(model, key="existing-1", **{gps_field: "-10 20"})
    history_count = existing.history.count()
    csv_path = write_model_csv(
        tmp_path,
        command_name,
        gps_field,
        [
            {"key": "existing-1", gps_field: "-14.5 28.3"},
            {"key": "new-1", gps_field: "-15 29"},
        ],
    )

    output = run_command(command_name, csv_path, "--upsert", "--dry-run")

    existing.refresh_from_db()
    assert getattr(existing, gps_field) == "-10 20"
    assert existing.history.count() == history_count
    assert not model.objects.filter(key="new-1").exists()
    assert "[DRY RUN] Would import 1" in output
    assert "[DRY RUN] Upsert: would update 1 existing GPS values" in output
    assert "Sample of changes that would be applied:" in output


@pytest.mark.parametrize(("command_name", "model", "gps_field"), GPS_LOADERS)
def test_dry_run_summary_matches_real_run(tmp_path, command_name, model, gps_field):
    seed_definition(form_name_from_command(command_name))
    create_with_history(model, key="existing-1", **{gps_field: "-10 20"})
    csv_path = write_model_csv(
        tmp_path,
        command_name,
        gps_field,
        [
            {"key": "existing-1", gps_field: "-14.5 28.3"},
            {"key": "new-1", gps_field: "-15 29"},
        ],
    )

    dry_output = run_command(command_name, csv_path, "--upsert", "--dry-run")
    real_output = run_command(command_name, csv_path, "--upsert")

    assert "Would import 1" in dry_output
    assert "Imported 1" in real_output
    assert "would update 1 existing GPS values" in dry_output
    assert "updated 1 existing GPS values" in real_output


def write_va_csv(tmp_path, rows):
    path = tmp_path / "va.csv"
    columns = [
        "instanceid",
        "instancename",
        "Id10017",
        "Id10018",
        "Id10012",
        "Id10023",
        "geopoint",
    ]
    lines = [",".join(columns)]
    for row in rows:
        values = [
            row.get("instanceid", ""),
            row.get("instancename", f"_dec---geo person---{row['instanceid']}"),
            row.get("Id10017", "Geo"),
            row.get("Id10018", "Person"),
            row.get("Id10012", "2021-03-21"),
            row.get("Id10023", "2021-03-01"),
            row.get("geopoint", ""),
        ]
        lines.append(",".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_va_no_upsert_keeps_populated_geopoint(tmp_path):
    existing = VerbalAutopsyFactory.create(
        instanceid="va-existing-1",
        instancename="_dec---geo existing---2021-03-21",
        geopoint="-10 20",
    )
    history_count = existing.history.count()
    csv_path = write_va_csv(
        tmp_path,
        [
            {
                "instanceid": "va-existing-1",
                "instancename": "_dec---geo existing---2021-03-21",
                "geopoint": "-14.5 28.3",
            }
        ],
    )

    output = run_command("load_va_csv", csv_path)

    existing.refresh_from_db()
    assert existing.geopoint == "-10 20"
    assert existing.history.count() == history_count
    assert "1 ignored, 0 enriched" in output


def test_va_upsert_overwrites_populated_geopoint(tmp_path):
    existing = VerbalAutopsyFactory.create(
        instanceid="va-existing-2",
        instancename="_dec---geo existing 2---2021-03-21",
        geopoint="-10 20",
    )
    history_count = existing.history.count()
    csv_path = write_va_csv(
        tmp_path,
        [
            {
                "instanceid": "va-existing-2",
                "instancename": "_dec---geo existing 2---2021-03-21",
                "geopoint": "-14.5 28.3",
            }
        ],
    )

    output = run_command("load_va_csv", csv_path, "--upsert")

    existing.refresh_from_db()
    assert existing.geopoint == "-14.5 28.3"
    assert existing.history.count() == history_count + 1
    assert "Upsert: updated 1 existing GPS values" in output


def test_va_upsert_anti_clobber_skips_blank_incoming(tmp_path):
    existing = VerbalAutopsyFactory.create(
        instanceid="va-existing-3",
        instancename="_dec---geo existing 3---2021-03-21",
        geopoint="-10 20",
    )
    history_count = existing.history.count()
    csv_path = write_va_csv(
        tmp_path,
        [
            {
                "instanceid": "va-existing-3",
                "instancename": "_dec---geo existing 3---2021-03-21",
                "geopoint": "",
            }
        ],
    )

    output = run_command("load_va_csv", csv_path, "--upsert")

    existing.refresh_from_db()
    assert existing.geopoint == "-10 20"
    assert existing.history.count() == history_count
    assert "updated 0 existing GPS values" in output
    assert "1 incoming had no GPS value" in output


def test_va_dry_run_no_writes(tmp_path):
    existing = VerbalAutopsyFactory.create(
        instanceid="va-existing-4",
        instancename="_dec---geo existing 4---2021-03-21",
        geopoint="-10 20",
    )
    history_count = existing.history.count()
    csv_path = write_va_csv(
        tmp_path,
        [
            {
                "instanceid": "va-existing-4",
                "instancename": "_dec---geo existing 4---2021-03-21",
                "geopoint": "-14.5 28.3",
            },
            {"instanceid": "va-new-1", "geopoint": "-15 29"},
        ],
    )

    output = run_command("load_va_csv", csv_path, "--upsert", "--dry-run")

    existing.refresh_from_db()
    assert existing.geopoint == "-10 20"
    assert existing.history.count() == history_count
    assert not VerbalAutopsy.objects.filter(instanceid="va-new-1").exists()
    assert "[DRY RUN] Would load 1 verbal autopsies from CSV" in output
    assert "[DRY RUN] Upsert: would update 1 existing GPS values" in output


def test_va_dry_run_summary_matches_real_run(tmp_path):
    VerbalAutopsyFactory.create(
        instanceid="va-existing-5",
        instancename="_dec---geo existing 5---2021-03-21",
        geopoint="-10 20",
    )
    csv_path = write_va_csv(
        tmp_path,
        [
            {
                "instanceid": "va-existing-5",
                "instancename": "_dec---geo existing 5---2021-03-21",
                "geopoint": "-14.5 28.3",
            },
            {"instanceid": "va-new-2", "geopoint": "-15 29"},
        ],
    )

    dry_output = run_command("load_va_csv", csv_path, "--upsert", "--dry-run")
    real_output = run_command("load_va_csv", csv_path, "--upsert")

    assert "Would load 1 verbal autopsies" in dry_output
    assert "Loaded 1 verbal autopsies" in real_output
    assert "would update 1 existing GPS values" in dry_output
    assert "updated 1 existing GPS values" in real_output


def test_va_upsert_does_not_touch_soft_deleted_va(tmp_path):
    deleted = VerbalAutopsyFactory.create(
        instanceid="va-deleted-1",
        instancename="_dec---geo deleted---2021-03-21",
        geopoint="-10 20",
    )
    deleted.delete()
    csv_path = write_va_csv(
        tmp_path,
        [
            {
                "instanceid": "va-deleted-1",
                "instancename": "_dec---geo deleted---2021-03-21",
                "geopoint": "-14.5 28.3",
            }
        ],
    )

    run_command("load_va_csv", csv_path, "--upsert")

    deleted = VerbalAutopsy.all_objects.get(pk=deleted.pk)
    assert deleted.geopoint == "-10 20"
    assert VerbalAutopsy.objects.filter(
        instanceid="va-deleted-1", geopoint="-14.5 28.3"
    ).exists()


class TestScheduledODKVAUpsert:
    def test_va_geopoint_overwritten_on_scheduled_pull(self):
        existing = VerbalAutopsyFactory.create(
            instanceid="odk-va-1",
            instancename="_dec---odk va 1---2021-03-21",
            geopoint="-10 20",
        )
        df = pd.DataFrame.from_records(
            [
                {
                    "instanceid": "odk-va-1",
                    "instancename": "_dec---odk va 1---2021-03-21",
                    "Id10017": "Geo",
                    "Id10018": "Person",
                    "Id10012": "2021-03-21",
                    "Id10023": "2021-03-01",
                    "geopoint": "-14.5 28.3",
                }
            ]
        )

        counts = ODKPullService().upsert_into_models(df, form_name="verbal_autopsy")

        existing.refresh_from_db()
        assert existing.geopoint == "-14.5 28.3"
        assert counts["enriched"] == 1

    def test_va_geopoint_anti_clobber_on_scheduled_pull(self):
        existing = VerbalAutopsyFactory.create(
            instanceid="odk-va-2",
            instancename="_dec---odk va 2---2021-03-21",
            geopoint="-10 20",
        )
        df = pd.DataFrame.from_records(
            [
                {
                    "instanceid": "odk-va-2",
                    "instancename": "_dec---odk va 2---2021-03-21",
                    "Id10017": "Geo",
                    "Id10018": "Person",
                    "Id10012": "2021-03-21",
                    "Id10023": "2021-03-01",
                    "geopoint": "",
                }
            ]
        )

        counts = ODKPullService().upsert_into_models(df, form_name="verbal_autopsy")

        existing.refresh_from_db()
        assert existing.geopoint == "-10 20"
        assert counts["enriched"] == 0

    def test_soft_deleted_va_not_touched_on_scheduled_pull(self):
        deleted = VerbalAutopsyFactory.create(
            instanceid="odk-va-deleted-1",
            instancename="_dec---odk va deleted---2021-03-21",
            geopoint="-10 20",
        )
        deleted.delete()
        df = pd.DataFrame.from_records(
            [
                {
                    "instanceid": "odk-va-deleted-1",
                    "instancename": "_dec---odk va deleted---2021-03-21",
                    "Id10017": "Geo",
                    "Id10018": "Person",
                    "Id10012": "2021-03-21",
                    "Id10023": "2021-03-01",
                    "geopoint": "-14.5 28.3",
                }
            ]
        )

        ODKPullService().upsert_into_models(df, form_name="verbal_autopsy")

        deleted = VerbalAutopsy.all_objects.get(pk=deleted.pk)
        assert deleted.geopoint == "-10 20"
