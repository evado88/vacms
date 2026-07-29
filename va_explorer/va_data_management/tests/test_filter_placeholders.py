import pytest

from va_explorer.va_data_management.filters import (
    DeathFilter,
    HouseholdFilter,
    PregnancyFilter,
    PregnancyOutcomeFilter,
    VAFilter,
)

pytestmark = pytest.mark.django_db


def _assert_placeholder(form, name, placeholder):
    field = form.fields[name]
    assert field.label == "", f"{name} should have an empty label"
    assert field.widget.attrs.get("placeholder") == placeholder
    assert field.widget.attrs.get("aria-label") == placeholder


def _assert_date_placeholder(form, name, label, placeholder):
    field = form.fields[name]
    assert field.label == label
    assert field.widget.attrs.get("placeholder") == placeholder
    assert field.widget.attrs.get("aria-label") == placeholder
    assert field_widget_type(form, name) == "date"


def field_widget_type(form, name):
    return form.fields[name].widget.input_type


def test_death_filter_placeholders():
    form = DeathFilter().form
    for name, placeholder in [
        ("enumerator", "Enumerator"),
        ("district", "District"),
        ("ward", "Ward"),
        ("ea", "EA"),
        ("deceased", "Deceased"),
    ]:
        _assert_placeholder(form, name, placeholder)
    assert list(form.fields.keys()) == [
        "enumerator",
        "district",
        "ward",
        "ea",
        "deceased",
        "start_date",
        "end_date",
    ]
    _assert_date_placeholder(form, "start_date", "Earliest", "Earliest Date")
    _assert_date_placeholder(form, "end_date", "Latest", "Latest Date")


def test_pregnancy_filter_placeholders():
    form = PregnancyFilter().form
    for name, placeholder in [
        ("id", "ID"),
        ("enumerator", "Enumerator"),
        ("district", "District"),
        ("ward", "Ward"),
    ]:
        _assert_placeholder(form, name, placeholder)
    assert list(form.fields.keys()) == [
        "id",
        "enumerator",
        "district",
        "ward",
        "start_date",
        "end_date",
    ]
    _assert_date_placeholder(form, "start_date", "Earliest", "Earliest Date")
    _assert_date_placeholder(form, "end_date", "Latest", "Latest Date")


def test_household_filter_placeholders():
    form = HouseholdFilter().form
    for name, placeholder in [
        ("id", "ID"),
        ("enumerator", "Enumerator"),
        ("district", "District"),
        ("ward", "Ward"),
    ]:
        _assert_placeholder(form, name, placeholder)
    assert list(form.fields.keys()) == ["id", "enumerator", "district", "ward"]
    assert "supervisor" not in form.fields


def test_pregnancy_outcome_filter_placeholders_and_delivery_labels():
    form = PregnancyOutcomeFilter().form
    for name, placeholder in [
        ("enumerator", "Enumerator"),
        ("district", "District"),
        ("ward", "Ward"),
        ("outcome_type", "Pregnancy Outcome"),
    ]:
        _assert_placeholder(form, name, placeholder)
    assert list(form.fields.keys()) == [
        "enumerator",
        "district",
        "ward",
        "outcome_type",
        "start_date",
        "end_date",
    ]
    _assert_date_placeholder(form, "start_date", "Earliest", "Earliest Date")
    _assert_date_placeholder(form, "end_date", "Latest", "Latest Date")


def test_va_filter_placeholders():
    form = VAFilter().form
    for name, placeholder in [
        ("interviewer", "Interviewer"),
        ("deceased", "Deceased"),
        ("district", "District"),
        ("ward", "Ward"),
        ("cause", "Cause"),
    ]:
        _assert_placeholder(form, name, placeholder)
    # Non-placeholder fields keep their labels.
    _assert_date_placeholder(form, "start_date", "Earliest", "Earliest Date")
    _assert_date_placeholder(form, "end_date", "Latest", "Latest Date")
    assert "only_errors" not in form.fields
