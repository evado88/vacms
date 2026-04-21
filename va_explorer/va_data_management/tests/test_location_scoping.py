"""
Parametrized integration tests for location-scoped queryset access.

Each test creates a user restricted to a specific location level (province,
district, ward) and asserts that the queryset for VA, Death, Pregnancy, and
PregnancyOutcome returns only matching rows — never rows from another location.

These tests must stay green before and after any caching changes in Tier 4 so
that cache-key bugs can't silently leak data across users.
"""

import pytest
from va_explorer.tests.factories import (
    DeathFactory,
    LocationFactory,
    PregnancyFactory,
    PregnancyOutcomeFactory,
    UserFactory,
    VerbalAutopsyFactory,
)
from va_explorer.va_data_management.models import (
    Death,
    Pregnancy,
    PregnancyOutcome,
    VerbalAutopsy,
)
from va_explorer.va_data_management.utils.location_access import (
    restrict_queryset_to_user_locations,
    restrict_va_queryset_to_user_locations,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_province_location(name="Lusaka"):
    return LocationFactory.create(name=name, location_type="province", depth=1, path="0001")


def _make_district_location(name="Lusaka District", province_name="Lusaka"):
    return LocationFactory.create(
        name=name,
        location_type="district",
        depth=2,
        path="00010001",
    )


def _user_restricted_to(location):
    """Return a user whose location_restrictions is set to *location*."""
    user = UserFactory.create(location_restrictions=[location])
    # Clear per-request cache so tests are independent
    user._allowed_location_names_cache = None
    return user


# ---------------------------------------------------------------------------
# VerbalAutopsy scoping (via location FK)
# ---------------------------------------------------------------------------

class TestVALocationScoping:
    def test_unrestricted_user_sees_all_vas(self):
        VerbalAutopsyFactory.create()
        VerbalAutopsyFactory.create()
        user = UserFactory.create()
        qs = restrict_va_queryset_to_user_locations(VerbalAutopsy.objects.all(), user)
        assert qs.count() == 2

    def test_restricted_user_sees_only_own_location_vas(self):
        loc_a = _make_province_location("Province A")
        loc_b = LocationFactory.create(name="Province B", location_type="province", depth=1, path="0002")
        va_a = VerbalAutopsyFactory.create(location=loc_a, province="Province A")
        VerbalAutopsyFactory.create(location=loc_b, province="Province B")

        user = _user_restricted_to(loc_a)
        qs = restrict_va_queryset_to_user_locations(VerbalAutopsy.objects.all(), user)
        pks = list(qs.values_list("pk", flat=True))
        assert va_a.pk in pks
        assert qs.count() == 1

    def test_restricted_user_sees_no_vas_when_no_match(self):
        loc_a = _make_province_location("Province A")
        loc_b = LocationFactory.create(name="Province B", location_type="province", depth=1, path="0002")
        VerbalAutopsyFactory.create(location=loc_b, province="Province B")

        user = _user_restricted_to(loc_a)
        qs = restrict_va_queryset_to_user_locations(VerbalAutopsy.objects.all(), user)
        assert qs.count() == 0


# ---------------------------------------------------------------------------
# Death scoping (via text geography fields)
# ---------------------------------------------------------------------------

DEATH_LOCATION_LEVELS = ["province", "district", "ward"]


@pytest.mark.parametrize("level", DEATH_LOCATION_LEVELS)
def test_death_scoping_at_level(level):
    """A user restricted to a location at *level* sees only Deaths in that area."""
    location = LocationFactory.create(name="TestArea", location_type=level, depth=1, path="0001")
    match_kwargs = {level: "TestArea"}
    other_kwargs = {level: "OtherArea"}

    death_match = DeathFactory.create(**match_kwargs)
    DeathFactory.create(**other_kwargs)

    user = _user_restricted_to(location)
    qs = restrict_queryset_to_user_locations(Death.objects.all(), user)
    pks = list(qs.values_list("pk", flat=True))
    assert death_match.pk in pks
    assert qs.count() == 1


def test_death_unrestricted_user_sees_all():
    DeathFactory.create(province="P1")
    DeathFactory.create(province="P2")
    user = UserFactory.create()
    qs = restrict_queryset_to_user_locations(Death.objects.all(), user)
    assert qs.count() == 2


def test_death_case_insensitive_match():
    location = LocationFactory.create(name="lusaka", location_type="province", depth=1, path="0001")
    death = DeathFactory.create(province="Lusaka")
    user = _user_restricted_to(location)
    qs = restrict_queryset_to_user_locations(Death.objects.all(), user)
    assert death.pk in list(qs.values_list("pk", flat=True))


# ---------------------------------------------------------------------------
# Pregnancy scoping
# ---------------------------------------------------------------------------

PREGNANCY_LOCATION_LEVELS = ["province", "district", "ward"]


@pytest.mark.parametrize("level", PREGNANCY_LOCATION_LEVELS)
def test_pregnancy_scoping_at_level(level):
    location = LocationFactory.create(name="TestArea", location_type=level, depth=1, path="0001")
    match_kwargs = {level: "TestArea"}
    other_kwargs = {level: "OtherArea"}

    preg_match = PregnancyFactory.create(**match_kwargs)
    PregnancyFactory.create(**other_kwargs)

    user = _user_restricted_to(location)
    qs = restrict_queryset_to_user_locations(Pregnancy.objects.all(), user)
    pks = list(qs.values_list("pk", flat=True))
    assert preg_match.pk in pks
    assert qs.count() == 1


def test_pregnancy_unrestricted_user_sees_all():
    PregnancyFactory.create(province="P1")
    PregnancyFactory.create(province="P2")
    user = UserFactory.create()
    qs = restrict_queryset_to_user_locations(Pregnancy.objects.all(), user)
    assert qs.count() == 2


# ---------------------------------------------------------------------------
# PregnancyOutcome scoping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", ["province", "district", "ward"])
def test_pregnancy_outcome_scoping_at_level(level):
    location = LocationFactory.create(name="TestArea", location_type=level, depth=1, path="0001")
    match_kwargs = {level: "TestArea"}
    other_kwargs = {level: "OtherArea"}

    po_match = PregnancyOutcomeFactory.create(**match_kwargs)
    PregnancyOutcomeFactory.create(**other_kwargs)

    user = _user_restricted_to(location)
    qs = restrict_queryset_to_user_locations(PregnancyOutcome.objects.all(), user)
    pks = list(qs.values_list("pk", flat=True))
    assert po_match.pk in pks
    assert qs.count() == 1


def test_pregnancy_outcome_unrestricted_user_sees_all():
    PregnancyOutcomeFactory.create(province="P1")
    PregnancyOutcomeFactory.create(province="P2")
    user = UserFactory.create()
    qs = restrict_queryset_to_user_locations(PregnancyOutcome.objects.all(), user)
    assert qs.count() == 2


# ---------------------------------------------------------------------------
# Cross-user isolation — verify one user's cache doesn't bleed into another's
# ---------------------------------------------------------------------------

def test_two_users_see_different_deaths():
    loc_a = LocationFactory.create(name="Copperbelt", location_type="province", depth=1, path="0001")
    loc_b = LocationFactory.create(name="Eastern", location_type="province", depth=1, path="0002")

    death_a = DeathFactory.create(province="Copperbelt")
    death_b = DeathFactory.create(province="Eastern")

    user_a = _user_restricted_to(loc_a)
    user_b = _user_restricted_to(loc_b)

    qs_a = restrict_queryset_to_user_locations(Death.objects.all(), user_a)
    qs_b = restrict_queryset_to_user_locations(Death.objects.all(), user_b)

    assert list(qs_a.values_list("pk", flat=True)) == [death_a.pk]
    assert list(qs_b.values_list("pk", flat=True)) == [death_b.pk]
