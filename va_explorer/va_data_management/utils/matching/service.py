"""Reusable VA -> Death matching service.

Single entry point for every pathway (retrospective command, Celery sweeper,
ingestion hooks). Pure evaluation is separated from persistence so it is safe to
call from anywhere and easy to test.

Guiding rule (from the spec): an unmatched VA is preferable to a wrongly-linked
one. Automatic links require score strictly above the auto threshold, adequate
separation from the runner-up, and no serious contradiction.
"""

import logging

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from . import candidate_selection as cs
from .results import MatchResult
from .scoring import score_candidate

logger = logging.getLogger(__name__)


def _cfg(name, default):
    return getattr(settings, name, default)


def _thresholds():
    return {
        "auto": _cfg("VA_DEATH_MATCH_AUTO_THRESHOLD", 90),
        "review": _cfg("VA_DEATH_MATCH_REVIEW_THRESHOLD", 80),
        "min_sep": _cfg("VA_DEATH_MATCH_MIN_SEPARATION", 5),
        "tol_dod": _cfg("VA_DEATH_MATCH_MAX_DOD_TOLERANCE_DAYS", 3),
        "window": _cfg("VA_DEATH_MATCH_CANDIDATE_DOD_WINDOW_DAYS", 14),
        "max_candidates": _cfg("VA_DEATH_MATCH_MAX_CANDIDATES", 50),
        "version": _cfg("VA_DEATH_MATCH_ALGORITHM_VERSION", "1.0"),
    }


def _va_identifier(va):
    return getattr(va, "instanceid", None) or getattr(va, "key", None)


def _blank_result(va, status, source):
    from va_explorer.va_data_management.models import MatchStatus  # noqa: F401

    return MatchResult(
        va_pk=va.pk,
        va_model=va._meta.model_name,
        va_identifier=_va_identifier(va),
        status=status,
        method=None,
        death_pk=None,
        death_id=None,
        score=0.0,
        score_separation=0.0,
        algorithm_version=_thresholds()["version"],
        source=source,
    )


def _result_from_candidate(va, cand, status, *, source, separation, alternatives):
    return MatchResult(
        va_pk=va.pk,
        va_model=va._meta.model_name,
        va_identifier=_va_identifier(va),
        status=status,
        method=cand.method,
        death_pk=cand.death_pk,
        death_id=cand.death_id,
        score=cand.score,
        score_separation=round(separation, 2),
        supporting_fields=cand.supporting_fields,
        contradictory_fields=cand.contradictory_fields,
        missing_fields=cand.missing_fields,
        alternatives=alternatives,
        comparisons=[c.as_dict() for c in cand.comparisons],
        algorithm_version=_thresholds()["version"],
        source=source,
    )


def _evaluate(va, *, source):
    """Pure evaluation: returns ``(MatchResult, best_death_or_None)``. No writes."""
    from va_explorer.va_data_management.models import MatchStatus

    t = _thresholds()

    # --- Priority 1: trusted exact key (VASA DeathID / VerbalAutopsy Event) ---
    exact_death, exact_method = cs.exact_candidate(va)
    if exact_death is not None:
        cand = score_candidate(
            va,
            exact_death,
            method=exact_method,
            tol_dod_days=t["tol_dod"],
            review_threshold=t["review"],
        )
        if cand.hard_disqualified:
            # Key matched but demographics materially conflict -> human review.
            result = _result_from_candidate(
                va,
                cand,
                MatchStatus.PENDING_REVIEW,
                source=source,
                separation=cand.score,
                alternatives=[],
            )
            return result, exact_death
        # Trusted: the exact key is authoritative.
        cand.score = 100.0
        result = _result_from_candidate(
            va,
            cand,
            MatchStatus.MATCHED_AUTO,
            source=source,
            separation=100.0,
            alternatives=[],
        )
        return result, exact_death

    # --- Priority 2: fuzzy demographic matching ---
    candidates = cs.fuzzy_candidates(
        va, window_days=t["window"], max_candidates=t["max_candidates"]
    )
    if not candidates:
        return _blank_result(va, MatchStatus.UNMATCHED, source), None

    scored = sorted(
        (
            score_candidate(
                va,
                d,
                method=None,
                tol_dod_days=t["tol_dod"],
                review_threshold=t["review"],
            )
            for d in candidates
        ),
        key=lambda c: c.score,
        reverse=True,
    )
    from va_explorer.va_data_management.models import MatchMethod

    best = scored[0]
    best.method = MatchMethod.FUZZY_DEMOGRAPHIC
    second_score = scored[1].score if len(scored) > 1 else 0.0
    separation = best.score - second_score
    alternatives = [c.as_alternative() for c in scored[1:4]]

    if best.score > t["auto"] and not best.hard_disqualified:
        if separation >= t["min_sep"]:
            status = MatchStatus.MATCHED_AUTO
        else:
            # Top candidates too close to safely distinguish.
            status = MatchStatus.AMBIGUOUS
    elif t["review"] <= best.score <= t["auto"]:
        status = MatchStatus.PENDING_REVIEW
    else:
        status = MatchStatus.UNMATCHED

    best_death = next((d for d in candidates if d.pk == best.death_pk), None)
    result = _result_from_candidate(
        va,
        best,
        status,
        source=source,
        separation=separation,
        alternatives=alternatives,
    )
    # Only surface a proposed death for actionable outcomes.
    if status == MatchStatus.UNMATCHED:
        result.death_pk = None
        result.death_id = None
        best_death = None
    return result, best_death


def evaluate_va(va, *, source="evaluation") -> MatchResult:
    """Evaluate a VA and return the explainable result. Makes no DB changes."""
    result, _ = _evaluate(va, source=source)
    return result


def _existing_match(va):
    try:
        return va.va_death_match
    except ObjectDoesNotExist:
        return None


@transaction.atomic
def auto_link_verbal_autopsy_to_death(
    verbal_autopsy, *, save=True, force_recheck=False, source="service"
) -> MatchResult:
    """Evaluate one VA and (optionally) persist the link + audit row.

    Never raises into the caller unless ``VA_DEATH_MATCH_RAISE_INGESTION_ERRORS``
    is set: matching failures are logged and recorded as status=ERROR so
    ingestion is never broken by the matcher.
    """
    from va_explorer.va_data_management.models import MatchStatus

    va = verbal_autopsy
    existing = _existing_match(va)

    # Never overwrite a manually confirmed link unless explicitly rechecking.
    if existing is not None and existing.is_manual and not force_recheck:
        result = _blank_result(va, existing.status, source)
        result.death_pk = existing.death_id
        result.previously_linked = existing.is_linked
        result.previous_was_manual = True
        result.status = MatchStatus.SKIPPED
        return result

    try:
        result, best_death = _evaluate(va, source=source)
    except Exception as exc:  # noqa: BLE001 - matcher must not break ingestion
        logger.exception(
            "VA->Death matching failed for %s %s", va._meta.model_name, va.pk
        )
        if _cfg("VA_DEATH_MATCH_RAISE_INGESTION_ERRORS", False):
            raise
        result = _blank_result(va, MatchStatus.ERROR, source)
        result.error = str(exc)
        best_death = None
        if save:
            _persist(va, result, best_death, existing)
        return result

    result.previously_linked = bool(existing and existing.is_linked)
    result.previous_was_manual = bool(existing and existing.is_manual)

    if save:
        result.changed = _persist(va, result, best_death, existing)

    return result


def _persist(va, result, best_death, existing):
    """Write/update the VADeathMatch row. Returns True if anything changed."""
    from va_explorer.va_data_management.models import MatchStatus, VADeathMatch

    now = timezone.now()
    is_va = va._meta.model_name == "verbalautopsy"

    if existing is None:
        match = VADeathMatch(
            verbal_autopsy=va if is_va else None,
            vasa=None if is_va else va,
        )
        changed = True
    else:
        match = existing
        changed = (
            existing.status != result.status
            or (existing.death_id or None) != (result.death_pk or None)
            or existing.method != (result.method or None)
            or round(existing.score, 2) != round(result.score, 2)
        )

    if not changed:
        # Nothing meaningful changed: just bump the re-eval timestamp via an
        # UPDATE so simple_history records no spurious audit row.
        from va_explorer.va_data_management.models import VADeathMatch

        VADeathMatch.objects.filter(pk=match.pk).update(last_evaluated_at=now)
        return False

    match.death = best_death
    match.death_id_snapshot = result.death_id
    match.status = result.status
    match.method = result.method or None
    match.score = result.score
    match.score_separation = result.score_separation
    match.source = result.source
    match.algorithm_version = result.algorithm_version
    match.supporting_fields = result.supporting_fields
    match.contradictory_fields = result.contradictory_fields
    match.missing_fields = result.missing_fields
    match.alternatives = result.alternatives
    match.last_evaluated_at = now
    if result.status in {MatchStatus.MATCHED_AUTO, MatchStatus.MATCHED_MANUAL}:
        match.matched_at = now
    match.save()
    return True


def matching_enabled():
    """Whether VA->Death auto-linking is turned on (``VA_DEATH_MATCH_ENABLED``)."""
    return bool(_cfg("VA_DEATH_MATCH_ENABLED", False))


def schedule_link_for_va_ids(ids, *, model_name, source):
    """Ingestion seam: enqueue linking for freshly-written VA IDs.

    No-op unless ``VA_DEATH_MATCH_ENABLED`` — so every ingestion pathway can
    call this unconditionally and nothing happens until an operator turns
    auto-linking on. Enqueues after the surrounding transaction commits so the
    matcher never queries half-written data.
    """
    if not _cfg("VA_DEATH_MATCH_ENABLED", False):
        return
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return

    def _enqueue():
        from va_explorer.va_data_management.tasks import _LINK_BATCH_SIZE, link_va_batch

        for i in range(0, len(ids), _LINK_BATCH_SIZE):
            link_va_batch.delay(ids[i : i + _LINK_BATCH_SIZE], model_name, source)

    transaction.on_commit(_enqueue)


def run_matching(
    va_iterable,
    *,
    apply=False,
    force_recheck=False,
    source="retrospective_command",
    on_result=None,
):
    """Drive matching over an iterable of VA/VASA instances.

    Returns a summary dict of per-status counts. ``on_result`` (if given) is
    called with each ``MatchResult`` for streaming reports. In dry-run
    (``apply=False``) no DB writes occur but manual links are still respected.
    """
    from va_explorer.va_data_management.models import MatchStatus

    summary = {s.value: 0 for s in MatchStatus}
    summary.update({"already_linked": 0, "changed": 0, "failed": 0, "total": 0})

    for va in va_iterable:
        summary["total"] += 1
        if apply:
            result = auto_link_verbal_autopsy_to_death(
                va, save=True, force_recheck=force_recheck, source=source
            )
            if result.changed:
                summary["changed"] += 1
        else:
            existing = _existing_match(va)
            if existing is not None and existing.is_manual:
                result = _blank_result(va, MatchStatus.SKIPPED, source)
                result.previously_linked = existing.is_linked
                result.previous_was_manual = True
            else:
                result = evaluate_va(va, source=source)
                result.previously_linked = bool(existing and existing.is_linked)

        summary[result.status] = summary.get(result.status, 0) + 1
        if result.previously_linked:
            summary["already_linked"] += 1
        if result.status == MatchStatus.ERROR:
            summary["failed"] += 1
        if on_result is not None:
            on_result(result)

    return summary
