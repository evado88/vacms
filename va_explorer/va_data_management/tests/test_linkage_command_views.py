"""Tests for the link_deaths_to_vas command surface and the review UI."""

from io import StringIO

import pytest
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client

from va_explorer.tests.factories import GroupFactory, UserFactory
from va_explorer.users.models import UserActivity
from va_explorer.va_data_management.models import (
    MatchStatus,
    VADeathMatch,
    VADeathMatchRejection,
)

from .test_va_death_matching import _make_death, _make_va, _make_vasa

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# Command
# --------------------------------------------------------------------------
def test_calibrate_forbids_apply():
    with pytest.raises(CommandError, match="report-only"):
        call_command("link_deaths_to_vas", "--calibrate", "--apply")


def test_calibrate_reports_link_rate_vs_prior():
    _make_death()
    _make_va()
    out = StringIO()
    call_command("link_deaths_to_vas", "--calibrate", stdout=out)
    text = out.getvalue()
    assert "Expected prior" in text
    assert "Per-tool:" in text
    assert "Demoted previous links" in text
    assert VADeathMatch.objects.count() == 0  # report-only: no writes


def test_tool_flag_selects_vasa_only():
    _make_death()
    _make_va()
    _make_vasa(death_id="")
    out = StringIO()
    call_command("link_deaths_to_vas", "--tool", "vasa", stdout=out)
    assert "Total evaluated:      1" in out.getvalue()


def test_since_alias_filters_by_date():
    _make_death()
    _make_va()
    out = StringIO()
    call_command("link_deaths_to_vas", "--since", "2099-01-01", stdout=out)
    assert "Total evaluated:      0" in out.getvalue()


def test_apply_persists_and_is_idempotent():
    _make_death(DE_03="Unique Person")
    _make_va(Id10017="Unique", Id10018="Person")
    call_command("link_deaths_to_vas", "--apply")
    assert VADeathMatch.objects.count() == 1
    match = VADeathMatch.objects.first()
    assert match.comparisons  # per-field evidence snapshot persisted
    assert match.config_version == "default"
    history_before = match.history.count()
    call_command("link_deaths_to_vas", "--apply")
    match.refresh_from_db()
    assert match.history.count() == history_before  # no churn on re-run


# --------------------------------------------------------------------------
# Review UI
# --------------------------------------------------------------------------
@pytest.fixture
def view_group():
    perm = Permission.objects.get(codename="view_linkagereview")
    return GroupFactory.create(permissions=[perm])


@pytest.fixture
def review_group():
    perms = Permission.objects.filter(
        codename__in=["view_linkagereview", "review_linkage"]
    )
    return GroupFactory.create(permissions=list(perms))


@pytest.fixture
def pending_match():
    death = _make_death(DE_06="2024-03-05")  # ~2 months off -> review band
    va = _make_va(Id10023="2024-01-10")
    call_command("link_deaths_to_vas", "--apply")
    match = VADeathMatch.objects.get(verbal_autopsy=va)
    assert match.status == MatchStatus.PENDING_REVIEW
    return match, death, va


def _client_for(user):
    client = Client()
    client.force_login(user=user)
    return client


def test_index_requires_permission(pending_match):
    client = _client_for(UserFactory.create())
    response = client.get("/va_data_management/linkage-review")
    assert response.status_code == 403


def test_index_lists_pending_matches(view_group, pending_match):
    client = _client_for(UserFactory.create(groups=[view_group]))
    response = client.get("/va_data_management/linkage-review")
    assert response.status_code == 200
    assert b"Pending review" in response.content


def test_confirm_requires_review_permission(view_group, pending_match):
    match, _, _ = pending_match
    client = _client_for(UserFactory.create(groups=[view_group]))
    response = client.post(f"/va_data_management/linkage-review/{match.pk}/confirm")
    assert response.status_code == 403


def test_confirm_writes_reviewer_and_audit(review_group, pending_match):
    match, death, _ = pending_match
    user = UserFactory.create(groups=[review_group])
    client = _client_for(user)
    response = client.post(f"/va_data_management/linkage-review/{match.pk}/confirm")
    assert response.status_code == 302

    match.refresh_from_db()
    assert match.status == MatchStatus.MATCHED_MANUAL
    assert match.is_manual
    assert match.confirmed_by == user
    assert match.reviewed_by == user
    assert match.death_id == death.pk
    assert UserActivity.objects.filter(
        module="linkage_review", target_id=str(match.pk)
    ).exists()


def test_reject_records_pair_and_reevaluates(review_group, pending_match):
    match, death, va = pending_match
    user = UserFactory.create(groups=[review_group])
    client = _client_for(user)
    response = client.post(
        f"/va_data_management/linkage-review/{match.pk}/reject",
        {"reason": "different person"},
    )
    assert response.status_code == 302

    rejection = VADeathMatchRejection.objects.get(verbal_autopsy=va)
    assert rejection.death_id == death.pk
    assert rejection.rejected_by == user
    assert rejection.reason == "different person"

    # Re-evaluated immediately; the rejected pair is never re-proposed.
    match.refresh_from_db()
    assert not (match.death_id == death.pk and match.is_linked)
    assert UserActivity.objects.filter(
        module="linkage_review", details__icontains="Rejected"
    ).exists()
