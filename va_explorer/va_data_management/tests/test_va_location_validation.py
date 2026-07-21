from importlib import import_module

import pytest
from django.apps import apps as django_apps

from va_explorer.tests.factories import VerbalAutopsyFactory
from va_explorer.va_data_management.models import CauseCodingIssue, Location
from va_explorer.va_data_management.utils.validate import validate_vas_for_dashboard

pytestmark = pytest.mark.django_db


def _raw_error_texts(va):
    return list(
        CauseCodingIssue.objects.filter(
            verbalautopsy=va,
            algorithm="",
            severity="error",
        ).values_list("text", flat=True)
    )


def _raw_issue(va, text, severity="error"):
    return CauseCodingIssue.objects.create(
        verbalautopsy=va,
        text=text,
        severity=severity,
        algorithm="",
        settings="",
    )


def test_community_va_ignores_facility_and_area_validation():
    unknown = Location.add_root(
        name="Unknown",
        key="unknown",
        location_type="facility",
        is_active=True,
    )
    va = VerbalAutopsyFactory.create(
        community_va="yes",
        cluster=None,
        constituency="Community Constituency",
        district="",
        ward="",
        ea="",
        area="",
        hospital="not-a-valid-facility",
        location=unknown,
    )

    validate_vas_for_dashboard([va])

    assert _raw_error_texts(va) == []


def test_facility_va_ignores_community_geography_validation():
    facility = Location.add_root(
        name="Facility A",
        key="facility-a",
        location_type="facility",
        is_active=True,
    )
    va = VerbalAutopsyFactory.create(
        community_va="no",
        location=facility,
        area="Facility Area",
        hospital="facility-a",
        cluster=None,
        district="",
        constituency="",
        ward="",
        ea="",
    )

    validate_vas_for_dashboard([va])

    assert _raw_error_texts(va) == []


def test_location_validation_accepts_historical_va_instances():
    va = VerbalAutopsyFactory.create(
        community_va="yes",
        cluster=None,
        constituency="Historical Community Constituency",
        district="",
        ward="",
        ea="",
        area="",
        hospital="",
        location=None,
    )

    validate_vas_for_dashboard([va.history.first()])

    assert _raw_error_texts(va) == []


def test_data_migration_removes_only_cross_mode_location_issues():
    facility = Location.add_root(
        name="Facility A",
        key="facility-a",
        location_type="facility",
        is_active=True,
    )
    community_va = VerbalAutopsyFactory.create(
        community_va="yes",
        district="Community District",
        location=facility,
    )
    facility_va = VerbalAutopsyFactory.create(
        community_va="no",
        area="Facility Area",
        hospital="facility-a",
        location=facility,
    )

    stale_community_issue = _raw_issue(
        community_va, "ERROR: no location provided (or none detected)"
    )
    applicable_community_issue = _raw_issue(
        community_va, "ERROR: unmatched EA/cluster for community VA"
    )
    stale_facility_issue = _raw_issue(
        facility_va, "ERROR: unmatched EA/cluster for community VA"
    )
    applicable_facility_issue = _raw_issue(
        facility_va, "ERROR: no location provided (or none detected)"
    )

    migration = import_module(
        "va_explorer.va_data_management.migrations."
        "0056_remove_cross_mode_va_location_issues"
    )
    migration.remove_cross_mode_location_issues(django_apps, schema_editor=None)

    remaining_ids = set(CauseCodingIssue.objects.values_list("id", flat=True))
    assert stale_community_issue.id not in remaining_ids
    assert stale_facility_issue.id not in remaining_ids
    assert applicable_community_issue.id in remaining_ids
    assert applicable_facility_issue.id in remaining_ids
