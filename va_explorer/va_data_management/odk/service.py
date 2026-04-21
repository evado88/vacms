import logging
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from va_explorer.va_data_management.management.commands.load_form_csv import (
    FORM_MODEL_MAP,
)
from va_explorer.va_data_management.models import (
    ODKFormChoice,
    ODKPullLock,
    ODKPullState,
)
from va_explorer.va_data_management.utils.loading import (
    load_records_from_dataframe,
    normalize_dataframe_columns,
    normalize_value,
)
from va_explorer.va_data_management.utils.odk import make_pyodk_client

logger = logging.getLogger(__name__)

GLOBAL_LOCK = "__GLOBAL__"
FORM_UNIQUE_FIELD_MAP = {
    "household": "key",
    "pregnancy": "key",
    "pregnancy_outcome": "key",
    "death": "key",
    "csa_daily_tracker": "key",
}


class ODKPullLocked(Exception):
    """Raised when an ODK pull is already running."""


def load_definition_from_bytes(form_name: str, content: bytes) -> int:
    """Parse an XLSForm definition and populate ODKFormChoice."""
    survey = pd.read_excel(BytesIO(content), sheet_name="survey")
    choices = pd.read_excel(BytesIO(content), sheet_name="choices")
    label_col = None
    for col in choices.columns:
        if str(col).lower().startswith("label"):
            label_col = col
            break
    if label_col is None:
        return 0
    created = 0
    for _, srow in survey.iterrows():
        qtype = str(srow.get("type", ""))
        if not qtype.startswith("select_"):
            continue
        parts = qtype.split()
        if len(parts) < 2:
            continue
        list_name = parts[1]
        field = srow.get("name")
        if not field:
            continue
        sub = choices[choices["list_name"] == list_name]
        for _, crow in sub.iterrows():
            ODKFormChoice.objects.update_or_create(
                form_name=form_name,
                field_name=field,
                value=str(crow["name"]),
                defaults={"label": str(crow[label_col])},
            )
            created += 1
    return created


def _clean_import_value(value):
    if pd.isna(value):
        return None
    return value


def import_dataframe_records(form_name: str, df: pd.DataFrame) -> Dict[str, int]:
    """Map values using ODKFormChoice and save model records."""
    lookup: Dict[str, Dict[str, str]] = {}
    for choice in ODKFormChoice.objects.filter(form_name=form_name):
        lookup.setdefault(choice.field_name, {})[
            normalize_value(choice.value)
        ] = choice.label
    for field, mapping in lookup.items():
        if field in df.columns:
            df[field] = df[field].map(
                lambda value, _map=mapping: _map.get(normalize_value(value), value)
            )

    model = FORM_MODEL_MAP[form_name]
    df = normalize_dataframe_columns(df, model)
    df = df.loc[:, ~df.columns.duplicated()]

    unique_field = FORM_UNIQUE_FIELD_MAP.get(form_name)
    if not unique_field or unique_field not in df.columns:
        objects = [
            model(**{key: _clean_import_value(value) for key, value in row.items()})
            for row in df.to_dict(orient="records")
        ]
        model.objects.bulk_create(objects)
        return {
            "created": len(objects),
            "updated": 0,
            "ignored": 0,
            "duplicate_rows": 0,
        }

    df[unique_field] = df[unique_field].map(
        lambda value: value.strip() if isinstance(value, str) else value
    )
    before_blank_filter = len(df)
    df = df[df[unique_field].notna() & (df[unique_field].astype(str).str.strip() != "")]
    ignored_count = before_blank_filter - len(df)

    before_dedup = len(df)
    df = df.drop_duplicates(subset=[unique_field], keep="last")
    duplicate_rows = before_dedup - len(df)
    if df.empty:
        return {
            "created": 0,
            "updated": 0,
            "ignored": ignored_count,
            "duplicate_rows": duplicate_rows,
        }

    concrete_fields = [
        field.name
        for field in model._meta.concrete_fields
        if not field.auto_created and field.name != model._meta.pk.name
    ]
    update_fields = [field for field in concrete_fields if field != unique_field]
    records = [
        {
            key: _clean_import_value(value)
            for key, value in row.items()
            if key in concrete_fields
        }
        for row in df.to_dict(orient="records")
    ]
    keys = [record[unique_field] for record in records]
    existing_by_key = {
        getattr(obj, unique_field): obj
        for obj in model.objects.filter(**{f"{unique_field}__in": keys})
    }

    to_create = []
    to_update = []
    for record in records:
        key = record[unique_field]
        existing = existing_by_key.get(key)
        if existing is None:
            to_create.append(model(**record))
            continue

        for field in update_fields:
            setattr(existing, field, record.get(field))
        to_update.append(existing)

    with transaction.atomic():
        if to_create:
            model.objects.bulk_create(to_create)
        if to_update:
            model.objects.bulk_update(to_update, fields=update_fields)

    return {
        "created": len(to_create),
        "updated": len(to_update),
        "ignored": ignored_count,
        "duplicate_rows": duplicate_rows,
    }


class ODKPullService:
    """Encapsulate pyODK usage, locking, and incremental state."""

    def __init__(
        self,
        default_project_id: Optional[int] = None,
        lock_ttl_seconds: int = 1800,
        allow_attachments: bool = True,
    ):
        self.default_project_id = default_project_id or getattr(
            settings, "ODK_DEFAULT_PROJECT_ID", None
        )
        self.lock_ttl_seconds = lock_ttl_seconds
        self.allow_attachments = allow_attachments
        self._logger = logger

    def get_client(self, project_id: Optional[int] = None):
        pid = project_id or self.default_project_id
        return make_pyodk_client(pid)

    def _acquire_lock(self, form_id: str, project_id: int):
        return self._acquire(form_id=form_id, project_id=project_id)

    def _acquire_global(self, project_id: int):
        return self._acquire(form_id=GLOBAL_LOCK, project_id=project_id)

    @contextmanager
    def _acquire(self, form_id: str, project_id: int):
        now = timezone.now()
        expires = now + timedelta(seconds=self.lock_ttl_seconds)
        with transaction.atomic():
            lock, created = (
                ODKPullLock.objects.select_for_update()
                .filter(form_id=form_id, project_id=project_id)
                .get_or_create(defaults={"expires_at": expires, "locked_at": now})
            )
            if not created and lock.expires_at and lock.expires_at > now:
                raise ODKPullLocked(f"Pull already running for {form_id}")
            ODKPullLock.objects.filter(pk=lock.pk).update(
                expires_at=expires,
                locked_at=now,
            )
        try:
            yield
        finally:
            ODKPullLock.objects.filter(form_id=form_id, project_id=project_id).delete()

    def pull_forms(
        self,
        form_configs: Iterable[Dict[str, Any]],
        since: Optional[datetime] = None,
        full_refresh: bool = False,
        dry_run: bool = False,
        no_attachments: bool = False,
        ignore_frequency: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        """Pull multiple forms; returns summary per form_id."""
        _ = ignore_frequency
        summary: Dict[str, Dict[str, Any]] = {}
        for cfg in form_configs:
            form_id = cfg.get("form_id")
            if not form_id:
                continue
            if cfg.get("enabled") is False:
                summary[form_id] = {"skipped": True, "reason": "disabled"}
                continue
            project_id = cfg.get("project_id") or self.default_project_id
            form_name = cfg.get("form_name")
            summary[form_id] = self.pull_form(
                form_id=form_id,
                project_id=project_id,
                form_name=form_name,
                fetch_expand=cfg.get("fetch_expand"),
                since=since,
                full_refresh=full_refresh,
                dry_run=dry_run,
                no_attachments=no_attachments,
            )
        return summary

    def pull_form(
        self,
        form_id: str,
        project_id: Optional[int] = None,
        form_name: Optional[str] = None,
        fetch_expand: Optional[str] = None,
        since: Optional[datetime] = None,
        full_refresh: bool = False,
        dry_run: bool = False,
        no_attachments: bool = False,
    ) -> Dict[str, Any]:
        """Pull a single form and load it into models."""
        project_id = project_id or self.default_project_id
        if not project_id:
            raise ValueError("project_id is required to pull ODK data")
        state, _ = ODKPullState.objects.get_or_create(
            form_id=form_id, project_id=project_id
        )
        # Only hold the project-wide global lock for full refreshes; incremental pulls
        # use only the per-form lock so concurrent pulls of different forms can proceed.
        global_ctx = self._acquire_global(project_id) if full_refresh else nullcontext()
        with global_ctx, self._acquire_lock(form_id, project_id):
            state.mark_started()
            self._logger.info(
                "Starting ODK pull",
                extra={
                    "form_id": form_id,
                    "form_name": form_name,
                    "project_id": project_id,
                    "full_refresh": full_refresh,
                    "since": since,
                    "dry_run": dry_run,
                    "fetch_expand": fetch_expand,
                },
            )
            try:
                since_dt = (
                    None
                    if full_refresh
                    else self._resolve_since_timestamp(state, since)
                )
                df, watermarks = self.list_submissions(
                    form_id=form_id,
                    project_id=project_id,
                    since=since_dt,
                    fetch_expand=fetch_expand,
                    include_attachments=not no_attachments and self.allow_attachments,
                )
                counts: Dict[str, Any] = {"fetched": len(df)}
                self._logger.info(
                    "Fetched ODK submissions",
                    extra={
                        "form_id": form_id,
                        "form_name": form_name,
                        "project_id": project_id,
                        "fetched": len(df),
                        "since": since_dt,
                        "watermarks": watermarks,
                        "dry_run": dry_run,
                    },
                )
                if dry_run or df.empty:
                    state.mark_finished(
                        ODKPullState.STATUS_SUCCESS,
                        counts=counts,
                        last_submission_at=watermarks["submission_at"],
                        last_server_submission_at=watermarks["server_submission_at"],
                    )
                    return {
                        **counts,
                        "status": ODKPullState.STATUS_SUCCESS,
                        "dry_run": dry_run,
                    }

                loaded_counts = self.upsert_into_models(df, form_name=form_name)
                counts.update(loaded_counts)
                state.mark_finished(
                    ODKPullState.STATUS_SUCCESS,
                    counts=counts,
                    last_submission_at=watermarks["submission_at"],
                    last_server_submission_at=watermarks["server_submission_at"],
                )
                self._logger.info(
                    "Finished ODK pull",
                    extra={
                        "form_id": form_id,
                        "form_name": form_name,
                        "project_id": project_id,
                        "counts": counts,
                        "last_submission_at": watermarks["submission_at"],
                        "last_server_submission_at": watermarks["server_submission_at"],
                    },
                )
                return {**counts, "status": ODKPullState.STATUS_SUCCESS}
            except Exception as exc:
                state.mark_finished(
                    ODKPullState.STATUS_FAILED, counts=state.last_counts, error=str(exc)
                )
                self._logger.exception("ODK pull failed for form %s", form_id)
                raise

    def list_submissions(
        self,
        form_id: str,
        project_id: int,
        since: Optional[datetime] = None,
        fetch_expand: Optional[str] = None,
        include_attachments: bool = False,
    ) -> Tuple[pd.DataFrame, Dict[str, Optional[datetime]]]:
        """Return submissions DataFrame and extracted watermark timestamps."""
        with self.get_client(project_id).open() as client:
            filter_expr = None
            if since:
                since_utc = self._as_utc(since)
                filter_expr = f"updatedAt ge {since_utc}"
            get_table_kwargs = {
                "form_id": form_id,
                "project_id": project_id,
                "filter": filter_expr,
            }
            if fetch_expand:
                get_table_kwargs["expand"] = fetch_expand
            data = client.submissions.get_table(**get_table_kwargs)
            records = data.get("value", [])
            if not records:
                return pd.DataFrame(), {
                    "submission_at": None,
                    "server_submission_at": None,
                }

            df = pd.DataFrame.from_records(records)
            df.columns = [c.rsplit("-", 1)[-1] for c in df.columns]

            if include_attachments:
                self.download_attachments_if_needed(
                    client=client,
                    project_id=project_id,
                    form_id=form_id,
                    records=records,
                )

            return df, self._extract_watermarks(df)

    def download_attachments_if_needed(
        self,
        client,
        project_id: int,
        form_id: str,
        records: List[Dict[str, Any]],
    ) -> None:
        """Fetch attachments for submissions that report missing attachments."""
        # Placeholder: VACMS currently stores tabular data; attachments can be added later.
        missing = [
            r
            for r in records
            if r.get("attachmentsPresent") and not r.get("attachments")
        ]
        if missing:
            self._logger.info(
                "Skipping attachment download for %s submissions (not implemented)",
                len(missing),
            )

    def upsert_into_models(
        self, df: pd.DataFrame, form_name: Optional[str]
    ) -> Dict[str, Any]:
        """Route data to appropriate loader."""
        if form_name and form_name in FORM_MODEL_MAP:
            return import_dataframe_records(form_name, df)
        with transaction.atomic():
            results = load_records_from_dataframe(df)
        return {
            "created": len(results["created"]),
            "ignored": len(results["ignored"]),
            "outdated": len(results["outdated"]),
            "corrected": len(results.get("corrected", [])),
        }

    def _extract_watermarks(self, df: pd.DataFrame) -> Dict[str, Optional[datetime]]:
        return {
            "submission_at": self._max_timestamp(df, ("updatedAt", "createdAt")),
            "server_submission_at": self._max_timestamp(
                df,
                (
                    "__system/submissionDate",
                    "__system.submissionDate",
                    "submissionDate",
                    "SubmissionDate",
                ),
            ),
        }

    def _max_timestamp(
        self,
        df: pd.DataFrame,
        columns: Tuple[str, ...],
    ) -> Optional[datetime]:
        for col in columns:
            if col in df.columns:
                ts = pd.to_datetime(df[col], errors="coerce")
                if ts.notnull().any():
                    return ts.max().to_pydatetime()
        return None

    def _resolve_since_timestamp(
        self,
        state: ODKPullState,
        since: Optional[datetime],
    ) -> Optional[datetime]:
        if since is not None:
            return since

        candidates = [
            dt
            for dt in (state.last_submission_at, state.last_server_submission_at)
            if dt is not None
        ]
        if not candidates:
            return None

        overlap_seconds = getattr(settings, "ODK_PULL_OVERLAP_SECONDS", 300)
        return min(candidates) - timedelta(seconds=max(overlap_seconds, 0))

    def _as_utc(self, dt: datetime) -> str:
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, timezone=timezone.utc)
        return (
            dt.astimezone(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
