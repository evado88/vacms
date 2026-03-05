from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from va_explorer.tests.factories import VerbalAutopsyFactory
from va_explorer.va_data_management.models import CauseOfDeath

pytestmark = pytest.mark.django_db


def test_upload_causeofdeath_csv_accepts_python_dict_settings(tmp_path: Path):
    va_one = VerbalAutopsyFactory.create()
    va_two = VerbalAutopsyFactory.create()

    csv_path = tmp_path / "causeofdeath.csv"
    csv_path.write_text(
        "cause,algorithm,settings,created,updated,verbalautopsy_id\n"
        "Stroke,InterVA5,\"{'HIV': 'v', 'api': 'True'}\",2026-02-20T05:05:53.998965+00:00,2026-02-20T05:05:53.999000+00:00,"
        f"{va_one.id}\n"
        "Diabetes mellitus,InterVA5,\"{'HIV': 'l', 'Malaria': 'l'}\",2026-02-20T05:05:53.999032+00:00,2026-02-20T05:05:53.999040+00:00,"
        f"{va_two.id}\n",
        encoding="utf-8",
    )

    call_command("upload_causeofdeath_csv", str(csv_path))

    causes = CauseOfDeath.objects.order_by("id")
    assert causes.count() == 2
    assert causes[0].verbalautopsy_id == va_one.id
    assert causes[0].cause == "Stroke"
    assert causes[0].settings == {"HIV": "v", "api": "True"}
    assert causes[1].verbalautopsy_id == va_two.id
    assert causes[1].cause == "Diabetes mellitus"
    assert causes[1].settings == {"HIV": "l", "Malaria": "l"}


def test_upload_causeofdeath_csv_accepts_float_style_verbalautopsy_id(tmp_path: Path):
    va = VerbalAutopsyFactory.create()

    csv_path = tmp_path / "causeofdeath.csv"
    csv_path.write_text(
        "cause,algorithm,settings,created,updated,verbalautopsy_id\n"
        "Stroke,InterVA5,\"{'HIV': 'v'}\",2026-02-20T05:05:53.998965+00:00,2026-02-20T05:05:53.999000+00:00,"
        f"{va.id}.0\n",
        encoding="utf-8",
    )

    call_command("upload_causeofdeath_csv", str(csv_path))

    assert CauseOfDeath.objects.count() == 1
    assert CauseOfDeath.objects.first().verbalautopsy_id == va.id


def test_upload_causeofdeath_csv_dry_run_does_not_insert(tmp_path: Path):
    va = VerbalAutopsyFactory.create()

    csv_path = tmp_path / "causeofdeath.csv"
    csv_path.write_text(
        "cause,algorithm,settings,created,updated,verbalautopsy_id\n"
        "Stroke,InterVA5,\"{'HIV': 'v'}\",2026-02-20T05:05:53.998965+00:00,2026-02-20T05:05:53.999000+00:00,"
        f"{va.id}\n",
        encoding="utf-8",
    )

    stdout = StringIO()
    call_command("upload_causeofdeath_csv", str(csv_path), "--dry-run", stdout=stdout)

    assert CauseOfDeath.objects.count() == 0
    assert "Dry run enabled: no rows were inserted." in stdout.getvalue()


def test_upload_causeofdeath_csv_upserts_on_verbalautopsy_and_algorithm(tmp_path: Path):
    va = VerbalAutopsyFactory.create()

    first_csv = tmp_path / "causeofdeath_first.csv"
    first_csv.write_text(
        "cause,algorithm,settings,created,updated,verbalautopsy_id\n"
        f"Stroke,InterVA5,\"{{'HIV': 'v'}}\",2026-02-20T05:05:53.998965+00:00,2026-02-20T05:05:53.999000+00:00,{va.id}\n",
        encoding="utf-8",
    )
    call_command("upload_causeofdeath_csv", str(first_csv))

    second_csv = tmp_path / "causeofdeath_second.csv"
    second_csv.write_text(
        "cause,algorithm,settings,created,updated,verbalautopsy_id\n"
        f"Diabetes mellitus,InterVA5,\"{{'HIV': 'l'}}\",2026-02-21T05:05:53.998965+00:00,2026-02-21T05:05:53.999000+00:00,{va.id}\n",
        encoding="utf-8",
    )
    call_command("upload_causeofdeath_csv", str(second_csv))

    assert CauseOfDeath.objects.count() == 1
    cod = CauseOfDeath.objects.get(verbalautopsy_id=va.id, algorithm="InterVA5")
    assert cod.cause == "Diabetes mellitus"
    assert cod.settings == {"HIV": "l"}


def test_upload_causeofdeath_csv_allows_multiple_algorithms_for_same_va(tmp_path: Path):
    va = VerbalAutopsyFactory.create()

    csv_path = tmp_path / "causeofdeath.csv"
    csv_path.write_text(
        "cause,algorithm,settings,created,updated,verbalautopsy_id\n"
        f"Stroke,InterVA5,\"{{'HIV': 'v'}}\",2026-02-20T05:05:53.998965+00:00,2026-02-20T05:05:53.999000+00:00,{va.id}\n"
        f"Stroke,SmartVA,\"{{'HIV': 'v'}}\",2026-02-20T05:05:53.998965+00:00,2026-02-20T05:05:53.999000+00:00,{va.id}\n",
        encoding="utf-8",
    )

    call_command("upload_causeofdeath_csv", str(csv_path))

    assert CauseOfDeath.objects.filter(verbalautopsy_id=va.id).count() == 2
