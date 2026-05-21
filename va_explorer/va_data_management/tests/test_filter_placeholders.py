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


def test_death_filter_placeholders():
    form = DeathFilter().form
    for name, placeholder in [
        ("district", "District"),
        ("ward", "Ward"),
        ("enumerator", "Enumerator"),
        ("deceased", "Deceased"),
    ]:
        _assert_placeholder(form, name, placeholder)
    # Date labels kept (renamed last task), no colon stripping needed at field level.
    assert form.fields["start_date"].label == "Earliest DOD"
    assert form.fields["end_date"].label == "Latest DOD"


def test_pregnancy_filter_placeholders():
    form = PregnancyFilter().form
    for name, placeholder in [
        ("district", "District"),
        ("ward", "Ward"),
        ("enumerator", "Enumerator"),
        ("id", "ID"),
    ]:
        _assert_placeholder(form, name, placeholder)


def test_household_filter_placeholders():
    form = HouseholdFilter().form
    for name, placeholder in [
        ("district", "District"),
        ("ward", "Ward"),
        ("enumerator", "Enumerator"),
        ("id", "ID"),
        ("supervisor", "Supervisor"),
    ]:
        _assert_placeholder(form, name, placeholder)


def test_pregnancy_outcome_filter_placeholders_and_delivery_labels():
    form = PregnancyOutcomeFilter().form
    for name, placeholder in [
        ("district", "District"),
        ("ward", "Ward"),
        ("enumerator", "Enumerator"),
        ("outcome_type", "Pregnancy Outcome"),
    ]:
        _assert_placeholder(form, name, placeholder)
    assert form.fields["start_date"].label == "Earliest Delivery"
    assert form.fields["end_date"].label == "Latest Delivery"


def test_va_filter_placeholders():
    form = VAFilter().form
    for name, placeholder in [
        ("interviewer", "Interviewer"),
        ("deceased", "Deceased"),
        ("district", "District"),
        ("cause", "Cause"),
    ]:
        _assert_placeholder(form, name, placeholder)
    # Non-placeholder fields keep their labels.
    assert form.fields["start_date"].label == "Earliest Date"
    assert form.fields["only_errors"].label == "Only Errors"
