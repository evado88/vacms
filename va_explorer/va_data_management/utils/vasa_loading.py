"""Ingestion for VASA submissions (main record + social autopsy + repeats).

Mirrors the VA loader's shape (strip machinery, map to WHO columns, parse dates,
idempotent upsert by instanceID) but adds the VASA-specific concerns:

* VASA pseudo-names (``Q1xxx``/``N2xxx``/...) are mapped to WHO ``Idxxxxx`` columns
  via the generated :data:`PSEUDO_TO_WHO`; duplicated WHO Ids across the mutually
  exclusive instrument sections are coalesced (a same-Id collision is logged).
* VASA select values are numeric codes; :data:`VALUE_CROSSWALK` decodes them into
  the WHO value-space the coding pipeline expects (``1`` -> ``yes``).
* empty-``WHO_name`` answers are routed to the separate ``VASASocialAutopsy``
  record so they can never enter coding input.
* the three care-seeking repeat groups are linked to their parent by KEY.
"""

import logging

import pandas as pd
from django.db import transaction

from ..models import (
    VASA,
    VASACareSeekingAdult,
    VASACareSeekingChild,
    VASACareSeekingNeonate,
    VASASocialAutopsy,
)
from ..vasa.field_map import PSEUDO_TO_WHO, VALUE_CROSSWALK
from ..vasa.model_fields import REPEAT_FIELD_DEFS, SA_FIELD_DEFS
from .date_parsing import parse_date
from .geopoint import merge_geopoint_into
from .loading import _has_meaningful_value

logger = logging.getLogger(__name__)

VASA_FIELD_NAMES = {f.name for f in VASA._meta.fields}
SA_COLUMN_TO_FIELD = {column: field for column, field, _label in SA_FIELD_DEFS}
REPEAT_COLUMN_TO_FIELD = {
    group: {column: field for column, field, _label in defs}
    for group, defs in REPEAT_FIELD_DEFS.items()
}
REPEAT_MODELS = {
    "br_N2213": VASACareSeekingNeonate,
    "AD_C3253_1": VASACareSeekingChild,
    "ar_A4252": VASACareSeekingAdult,
}

# Location / device columns stored directly on the VASA record. The final form
# uses constituency/ward/ea; legacy exports use province/area/hospital — both are
# tolerated (whichever are present are stored).
ADMIN_COLUMNS = (
    "deviceid",
    "province",
    "district",
    "constituency",
    "ward",
    "ea",
    "area",
    "hospital",
)

# Which instrument section was completed (first populated trigger wins).
_INSTRUMENT_TRIGGERS = (("N2001", "neonate"), ("C3001", "child"), ("A4001", "adult"))

# WHO Ids that, for a female adult, mark the death as maternal.
_MATERNAL_WHO_IDS = frozenset(
    f"Id{n}" for n in range(10294, 10311)  # pregnancy / maternal block
)

DATE_WHO_FIELDS = ("Id10023", "Id10012")


def _mapped_value(pseudo, raw_value):
    """Decode a VASA select code to its WHO value; pass other types through.

    Handles ``select_multiple`` (space-separated codes) by decoding each token
    and re-joining comma-separated, matching the VA multi-select convention.
    """
    crosswalk = VALUE_CROSSWALK.get(pseudo)
    if crosswalk is None:
        return raw_value
    tokens = str(raw_value).split()
    return ",".join(crosswalk.get(token, token) for token in tokens)


def _detect_instrument(raw, who_values):
    instrument = ""
    for trigger, name in _INSTRUMENT_TRIGGERS:
        if _has_meaningful_value(raw.get(trigger)):
            instrument = name
            break
    if (
        instrument == "adult"
        and who_values.get("Id10019") == "female"
        and any(_has_meaningful_value(who_values.get(wid)) for wid in _MATERNAL_WHO_IDS)
    ):
        instrument = "maternal"
    return instrument


def _build_vasa_record(raw, raw_ci):
    who_values = {}
    collisions = []
    for pseudo, who in PSEUDO_TO_WHO.items():
        raw_value = raw.get(pseudo)
        if not _has_meaningful_value(raw_value):
            continue
        value = _mapped_value(pseudo, raw_value)
        if who in who_values:
            if who_values[who] != value:
                collisions.append((who, pseudo))
            continue
        who_values[who] = value

    record = {who: val for who, val in who_values.items() if who in VASA_FIELD_NAMES}

    for column in ADMIN_COLUMNS:
        value = raw_ci.get(column)
        if _has_meaningful_value(value):
            record[column] = value

    record["instanceid"] = raw_ci.get("instanceid") or raw_ci.get("key") or ""
    record["key"] = raw_ci.get("key") or record["instanceid"]
    submission = raw_ci.get("submissiondate")
    if _has_meaningful_value(submission):
        record["submissiondate"] = submission
    if _has_meaningful_value(raw_ci.get("geopoint")):
        record["geopoint"] = raw_ci["geopoint"]
    record["instrument"] = _detect_instrument(raw, who_values)

    for field in DATE_WHO_FIELDS:
        if field in record:
            record[field] = parse_date(record[field], strict=False)

    return record, collisions


def _build_social_autopsy(raw):
    values = {}
    for column, field in SA_COLUMN_TO_FIELD.items():
        raw_value = raw.get(column)
        if _has_meaningful_value(raw_value):
            values[field] = str(raw_value)
    return values


def _load_repeat(group, df, vasa_by_key):
    model = REPEAT_MODELS[group]
    column_to_field = REPEAT_COLUMN_TO_FIELD[group]
    stats = {"created": 0, "updated": 0, "skipped": 0}
    for raw in df.to_dict(orient="records"):
        raw_ci = {str(k).strip().lower(): v for k, v in raw.items()}
        parent_key = str(raw_ci.get("parent_key") or "").strip()
        child_key = str(raw_ci.get("key") or "").strip()
        if not _has_meaningful_value(parent_key):
            stats["skipped"] += 1
            continue
        vasa = (
            vasa_by_key.get(parent_key)
            or VASA.all_objects.filter(key=parent_key).first()
        )
        if vasa is None:
            stats["skipped"] += 1
            continue
        defaults = {
            field: str(raw.get(column))
            for column, field in column_to_field.items()
            if _has_meaningful_value(raw.get(column))
        }
        defaults["parent_key"] = parent_key
        _obj, created = model.objects.update_or_create(
            vasa=vasa, key=child_key, defaults=defaults
        )
        stats["created" if created else "updated"] += 1
    return stats


def load_vasa_from_dataframes(main_df, repeat_dfs=None):
    """Load VASA submissions (idempotent upsert by instanceID) plus SA + repeats.

    Per-record error isolation: one bad submission is logged and skipped without
    aborting the batch. Returns a summary dict.
    """
    main_df = merge_geopoint_into(main_df, source_prefix="GPS", target_field="geopoint")
    summary = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "collisions": 0,
        "repeats": {},
    }
    vasa_by_key = {}
    vasa_ids = []

    for raw in main_df.to_dict(orient="records"):
        raw_ci = {str(k).strip().lower(): v for k, v in raw.items()}
        try:
            with transaction.atomic():
                record, collisions = _build_vasa_record(raw, raw_ci)
                if not _has_meaningful_value(record.get("instanceid")):
                    summary["skipped"] += 1
                    continue
                if collisions:
                    summary["collisions"] += len(collisions)
                    logger.warning(
                        "VASA %s: %d duplicate WHO-Id collisions across sections: %s",
                        record["instanceid"],
                        len(collisions),
                        collisions,
                    )
                existing = VASA.all_objects.filter(
                    instanceid=record["instanceid"]
                ).first()
                if existing is not None:
                    for key, value in record.items():
                        setattr(existing, key, value)
                    existing.save()
                    vasa = existing
                    summary["updated"] += 1
                else:
                    vasa = VASA.objects.create(**record)
                    summary["created"] += 1

                VASASocialAutopsy.objects.update_or_create(
                    vasa=vasa, defaults=_build_social_autopsy(raw)
                )
                if _has_meaningful_value(vasa.key):
                    vasa_by_key[str(vasa.key).strip()] = vasa
                vasa_ids.append(vasa.pk)
        except Exception:
            summary["errors"] += 1
            logger.exception("Failed to load VASA submission")

    for group, df in (repeat_dfs or {}).items():
        if group not in REPEAT_MODELS:
            logger.warning("Unknown VASA repeat group %s; skipping", group)
            continue
        if df is None or df.empty:
            summary["repeats"][group] = {"created": 0, "updated": 0, "skipped": 0}
            continue
        summary["repeats"][group] = _load_repeat(group, df, vasa_by_key)

    # VASA records arrive with a field-entered DeathID; validate them all
    # (format/checksum + existence in the death register) and flag problems as
    # DataQualityIssues. Never blocks or mutates the imported records.
    from va_explorer.va_data_management.utils.death_ids import (
        validate_vasa_death_ids,
    )

    try:
        summary["death_id_validation"] = validate_vasa_death_ids()
    except Exception:
        summary["death_id_validation"] = None
        logger.exception("VASA DeathID validation failed after load")

    # Auto-link ingested VASA records to Death records (no-op unless the feature
    # flag is enabled). VASA carries a DeathID, so this is usually an exact join.
    if vasa_ids:
        from va_explorer.va_data_management.utils.matching import (
            schedule_link_for_va_ids,
        )

        schedule_link_for_va_ids(vasa_ids, model_name="vasa", source="pyodk_ingestion")

    return summary


def load_vasa_repeat_dataframe(group, df):
    """Load a single VASA repeat group pulled independently (e.g. via ODK OData).

    Parents are resolved from the DB by ``key`` (the main form is expected to
    have been loaded already).
    """
    if group not in REPEAT_MODELS or df is None or df.empty:
        return {"created": 0, "updated": 0, "skipped": 0}
    return _load_repeat(group, df, {})


def load_vasa_from_csv(main_csv, repeat_csvs=None):
    """Convenience wrapper: read CSV paths into DataFrames and load them."""
    main_df = pd.read_csv(main_csv, dtype=str, low_memory=False).fillna("")
    repeat_dfs = {}
    for group, path in (repeat_csvs or {}).items():
        repeat_dfs[group] = pd.read_csv(path, dtype=str, low_memory=False).fillna("")
    return load_vasa_from_dataframes(main_df, repeat_dfs)
