from pathlib import Path

import pytest
from django.core.management import call_command

from va_explorer.va_data_management.models import Household, ODKFormChoice

pytestmark = pytest.mark.django_db


def test_load_household_csv_skips_duplicate_keys(tmp_path):
    """Ensure duplicate keys (in CSV or DB) are skipped before insert."""

    # Minimal ODK definition so the command can run
    ODKFormChoice.objects.create(
        form_name="household", field_name="consent", value="1", label="Yes"
    )

    Household.objects.create(key="existing", consent="Yes")

    csv_path = Path(tmp_path) / "households.csv"
    csv_path.write_text(
        "key,consent\n" "existing,1\n" "duplicate,1\n" "duplicate,1\n" "unique,0\n"
    )

    call_command("load_household_csv", str(csv_path))

    keys = set(Household.objects.values_list("key", flat=True))
    assert keys == {"existing", "duplicate", "unique"}
    assert Household.objects.count() == 3
