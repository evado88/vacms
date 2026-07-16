"""Reusable VA -> Death matching service (v2).

Single entry point for every pathway (retrospective command, Celery sweeper,
ingestion hooks). Pure evaluation is separated from persistence so it is safe
to call from anywhere and easy to test.

v2 pipeline per VA: Stage 1 deterministic keys (DeathID, Event bridge, NRC,
registration number, exact composite) each guarded by the full scorer; Stage 2
probabilistic scoring over union-of-blocks candidates. Weights/thresholds come
from the ``MatchingConfig`` table (audited), falling back to settings.

Two guiding rules (from the specs):

* Prefer routing borderline pairs to human review over leaving them unlinked —
  the review band is wide and ties go to review, not to silence.
* Never silently unlink: when a previously auto-linked VA re-evaluates to a
  different death (or to no link), the row is demoted to ``pending_review``
  with the previous death preserved in ``alternatives`` — a human decides.
"""

import logging

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from . import candidate_selection as cs
from .config import resolve_config
from .death_index import DeathIndex
from .profiles import build_va_profile
from .results import MatchResult
from .scoring import dod_year_conflict, score_candidate

logger = logging.getLogger(__name__)


def _cfg(name, default):
    return getattr(settings, name, default)


def _va_identifier(va):
    return getattr(va, "instanceid", None) or getattr(va, "key", None)


def _blank_result(va, status, source, config):
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
        config_name=config["name"],
        algorithm_version=config["algorithm_version"],
        source=source,
    )


def _result_from_candidate(
    va, cand, status, *, source, separation, alternatives, config
):
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
        block_sources=cand.block_sources,
        config_name=config["name"],
        algorithm_version=config["algorithm_version"],
        source=source,
    )


def _evaluate(va, *, source, config, index):
    """Pure evaluation: ``(MatchResult, best_death_or_None, block_stats)``."""
    from va_explorer.va_data_management.models import MatchMethod, MatchStatus

    thresholds = config["thresholds"]
    profile = build_va_profile(va)
    rejected = cs.rejected_pks(va)

    def _finish(result, best_death, block_stats):
        result.va_tool = profile.tool
        return result, best_death, block_stats

    # --- Stage 1: deterministic keys, each with a contradiction guard --------
    det_death, det_method = cs.deterministic_candidate(
        va, profile, index, rejected=rejected
    )
    if det_death is not None:
        death_profile = index.profile(det_death.pk)
        cand = score_candidate(
            va,
            det_death,
            method=det_method,
            config=config,
            va_profile=profile,
            death_profile=death_profile,
            block_sources=["deterministic"],
        )
        if cand.hard_disqualified or dod_year_conflict(profile, death_profile):
            # Key matched but demographics materially conflict -> human review.
            result = _result_from_candidate(
                va,
                cand,
                MatchStatus.PENDING_REVIEW,
                source=source,
                separation=cand.score,
                alternatives=[],
                config=config,
            )
            return _finish(result, det_death, {})
        if det_method == MatchMethod.EXACT_DEATH_ID:
            # The interviewer-entered Death ID is authoritative.
            cand.score = 100.0
        result = _result_from_candidate(
            va,
            cand,
            MatchStatus.MATCHED_AUTO,
            source=source,
            separation=cand.score,
            alternatives=[],
            config=config,
        )
        return _finish(result, det_death, {})

    # --- Stage 2: probabilistic over union-of-blocks candidates --------------
    candidates, block_stats = cs.union_block_candidates(
        profile, index, thresholds=thresholds, rejected=rejected
    )
    if not candidates:
        return _finish(
            _blank_result(va, MatchStatus.UNMATCHED, source, config),
            None,
            block_stats,
        )

    scored = sorted(
        (
            score_candidate(
                va,
                death,
                method=MatchMethod.PROBABILISTIC,
                config=config,
                va_profile=profile,
                death_profile=index.profile(death.pk),
                block_sources=blocks,
            )
            for death, blocks in candidates
        ),
        key=lambda c: c.score,
        reverse=True,
    )

    best = scored[0]
    second_score = scored[1].score if len(scored) > 1 else 0.0
    separation = best.score - second_score
    alternatives = [c.as_alternative() for c in scored[1:4]]

    auto = float(thresholds["auto"])
    review = float(thresholds["review"])
    tie_margin = float(thresholds.get("tie_margin", thresholds.get("min_sep", 5)))
    min_sep = float(thresholds.get("min_sep", 5))

    if best.score >= auto and not best.hard_disqualified:
        if separation >= max(min_sep, tie_margin):
            status = MatchStatus.MATCHED_AUTO
        else:
            # Top candidates too close to safely distinguish -> review.
            status = MatchStatus.AMBIGUOUS
    elif review <= best.score < auto:
        status = MatchStatus.PENDING_REVIEW
    else:
        status = MatchStatus.UNMATCHED

    best_death = index.death(best.death_pk)
    result = _result_from_candidate(
        va,
        best,
        status,
        source=source,
        separation=separation,
        alternatives=alternatives,
        config=config,
    )
    # Only surface a proposed death for actionable outcomes.
    if status == MatchStatus.UNMATCHED:
        result.death_pk = None
        result.death_id = None
        best_death = None
    return _finish(result, best_death, block_stats)


def _index_for(va, index):
    if index is not None:
        return index
    return DeathIndex.build_for_profile(build_va_profile(va))


def evaluate_va(va, *, source="evaluation", config=None, index=None) -> MatchResult:
    """Evaluate a VA and return the explainable result. Makes no DB changes."""
    config = config or resolve_config()
    result, _, _ = _evaluate(
        va, source=source, config=config, index=_index_for(va, index)
    )
    return result


def _existing_match(va):
    try:
        return va.va_death_match
    except ObjectDoesNotExist:
        return None


def _reconcile_previous_link(result, existing):
    """Never silently unlink: demote a changed auto-link to review.

    When the existing row is an authoritative *automatic* link and the fresh
    evaluation proposes a different death (or none, or a non-linked status),
    the outcome becomes ``pending_review`` with the previously linked death
    preserved at the top of ``alternatives``. Manual links never reach here
    (they are skipped upstream unless force-rechecked by an authorised run).
    """
    from va_explorer.va_data_management.models import MatchStatus

    if existing is None or existing.is_manual or not existing.is_linked:
        return result
    same_death = (result.death_pk or None) == (existing.death_id or None)
    if same_death and result.status == MatchStatus.MATCHED_AUTO:
        return result

    result.status = MatchStatus.PENDING_REVIEW
    result.demoted_previous_link = True
    previous = {
        "death_pk": existing.death_id,
        "death_id": existing.death_id_snapshot,
        "score": existing.score,
        "method": existing.method,
        "previous_link": True,
        "algorithm_version": existing.algorithm_version,
    }
    result.alternatives = [previous] + [
        a for a in result.alternatives if a.get("death_pk") != existing.death_id
    ]
    return result


@transaction.atomic
def auto_link_verbal_autopsy_to_death(
    verbal_autopsy,
    *,
    save=True,
    force_recheck=False,
    source="service",
    config=None,
    index=None,
) -> MatchResult:
    """Evaluate one VA and (optionally) persist the link + audit row.

    Never raises into the caller unless ``VA_DEATH_MATCH_RAISE_INGESTION_ERRORS``
    is set: matching failures are logged and recorded as status=ERROR so
    ingestion is never broken by the matcher.
    """
    from va_explorer.va_data_management.models import MatchStatus

    va = verbal_autopsy
    config = config or resolve_config()
    existing = _existing_match(va)

    # Never overwrite a manually confirmed link unless explicitly rechecking.
    if existing is not None and existing.is_manual and not force_recheck:
        result = _blank_result(va, existing.status, source, config)
        result.death_pk = existing.death_id
        result.previously_linked = existing.is_linked
        result.previous_was_manual = True
        result.status = MatchStatus.SKIPPED
        return result

    try:
        result, best_death, _ = _evaluate(
            va, source=source, config=config, index=_index_for(va, index)
        )
        result = _reconcile_previous_link(result, existing)
        if result.demoted_previous_link and result.death_pk is None:
            # Keep pointing at the previous death while a human reviews.
            best_death = existing.death
            result.death_pk = existing.death_id
            result.death_id = existing.death_id_snapshot
    except Exception as exc:  # noqa: BLE001 - matcher must not break ingestion
        logger.exception(
            "VA->Death matching failed for %s %s", va._meta.model_name, va.pk
        )
        if _cfg("VA_DEATH_MATCH_RAISE_INGESTION_ERRORS", False):
            raise
        result = _blank_result(va, MatchStatus.ERROR, source, config)
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
    match.config_version = result.config_name or None
    match.supporting_fields = result.supporting_fields
    match.contradictory_fields = result.contradictory_fields
    match.missing_fields = result.missing_fields
    match.alternatives = result.alternatives
    match.comparisons = result.comparisons
    match.block_sources = result.block_sources
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
    config_name=None,
    death_index=None,
):
    """Drive matching over an iterable of VA/VASA instances.

    Returns a summary dict of per-status counts plus per-method counts and
    per-block candidate statistics. ``on_result`` (if given) is called with
    each ``MatchResult`` for streaming reports. In dry-run (``apply=False``)
    no DB writes occur but manual links are still respected. The Death index
    is built once for the whole run unless one is passed in.
    """
    from va_explorer.va_data_management.models import MatchStatus

    config = resolve_config(config_name)
    index = death_index if death_index is not None else DeathIndex.build()

    summary = {s.value: 0 for s in MatchStatus}
    summary.update(
        {
            "already_linked": 0,
            "changed": 0,
            "failed": 0,
            "total": 0,
            "demoted_previous_link": 0,
            "methods": {},
            "block_stats": {},
            "config": config["name"],
            "deaths_indexed": index.size,
        }
    )

    def _collect_blocks(result, block_stats):
        for block, count in block_stats.items():
            summary["block_stats"].setdefault(block, []).append(count)

    for va in va_iterable:
        summary["total"] += 1
        if apply:
            result = auto_link_verbal_autopsy_to_death(
                va,
                save=True,
                force_recheck=force_recheck,
                source=source,
                config=config,
                index=index,
            )
            if result.changed:
                summary["changed"] += 1
        else:
            existing = _existing_match(va)
            if existing is not None and existing.is_manual and not force_recheck:
                result = _blank_result(va, MatchStatus.SKIPPED, source, config)
                result.previously_linked = existing.is_linked
                result.previous_was_manual = True
            else:
                try:
                    result, _, block_stats = _evaluate(
                        va, source=source, config=config, index=index
                    )
                    result = _reconcile_previous_link(result, existing)
                    _collect_blocks(result, block_stats)
                except Exception as exc:  # noqa: BLE001 - keep dry runs total
                    logger.exception(
                        "VA->Death matching failed for %s %s",
                        va._meta.model_name,
                        va.pk,
                    )
                    result = _blank_result(va, MatchStatus.ERROR, source, config)
                    result.error = str(exc)
                result.previously_linked = bool(existing and existing.is_linked)
                result.previous_was_manual = bool(existing and existing.is_manual)

        summary[result.status] = summary.get(result.status, 0) + 1
        if result.method:
            summary["methods"][result.method] = (
                summary["methods"].get(result.method, 0) + 1
            )
        if result.previously_linked:
            summary["already_linked"] += 1
        if result.demoted_previous_link:
            summary["demoted_previous_link"] += 1
        if result.status == MatchStatus.ERROR:
            summary["failed"] += 1
        if on_result is not None:
            on_result(result)

    return summary
