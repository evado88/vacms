from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from django.core.management import CommandError, call_command

from va_explorer.va_data_management.models import (
    Death,
    ODKFormChoice,
    Pregnancy,
    PregnancyOutcome,
    PregnancyOutcomeBaby,
)
from va_explorer.va_data_management.odk.service import import_dataframe_records

pytestmark = pytest.mark.django_db


def make_definition(tmpdir):
    survey = pd.DataFrame(
        {
            "type": ["select_one yesno"],
            "name": ["consent"],
            "label": ["Consent"],
        }
    )
    choices = pd.DataFrame(
        {
            "list_name": ["yesno", "yesno"],
            "name": ["1", "0"],
            "label": ["Yes", "No"],
        }
    )
    xls_path = Path(tmpdir) / "def.xlsx"
    with pd.ExcelWriter(xls_path) as writer:
        survey.to_excel(writer, sheet_name="survey", index=False)
        choices.to_excel(writer, sheet_name="choices", index=False)
    return xls_path


def test_import_definition_and_csv(tmp_path):
    definition = make_definition(tmp_path)
    call_command("load_pregnancy_definition", str(definition))
    assert ODKFormChoice.objects.count() == 2

    csv_path = Path(tmp_path) / "data.csv"
    csv_path.write_text("consent\n1\n")
    call_command("load_pregnancy_csv", str(csv_path))
    preg = Pregnancy.objects.get()
    assert preg.consent == "Yes"


def test_death_import_definition_and_csv(tmp_path):
    definition = make_definition(tmp_path)
    call_command("load_death_definition", str(definition))
    assert ODKFormChoice.objects.filter(form_name="death").count() == 2

    csv_path = Path(tmp_path) / "data.csv"
    csv_path.write_text("consent\n1\n")
    call_command("load_death_csv", str(csv_path))
    death = Death.objects.get()
    assert death.consent == "Yes"


def test_pregnancy_outcome_import_definition_and_csv(tmp_path):
    definition = make_definition(tmp_path)
    call_command("load_pregnancy_outcome_definition", str(definition))
    assert ODKFormChoice.objects.filter(form_name="pregnancy_outcome").count() == 2

    csv_path = Path(tmp_path) / "data.csv"
    csv_path.write_text("consent\n1\n")
    call_command("load_pregnancy_outcome_csv", str(csv_path))
    outcome = PregnancyOutcome.objects.get()
    assert outcome.consent == "Yes"


def test_import_without_definition(tmp_path):
    csv_path = Path(tmp_path) / "data.csv"
    csv_path.write_text("consent\n1\n")
    with pytest.raises(CommandError):
        call_command("load_pregnancy_csv", str(csv_path))


def test_death_import_without_definition(tmp_path):
    csv_path = Path(tmp_path) / "data.csv"
    csv_path.write_text("consent\n1\n")
    with pytest.raises(CommandError):
        call_command("load_death_csv", str(csv_path))


def test_pregnancy_outcome_import_without_definition(tmp_path):
    csv_path = Path(tmp_path) / "data.csv"
    csv_path.write_text("consent\n1\n")
    with pytest.raises(CommandError):
        call_command("load_pregnancy_outcome_csv", str(csv_path))


def test_pregnancy_outcome_baby_details_linking_and_missing_parent_skip(tmp_path):
    definition = make_definition(tmp_path)
    call_command("load_pregnancy_outcome_definition", str(definition))

    parent_csv = Path(tmp_path) / "pregnancy_outcome.csv"
    parent_csv.write_text("key,consent\nparent-1,1\n")
    call_command("load_pregnancy_outcome_csv", str(parent_csv))
    assert PregnancyOutcome.objects.filter(key="parent-1").exists()

    baby_csv = Path(tmp_path) / "pregnancy_outcome_baby_details.csv"
    baby_csv.write_text(
        "PARENT_KEY,KEY,PO-49A,PO-49B,PO-49C\n"
        "parent-1,parent-1/baby[1],Baby 1,First Child,01\n"
        "missing-parent,missing-parent/baby[1],Baby X,Orphan Child,02\n"
    )

    call_command(
        "load_po_baby_details",
        str(baby_csv),
        "--log_dir",
        str(tmp_path),
    )

    assert PregnancyOutcomeBaby.objects.count() == 1
    baby = PregnancyOutcomeBaby.objects.get()
    assert baby.pregnancy_outcome.key == "parent-1"
    assert baby.PO_49B == "First Child"


# ---------------------------------------------------------------------------
# GPS geopoint merging — CSV and pyODK import paths
# ---------------------------------------------------------------------------


def _make_service_df(form_name, row):
    """Build a minimal DataFrame as import_dataframe_records expects."""
    return pd.DataFrame.from_records([row])


def _seed_odk_definition(form_name):
    """Create a minimal ODKFormChoice entry so CSV-load commands pass the 'definition loaded' guard."""
    ODKFormChoice.objects.get_or_create(
        form_name=form_name,
        field_name="_seed",
        value="_seed",
        defaults={"label": "_seed"},
    )


_FORM_MODELS = {
    "pregnancy": Pregnancy,
    "death": Death,
    "pregnancy_outcome": PregnancyOutcome,
}


@pytest.mark.parametrize(
    ("form_name", "lat_col", "lon_col", "alt_col", "acc_col", "gps_field"),
    [
        (
            "pregnancy",
            "PE-25-Latitude",
            "PE-25-Longitude",
            "PE-25-Altitude",
            "PE-25-Accuracy",
            "PE_25",
        ),
        (
            "death",
            "DE-34-Latitude",
            "DE-34-Longitude",
            "DE-34-Altitude",
            "DE-34-Accuracy",
            "DE_34",
        ),
        (
            "pregnancy_outcome",
            "gps-Latitude",
            "gps-Longitude",
            "gps-Altitude",
            "gps-Accuracy",
            "gps",
        ),
    ],
)
def test_csv_gps_columns_merged_to_single_field(
    tmp_path, form_name, lat_col, lon_col, alt_col, acc_col, gps_field
):
    _seed_odk_definition(form_name)

    csv_path = tmp_path / "data.csv"
    # pregnancy_outcome CSV loader requires an 'ea' column for cluster lookup
    ea_col = "ea," if form_name == "pregnancy_outcome" else ""
    ea_val = "," if form_name == "pregnancy_outcome" else ""
    csv_path.write_text(
        f"key,{ea_col}{lat_col},{lon_col},{alt_col},{acc_col}\n"
        f"k1,{ea_val}-14.5,28.3,1242,5\n"
    )
    call_command(f"load_{form_name}_csv", str(csv_path))

    obj = _FORM_MODELS[form_name].objects.get(key="k1")
    assert getattr(obj, gps_field) == "-14.5 28.3 1242 5"


@pytest.mark.parametrize(
    ("form_name", "lat_col", "lon_col", "gps_field"),
    [
        ("pregnancy", "PE-25-Latitude", "PE-25-Longitude", "PE_25"),
        ("death", "DE-34-Latitude", "DE-34-Longitude", "DE_34"),
        ("pregnancy_outcome", "gps-Latitude", "gps-Longitude", "gps"),
    ],
)
def test_csv_partial_gps_lat_lon_only(tmp_path, form_name, lat_col, lon_col, gps_field):
    _seed_odk_definition(form_name)

    csv_path = tmp_path / "data.csv"
    ea_extra = ",ea" if form_name == "pregnancy_outcome" else ""
    ea_val = "," if form_name == "pregnancy_outcome" else ""
    csv_path.write_text(f"key{ea_extra},{lat_col},{lon_col}\nk2{ea_val},-14.5,28.3\n")
    call_command(f"load_{form_name}_csv", str(csv_path))

    obj = _FORM_MODELS[form_name].objects.get(key="k2")
    assert getattr(obj, gps_field) == "-14.5 28.3"


@pytest.mark.parametrize(
    ("form_name", "lat_col", "lon_col", "gps_field"),
    [
        ("pregnancy", "PE-25-Latitude", "PE-25-Longitude", "PE_25"),
        ("death", "DE-34-Latitude", "DE-34-Longitude", "DE_34"),
        ("pregnancy_outcome", "gps-Latitude", "gps-Longitude", "gps"),
    ],
)
def test_csv_blank_gps_leaves_field_empty(
    tmp_path, form_name, lat_col, lon_col, gps_field
):
    _seed_odk_definition(form_name)

    csv_path = tmp_path / "data.csv"
    ea_extra = ",ea" if form_name == "pregnancy_outcome" else ""
    ea_val = "," if form_name == "pregnancy_outcome" else ""
    csv_path.write_text(f"key{ea_extra},{lat_col},{lon_col}\nk3{ea_val},,\n")
    call_command(f"load_{form_name}_csv", str(csv_path))

    obj = _FORM_MODELS[form_name].objects.get(key="k3")
    assert not getattr(obj, gps_field)


def test_service_pregnancy_gps_merge():
    """import_dataframe_records composes PE-25-* columns into PE_25."""
    df = _make_service_df(
        "pregnancy",
        {
            "key": "svc-p1",
            "PE-25-Latitude": -14.5,
            "PE-25-Longitude": 28.3,
            "PE-25-Altitude": 1242.0,
            "PE-25-Accuracy": 5.0,
        },
    )
    counts = import_dataframe_records("pregnancy", df)
    obj = Pregnancy.objects.get(key="svc-p1")
    assert obj.PE_25 == "-14.5 28.3 1242 5"
    assert counts["created"] == 1


def test_service_death_gps_merge():
    """import_dataframe_records composes DE-34-* columns into DE_34."""
    df = _make_service_df(
        "death",
        {
            "key": "svc-d1",
            "DE-34-Latitude": -14.5,
            "DE-34-Longitude": 28.3,
        },
    )
    import_dataframe_records("death", df)
    obj = Death.objects.get(key="svc-d1")
    assert obj.DE_34 == "-14.5 28.3"


def test_service_pregnancy_outcome_gps_merge():
    """import_dataframe_records composes gps-* columns into gps."""
    df = _make_service_df(
        "pregnancy_outcome",
        {
            "key": "svc-po1",
            "gps-Latitude": -14.5,
            "gps-Longitude": 28.3,
            "gps-Altitude": 1242.0,
            "gps-Accuracy": 5.0,
        },
    )
    import_dataframe_records("pregnancy_outcome", df)
    obj = PregnancyOutcome.objects.get(key="svc-po1")
    assert obj.gps == "-14.5 28.3 1242 5"


def test_merge_geopoint_into_anti_clobber():
    """merge_geopoint_into does not overwrite a pre-existing GPS value with blank GPS."""
    from va_explorer.va_data_management.utils.geopoint import merge_geopoint_into

    df = pd.DataFrame(
        [
            {
                "key": "clobber-1",
                "PE_25": "-10 20 30 40",
                "PE-25-Latitude": np.nan,
                "PE-25-Longitude": np.nan,
            }
        ]
    )
    result = merge_geopoint_into(df, source_prefix="PE-25", target_field="PE_25")
    assert result.loc[0, "PE_25"] == "-10 20 30 40"


def test_merge_geopoint_into_retrospective_enrichment():
    """merge_geopoint_into fills in an empty GPS field from split columns."""
    from va_explorer.va_data_management.utils.geopoint import merge_geopoint_into

    df = pd.DataFrame(
        [
            {
                "key": "enrich-1",
                "PE_25": None,
                "PE-25-Latitude": -14.5,
                "PE-25-Longitude": 28.3,
            }
        ]
    )
    result = merge_geopoint_into(df, source_prefix="PE-25", target_field="PE_25")
    assert result.loc[0, "PE_25"] == "-14.5 28.3"


def test_find_geopoint_columns_bare_fallback():
    """Bare component names match when no prefixed columns exist (post-strip ODK pull)."""
    from va_explorer.va_data_management.utils.geopoint import (
        find_geopoint_columns_for_prefix,
    )

    cols = ["key", "Latitude", "Longitude", "Altitude", "Accuracy"]
    found = find_geopoint_columns_for_prefix(cols, "PE-25")
    assert found == {
        "latitude": "Latitude",
        "longitude": "Longitude",
        "altitude": "Altitude",
        "accuracy": "Accuracy",
    }


def test_find_geopoint_columns_prefixed_wins_over_bare():
    """Prefixed columns take precedence; bare fallback only fires when none match."""
    from va_explorer.va_data_management.utils.geopoint import (
        find_geopoint_columns_for_prefix,
    )

    cols = ["key", "PE-25-Latitude", "PE-25-Longitude", "Latitude", "Longitude"]
    found = find_geopoint_columns_for_prefix(cols, "PE-25")
    assert found == {"latitude": "PE-25-Latitude", "longitude": "PE-25-Longitude"}


@pytest.mark.parametrize(
    ("form_name", "gps_field"),
    [
        ("pregnancy", "PE_25"),
        ("death", "DE_34"),
        ("pregnancy_outcome", "gps"),
    ],
)
def test_odk_pull_stripped_gps_columns_merged(form_name, gps_field):
    """GPS survives the full pull pipeline including list_submissions' prefix strip.

    list_submissions renames columns via c.rsplit("-", 1)[-1], turning
    PE-25-Latitude into bare Latitude before import_dataframe_records runs.
    Regression test: this used to leave the GPS field empty for every
    ODK-pulled record.
    """
    prefix = {"pregnancy": "PE-25", "death": "DE-34", "pregnancy_outcome": "gps"}[
        form_name
    ]
    df = _make_service_df(
        form_name,
        {
            "key": f"strip-{form_name}",
            f"{prefix}-Latitude": -14.5,
            f"{prefix}-Longitude": 28.3,
            f"{prefix}-Altitude": 1242.0,
            f"{prefix}-Accuracy": 5.0,
        },
    )
    # Same strip list_submissions applies to every pulled DataFrame.
    df.columns = [c.rsplit("-", 1)[-1] for c in df.columns]
    import_dataframe_records(form_name, df)
    obj = _FORM_MODELS[form_name].objects.get(key=f"strip-{form_name}")
    assert getattr(obj, gps_field) == "-14.5 28.3 1242 5"
