import logging

from celery.schedules import crontab
from django.conf import settings

from config.celery_app import app
from config.settings.base import env
from va_explorer.va_data_management.management.commands.import_from_kobo import (
    BATCH_SIZE,
)
from va_explorer.va_data_management.models import VASA, VerbalAutopsy
from va_explorer.va_data_management.odk.config import get_odk_form_configs
from va_explorer.va_data_management.odk.service import ODKPullService
from va_explorer.va_data_management.utils import coding, kobo
from va_explorer.va_data_management.utils.loading import load_records_from_dataframe

_CODING_BATCH_SIZE = 500

logger = logging.getLogger(__name__)


@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    # Import from Kobo daily at 00:00
    sender.add_periodic_task(
        crontab(hour=0, minute=0), import_from_kobo.s(), name="Import from Kobo daily"
    )

    # Run Coding Algorithms daily at 00:30
    sender.add_periodic_task(
        crontab(hour=0, minute=30),
        run_coding_algorithms.s(),
        name="Run Coding Algorithms daily",
    )

    odk_schedules = getattr(settings, "ODK_PULL_SCHEDULES", {})
    for schedule_key, schedule in odk_schedules.items():
        sender.add_periodic_task(
            crontab(**schedule),
            pull_odk_scheduled.s(schedule_key=schedule_key),
            name=f"Pull ODK submissions ({schedule_key})",
        )

    # Assign death IDs every 15 minutes — on-create signal handles low-latency cases
    sender.add_periodic_task(
        900.0,
        assign_death_ids_task.s(),
        name="Assign death IDs to new deaths",
    )

    # Pre-warm home dashboard cache every 5 minutes
    sender.add_periodic_task(
        300.0,
        app.signature("va_explorer.home.tasks.prewarm_home_dashboard_cache"),
        name="Pre-warm home dashboard cache",
    )

    # Sweep newly-ingested VAs for Death links hourly. Only scheduled when
    # auto-linking is enabled; the task itself also guards on the flag.
    if getattr(settings, "VA_DEATH_MATCH_ENABLED", False):
        sender.add_periodic_task(
            crontab(minute=15),
            sweep_unlinked_vas.s(),
            name="Sweep unlinked VAs for Death links",
        )
        # Weekly report-only calibration: watches the link rate against the
        # expected prior so a drifting crosswalk/config gets noticed.
        sender.add_periodic_task(
            crontab(day_of_week=1, hour=3, minute=0),
            linkage_calibration_report.s(),
            name="Weekly VA-Death linkage calibration report",
        )


# Result of tasks need to be json serializable so return dicts.
@app.task()
def run_coding_algorithms():
    """Orchestrator: snapshot uncoded VA IDs and dispatch one batch task per 500 VAs.

    Splitting into independent sub-tasks prevents any single Celery call from
    running longer than the worker's soft time limit on large backlogs.
    """
    num_batches = 0
    total_uncoded = 0
    # VA and VASA share the coding pipeline; dispatch batches for each, tagging
    # the batch with the FK field its results should be stored against.
    for model, fk_field in ((VerbalAutopsy, "verbalautopsy"), (VASA, "vasa")):
        record_ids = list(
            model.objects.filter(causes__isnull=True).values_list("id", flat=True)
        )
        for i in range(0, len(record_ids), _CODING_BATCH_SIZE):
            batch_ids = record_ids[i : i + _CODING_BATCH_SIZE]
            run_coding_algorithms_batch.delay(batch_ids, fk_field)
            num_batches += 1
        total_uncoded += len(record_ids)

    if not total_uncoded:
        return {"num_batches": 0, "total_uncoded": 0}

    logger.info(
        "Coding orchestrator dispatched %d batch task(s) for %d records",
        num_batches,
        total_uncoded,
    )
    return {"num_batches": num_batches, "total_uncoded": total_uncoded}


@app.task()
def run_coding_algorithms_batch(record_ids, fk_field="verbalautopsy"):
    """Code a batch of up to _CODING_BATCH_SIZE records (VA or VASA) by their PKs."""
    model = VASA if fk_field == "vasa" else VerbalAutopsy
    records = list(
        model.objects.filter(id__in=record_ids, causes__isnull=True).order_by("id")
    )
    if not records:
        return {"num_coded": 0, "num_issues": 0}

    causes, issues = coding.run_interva5(records, fk_field=fk_field)
    return {"num_coded": len(causes), "num_issues": len(issues)}


_LINK_BATCH_SIZE = 500


@app.task()
def sweep_unlinked_vas():
    """Snapshot never-evaluated VA/VASA IDs and dispatch link batches.

    Mirrors ``run_coding_algorithms``. No-ops unless ``VA_DEATH_MATCH_ENABLED``
    so it is inert while auto-linking is disabled.
    """
    if not getattr(settings, "VA_DEATH_MATCH_ENABLED", False):
        return {"enabled": False, "num_batches": 0}

    num_batches = 0
    total = 0
    for model, model_name in ((VerbalAutopsy, "verbalautopsy"), (VASA, "vasa")):
        record_ids = list(
            model.objects.filter(va_death_match__isnull=True).values_list(
                "id", flat=True
            )
        )
        for i in range(0, len(record_ids), _LINK_BATCH_SIZE):
            link_va_batch.delay(
                record_ids[i : i + _LINK_BATCH_SIZE], model_name, "sweeper"
            )
            num_batches += 1
        total += len(record_ids)

    logger.info("Link sweeper dispatched %d batch(es) for %d VAs", num_batches, total)
    return {"enabled": True, "num_batches": num_batches, "total": total}


@app.task()
def link_va_batch(record_ids, model_name="verbalautopsy", source="sweeper"):
    """Link a batch of VA/VASA records to Death records by their PKs."""
    from va_explorer.va_data_management.utils.matching import run_matching

    model = VASA if model_name == "vasa" else VerbalAutopsy
    qs = model.objects.filter(id__in=record_ids).select_related("va_death_match")
    if model is VerbalAutopsy:
        qs = qs.prefetch_related("events__death")
    else:
        qs = qs.select_related("social_autopsy")
    summary = run_matching(qs.order_by("id"), apply=True, source=source)
    return {"total": summary.get("total", 0), "changed": summary.get("changed", 0)}


@app.task()
def linkage_calibration_report():
    """Weekly report-only calibration pass over every VA/VASA.

    Logs the achieved link rate against the expected prior
    (``VA_DEATH_MATCH_EXPECTED_LINK_RATE``, default 75%) and warns when
    candidate coverage falls below it — the spec's signal that blocking,
    thresholds or the crosswalk are losing true matches.
    """
    from va_explorer.va_data_management.utils.matching import run_matching

    if not getattr(settings, "VA_DEATH_MATCH_ENABLED", False):
        return {"enabled": False}

    def _iter_vas():
        for model in (VerbalAutopsy, VASA):
            qs = model.objects.all().select_related("va_death_match")
            if model is VerbalAutopsy:
                qs = qs.prefetch_related("events__death")
            else:
                qs = qs.select_related("social_autopsy")
            yield from qs.order_by("id").iterator(chunk_size=_LINK_BATCH_SIZE)

    summary = run_matching(_iter_vas(), apply=False, source="calibration_task")
    total = summary.get("total", 0) or 1
    linked = (
        summary.get("matched_auto", 0)
        + summary.get("matched_manual", 0)
        + summary.get("skipped", 0)  # existing manual links
    )
    queued = summary.get("pending_review", 0) + summary.get("ambiguous", 0)
    coverage = 100.0 * (linked + queued) / total
    prior = float(getattr(settings, "VA_DEATH_MATCH_EXPECTED_LINK_RATE", 75))
    report = {
        "enabled": True,
        "total": total,
        "linked": linked,
        "review_queue": queued,
        "coverage_pct": round(coverage, 1),
        "expected_prior_pct": prior,
        "config": summary.get("config"),
    }
    if coverage < prior:
        logger.warning(
            "Linkage coverage %.1f%% is below the %.0f%% prior — investigate "
            "blocking/thresholds/crosswalk. %s",
            coverage,
            prior,
            report,
        )
    else:
        logger.info("Linkage calibration OK: %s", report)
    return report


@app.task()
def import_from_odk():
    """Legacy entrypoint maintained for compatibility; delegates to pull_odk_scheduled."""
    return pull_odk_scheduled()


@app.task(rate_limit="10/m")
def import_from_kobo():
    options = {
        "token": env("KOBO_API_TOKEN"),
        "asset_id": env("KOBO_ASSET_ID"),
    }
    data, next_page = kobo.download_responses(
        options["token"], options["asset_id"], BATCH_SIZE, None
    )
    results = load_records_from_dataframe(data)

    num_created = len(results["created"])
    num_ignored = len(results["ignored"])
    num_outdated = len(results["outdated"])
    num_enriched = len(results["enriched"])
    num_corrected = len(results["corrected"])
    num_invalid = len(results["removed"])

    # Process all available pages of kobo data since it is provided via pagination
    while next_page is not None:
        data, next_page = kobo.download_responses(
            options["token"], options["asset_id"], BATCH_SIZE, next_page
        )
        results = load_records_from_dataframe(data)
        num_created = num_created + len(results["created"])
        num_ignored = num_ignored + len(results["ignored"])
        num_outdated = num_outdated + len(results["outdated"])
        num_enriched = num_enriched + len(results["enriched"])
        num_corrected = num_corrected + len(results["corrected"])
        num_invalid = num_invalid + len(results["removed"])

    counts = {
        "num_ignored": num_ignored,
        "num_outdated": num_outdated,
        "num_created": num_created,
        "num_enriched": num_enriched,
        "num_corrected": num_corrected,
        "num_removed": num_invalid,
    }

    from va_explorer.users.models import UserActivity

    UserActivity.log(
        UserActivity.DATA_IMPORT,
        module="kobo",
        details=f"Kobo import: {num_created} created, {num_enriched} enriched, "
        f"{num_corrected} corrected, {num_ignored} ignored, {num_invalid} removed.",
        extra=counts,
    )
    return counts


@app.task()
def import_odk_forms():
    """Legacy alias to keep historical schedule names working."""
    return pull_odk_scheduled()


def _odk_form_configs(schedule_key=None):
    configs = get_odk_form_configs(
        default_project_id=getattr(settings, "ODK_DEFAULT_PROJECT_ID", None)
    )
    if schedule_key is None:
        return configs
    return [cfg for cfg in configs if cfg.get("schedule_key") == schedule_key]


@app.task(rate_limit="10/m")
def pull_odk_scheduled(schedule_key=None):
    service = ODKPullService(
        default_project_id=getattr(settings, "ODK_DEFAULT_PROJECT_ID", None)
    )
    configs = _odk_form_configs(schedule_key=schedule_key)
    if not configs:
        logger.info(
            "No ODK forms configured for scheduled pull",
            extra={"schedule_key": schedule_key},
        )
        return {}
    summary = service.pull_forms(configs)
    logger.info(
        "ODK scheduled pull complete",
        extra={"schedule_key": schedule_key, "summary": summary},
    )

    from va_explorer.users.models import UserActivity

    UserActivity.log(
        UserActivity.DATA_IMPORT,
        module="odk",
        details=f"ODK scheduled pull complete (schedule={schedule_key or 'all'}).",
        extra={"schedule_key": schedule_key, "summary": summary},
    )
    return summary


@app.task()
def assign_death_ids_task():
    """Periodic sweeper that assigns Death IDs to Death records without them.

    Delegates to the shared service in ``utils/death_ids.py`` (same code path
    as the ``assign_death_ids`` command and the import hooks); the service
    serialises concurrent runs via a PostgreSQL advisory lock.
    """
    from va_explorer.va_data_management.utils.death_ids import assign_death_ids

    try:
        summary = assign_death_ids()
        if summary["assigned"]:
            logger.info("Assigned death_ids to %s death(s)", summary["assigned"])
        for ward, info in summary["skipped_wards"].items():
            logger.warning(
                "Skipped %s death(s) in ward '%s' (%s)",
                info["count"],
                ward,
                info["reason"],
            )
        return {
            "status": "success",
            "deaths_assigned": summary["assigned"],
            "total_missing": summary["total_missing"],
            "wards_skipped": len(summary["skipped_wards"]),
            "malformed_existing": len(summary["malformed_existing"]),
            "overflow_wards": [o["ward"] for o in summary["overflow_wards"]],
        }
    except Exception as e:
        logger.exception("Error in assign_death_ids_task: %s", e)
        return {"status": "error", "error": str(e)}
