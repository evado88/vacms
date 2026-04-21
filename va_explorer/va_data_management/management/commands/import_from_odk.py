import os
from datetime import datetime, timedelta
from typing import List, Optional

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from va_explorer.va_data_management.odk.config import resolve_odk_form_configs
from va_explorer.va_data_management.odk.service import ODKPullService


class Command(BaseCommand):
    help = "Loads a verbal autopsy data from ODK into the database"

    def add_arguments(self, parser):
        parser.add_argument(
            "--form-id",
            action="append",
            dest="form_ids",
            help="ODK xmlFormId to pull (can be repeated); defaults to configured forms.",
        )
        parser.add_argument(
            "--project-id",
            type=int,
            required=False,
            default=os.environ.get("ODK_PROJECT_ID"),
            help="Override project ID (defaults to ODK_PROJECT_ID env/setting).",
        )
        parser.add_argument(
            "--since",
            type=str,
            required=False,
            help="ISO timestamp or relative window (e.g., 7d) to pull incremental data.",
        )
        parser.add_argument(
            "--full-refresh",
            action="store_true",
            help="Ignore stored state and pull all submissions (idempotent).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch data but do not write to DB; still updates state timestamps.",
        )
        parser.add_argument(
            "--no-attachments",
            action="store_true",
            help="Skip attachment downloads.",
        )

    def handle(self, *args, **options):
        _ = args  # unused
        form_ids: Optional[List[str]] = options.get("form_ids")
        project_id = options.get("project_id")
        since_raw = options.get("since")
        since_dt = self._parse_since(since_raw) if since_raw else None
        full_refresh = bool(options.get("full_refresh"))
        dry_run = bool(options.get("dry_run"))
        no_attachments = bool(options.get("no_attachments"))

        service = ODKPullService(default_project_id=project_id)
        form_configs = self._resolve_forms(form_ids, project_id)
        if not form_configs:
            raise CommandError("No form IDs provided or configured.")

        summary = service.pull_forms(
            form_configs,
            since=since_dt,
            full_refresh=full_refresh,
            dry_run=dry_run,
            no_attachments=no_attachments,
            ignore_frequency=True,
        )
        self._print_summary(summary)

    def _resolve_forms(self, form_ids: Optional[List[str]], project_id: Optional[int]):
        return resolve_odk_form_configs(form_ids=form_ids, project_id=project_id)

    def _parse_since(self, raw: str) -> datetime:
        if raw.endswith("d") and raw[:-1].isdigit():
            days = int(raw[:-1])
            return timezone.now() - timedelta(days=days)
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:  # pragma: no cover - defensive
            raise CommandError(f"Could not parse --since value '{raw}'") from exc
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone=timezone.utc)
        return parsed

    def _print_summary(self, summary):
        for form_id, info in summary.items():
            if info.get("skipped"):
                self.stdout.write(
                    f"[{form_id}] skipped ({info.get('reason', 'unknown')})"
                )
                continue
            counts = {k: v for k, v in info.items() if k not in ("status",)}
            self.stdout.write(f"[{form_id}] status={info.get('status')} {counts}")
