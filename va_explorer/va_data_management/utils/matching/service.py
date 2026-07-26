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

import copy
import logging

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from . import candidate_selection as cs
from . import normalization as N
from .config import resolve_config
from .death_index import DeathIndex
from .profiles import build_va_profile
from .results import MatchResult
from .scoring import dod_year_conflict, score_candidate

logger = logging.getLogger(__name__)
MATCHING_MODES = {"off", "shadow", "review_only", "auto"}


def _cfg(name, default):
    return getattr(settings, name, default)


def _va_identifier(va):
    return getattr(va, "instanceid", None) or getattr(va, "key", None)


def _has_meaningful_value(value):
    normalized = ("" if value is None else str(value)).strip().lower()
    return normalized not in {"", "nan", "none", "null"}


def _is_facility_va_excluded(va):
    """Facility-source VAs do not have Death notifications to link against."""
    if _has_meaningful_value(getattr(va, "hospital", None)):
        return True
    if _has_meaningful_value(getattr(va, "area", None)):
        return True
    try:
        if va.community_va_normalized == "no":
            return True
    except AttributeError:
        value = str(getattr(va, "community_va", "") or "").strip().lower()
        if value in {"no", "n", "false", "0"}:
            return True
    return False


def _crosswalk_version():
    from va_explorer.va_data_management.linkage.linkage_map import CROSSWALK_VERSION

    return CROSSWALK_VERSION


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
        config_snapshot=copy.deepcopy(config),
        crosswalk_version=_crosswalk_version(),
        algorithm_version=config["algorithm_version"],
        source=source,
    )


def _facility_excluded_result(va, source, config):
    from va_explorer.va_data_management.models import MatchStatus

    return _blank_result(va, MatchStatus.NO_DEATH_EXPECTED, source, config)


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
        config_snapshot=copy.deepcopy(config),
        crosswalk_version=_crosswalk_version(),
        algorithm_version=config["algorithm_version"],
        source=source,
    )


def _evaluate(va, *, source, config, index):
    """Pure evaluation: ``(MatchResult, best_death_or_None, block_stats)``."""
    from va_explorer.va_data_management.models import (
        MatchMethod,
        MatchStatus,
        ReviewMode,
        ReviewReason,
    )

    thresholds = config["thresholds"]
    profile = build_va_profile(va)
    rejected = cs.rejected_pks(va)

    def _finish(result, best_death, block_stats):
        result.va_tool = profile.tool
        result.block_stats = block_stats
        return result, best_death, block_stats

    def _deterministic_result(det_death, det_method):
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
            result.review_reason = ReviewReason.HARD_CONTRADICTION
            result.review_mode = ReviewMode.IN_PERSON
            return result, det_death
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
        return result, det_death

    # --- Stage 1: primary exact keys, guarded against contradictions ---------
    det_death, det_method = cs.deterministic_candidate(
        va, profile, index, rejected=rejected, phase="primary"
    )
    if det_death is not None:
        result, det_death = _deterministic_result(det_death, det_method)
        return _finish(result, det_death, {})

    # --- Stage 2: exact NRC, with exact/fuzzy component-level name gates -----
    nrc_candidates = cs.nrc_name_candidates(profile, index, rejected=rejected)
    if nrc_candidates:
        unique_nrc = len(nrc_candidates) == 1
        death, evidence = nrc_candidates[0]
        death_profile = index.profile(death.pk)
        method = (
            MatchMethod.NAME_NRC_EXACT
            if evidence["exact"]
            else MatchMethod.NAME_NRC_PROBABILISTIC
        )
        cand = score_candidate(
            va,
            death,
            method=method,
            config=config,
            va_profile=profile,
            death_profile=death_profile,
            block_sources=["nrc"],
        )
        component_comparison = {
            "field": "nrc_name_components",
            "va_value": profile.name_raw,
            "death_value": death_profile.name_raw,
            "classification": "exact" if evidence["exact"] else "fuzzy",
            "weight": 0,
            "awarded": 0,
            "similarity": evidence["overall_name_similarity"],
            "evidence": evidence,
        }

        has_complete_components = bool(
            N.name_token_set(profile.given_name_raw)
            and N.name_token_set(profile.surname_raw)
            and evidence["given_name_min_similarity"] is not None
            and evidence["surname_min_similarity"] is not None
        )
        no_unpaired_tokens = not any(evidence["unpaired_tokens"].values())
        exact_safe = unique_nrc and has_complete_components and evidence["exact"]
        fuzzy_safe = bool(
            unique_nrc
            and has_complete_components
            and thresholds.get("name_nrc_probabilistic_auto_enabled", False)
            and (evidence["overall_name_similarity"] or 0)
            >= float(thresholds["name_nrc_auto_similarity"])
            and (evidence["surname_min_similarity"] or 0)
            >= float(thresholds["name_nrc_surname_min_similarity"])
            and (evidence["given_name_min_similarity"] or 0)
            >= float(thresholds["name_nrc_given_min_similarity"])
            and no_unpaired_tokens
        )
        hard_conflict = dod_year_conflict(profile, death_profile) or any(
            comparison.field in {"sex", "dod"}
            and comparison.classification == "contradictory"
            for comparison in cand.comparisons
        )

        if not unique_nrc:
            status = MatchStatus.PENDING_REVIEW
            review_reason = ReviewReason.NRC_DUPLICATE
        elif hard_conflict:
            status = MatchStatus.PENDING_REVIEW
            review_reason = ReviewReason.HARD_CONTRADICTION
        elif exact_safe or fuzzy_safe:
            status = MatchStatus.MATCHED_AUTO
            review_reason = None
        elif not has_complete_components:
            status = MatchStatus.PENDING_REVIEW
            review_reason = ReviewReason.NRC_NAME_MISSING
        elif (evidence["surname_min_similarity"] or 0) >= float(
            thresholds["name_nrc_surname_min_similarity"]
        ) and (evidence["given_name_min_similarity"] or 0) < float(
            thresholds["name_nrc_given_min_similarity"]
        ):
            status = MatchStatus.PENDING_REVIEW
            review_reason = ReviewReason.NRC_GIVEN_NAME_CONFLICT
        else:
            status = MatchStatus.PENDING_REVIEW
            review_reason = ReviewReason.NRC_NAME_CONFLICT

        alternatives = []
        for alternate_death, alternate_evidence in nrc_candidates[1:4]:
            alternate_profile = index.profile(alternate_death.pk)
            alternate = score_candidate(
                va,
                alternate_death,
                method=(
                    MatchMethod.NAME_NRC_EXACT
                    if alternate_evidence["exact"]
                    else MatchMethod.NAME_NRC_PROBABILISTIC
                ),
                config=config,
                va_profile=profile,
                death_profile=alternate_profile,
                block_sources=["nrc"],
            ).as_alternative()
            alternate["name_evidence"] = alternate_evidence
            alternatives.append(alternate)

        result = _result_from_candidate(
            va,
            cand,
            status,
            source=source,
            separation=cand.score,
            alternatives=alternatives,
            config=config,
        )
        result.supporting_fields = list(
            dict.fromkeys(["nrc", *result.supporting_fields])
        )
        result.comparisons.append(component_comparison)
        result.review_reason = review_reason
        result.review_mode = ReviewMode.IN_PERSON if review_reason else None
        return _finish(result, death, {"nrc": len(nrc_candidates)})

    # --- Stage 3: secondary exact keys, guarded against contradictions -------
    det_death, det_method = cs.deterministic_candidate(
        va, profile, index, rejected=rejected, phase="secondary"
    )
    if det_death is not None:
        result, det_death = _deterministic_result(det_death, det_method)
        return _finish(result, det_death, {})

    # --- Stage 4: probabilistic over union-of-blocks candidates --------------
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

    if best.score >= review and separation < tie_margin:
        status = MatchStatus.AMBIGUOUS
    elif best.score >= auto and not best.hard_disqualified:
        if separation >= min_sep:
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
    if status == MatchStatus.AMBIGUOUS:
        result.review_reason = ReviewReason.CLOSE_CANDIDATES
        result.review_mode = ReviewMode.DESK
    elif status == MatchStatus.PENDING_REVIEW:
        result.review_reason = ReviewReason.SCORE_REVIEW_BAND
        result.review_mode = ReviewMode.DESK
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
    if _is_facility_va_excluded(va):
        return _facility_excluded_result(va, source, config)
    result, _, _ = _evaluate(
        va, source=source, config=config, index=_index_for(va, index)
    )
    return result


def _existing_match(va):
    try:
        return va.va_death_match
    except ObjectDoesNotExist:
        return None


def _locked_existing_match(va):
    from va_explorer.va_data_management.models import VADeathMatch

    field = "verbal_autopsy" if va._meta.model_name == "verbalautopsy" else "vasa"
    return VADeathMatch.objects.select_for_update().filter(**{field: va}).first()


def _reconcile_previous_link(result, existing):
    """Never silently unlink: demote a changed auto-link to review.

    When the existing row is an authoritative *automatic* link and the fresh
    evaluation proposes a different death (or none, or a non-linked status),
    the outcome becomes ``pending_review`` with the previously linked death
    preserved at the top of ``alternatives``. Manual links never reach here
    (they are skipped upstream unless force-rechecked by an authorised run).
    """
    from va_explorer.va_data_management.models import (
        MatchStatus,
        ReviewMode,
        ReviewReason,
    )

    if existing is None or existing.is_manual or not existing.is_linked:
        return result
    same_death = (result.death_pk or None) == (existing.death_id or None)
    if same_death and result.status == MatchStatus.MATCHED_AUTO:
        return result

    result.status = MatchStatus.PENDING_REVIEW
    result.review_reason = ReviewReason.PREVIOUS_LINK_CHANGED
    result.review_mode = ReviewMode.DESK
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
    review_only=False,
    source="service",
    config=None,
    index=None,
) -> MatchResult:
    """Evaluate one VA and (optionally) persist the link + audit row.

    Never raises into the caller unless ``VA_DEATH_MATCH_RAISE_INGESTION_ERRORS``
    is set: matching failures are logged and recorded as status=ERROR so
    ingestion is never broken by the matcher.
    """
    from va_explorer.va_data_management.models import (
        MatchStatus,
        ReviewMode,
        ReviewReason,
    )

    va = verbal_autopsy
    config = config or resolve_config()
    existing = _locked_existing_match(va) if save else _existing_match(va)

    if _is_facility_va_excluded(va):
        result = _facility_excluded_result(va, source, config)
        result.previously_linked = bool(existing and existing.is_linked)
        result.previous_was_manual = bool(existing and existing.is_manual)
        if existing is not None and existing.is_manual:
            result.status = MatchStatus.SKIPPED
            result.death_pk = existing.death_id
            return result
        if save:
            result.changed = _persist(va, result, None, existing)
        return result

    # Human-confirmed links are authoritative. Force recheck may inspect them in
    # dry-run mode, but no automatic pathway may overwrite one.
    if existing is not None and existing.is_manual:
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
        if review_only and result.status == MatchStatus.MATCHED_AUTO:
            result.status = MatchStatus.PENDING_REVIEW
            result.review_reason = ReviewReason.SCORE_REVIEW_BAND
            result.review_mode = ReviewMode.DESK
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
        if save:
            if existing is not None and existing.is_linked:
                _record_error(existing, result)
            else:
                _persist(va, result, None, existing)
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
            or existing.review_reason != (result.review_reason or None)
            or existing.review_mode != (result.review_mode or None)
            or existing.config_snapshot != result.config_snapshot
            or existing.crosswalk_version != (result.crosswalk_version or None)
        )

    if not changed:
        # Nothing meaningful changed: just bump the re-eval timestamp via an
        # UPDATE so simple_history records no spurious audit row.
        VADeathMatch.objects.filter(pk=match.pk).update(
            last_evaluated_at=now, last_error={}
        )
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
    match.config_snapshot = result.config_snapshot
    match.crosswalk_version = result.crosswalk_version or None
    match.review_reason = result.review_reason or None
    match.review_mode = result.review_mode or None
    match.rejected_reason = None
    match.last_error = {}
    match.supporting_fields = result.supporting_fields
    match.contradictory_fields = result.contradictory_fields
    match.missing_fields = result.missing_fields
    match.alternatives = result.alternatives
    match.comparisons = result.comparisons
    match.block_sources = result.block_sources
    match.last_evaluated_at = now
    if result.status in {MatchStatus.MATCHED_AUTO, MatchStatus.MATCHED_MANUAL}:
        match.matched_at = now
    elif match.death_id is None:
        match.matched_at = None
    match.decision_version += 1
    match.save()
    return True


def _record_error(match, result):
    """Record matcher failure without destroying an authoritative link."""
    match.last_error = {
        "message": result.error,
        "source": result.source,
        "occurred_at": timezone.now().isoformat(),
        "algorithm_version": result.algorithm_version,
        "config_version": result.config_name,
        "crosswalk_version": result.crosswalk_version,
    }
    match.last_evaluated_at = timezone.now()
    match.save(update_fields=["last_error", "last_evaluated_at"])


def matching_enabled():
    """Whether any automatic VA->Death evaluation mode is enabled."""
    return matching_mode() != "off"


def matching_mode():
    """Return off/shadow/review_only/auto with legacy-flag compatibility."""
    mode = _cfg("VA_DEATH_MATCH_MODE", None)
    if mode in (None, ""):
        return "auto" if _cfg("VA_DEATH_MATCH_ENABLED", False) else "off"
    mode = str(mode).strip().lower()
    if mode not in MATCHING_MODES:
        raise ValueError(
            f"VA_DEATH_MATCH_MODE must be one of {sorted(MATCHING_MODES)}; "
            f"got {mode!r}."
        )
    return mode


def schedule_link_for_va_ids(ids, *, model_name, source):
    """Ingestion seam: enqueue linking for freshly-written VA IDs.

    No-op unless ``VA_DEATH_MATCH_ENABLED`` — so every ingestion pathway can
    call this unconditionally and nothing happens until an operator turns
    auto-linking on. Enqueues after the surrounding transaction commits so the
    matcher never queries half-written data.
    """
    if not matching_enabled():
        return
    ids = [int(i) for i in ids if i is not None]
    if not ids:
        return

    def _enqueue():
        from va_explorer.va_data_management.tasks import _LINK_BATCH_SIZE, link_va_batch

        try:
            for i in range(0, len(ids), _LINK_BATCH_SIZE):
                link_va_batch.delay(ids[i : i + _LINK_BATCH_SIZE], model_name, source)
        except Exception:  # noqa: BLE001 - broker availability must not undo ingestion
            logger.exception(
                "Could not enqueue VA->Death linking for %d %s record(s); "
                "the next linkage sweep can process them.",
                len(ids),
                model_name,
            )
            if _cfg("VA_DEATH_MATCH_RAISE_INGESTION_ERRORS", False):
                raise

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
    review_only=False,
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
    index = death_index

    def _get_index():
        nonlocal index
        if index is None:
            index = DeathIndex.build()
        return index

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
            "deaths_indexed": death_index.size if death_index is not None else 0,
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
                review_only=review_only,
                source=source,
                config=config,
                index=None if _is_facility_va_excluded(va) else _get_index(),
            )
            if result.changed:
                summary["changed"] += 1
            _collect_blocks(result, result.block_stats)
        else:
            existing = _existing_match(va)
            if existing is not None and existing.is_manual and not force_recheck:
                result = _blank_result(va, MatchStatus.SKIPPED, source, config)
                result.previously_linked = existing.is_linked
                result.previous_was_manual = True
            elif _is_facility_va_excluded(va):
                result = _facility_excluded_result(va, source, config)
                result.previously_linked = bool(existing and existing.is_linked)
                result.previous_was_manual = bool(existing and existing.is_manual)
            else:
                try:
                    result, _, block_stats = _evaluate(
                        va, source=source, config=config, index=_get_index()
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

    summary["deaths_indexed"] = index.size if index is not None else 0
    return summary
