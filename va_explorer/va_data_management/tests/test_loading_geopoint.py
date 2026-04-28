import numpy as np
import pandas
import pytest

from va_explorer.tests.factories import VerbalAutopsyFactory
from va_explorer.va_data_management.models import VerbalAutopsy
from va_explorer.va_data_management.utils.geopoint import compose_geopoint
from va_explorer.va_data_management.utils.loading import load_records_from_dataframe


def va_row(instanceid="geo-instance-1", instancename=None, **extra):
    row = {
        "instanceid": instanceid,
        "Id10017": "Geo",
        "Id10018": "Person",
        "Id10012": "2021-03-21",
        "Id10023": "2021-03-01",
        "instancename": instancename
        or f"_dec---geo person---2021-03-21---{instanceid}",
    }
    row.update(extra)
    return row


def load_single_row(row):
    return load_records_from_dataframe(pandas.DataFrame.from_records([row]))


def test_compose_geopoint_formats_full_odk_components():
    assert (
        compose_geopoint(
            {
                "geopoint-Latitude": -15.41,
                "geopoint-Longitude": 28.28,
                "geopoint-Altitude": 1200.0,
                "geopoint-Accuracy": 5.0,
            }
        )
        == "-15.41 28.28 1200 5"
    )


def test_compose_geopoint_allows_latitude_and_longitude_only():
    assert (
        compose_geopoint(
            {
                "geopoint-Latitude": "-15.41",
                "geopoint-Longitude": "28.28",
            }
        )
        == "-15.41 28.28"
    )


@pytest.mark.parametrize(
    "row",
    [
        {"geopoint-Latitude": "", "geopoint-Longitude": "28.28"},
        {"geopoint-Latitude": np.nan, "geopoint-Longitude": "28.28"},
        {"geopoint-Latitude": "null", "geopoint-Longitude": "28.28"},
        {"geopoint-Latitude": "not-a-number", "geopoint-Longitude": "28.28"},
        {"geopoint-Latitude": "-15.41", "geopoint-Longitude": "not-a-number"},
        None,
    ],
)
def test_compose_geopoint_returns_none_for_empty_or_bad_input(row):
    assert compose_geopoint(row) is None


@pytest.mark.django_db
def test_loading_geopoint_columns_populates_new_va():
    result = load_single_row(
        va_row(
            "geo-full-1",
            **{
                "geopoint-Latitude": -15.41,
                "geopoint-Longitude": 28.28,
                "geopoint-Altitude": 1200.0,
                "geopoint-Accuracy": 5.0,
            },
        )
    )

    va = VerbalAutopsy.objects.get(instanceid="geo-full-1")
    assert len(result["created"]) == 1
    assert va.geopoint == "-15.41 28.28 1200 5"


@pytest.mark.django_db
def test_loading_latitude_and_longitude_only_populates_new_va():
    load_single_row(
        va_row(
            "geo-partial-1",
            **{
                "geopoint-Latitude": -15.41,
                "geopoint-Longitude": 28.28,
            },
        )
    )

    va = VerbalAutopsy.objects.get(instanceid="geo-partial-1")
    assert va.geopoint == "-15.41 28.28"


@pytest.mark.django_db
def test_loading_blank_geopoint_components_leave_new_va_blank():
    result = load_single_row(
        va_row(
            "geo-blank-1",
            **{
                "geopoint-Latitude": np.nan,
                "geopoint-Longitude": "",
                "geopoint-Altitude": np.nan,
                "geopoint-Accuracy": "",
            },
        )
    )

    va = VerbalAutopsy.objects.get(instanceid="geo-blank-1")
    assert len(result["created"]) == 1
    assert va.geopoint == ""


@pytest.mark.django_db
def test_loading_second_import_enriches_existing_empty_geopoint_with_history():
    existing = VerbalAutopsyFactory.create(
        instanceid="geo-existing-empty-1",
        instancename="_dec---geo existing---2021-03-21",
        geopoint="",
    )
    initial_history_count = existing.history.count()

    result = load_single_row(
        va_row(
            "geo-existing-empty-1",
            instancename="_dec---geo existing---2021-03-21",
            **{
                "geopoint-Latitude": -15.41,
                "geopoint-Longitude": 28.28,
                "geopoint-Altitude": 1200,
                "geopoint-Accuracy": 5,
            },
        )
    )

    existing.refresh_from_db()
    assert existing.geopoint == "-15.41 28.28 1200 5"
    assert [va.pk for va in result["enriched"]] == [existing.pk]
    assert result["ignored"] == []
    assert existing.history.count() == initial_history_count + 1


@pytest.mark.django_db
def test_loading_second_import_does_not_overwrite_existing_geopoint():
    existing = VerbalAutopsyFactory.create(
        instanceid="geo-existing-full-1",
        instancename="_dec---geo existing full---2021-03-21",
        geopoint="-10 20 30 40",
    )
    initial_history_count = existing.history.count()

    result = load_single_row(
        va_row(
            "geo-existing-full-1",
            instancename="_dec---geo existing full---2021-03-21",
            **{
                "geopoint-Latitude": -15.41,
                "geopoint-Longitude": 28.28,
                "geopoint-Altitude": 1200,
                "geopoint-Accuracy": 5,
            },
        )
    )

    existing.refresh_from_db()
    assert existing.geopoint == "-10 20 30 40"
    assert [va.instanceid for va in result["ignored"]] == ["geo-existing-full-1"]
    assert result["enriched"] == []
    assert existing.history.count() == initial_history_count


@pytest.mark.django_db
def test_loading_edited_va_replacement_preserves_existing_geopoint():
    instancename = "_dec---geo edited---2021-03-21"
    VerbalAutopsyFactory.create(
        instanceid="geo-edited-old-1",
        instancename=instancename,
        geopoint="-10 20 30 40",
    )

    result = load_single_row(
        va_row(
            "geo-edited-new-1",
            instancename=instancename,
            **{
                "geopoint-Latitude": "",
                "geopoint-Longitude": np.nan,
                "geopoint-Altitude": "",
                "geopoint-Accuracy": np.nan,
            },
        )
    )

    assert len(result["created"]) == 1
    assert len(result["outdated"]) == 1
    assert not VerbalAutopsy.objects.filter(instanceid="geo-edited-old-1").exists()
    va = VerbalAutopsy.objects.get(instanceid="geo-edited-new-1")
    assert va.geopoint == "-10 20 30 40"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("latitude_column", "longitude_column"),
    [
        ("geopoint_latitude", "geopoint_longitude"),
        ("geopoint.Latitude", "geopoint.Longitude"),
    ],
)
def test_loading_geopoint_column_naming_variants(latitude_column, longitude_column):
    load_single_row(
        va_row(
            f"geo-variant-{latitude_column}",
            **{
                latitude_column: -15.41,
                longitude_column: 28.28,
            },
        )
    )

    va = VerbalAutopsy.objects.get(instanceid=f"geo-variant-{latitude_column}")
    assert va.geopoint == "-15.41 28.28"
