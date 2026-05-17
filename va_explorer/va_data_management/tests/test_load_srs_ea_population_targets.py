from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from va_explorer.va_data_management.models import SRSEAPopulationTarget

pytestmark = pytest.mark.django_db

COMMAND = "load_srs_ea_population_targets"

HEADER = [
    "s/n",
    "PROV_NAME",
    "PROV_CODE",
    "DIST_NAME",
    "CONST_NAME",
    "WARD_NAME",
    "DIST_CODE",
    "CONST_CODE",
    "WARD_CODE",
    "REG",
    "Urban/Rural",
    "GEOID",
    "POP",
    "HH",
    "COMMENTS",
]


def make_row(geoid="01021011002", pop="694", hh="126", **overrides):
    row = {
        "s/n": "1",
        "PROV_NAME": "CENTRAL",
        "PROV_CODE": "01",
        "DIST_NAME": "CHISAMBA",
        "CONST_NAME": "CHISAMBA",
        "WARD_NAME": "CHAMUKA",
        "DIST_CODE": "0102",
        "CONST_CODE": "01021",
        "WARD_CODE": "0102101",
        "REG": "1",
        "Urban/Rural": "Rural",
        "GEOID": geoid,
        "POP": pop,
        "HH": hh,
        "COMMENTS": "OKAY",
    }
    row.update(overrides)
    return row


def write_csv(tmp_path, rows, *, header=HEADER, bom=False, name="srs.csv"):
    lines = [",".join(header)]
    for row in rows:
        lines.append(",".join(str(row.get(col, "")) for col in header))
    path = tmp_path / name
    # utf-8-sig writes a leading BOM, matching the real source file.
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig" if bom else "utf-8")
    return path


def run(csv_path, *args):
    output = StringIO()
    call_command(COMMAND, str(csv_path), *args, stdout=output, stderr=output)
    return output.getvalue()


def test_initial_import_creates_rows(tmp_path):
    csv_path = write_csv(
        tmp_path,
        [
            make_row(geoid="01021011002", pop="694", hh="126"),
            make_row(geoid="01021011004", pop="466", hh="120"),
        ],
    )

    output = run(csv_path)

    assert SRSEAPopulationTarget.objects.count() == 2
    assert "created=2" in output
    obj = SRSEAPopulationTarget.objects.get(geoid="01021011002")
    assert obj.population_target == 694
    assert obj.household_target == 126
    assert obj.ward_name == "CHAMUKA"
    assert obj.rural_urban == "Rural"
    assert obj.source_filename == "srs.csv"


def test_reimport_upserts_without_duplicating(tmp_path):
    write = lambda pop: write_csv(  # noqa: E731
        tmp_path, [make_row(geoid="01021011002", pop=pop)]
    )

    run(write("694"))
    output = run(write("750"))

    assert SRSEAPopulationTarget.objects.count() == 1
    assert "created=0" in output
    assert "updated=1" in output
    assert (
        SRSEAPopulationTarget.objects.get(geoid="01021011002").population_target == 750
    )


def test_intrafile_duplicate_geoid_keeps_last(tmp_path):
    csv_path = write_csv(
        tmp_path,
        [
            make_row(geoid="03151041001", pop="451", hh="106"),
            make_row(geoid="03151041001", pop="999", hh="200"),
        ],
    )

    output = run(csv_path)

    assert SRSEAPopulationTarget.objects.count() == 1
    assert "intrafile_duplicates=1" in output
    obj = SRSEAPopulationTarget.objects.get(geoid="03151041001")
    assert obj.population_target == 999
    assert obj.household_target == 200


def test_blank_geoid_rows_are_skipped(tmp_path):
    csv_path = write_csv(
        tmp_path,
        [
            make_row(geoid="01021011002"),
            make_row(geoid="   "),
        ],
    )

    output = run(csv_path)

    assert SRSEAPopulationTarget.objects.count() == 1
    assert "skipped_blank_geoid=1" in output


def test_invalid_population_rows_are_skipped(tmp_path):
    csv_path = write_csv(
        tmp_path,
        [
            make_row(geoid="01021011002", pop="694"),
            make_row(geoid="01021011004", pop="not-a-number"),
            make_row(geoid="01021011006", hh=""),
        ],
    )

    output = run(csv_path)

    assert SRSEAPopulationTarget.objects.count() == 1
    assert "skipped_invalid=2" in output


def test_dry_run_writes_nothing(tmp_path):
    csv_path = write_csv(tmp_path, [make_row(geoid="01021011002")])

    output = run(csv_path, "--dry-run")

    assert SRSEAPopulationTarget.objects.count() == 0
    assert output.startswith("[DRY RUN]")
    assert "created=1" in output


def test_bom_and_leading_zeros_are_preserved(tmp_path):
    csv_path = write_csv(
        tmp_path,
        [make_row(geoid="01021011002", **{"PROV_CODE": "1"})],
        bom=True,
    )

    run(csv_path)

    obj = SRSEAPopulationTarget.objects.get()
    # GEOID keeps its leading zero; province code is zero-padded to 2 digits.
    assert obj.geoid == "01021011002"
    assert obj.province_code == "01"


def test_missing_required_column_raises(tmp_path):
    header = [col for col in HEADER if col != "GEOID"]
    csv_path = write_csv(tmp_path, [make_row()], header=header)

    with pytest.raises(CommandError, match="GEOID"):
        run(csv_path)


def test_malformed_geoid_imported_with_warning(tmp_path):
    csv_path = write_csv(tmp_path, [make_row(geoid="123")])

    output = run(csv_path)

    assert SRSEAPopulationTarget.objects.filter(geoid="123").exists()
    assert "not 11 digits" in output


def test_non_positive_batch_size_raises(tmp_path):
    csv_path = write_csv(tmp_path, [make_row()])

    with pytest.raises(CommandError, match="batch-size"):
        run(csv_path, "--batch-size", "0")
