import logging
import os
import sys
from datetime import timedelta

from django.apps import AppConfig
from django.conf import settings
from django.db import OperationalError, ProgrammingError, connection
from django.utils import timezone

from va_explorer.va_data_management.odk.config import get_odk_form_configs

logger = logging.getLogger(__name__)


class VaDataManagementConfig(AppConfig):
    name = "va_explorer.va_data_management"

    def ready(self):
        import va_explorer.va_data_management.signals  # noqa: F401

        # Disable via env flag
        flag = os.getenv("ODK_PULL_ON_STARTUP", "1").strip().lower()
        if flag in ("0", "false", "no", "off"):
            logger.info("ODK pull on startup disabled via ODK_PULL_ON_STARTUP=%s", flag)
            return

        # Skip during management commands
        management_cmds_to_skip = {
            "migrate",
            "makemigrations",
            "collectstatic",
            "dbshell",
            "shell",
            "createsuperuser",
            "check",
            "showmigrations",
            "flush",
            "loaddata",
            "dumpdata",
            "test",
        }

        if any(cmd in sys.argv for cmd in management_cmds_to_skip):
            logger.info(
                "Skipping ODK pull on startup for management command: %s", sys.argv
            )
            return

        self._trigger_stale_odk_pull()

    def _trigger_stale_odk_pull(self):
        from va_explorer.va_data_management.models import ODKPullState
        from va_explorer.va_data_management.odk.service import ODKPullService

        if not self._odk_state_table_exists(ODKPullState):
            return

        configs = self._get_target_form_configs()
        if not configs:
            return

        cutoff = timezone.now() - timedelta(hours=24)
        stale_configs = []
        for cfg in configs:
            state = (
                ODKPullState.objects.filter(
                    form_id=cfg["form_id"], project_id=cfg["project_id"]
                )
                .only("last_submission_at")
                .first()
            )
            last_submission_at = getattr(state, "last_submission_at", None)
            if last_submission_at and timezone.is_naive(last_submission_at):
                last_submission_at = timezone.make_aware(
                    last_submission_at, timezone=timezone.utc
                )
            if not last_submission_at or last_submission_at < cutoff:
                stale_configs.append(cfg)

        if not stale_configs:
            return

        service = ODKPullService(
            default_project_id=getattr(settings, "ODK_DEFAULT_PROJECT_ID", None)
        )
        summary = service.pull_forms(stale_configs, ignore_frequency=True)
        logger.info("ODK startup pull complete", extra={"summary": summary})

    def _get_target_form_configs(self):
        target_names = {"pregnancy", "pregnancy_outcome", "death", "verbal_autopsy"}
        configs = []
        for cfg in get_odk_form_configs(
            default_project_id=getattr(settings, "ODK_DEFAULT_PROJECT_ID", None)
        ):
            if cfg.get("form_name") not in target_names:
                continue
            if cfg.get("enabled") is False:
                continue
            configs.append(cfg)
        return configs

    def _odk_state_table_exists(self, odk_pull_state_model):
        try:
            table_names = connection.introspection.table_names()
        except (OperationalError, ProgrammingError):
            return False
        return odk_pull_state_model._meta.db_table in table_names
