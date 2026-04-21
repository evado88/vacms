from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from va_explorer.home.views import RegionalOperationsComponentContextMixin
from va_explorer.va_data_management.models import CSASupervisorTargetMap
from va_explorer.va_data_management.utils.csa_names import (
    format_csa_display_name,
    normalize_csa_name_for_join,
)

pytestmark = pytest.mark.django_db


def _write_csv(path, content):
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_csa_name_normalization_matches_dashboard_logic():
    raw = "  Jane_Doe_123  "
    display = format_csa_display_name(raw)

    assert display == RegionalOperationsComponentContextMixin._format_csa_display_name(
        raw
    )
    assert normalize_csa_name_for_join(display) == "jane doe"


def test_load_csa_target_supervisor_map_imports_updates_and_handles_duplicates(
    tmp_path,
):
    first_csv = _write_csv(
        tmp_path / "csa_map_1.csv",
        "CSA,Supervisor,CSA Target\n"
        " Alice_1 , Supervisor A , 10 \n"
        "Alice 1,Supervisor B,11\n"
        "Bob, Supervisor C , not_a_number\n",
    )

    stdout = StringIO()
    call_command("load_csa_target_supervisor_map", first_csv, stdout=stdout)
    output = stdout.getvalue()

    assert "created=2" in output
    assert "updated=0" in output
    assert "duplicate_rows=1" in output

    assert CSASupervisorTargetMap.objects.count() == 2

    alice = CSASupervisorTargetMap.objects.get(csa_name_normalized="alice")
    assert alice.csa_display_name == "Alice"
    assert alice.supervisor_name == "Supervisor B"
    assert alice.csa_target == 11

    bob = CSASupervisorTargetMap.objects.get(csa_name_normalized="bob")
    assert bob.csa_target is None

    second_csv = _write_csv(
        tmp_path / "csa_map_2.csv",
        "CSA,Supervisor,CSA Target\n"
        "Alice 2,Supervisor X,15\n"
        "Bob,Supervisor Y,9\n",
    )

    stdout2 = StringIO()
    call_command("load_csa_target_supervisor_map", second_csv, stdout=stdout2)
    output2 = stdout2.getvalue()

    assert "created=0" in output2
    assert "updated=2" in output2

    alice.refresh_from_db()
    bob.refresh_from_db()
    assert alice.csa_display_name == "Alice"
    assert alice.supervisor_name == "Supervisor X"
    assert alice.csa_target == 15
    assert bob.supervisor_name == "Supervisor Y"
    assert bob.csa_target == 9


def test_load_csa_target_supervisor_map_validates_required_columns(tmp_path):
    bad_csv = _write_csv(
        tmp_path / "bad.csv",
        "CSA,Supervisor\nAlice,Supervisor A\n",
    )

    with pytest.raises(CommandError):
        call_command("load_csa_target_supervisor_map", bad_csv)
