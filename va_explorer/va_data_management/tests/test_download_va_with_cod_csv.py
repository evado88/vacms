import csv
from io import StringIO

import pytest
from django.core.management import call_command

from va_explorer.tests.factories import CauseOfDeathFactory, VerbalAutopsyFactory

pytestmark = pytest.mark.django_db


def _read_csv(path):
    with path.open(newline="", encoding="utf-8") as csv_file:
        return list(csv.DictReader(csv_file))


def test_download_va_with_cod_csv_exports_geopoint_components(tmp_path):
    va = VerbalAutopsyFactory.create(geopoint="-14.5 28.3 1200 5")
    CauseOfDeathFactory.create(verbalautopsy=va, cause="Old cause")
    CauseOfDeathFactory.create(verbalautopsy=va, cause="Latest cause")

    stdout = StringIO()
    call_command(
        "download_va_with_cod_csv",
        output_dir=str(tmp_path),
        filename="va_with_cod.csv",
        stdout=stdout,
    )

    rows = _read_csv(tmp_path / "va_with_cod.csv")
    assert len(rows) == 1
    row = rows[0]
    assert row["geopoint"] == "-14.5 28.3 1200 5"
    assert float(row["geopoint_latitude"]) == -14.5
    assert float(row["geopoint_longitude"]) == 28.3
    assert float(row["geopoint_altitude"]) == 1200.0
    assert float(row["geopoint_accuracy"]) == 5.0
    assert row["cod_cause"] == "Latest cause"
    assert "Geopoints: 1 parsed, 0 missing, 0 invalid." in stdout.getvalue()


def test_download_va_with_cod_csv_reports_missing_and_invalid_geopoints(tmp_path):
    blank_va = VerbalAutopsyFactory.create(geopoint="")
    invalid_va = VerbalAutopsyFactory.create(geopoint="not coordinates")
    CauseOfDeathFactory.create(verbalautopsy=blank_va)
    CauseOfDeathFactory.create(verbalautopsy=invalid_va)

    stdout = StringIO()
    call_command(
        "download_va_with_cod_csv",
        output_dir=str(tmp_path),
        filename="va_with_cod.csv",
        stdout=stdout,
    )

    rows = _read_csv(tmp_path / "va_with_cod.csv")
    assert len(rows) == 2
    for row in rows:
        assert row["geopoint_latitude"] == ""
        assert row["geopoint_longitude"] == ""
        assert row["geopoint_altitude"] == ""
        assert row["geopoint_accuracy"] == ""
    assert "Geopoints: 0 parsed, 1 missing, 1 invalid." in stdout.getvalue()
