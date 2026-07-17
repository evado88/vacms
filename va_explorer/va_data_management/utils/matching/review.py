"""Transactional human decisions for VA-to-Death linkage."""

from copy import deepcopy

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from va_explorer.va_data_management.models import (
    Death,
    MatchMethod,
    MatchStatus,
    VADeathMatch,
    VADeathMatchDecision,
    VADeathMatchRejection,
)

from .config import resolve_config, validate_config
from .profiles import build_death_profile, build_va_profile
from .scoring import score_candidate
from .service import auto_link_verbal_autopsy_to_death


def _assert_version(match, expected_version):
    if expected_version in (None, ""):
        return
    try:
        expected_version = int(expected_version)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Invalid linkage decision version.") from exc
    if match.decision_version != expected_version:
        raise ValidationError(
            "This match changed after the page was loaded. Review the latest evidence."
        )


def _config_for(match):
    if match.config_snapshot:
        return validate_config(deepcopy(match.config_snapshot))
    return resolve_config(match.config_version)


def _death_snapshot(death):
    return getattr(death, "death_id", None) if death is not None else None


def _record_decision(match, *, action, actor, prior_death, new_death, reason):
    va = match.va
    return VADeathMatchDecision.objects.create(
        match=match,
        va_model=va._meta.model_name,
        va_pk=va.pk,
        va_identifier=getattr(va, "instanceid", None) or getattr(va, "key", None),
        action=action,
        prior_death=prior_death,
        new_death=new_death,
        prior_death_id_snapshot=_death_snapshot(prior_death),
        new_death_id_snapshot=_death_snapshot(new_death),
        actor=actor,
        reason=reason or None,
        evidence={
            "status": match.status,
            "method": match.method,
            "score": match.score,
            "comparisons": deepcopy(match.comparisons),
            "alternatives": deepcopy(match.alternatives),
        },
        config_snapshot=deepcopy(match.config_snapshot),
        config_version=match.config_version,
        crosswalk_version=match.crosswalk_version,
    )


def _locked_match(match_pk):
    # Lock only the match row. PostgreSQL rejects FOR UPDATE across nullable
    # outer joins (both VA-side FKs are nullable by design).
    return VADeathMatch.objects.select_for_update().get(pk=match_pk)


@transaction.atomic
def confirm_match(match_pk, death_pk, *, actor, reason="", expected_version=None):
    """Confirm the proposal or one of its recorded alternatives."""
    match = _locked_match(match_pk)
    _assert_version(match, expected_version)

    allowed_deaths = {match.death_id} if match.death_id else set()
    allowed_deaths.update(
        alternative.get("death_pk")
        for alternative in match.alternatives
        if alternative.get("death_pk")
    )
    death_pk = death_pk or match.death_id
    if death_pk is None:
        raise ValidationError("No proposed Death record is available to confirm.")
    try:
        death_pk = int(death_pk)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Invalid Death record.") from exc
    if death_pk not in allowed_deaths:
        raise ValidationError("The selected Death is not a recorded candidate.")

    death = Death.objects.get(pk=death_pk)
    prior_death = match.death
    if prior_death is not None and prior_death.pk != death.pk and not reason.strip():
        raise ValidationError("A reason is required to reassign a link.")
    config = _config_for(match)
    candidate = score_candidate(
        match.va,
        death,
        method=MatchMethod.MANUAL,
        config=config,
        va_profile=build_va_profile(match.va),
        death_profile=build_death_profile(death),
        block_sources=["manual_review"],
    )

    match.death = death
    match.death_id_snapshot = death.death_id
    match.status = MatchStatus.MATCHED_MANUAL
    match.method = MatchMethod.MANUAL
    match.score = candidate.score
    match.score_separation = 0
    match.supporting_fields = candidate.supporting_fields
    match.contradictory_fields = candidate.contradictory_fields
    match.missing_fields = candidate.missing_fields
    match.comparisons = [comparison.as_dict() for comparison in candidate.comparisons]
    match.block_sources = candidate.block_sources
    match.is_manual = True
    match.confirmed_by = actor
    match.reviewed_by = actor
    match.matched_at = timezone.now()
    match.last_evaluated_at = timezone.now()
    match.rejected_reason = None
    match.review_reason = None
    match.review_mode = None
    match.last_error = {}
    match.source = "review_confirm"
    match.decision_version += 1
    match.save()

    action = (
        VADeathMatchDecision.Action.REASSIGN
        if prior_death is not None and prior_death.pk != death.pk
        else VADeathMatchDecision.Action.CONFIRM
    )
    _record_decision(
        match,
        action=action,
        actor=actor,
        prior_death=prior_death,
        new_death=death,
        reason=reason,
    )
    return match


@transaction.atomic
def reject_match(match_pk, *, actor, reason="", expected_version=None):
    """Reject the current pair and surface the next candidate in review-only mode."""
    match = _locked_match(match_pk)
    _assert_version(match, expected_version)
    if match.death is None:
        raise ValidationError("No proposed Death record is available to reject.")

    prior_death = match.death
    pair = {"death": prior_death}
    if match.vasa_id:
        pair["vasa"] = match.vasa
    else:
        pair["verbal_autopsy"] = match.verbal_autopsy
    rejection, created = VADeathMatchRejection.objects.get_or_create(
        defaults={"rejected_by": actor, "reason": reason or None}, **pair
    )
    if not created and (rejection.rejected_by_id is None or reason):
        rejection.rejected_by = rejection.rejected_by or actor
        rejection.reason = reason or rejection.reason
        rejection.save(update_fields=["rejected_by", "reason"])

    match.status = MatchStatus.REJECTED
    match.is_manual = False
    match.reviewed_by = actor
    match.rejected_reason = reason or None
    match.review_reason = None
    match.review_mode = None
    match.decision_version += 1
    match.save()
    _record_decision(
        match,
        action=VADeathMatchDecision.Action.REJECT,
        actor=actor,
        prior_death=prior_death,
        new_death=None,
        reason=reason,
    )

    result = auto_link_verbal_autopsy_to_death(
        match.va,
        save=True,
        source="review_reject",
        review_only=True,
    )
    match.refresh_from_db()
    return match, result


@transaction.atomic
def unlink_match(match_pk, *, actor, reason="", expected_version=None):
    """Remove a human or automatic link while preserving an immutable event."""
    match = _locked_match(match_pk)
    _assert_version(match, expected_version)
    if not reason.strip():
        raise ValidationError("A reason is required to unlink a record.")
    if match.death is None:
        raise ValidationError("This record is not linked to a Death record.")

    prior_death = match.death
    _record_decision(
        match,
        action=VADeathMatchDecision.Action.UNLINK,
        actor=actor,
        prior_death=prior_death,
        new_death=None,
        reason=reason,
    )
    match.death = None
    match.death_id_snapshot = None
    match.status = MatchStatus.UNMATCHED
    match.method = None
    match.score = 0
    match.score_separation = 0
    match.supporting_fields = []
    match.contradictory_fields = []
    match.missing_fields = []
    match.alternatives = []
    match.comparisons = []
    match.block_sources = []
    match.is_manual = False
    match.confirmed_by = None
    match.reviewed_by = actor
    match.matched_at = None
    match.rejected_reason = reason or None
    match.review_reason = None
    match.review_mode = None
    match.last_error = {}
    match.source = "review_unlink"
    match.last_evaluated_at = timezone.now()
    match.decision_version += 1
    match.save()
    return match
