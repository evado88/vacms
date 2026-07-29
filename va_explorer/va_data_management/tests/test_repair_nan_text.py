import pytest
from django.core.management import call_command
from django.utils import timezone

from va_explorer.va_data_management.models import Death, Pregnancy

pytestmark = pytest.mark.django_db


def _make_death(**kwargs):
    """Create a Death without tripping the post_save Celery signal.

    Death.post_save queues assign_death_ids against a broker the test suite
    cannot reach, so bulk_create is the only safe construction path here.
    See tasks/lessons.md.
    """
    (death,) = Death.objects.bulk_create([Death(**kwargs)])
    return Death.objects.get(key=kwargs["key"])


def test_repair_replaces_nan_with_null():
    _make_death(key="uuid:corrupt", DE_03="Alice Banda", DE_15="nan", DE_22="nan")

    call_command("repair_nan_text")

    death = Death.objects.get(key="uuid:corrupt")
    assert death.DE_15 is None
    assert death.DE_22 is None
    assert death.DE_03 == "Alice Banda"


def test_repair_is_case_insensitive():
    _make_death(key="uuid:case", DE_15="NaN", DE_22="NAN")

    call_command("repair_nan_text")

    death = Death.objects.get(key="uuid:case")
    assert death.DE_15 is None
    assert death.DE_22 is None


def test_repair_leaves_real_values_alone():
    """999 is an enumerator convention for refused/unknown, not corruption."""
    _make_death(
        key="uuid:real",
        DE_08="999",
        DE_03="Nancy Chumbwa",
        DE_15="Banana",
        DE_22="none",
    )

    call_command("repair_nan_text")

    death = Death.objects.get(key="uuid:real")
    assert death.DE_08 == "999"
    assert death.DE_03 == "Nancy Chumbwa"
    assert death.DE_15 == "Banana"
    # "none"/"null" are deliberately out of scope -- only "nan" is repaired.
    assert death.DE_22 == "none"


def test_dry_run_writes_nothing(capsys):
    _make_death(key="uuid:dry", DE_15="nan")

    call_command("repair_nan_text", "--dry-run")

    assert Death.objects.get(key="uuid:dry").DE_15 == "nan"
    assert "DRY RUN" in capsys.readouterr().out


def test_repair_is_idempotent():
    _make_death(key="uuid:twice", DE_15="nan")

    call_command("repair_nan_text")
    call_command("repair_nan_text")

    assert Death.objects.get(key="uuid:twice").DE_15 is None


def test_repair_cleans_history_rows():
    """A stale history row would resurrect 'nan' on revert, and shows in the UI."""
    death = _make_death(key="uuid:hist", DE_15="ok")
    death.DE_15 = "nan"
    death.save_without_historical_record()
    Death.history.model.objects.create(
        id=death.id,
        key=death.key,
        DE_15="nan",
        history_type="~",
        history_date=timezone.now(),
    )

    call_command("repair_nan_text")

    assert not Death.history.model.objects.filter(DE_15__iexact="nan").exists()


def test_repair_blanks_non_nullable_fields_with_empty_string():
    """Pregnancy has NOT NULL text columns; writing None there would blow up."""
    non_nullable = [
        field.name
        for field in Pregnancy._meta.fields
        if field.get_internal_type() in {"TextField", "CharField"} and not field.null
    ]
    assert non_nullable, "expected Pregnancy to have NOT NULL text columns"
    field_name = non_nullable[0]

    Pregnancy.objects.bulk_create(
        [Pregnancy(key="uuid:preg-nan", **{field_name: "nan"})]
    )

    call_command("repair_nan_text")

    preg = Pregnancy.objects.get(key="uuid:preg-nan")
    assert getattr(preg, field_name) == ""


def test_model_option_scopes_the_repair():
    _make_death(key="uuid:scoped", DE_15="nan")
    Pregnancy.objects.bulk_create([Pregnancy(key="uuid:preg-scoped", PE_02="nan")])

    call_command("repair_nan_text", "--model", "Pregnancy")

    assert Death.objects.get(key="uuid:scoped").DE_15 == "nan"
    assert Pregnancy.objects.get(key="uuid:preg-scoped").PE_02 is None
