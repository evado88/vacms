"""Tests for the link_deaths_to_vas command surface and the review UI."""

from io import StringIO

import pytest
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client

from va_explorer.tests.factories import GroupFactory, LocationFactory, UserFactory
from va_explorer.users.models import UserActivity
from va_explorer.va_data_management.models import (
    MatchStatus,
    VADeathMatch,
    VADeathMatchDecision,
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


def test_dry_run_and_apply_are_mutually_exclusive():
    with pytest.raises(CommandError, match="mutually exclusive"):
        call_command("link_deaths_to_vas", "--dry-run", "--apply")


def test_shadow_mode_forbids_apply():
    with pytest.raises(CommandError, match="shadow"):
        call_command("link_deaths_to_vas", "--mode", "shadow", "--apply")


@pytest.mark.parametrize(("option", "value"), [("--limit", "0"), ("--batch-size", "0")])
def test_command_rejects_nonpositive_limits(option, value):
    with pytest.raises(CommandError, match="positive"):
        call_command("link_deaths_to_vas", option, value)


def test_command_rejects_unknown_config_name():
    with pytest.raises(CommandError, match="does not exist"):
        call_command("link_deaths_to_vas", "--config", "missing-revision")


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
    match, _, _ = pending_match
    match.score_separation = 8.9
    match.alternatives = [{"death_id": "next-best", "score": 41.6}]
    match.save(update_fields=["score_separation", "alternatives"])

    client = _client_for(UserFactory.create(groups=[view_group]))
    response = client.get("/va_data_management/linkage-review")
    assert response.status_code == 200
    assert b"<title>Death-VA Linkage</title>" in response.content
    assert b"Death-VA Linkage" in response.content
    assert b">Deceased<" in response.content
    assert b"<th>Death ID</th>" in response.content
    assert b"<th>Next Best</th>" in response.content
    assert b"<th>Review Route</th>" in response.content
    assert b"<th>Matching Fields</th>" in response.content
    assert b"<th>Tool</th>" not in response.content
    assert b">41.6<" in response.content
    assert b">8.9<" not in response.content
    assert b"Review mode" not in response.content
    assert b"Reason" not in response.content


def test_review_detail_is_scoped_to_user_locations(view_group):
    loc_a = LocationFactory.create(
        name="Province A", location_type="province", depth=1, path="0001"
    )
    loc_b = LocationFactory.create(
        name="Province B", location_type="province", depth=1, path="0002"
    )
    va_a = _make_va(location=loc_a, province="Province A")
    va_b = _make_va(location=loc_b, province="Province B")
    match_a = VADeathMatch.objects.create(
        verbal_autopsy=va_a,
        death=_make_death(key="a"),
        status=MatchStatus.PENDING_REVIEW,
    )
    match_b = VADeathMatch.objects.create(
        verbal_autopsy=va_b,
        death=_make_death(key="b"),
        status=MatchStatus.PENDING_REVIEW,
    )
    user = UserFactory.create(groups=[view_group], location_restrictions=[loc_a])
    client = _client_for(user)

    assert (
        client.get(f"/va_data_management/linkage-review/{match_a.pk}").status_code
        == 200
    )
    assert (
        client.get(f"/va_data_management/linkage-review/{match_b.pk}").status_code
        == 404
    )


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
    assert match.comparisons
    assert VADeathMatchDecision.objects.filter(
        match=match, action=VADeathMatchDecision.Action.CONFIRM, actor=user
    ).exists()
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
    assert VADeathMatchDecision.objects.filter(
        match=match, action=VADeathMatchDecision.Action.REJECT, actor=user
    ).exists()

    # Re-evaluated immediately; the rejected pair is never re-proposed.
    match.refresh_from_db()
    assert not (match.death_id == death.pk and match.is_linked)
    assert UserActivity.objects.filter(
        module="linkage_review", details__icontains="Rejected"
    ).exists()


def test_confirming_alternative_recomputes_evidence(review_group, pending_match):
    match, _, _ = pending_match
    alternative = _make_death(DE_03="Jane Phiri", key="alternative")
    match.alternatives = [
        {
            "death_pk": alternative.pk,
            "death_id": alternative.death_id,
            "score": 70,
            "method": "probabilistic",
        }
    ]
    match.save(update_fields=["alternatives"])
    user = UserFactory.create(groups=[review_group])

    response = _client_for(user).post(
        f"/va_data_management/linkage-review/{match.pk}/confirm",
        {
            "death_pk": alternative.pk,
            "decision_version": match.decision_version,
            "reason": "better evidence",
        },
    )

    assert response.status_code == 302
    match.refresh_from_db()
    assert match.death_id == alternative.pk
    name_comparison = next(c for c in match.comparisons if c["field"] == "name")
    assert name_comparison["death_value"] == "Jane Phiri"
    assert VADeathMatchDecision.objects.filter(
        match=match, action=VADeathMatchDecision.Action.REASSIGN
    ).exists()


def test_stale_decision_version_does_not_mutate_match(review_group, pending_match):
    match, _, _ = pending_match
    user = UserFactory.create(groups=[review_group])

    response = _client_for(user).post(
        f"/va_data_management/linkage-review/{match.pk}/confirm",
        {"decision_version": match.decision_version + 1},
    )

    assert response.status_code == 302
    match.refresh_from_db()
    assert match.status == MatchStatus.PENDING_REVIEW
    assert not VADeathMatchDecision.objects.filter(match=match).exists()


def test_unlink_clears_link_and_records_decision(review_group, pending_match):
    match, _, _ = pending_match
    user = UserFactory.create(groups=[review_group])
    client = _client_for(user)
    client.post(f"/va_data_management/linkage-review/{match.pk}/confirm")
    match.refresh_from_db()
    prior_comparisons = match.comparisons
    prior_score = match.score

    response = client.post(
        f"/va_data_management/linkage-review/{match.pk}/unlink",
        {"reason": "linked in error", "decision_version": match.decision_version},
    )

    assert response.status_code == 302
    match.refresh_from_db()
    assert match.status == MatchStatus.UNMATCHED
    assert match.death_id is None
    decision = VADeathMatchDecision.objects.get(
        match=match,
        action=VADeathMatchDecision.Action.UNLINK,
        reason="linked in error",
    )
    assert decision.evidence["score"] == prior_score
    assert decision.evidence["comparisons"] == prior_comparisons


def test_review_redirect_rejects_external_next(review_group, pending_match):
    match, _, _ = pending_match
    user = UserFactory.create(groups=[review_group])
    response = _client_for(user).post(
        f"/va_data_management/linkage-review/{match.pk}/confirm",
        {"next": "https://attacker.example/phish"},
    )
    assert response.status_code == 302
    assert response["Location"] == "/va_data_management/linkage-review"
