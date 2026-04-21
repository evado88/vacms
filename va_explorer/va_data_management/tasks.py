import logging
from collections import defaultdict

from celery.schedules import crontab
from django.conf import settings
from django.db.models import Q

from config.celery_app import app
from config.settings.base import env
from va_explorer.va_data_management.management.commands.import_from_kobo import (
    BATCH_SIZE,
)
from va_explorer.va_data_management.models import (
    ClusterLocationCodes,
    Death,
    VerbalAutopsy,
)
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


# Result of tasks need to be json serializable so return dicts.
@app.task()
def run_coding_algorithms():
    """Orchestrator: snapshot uncoded VA IDs and dispatch one batch task per 500 VAs.

    Splitting into independent sub-tasks prevents any single Celery call from
    running longer than the worker's soft time limit on large backlogs.
    """
    va_ids = list(
        VerbalAutopsy.objects.filter(causes__isnull=True).values_list("id", flat=True)
    )
    if not va_ids:
        return {"num_batches": 0, "total_uncoded": 0}

    num_batches = 0
    for i in range(0, len(va_ids), _CODING_BATCH_SIZE):
        batch_ids = va_ids[i : i + _CODING_BATCH_SIZE]
        run_coding_algorithms_batch.delay(batch_ids)
        num_batches += 1

    logger.info(
        "Coding orchestrator dispatched %d batch task(s) for %d VAs",
        num_batches,
        len(va_ids),
    )
    return {"num_batches": num_batches, "total_uncoded": len(va_ids)}


@app.task()
def run_coding_algorithms_batch(va_ids):
    """Code a single batch of up to _CODING_BATCH_SIZE VAs identified by their PKs."""
    verbal_autopsies = list(
        VerbalAutopsy.objects.filter(id__in=va_ids, causes__isnull=True).order_by("id")
    )
    if not verbal_autopsies:
        return {"num_coded": 0, "num_issues": 0}

    causes, issues = coding.run_interva5(verbal_autopsies)
    return {"num_coded": len(causes), "num_issues": len(issues)}


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
        num_corrected = num_corrected + len(results["corrected"])
        num_invalid = num_invalid + len(results["removed"])

    return {
        "num_ignored": num_ignored,
        "num_outdated": num_outdated,
        "num_created": num_created,
        "num_corrected": num_corrected,
        "num_removed": num_invalid,
    }


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
    return summary


@app.task()
def assign_death_ids_task():
    """Periodic task to assign death_ids to Death records without them.

    Runs every 5 minutes to batch-assign sequential death_ids based on cluster codes.
    Format: cluster_code + 4-digit sequential number (e.g., 1213SM0001, 1213SM0002).
    """
    try:
        # Query deaths without death_id
        deaths_qs = Death.objects.filter(Q(death_id__isnull=True) | Q(death_id=""))
        total_missing = deaths_qs.count()

        if total_missing == 0:
            logger.debug("No deaths without death_ids found")
            return {
                "status": "success",
                "deaths_assigned": 0,
                "message": "No deaths to process",
            }

        logger.info("Found %s deaths without death_ids", total_missing)

        # Group deaths by ward
        deaths_by_ward = defaultdict(list)
        for death in deaths_qs:
            ward = (death.ward or "").strip()
            if ward:
                deaths_by_ward[ward].append(death)

        if not deaths_by_ward:
            logger.warning("No wards found in death records")
            return {
                "status": "success",
                "deaths_assigned": 0,
                "message": "No wards in death records",
            }

        # Build mapping: ward_code -> cluster_code
        ward_to_cluster = {}
        missing_wards = []

        for ward_name in deaths_by_ward:
            cluster_loc = ClusterLocationCodes.objects.filter(
                ward_code__iexact=ward_name
            ).first()

            if cluster_loc and cluster_loc.cluster_code:
                ward_to_cluster[ward_name] = cluster_loc.cluster_code
            else:
                missing_wards.append(ward_name)

        if missing_wards:
            logger.warning(
                "No cluster code found for %s ward(s): %s",
                len(missing_wards),
                ", ".join(missing_wards[:5]),
            )

        # Assign death_ids
        deaths_to_update = []
        assignments_count = 0

        for ward_name, deaths_in_ward in deaths_by_ward.items():
            cluster_code = ward_to_cluster.get(ward_name)

            if not cluster_code:
                logger.warning(
                    "Skipping %s death(s) in ward '%s' (no cluster code)",
                    len(deaths_in_ward),
                    ward_name,
                )
                continue

            # Get existing deaths in this ward to find next sequence
            existing_deaths = Death.objects.filter(ward__iexact=ward_name).exclude(
                Q(death_id__isnull=True) | Q(death_id="")
            )

            # Extract sequence numbers from existing death_ids
            existing_sequences = []
            for existing_death in existing_deaths:
                if existing_death.death_id and existing_death.death_id.startswith(
                    cluster_code
                ):
                    suffix = existing_death.death_id[len(cluster_code) :]
                    try:
                        seq_num = int(suffix)
                        existing_sequences.append(seq_num)
                    except ValueError:
                        pass

            next_seq = max(existing_sequences) + 1 if existing_sequences else 1

            # Assign sequential death_ids
            for death in sorted(deaths_in_ward, key=lambda d: d.id):
                death_id = f"{cluster_code}{next_seq:04d}"
                death.death_id = death_id
                deaths_to_update.append(death)
                assignments_count += 1
                next_seq += 1

        if not deaths_to_update:
            logger.info("No deaths to update (all missing cluster codes)")
            return {
                "status": "success",
                "deaths_assigned": 0,
                "message": "No deaths with valid cluster codes",
            }

        # Persist changes
        Death.objects.bulk_update(deaths_to_update, ["death_id"], batch_size=1000)

        logger.info("Successfully assigned death_ids to %s death(s)", assignments_count)
        return {
            "status": "success",
            "deaths_assigned": assignments_count,
            "wards_processed": len(ward_to_cluster),
            "wards_skipped": len(missing_wards),
        }

    except Exception as e:
        logger.exception("Error in assign_death_ids_task: %s", e)
        return {"status": "error", "error": str(e)}
