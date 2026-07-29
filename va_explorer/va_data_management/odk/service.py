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
    Death,
    ODKFormChoice,
    ODKPullLock,
    ODKPullState,
)
from va_explorer.va_data_management.utils.geopoint import (
    _has_meaningful_value as _gp_meaningful,
)
from va_explorer.va_data_management.utils.geopoint import (
    is_bare_geopoint_component,
    merge_geopoint_into,
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
    "social_mobilization": "key",
}

# After list_submissions strips group-name prefixes (e.g. "event_gps-Latitude" →
# "Latitude"), these per-form renames restore the full model field name before
# normalize_dataframe_columns runs.
FORM_PRE_NORMALIZE_RENAMES = {
    "social_mobilization": {
        "Latitude": "event_gps_latitude",
        "Longitude": "event_gps_longitude",
        "Altitude": "event_gps_altitude",
        "Accuracy": "event_gps_accuracy",
    }
}

# Maps form_name → OData table_name for ODK repeat-group child forms.
# ODK Central serves a repeat group from the same form_id but a different table.
FORM_REPEAT_TABLE_MAP = {
    "social_mobilization_rumor_log": "Submissions.rumors_log_repeat",
    "vasa_care_seeking_neonate": "Submissions.br_N2213",
    "vasa_care_seeking_child": "Submissions.AD_C3253_1",
    "vasa_care_seeking_adult": "Submissions.ar_A4252",
}

# Maps VASA repeat form_name → the mapping's repeat-group key.
VASA_REPEAT_FORMS = {
    "vasa_care_seeking_neonate": "br_N2213",
    "vasa_care_seeking_child": "AD_C3253_1",
    "vasa_care_seeking_adult": "ar_A4252",
}

# Maps form_name → (ODK split-column prefix, single target model field).
# The four ODK component columns (e.g. PE-25-Latitude/Longitude/Altitude/Accuracy)
# are composed into the existing single GPS TextField before column normalisation.
FORM_GEOPOINT_MAP = {
    "pregnancy": ("PE-25", "PE_25"),
    "pregnancy_outcome": ("gps", "gps"),
    "death": ("DE-34", "DE_34"),
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


def _strip_group_prefix(column):
    """Collapse a split-GPS column to its bare component name.

    ODK Central returns geopoints as split columns qualified by their group
    (``PE-25-Latitude``), and the geopoint merge expects to find them by
    component. Only that trailing segment is safe to strip: the death form
    names its questions with hyphens (``DE-01``, ``DE-32_mccd``), so stripping
    unconditionally turned them into ``01``/``32_mccd``, which match no model
    field and were silently dropped -- every pulled death landed with empty
    DE_* columns. Everything that is not a GPS component keeps its full name
    and is resolved later by ``normalize_dataframe_columns``.
    """
    text = str(column)
    tail = text.rsplit("-", 1)[-1]
    if tail != text and is_bare_geopoint_component(tail):
        return tail
    return text


def _run_dq_after_import(form_name: str, created: int, updated: int) -> None:
    """Run data-quality checks after an ODK import, fail-safe.

    A DQ error must never break ingestion, so failures are logged and swallowed.
    """
    if (created + updated) <= 0:
        return
    try:
        from va_explorer.va_data_management.utils.dq_specs import run_dq_for_form

        run_dq_for_form(form_name)
    except Exception:
        logger.exception(
            "Data quality checks failed after ODK import of form %s", form_name
        )


def _assign_death_ids_after_import(
    *,
    form_name: str,
    keys: Optional[Iterable[Any]] = None,
    objects: Optional[Iterable[Any]] = None,
) -> None:
    """Mint Death IDs for death rows touched by a bulk import.

    Bulk create/update bypasses the Death post_save signal, so ODK imports need
    an explicit hook. The assignment service is idempotent and only fills blank
    death_id values.
    """
    if form_name != "death":
        return

    pks = [getattr(obj, "pk", None) for obj in objects or []]
    pks = [pk for pk in pks if pk is not None]

    if not pks and keys:
        import_keys = [str(key).strip() for key in keys if str(key or "").strip()]
        if import_keys:
            pks = list(
                Death.objects.filter(key__in=import_keys).values_list("id", flat=True)
            )

    if not pks:
        return

    from va_explorer.va_data_management.utils.death_ids import assign_death_ids

    summary = assign_death_ids(death_ids=pks)
    if summary.get("assigned"):
        logger.info(
            "Assigned death_ids to %d death row(s) after ODK import",
            summary["assigned"],
        )
    for ward, info in summary.get("skipped_wards", {}).items():
        logger.warning(
            "No death_id assigned after ODK import for %d death row(s) in ward %s: %s",
            info.get("count", 0),
            ward,
            info.get("reason"),
        )


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

    pre_renames = FORM_PRE_NORMALIZE_RENAMES.get(form_name, {})
    if pre_renames:
        df = df.rename(
            columns={k: v for k, v in pre_renames.items() if k in df.columns}
        )

    geopoint_cfg = FORM_GEOPOINT_MAP.get(form_name)
    if geopoint_cfg:
        source_prefix, gps_field = geopoint_cfg
        df = merge_geopoint_into(
            df, source_prefix=source_prefix, target_field=gps_field
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
        _assign_death_ids_after_import(form_name=form_name, objects=objects)
        _run_dq_after_import(form_name, len(objects), 0)
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

    gps_target = FORM_GEOPOINT_MAP.get(form_name, (None, None))[1]

    to_create = []
    to_update = []
    for record in records:
        key = record[unique_field]
        existing = existing_by_key.get(key)
        if existing is None:
            to_create.append(model(**record))
            continue

        for field in update_fields:
            new_val = record.get(field)
            if (
                field == gps_target
                and not _gp_meaningful(new_val)
                and _gp_meaningful(getattr(existing, field, None))
            ):
                continue
            setattr(existing, field, new_val)
        to_update.append(existing)

    with transaction.atomic():
        if to_create:
            model.objects.bulk_create(to_create)
        if to_update:
            model.objects.bulk_update(to_update, fields=update_fields)

    _assign_death_ids_after_import(form_name=form_name, keys=keys)
    _run_dq_after_import(form_name, len(to_create), len(to_update))

    return {
        "created": len(to_create),
        "updated": len(to_update),
        "ignored": ignored_count,
        "duplicate_rows": duplicate_rows,
    }


# Rumour descriptions that carry no real information (typos, placeholders, negations).
# Public so management commands can import it and avoid duplication.
# Comparison is case-insensitive and whitespace-stripped.
RUMOR_JUNK_DESCRIPTIONS: frozenset = frozenset(
    d.lower()
    for d in [
        "0",
        "12",
        "3",
        "4",
        "E",
        "N",
        "O",
        "N0",
        "Ni",
        "No",
        "Ye",
        "999",
        "Csa",
        "Ele",
        "Mil",
        "Ni5",
        "Nii",
        "Nil",
        "Nmx",
        "Non",
        "Srs",
        "Ues",
        "Yes",
        "Good",
        "Nill",
        "No e",
        "None",
        "Npne",
        ".none",
        "Birth",
        "Death",
        "No ne",
        "None.",
        "Junkies",
        "Nothing",
        "Postive",
        "Unknown",
        "Ni rumor",
        "No rumor",
        "About srs",
        "I'm a CSA",
        "Ni rumour",
        "No rumors",
        "No rumour",
        "N0 rumours",
        "No rumours",
        "Norl Rumor",
        "Never heard",
        "Nor rumours",
        "No rumourals",
    ]
)

# Keep private alias for backward compat within this module
_RUMOR_JUNK_DESCRIPTIONS = RUMOR_JUNK_DESCRIPTIONS

_RUMOR_UPDATE_FIELDS = [
    "rumor_description",
    "rumor_source",
    "community_group_name",
    "person_name",
    "offline_channel_name",
    "online_page_group_name",
]


def purge_junk_rumor_logs() -> int:
    """Delete any RumorLog rows whose description is in RUMOR_JUNK_DESCRIPTIONS.

    Safe to call repeatedly — idempotent once junk records have been removed.
    Returns the number of records deleted.
    """
    from django.db.models.functions import Lower

    from va_explorer.va_data_management.models import RumorLog

    junk_ids = list(
        RumorLog.objects.annotate(desc_lower=Lower("rumor_description"))
        .filter(desc_lower__in=RUMOR_JUNK_DESCRIPTIONS)
        .values_list("id", flat=True)
    )
    if not junk_ids:
        return 0
    deleted, _ = RumorLog.objects.filter(id__in=junk_ids).delete()
    return deleted


def import_rumor_log_records(df: pd.DataFrame) -> Dict[str, int]:
    """Load RumorLog child records from a DataFrame.

    Handles both the ODK OData API path (columns after group-prefix stripping:
    ``__id`` for the child key, ``id`` for the parent submission key) and the
    direct CSV / test path (``KEY`` / ``PARENT_KEY`` columns).

    Records are upserted by KEY; rows with junk descriptions are skipped.
    """
    from va_explorer.va_data_management.models import RumorLog, SocialMobilization
    from va_explorer.va_data_management.utils.loading import normalize_dataframe_columns

    # Normalise key columns for both CSV and ODK API paths.
    rename: Dict[str, str] = {}
    if "KEY" in df.columns:
        rename["KEY"] = "key"
    if "PARENT_KEY" in df.columns:
        rename["PARENT_KEY"] = "parent_key"
    # ODK OData: __id = child record UUID; id (from __Submissions-id) = parent key
    if "__id" in df.columns and "key" not in df.columns and "KEY" not in df.columns:
        rename["__id"] = "key"
    if (
        "id" in df.columns
        and "parent_key" not in df.columns
        and "PARENT_KEY" not in df.columns
    ):
        rename["id"] = "parent_key"
    if rename:
        df = df.rename(columns=rename)

    if "parent_key" not in df.columns:
        logger.warning(
            "import_rumor_log_records: no parent_key column; skipping all rows"
        )
        return {"created": 0, "updated": 0, "skipped": 0}

    df["parent_key"] = df["parent_key"].astype(str).str.strip()
    df = df[
        df["parent_key"].notna()
        & (df["parent_key"] != "")
        & (df["parent_key"] != "nan")
    ]
    if df.empty:
        return {"created": 0, "updated": 0, "skipped": 0}

    if "key" in df.columns:
        df["key"] = df["key"].astype(str).str.strip()
        df = df[df["key"].notna() & (df["key"] != "") & (df["key"] != "nan")]
        df = df.drop_duplicates(subset=["key"], keep="last")

    parent_keys = df["parent_key"].copy()
    child_keys = (
        df["key"].copy()
        if "key" in df.columns
        else pd.Series([None] * len(df), index=df.index)
    )

    df = normalize_dataframe_columns(df, RumorLog)
    df["parent_key"] = parent_keys
    df["key"] = child_keys

    # Snapshot existing keys before upsert for created/updated counting
    csv_keys = [
        str(k).strip()
        for k in child_keys
        if not pd.isnull(k) and str(k).strip() not in ("", "nan")
    ]
    existing_keys = (
        set(RumorLog.objects.filter(key__in=csv_keys).values_list("key", flat=True))
        if csv_keys
        else set()
    )

    sm_map = {
        sm.key: sm
        for sm in SocialMobilization.objects.exclude(key__isnull=True).only("key")
    }

    objects = []
    skipped = 0
    for _, row in df.iterrows():
        parent_key = (row.get("parent_key") or "").strip()
        social_mobilization = sm_map.get(parent_key)
        if not social_mobilization:
            skipped += 1
            continue

        description = str(row.get("rumor_description") or "").strip()
        if description.lower() in _RUMOR_JUNK_DESCRIPTIONS:
            skipped += 1
            continue

        data = row.drop(["parent_key"]).to_dict()
        if "key" in data and not pd.isnull(data.get("key")):
            data["key"] = str(data["key"]).strip() or None
        else:
            data.pop("key", None)
        obj = RumorLog(**{k: _clean_import_value(v) for k, v in data.items()})
        obj.social_mobilization = social_mobilization
        objects.append(obj)

    if objects:
        RumorLog.objects.bulk_create(
            objects,
            batch_size=2000,
            update_conflicts=True,
            unique_fields=["key"],
            update_fields=_RUMOR_UPDATE_FIELDS,
        )

    created = sum(1 for obj in objects if (obj.key or "") not in existing_keys)
    updated = len(objects) - created
    return {"created": created, "updated": updated, "skipped": skipped}


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
                    table_name=FORM_REPEAT_TABLE_MAP.get(form_name),
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
        table_name: Optional[str] = None,
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
            if table_name:
                get_table_kwargs["table_name"] = table_name
            data = client.submissions.get_table(**get_table_kwargs)
            records = data.get("value", [])
            if not records:
                return pd.DataFrame(), {
                    "submission_at": None,
                    "server_submission_at": None,
                }

            df = pd.DataFrame.from_records(records)
            df.columns = [_strip_group_prefix(c) for c in df.columns]

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
        if form_name == "social_mobilization_rumor_log":
            return import_rumor_log_records(df)
        if form_name == "vasa":
            from va_explorer.va_data_management.utils.vasa_loading import (
                load_vasa_from_dataframes,
            )

            return load_vasa_from_dataframes(df)
        if form_name in VASA_REPEAT_FORMS:
            from va_explorer.va_data_management.utils.vasa_loading import (
                load_vasa_repeat_dataframe,
            )

            return load_vasa_repeat_dataframe(VASA_REPEAT_FORMS[form_name], df)
        if form_name and form_name in FORM_MODEL_MAP:
            return import_dataframe_records(form_name, df)
        with transaction.atomic():
            results = load_records_from_dataframe(df, upsert_gps=True)
        return {
            "created": len(results["created"]),
            "ignored": len(results["ignored"]),
            "outdated": len(results["outdated"]),
            "enriched": len(results.get("enriched", [])),
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
