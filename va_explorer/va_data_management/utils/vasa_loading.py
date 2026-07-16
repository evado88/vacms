"""Ingestion for VASA submissions (main record + social autopsy + repeats).

Mirrors the VA loader's shape (strip machinery, map to WHO columns, parse dates,
idempotent upsert by instanceID) but adds the VASA-specific concerns:

* VASA pseudo-names (``Q1xxx``/``N2xxx``/...) are mapped to WHO ``Idxxxxx`` columns
  via the generated :data:`PSEUDO_TO_WHO`; duplicated WHO Ids across the mutually
  exclusive instrument sections are coalesced (a same-Id collision is logged with
  both values; genuine conflicts are flagged as ``DataQualityIssue``s).
* combined pseudo-fields (:data:`COMBINED_WHO`) are split into their WHO
  components: ``Q1202`` (full name) -> ``Id10017``/``Id10018`` reconciled
  against the section-specific given-name field, ``N2005`` routed by its unit.
* VASA select values are numeric codes; :mod:`..vasa.value_decode` decodes them
  (via the generated :data:`VALUE_CROSSWALK`) into the WHO value-space the coding
  pipeline expects (``1`` -> ``yes``) and adopts them in place on the canonical
  fields. The raw codes are preserved on ``VASA.raw_answers`` (versioned by the
  crosswalk/XLSForm hash) so a crosswalk change can be replayed via
  ``manage.py redecode_vasa``. Unknown codes and generation-time fallback values
  are surfaced as ``DataQualityIssue``s (non-blocking, self-resolving).
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
from ..vasa.field_map import (
    COMBINED_WHO,
    CROSSWALK_VERSION,
    INSTRUMENT_SECTION,
    PSEUDO_TO_WHO,
    VALUE_CROSSWALK,
    VASA_FORM_ID,
)
from ..vasa.model_fields import REPEAT_FIELD_DEFS, SA_FIELD_DEFS
from ..vasa.value_decode import FALLBACK, UNKNOWN_CODE, decode_value
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

# DataQualityIssue keys for WHO-Id conflicts the loader cannot resolve. Values
# live in ``details`` (not ``keys``) so a corrected re-import updates the same
# issue instead of accumulating new ones.
NAME_CONFLICT_KEYS = {"check": "name_conflict", "who": "Id10017"}
VASA_NAME_CONFLICT_TITLE = "VASA deceased name conflict between sections"
WHO_ID_CONFLICT_TITLE = "VASA conflicting duplicate WHO-Id values across sections"
UNKNOWN_CODE_TITLE = "VASA select answer contains a code missing from the crosswalk"
FALLBACK_VALUE_TITLE = (
    "VASA decoded value is a generation-time fallback (may not be recognised "
    "by coding)"
)
_CONFLICT_KEY_CHECKS = ("name_conflict", "who_id_conflict")
_DECODE_KEY_CHECKS = ("unknown_code", "fallback_value")
# Every check the loader owns end-to-end: flagged during load, auto-resolved
# when the record re-loads without the problem.
_LOADER_KEY_CHECKS = _CONFLICT_KEY_CHECKS + _DECODE_KEY_CHECKS

# Dry-run change reports are capped to keep summaries safe on large exports.
_DRY_RUN_CHANGE_CAP = 500


def _mapped_value(pseudo, raw_value):
    """Decode a VASA select code to its WHO value; pass other types through."""
    return decode_value(pseudo, raw_value).decoded


def _decode_flag(pseudo, who, result):
    """Build the DQ flag dict for an ``unknown_code`` / ``fallback`` decode."""
    check = "unknown_code" if result.value_rule == UNKNOWN_CODE else "fallback_value"
    bad_rule = UNKNOWN_CODE if check == "unknown_code" else FALLBACK
    return {
        "check": check,
        "who": who,
        "pseudo": pseudo,
        "raw": result.raw,
        "tokens": [t.raw for t in result.tokens if t.value_rule == bad_rule],
        "decoded": result.decoded,
    }


def _decode_pseudo_answers(raw):
    """Decode all crosswalk-mapped answers and coalesce duplicate WHO targets.

    Shared by the loader and ``redecode_vasa``. Returns
    ``(who_values, source_pseudo, collisions, decode_flags, raw_values)``:

    * ``who_values``   first-wins coalesced ``{WHO Id: decoded value}``
    * ``collisions``   duplicate WHO targets whose decoded values differ
    * ``decode_flags`` one dict per adopted answer whose decode was an
      ``unknown_code`` or ``fallback`` (see :func:`_decode_flag`)
    * ``raw_values``   ``{pseudo: raw code}`` provenance for every crosswalked
      select answer with a meaningful value (adopted or not)
    """
    who_values = {}
    source_pseudo = {}
    collisions = []
    decode_flags = []
    raw_values = {}
    for pseudo, who in PSEUDO_TO_WHO.items():
        if pseudo in COMBINED_WHO:
            continue  # combined fields are split into their components later
        raw_value = raw.get(pseudo)
        if not _has_meaningful_value(raw_value):
            continue
        result = decode_value(pseudo, raw_value)
        if pseudo in VALUE_CROSSWALK:
            raw_values[pseudo] = str(raw_value)
        value = result.decoded
        if who in who_values:
            if who_values[who] != value:
                kept = source_pseudo.get(who, "")
                collisions.append(
                    {
                        "who": who,
                        "kept_pseudo": kept,
                        "kept_section": INSTRUMENT_SECTION.get(kept, ""),
                        "kept_value": who_values[who],
                        "discarded_pseudo": pseudo,
                        "discarded_section": INSTRUMENT_SECTION.get(pseudo, ""),
                        "discarded_value": value,
                    }
                )
            continue
        who_values[who] = value
        source_pseudo[who] = pseudo
        if result.value_rule in (UNKNOWN_CODE, FALLBACK):
            decode_flags.append(_decode_flag(pseudo, who, result))
    return who_values, source_pseudo, collisions, decode_flags, raw_values


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


def _split_full_name(full_name):
    """Split a combined name: last whitespace token is the surname (Id10018),
    everything before it the given name(s) (Id10017). Single token -> given only.
    """
    tokens = str(full_name).split()
    if len(tokens) <= 1:
        return str(full_name).strip(), ""
    return " ".join(tokens[:-1]), tokens[-1]


def _reconcile_names(full_name, section_given):
    """Reconcile the combined ``Q1202`` full name with the section-specific
    given-name field (``A4001``/``C3012``/``N2023``).

    Returns ``(given, surname, resolution)`` where resolution is one of:

    - ``split``      no section value; Q1202 is split positionally.
    - ``consistent`` section value equals the full name or is a prefix of it;
      the section given name is kept, the leftover tokens become the surname.
    - ``component``  section tokens all appear in the full name (any order);
      section given name kept, unconsumed tokens become the surname.
    - ``conflict``   values genuinely disagree. Precedence: the section-specific
      given-name question wins for Id10017 (it is asked about the deceased
      directly inside the completed instrument); Q1202 remains the only surname
      source. The conflict is reported so it can be flagged for review.

    Token comparison is case-insensitive; returned values keep original casing.
    """
    full = str(full_name).strip()
    given = str(section_given).strip()
    full_tokens = full.split()
    given_tokens = given.split()
    full_lower = [t.lower() for t in full_tokens]
    given_lower = [t.lower() for t in given_tokens]

    if not given_tokens:
        first, last = _split_full_name(full)
        return first, last, "split"
    if given_lower == full_lower:
        first, last = _split_full_name(full)
        return first, last, "consistent"
    if (
        len(given_lower) < len(full_lower)
        and full_lower[: len(given_lower)] == given_lower
    ):
        return given, " ".join(full_tokens[len(given_tokens) :]), "consistent"

    # Order-insensitive component check (handles reversed name order).
    remaining, remaining_lower = list(full_tokens), list(full_lower)
    for token in given_lower:
        if token not in remaining_lower:
            break
        idx = remaining_lower.index(token)
        del remaining_lower[idx], remaining[idx]
    else:
        return given, " ".join(remaining), "component"

    _first, last = _split_full_name(full)
    return given, last, "conflict"


def _route_n2005(raw, who_values):
    """Route the combined ``N2005`` duration ("how long after delivery did the
    mother die") by its unit: days -> ``Id10359``, anything else (months, weeks,
    ref, DK, blank) -> ``Id10358``, whose companion ``Id10358_units`` retains
    the answered unit. Never overwrites an already-populated target.
    """
    raw_value = raw.get("N2005")
    if not _has_meaningful_value(raw_value):
        return
    unit = _mapped_value("N2005_units", raw.get("N2005_units", ""))
    target = "Id10359" if str(unit).strip().lower() == "days" else "Id10358"
    if not _has_meaningful_value(who_values.get(target)):
        who_values[target] = _mapped_value("N2005", raw_value)


def _build_vasa_record(raw, raw_ci):
    (
        who_values,
        source_pseudo,
        collisions,
        decode_flags,
        raw_values,
    ) = _decode_pseudo_answers(raw)

    # Combined Q1202 (full name) -> Id10017 + Id10018, reconciled against the
    # section-specific given-name field that already populated Id10017.
    name_resolutions = []
    q1202 = raw.get("Q1202")
    if _has_meaningful_value(q1202):
        section_value = who_values.get("Id10017", "")
        given, surname, resolution = _reconcile_names(q1202, section_value)
        if _has_meaningful_value(given):
            who_values["Id10017"] = given
        if _has_meaningful_value(surname) and not _has_meaningful_value(
            who_values.get("Id10018")
        ):
            who_values["Id10018"] = surname
        name_resolutions.append(
            {
                "who": "Id10017",
                "resolution": resolution,
                "section_pseudo": source_pseudo.get("Id10017", ""),
                "section_value": str(section_value),
                "combined_pseudo": "Q1202",
                "combined_value": str(q1202).strip(),
            }
        )
    _route_n2005(raw, who_values)

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

    # raw_values is returned separately (never inside ``record``) so the
    # blank/upsert policy and dry-run diffs stay untouched by provenance.
    return record, collisions, name_resolutions, decode_flags, raw_values


def _build_social_autopsy(raw, raw_ci):
    values = {}
    for column, field in SA_COLUMN_TO_FIELD.items():
        raw_value = raw.get(column)
        if not _has_meaningful_value(raw_value):
            raw_value = raw_ci.get(str(column).strip().lower())
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


def _tally_collision_events(summary, instanceid, collisions, name_resolutions):
    """Count and log collision events; return the record's unresolved conflicts.

    A duplicate name field is only a collision event when the section value is
    present and differs from Q1202 (identical duplicates are silently coalesced,
    matching the historical behavior for plain duplicates).
    """
    conflicts = []
    events = 0

    for coll in collisions:
        events += 1
        summary["collisions_unresolved"] += 1
        conflicts.append({"kind": "who_id_conflict", **coll})
        logger.warning(
            "VASA %s: duplicate WHO-Id %s: kept %s=%r (%s section), "
            "discarded %s=%r (%s section)",
            instanceid,
            coll["who"],
            coll["kept_pseudo"],
            coll["kept_value"],
            coll["kept_section"] or "?",
            coll["discarded_pseudo"],
            coll["discarded_value"],
            coll["discarded_section"] or "?",
        )

    for res in name_resolutions:
        section_value = res["section_value"]
        if not section_value or section_value == res["combined_value"]:
            continue  # nothing duplicated: Q1202 alone, or identical values
        events += 1
        if res["resolution"] == "conflict":
            summary["collisions_unresolved"] += 1
            conflicts.append({"kind": "name_conflict", **res})
            logger.warning(
                "VASA %s: conflicting deceased name: section %s=%r vs %s=%r; "
                "kept the section given name, flagged for review",
                instanceid,
                res["section_pseudo"],
                section_value,
                res["combined_pseudo"],
                res["combined_value"],
            )
        else:
            summary["collisions_resolved"] += 1
            logger.info(
                "VASA %s: reconciled deceased name (%s): %s=%r + %s=%r",
                instanceid,
                res["resolution"],
                res["section_pseudo"],
                section_value,
                res["combined_pseudo"],
                res["combined_value"],
            )

    summary["collisions"] += events
    if events:
        summary["records_with_collisions"] += 1
    return conflicts


def _tally_decode_events(decode_summary, decode_flags, raw_values):
    decode_summary["fields_decoded"] += len(raw_values)
    unknown = sum(1 for f in decode_flags if f["check"] == "unknown_code")
    fallback = sum(1 for f in decode_flags if f["check"] == "fallback_value")
    decode_summary["unknown_codes"] += unknown
    decode_summary["fallback_values"] += fallback
    if unknown:
        decode_summary["records_with_unknown"] += 1
    if fallback:
        decode_summary["records_with_fallback"] += 1


def _merged_raw_answers(existing, raw_values):
    """Provenance for the upsert: merge this import's raw codes over what is
    already stored (present keys only — a blank re-import never clears raws,
    matching the canonical-field blank policy).
    """
    merged = {}
    if existing is not None and isinstance(existing.raw_answers, dict):
        merged.update(existing.raw_answers.get("values", {}))
    merged.update(raw_values)
    return {
        "values": merged,
        "meta": {"crosswalk_version": CROSSWALK_VERSION, "form_id": VASA_FORM_ID},
    }


def _flag_conflict_issues(vasa, instanceid, conflicts):
    """Record unresolved WHO-Id conflicts as VALIDITY DataQualityIssues."""
    from va_explorer.va_data_management.models.data_quality import DataQualityIssue
    from va_explorer.va_data_management.utils.dq import upsert_issue

    for conflict in conflicts:
        details = {k: v for k, v in conflict.items() if k != "kind"}
        details["instanceid"] = instanceid
        if conflict["kind"] == "name_conflict":
            keys, title = NAME_CONFLICT_KEYS, VASA_NAME_CONFLICT_TITLE
        else:
            keys = {"check": "who_id_conflict", "who": conflict["who"]}
            title = WHO_ID_CONFLICT_TITLE
        upsert_issue(
            issue_type=DataQualityIssue.VALIDITY,
            model_class=VASA,
            member_ids=[vasa.pk],
            title=title,
            keys=keys,
            details=details,
        )


def _flag_decode_issues(vasa, instanceid, decode_flags):
    """Record unknown-code / fallback decodes as VALIDITY DataQualityIssues.

    ``keys`` carries only the stable identity (check + WHO field) so a corrected
    re-import updates the same issue; specifics live in ``details``.
    """
    from va_explorer.va_data_management.models.data_quality import DataQualityIssue
    from va_explorer.va_data_management.utils.dq import upsert_issue

    for flag in decode_flags:
        details = {k: v for k, v in flag.items() if k != "check"}
        details["instanceid"] = instanceid
        details["crosswalk_version"] = CROSSWALK_VERSION
        title = (
            UNKNOWN_CODE_TITLE
            if flag["check"] == "unknown_code"
            else FALLBACK_VALUE_TITLE
        )
        upsert_issue(
            issue_type=DataQualityIssue.VALIDITY,
            model_class=VASA,
            member_ids=[vasa.pk],
            title=title,
            keys={"check": flag["check"], "who": flag["who"]},
            details=details,
        )


def _resolve_stale_loader_issues(flagged_checks, checks=_LOADER_KEY_CHECKS):
    """Auto-resolve loader-owned issues for records that re-loaded clean.

    ``flagged_checks`` maps each upserted record's pk to the set of check names
    it was flagged with this batch; per check in ``checks``, any OPEN issue on a
    record loaded *without* that check is stale and gets resolved. Callers that
    only re-evaluate a subset (e.g. ``redecode_vasa``) pass their own ``checks``.
    """
    from django.contrib.contenttypes.models import ContentType

    from va_explorer.va_data_management.models.data_quality import DataQualityIssue

    if not flagged_checks:
        return
    ct = ContentType.objects.get_for_model(VASA)
    for check in checks:
        clean_pks = sorted(
            pk for pk, checks in flagged_checks.items() if check not in checks
        )
        if not clean_pks:
            continue
        stale = list(
            DataQualityIssue.objects.filter(
                issue_type=DataQualityIssue.VALIDITY,
                target_model=ct,
                status=DataQualityIssue.OPEN,
                keys__check=check,
                members__content_type=ct,
                members__object_id__in=clean_pks,
            )
            .values_list("pk", flat=True)
            .distinct()
        )
        if stale:
            DataQualityIssue.objects.filter(pk__in=stale).update(
                status=DataQualityIssue.RESOLVED
            )


def _stored_str(value):
    """DB-comparable string for a model attribute. MultiSelectFields hand back
    a list-like whose ``str()`` renders display labels; re-join the raw values
    (comma-separated, as stored) so diffs compare value-space to value-space.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value)


def _dry_run_changes(existing, record):
    """Field diffs exactly as the real upsert would apply them: only keys
    present in ``record`` are compared; blank CSV fields never clear DB values.
    """
    if existing is None:
        return {"action": "create"}
    fields = {}
    for key, value in record.items():
        old = _stored_str(getattr(existing, key, ""))
        new = str(value or "")
        if old != new:
            fields[key] = {"old": old, "new": new}
    return {"action": "update", "fields": fields}


def load_vasa_from_dataframes(main_df, repeat_dfs=None, *, dry_run=False):
    """Load VASA submissions (idempotent upsert by instanceID) plus SA + repeats.

    Per-record error isolation: one bad submission is logged and skipped without
    aborting the batch. Returns a summary dict.

    With ``dry_run=True`` nothing is written: the summary reports would-be
    created/updated counts plus per-record field diffs under ``changes``, and
    DeathID validation / repeat loading / Death linking are skipped.
    """
    main_df = merge_geopoint_into(main_df, source_prefix="GPS", target_field="geopoint")
    summary = {
        "rows": len(main_df),
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "collisions": 0,
        "records_with_collisions": 0,
        "collisions_resolved": 0,
        "collisions_unresolved": 0,
        "rows_with_death_id": 0,
        "rows_without_death_id": 0,
        "decode": {
            "crosswalk_version": CROSSWALK_VERSION,
            "fields_decoded": 0,
            "unknown_codes": 0,
            "fallback_values": 0,
            "records_with_unknown": 0,
            "records_with_fallback": 0,
        },
        "repeats": {},
        "dry_run": dry_run,
        "death_id_validation": None,
        "linking": {"enabled": False, "scheduled": 0},
    }
    if dry_run:
        summary["changes"] = []
    vasa_by_key = {}
    vasa_ids = []
    flagged_checks = {}

    for raw in main_df.to_dict(orient="records"):
        raw_ci = {str(k).strip().lower(): v for k, v in raw.items()}
        try:
            (
                record,
                collisions,
                name_resolutions,
                decode_flags,
                raw_values,
            ) = _build_vasa_record(raw, raw_ci)
            instanceid = record.get("instanceid")
            if not _has_meaningful_value(instanceid):
                summary["skipped"] += 1
                continue
            sa_values = _build_social_autopsy(raw, raw_ci)
            if _has_meaningful_value(sa_values.get("DeathID")):
                summary["rows_with_death_id"] += 1
            else:
                summary["rows_without_death_id"] += 1
            conflicts = _tally_collision_events(
                summary, instanceid, collisions, name_resolutions
            )
            _tally_decode_events(summary["decode"], decode_flags, raw_values)

            if dry_run:
                existing = VASA.all_objects.filter(instanceid=instanceid).first()
                summary["updated" if existing is not None else "created"] += 1
                if len(summary["changes"]) < _DRY_RUN_CHANGE_CAP:
                    summary["changes"].append(
                        {"instanceid": instanceid, **_dry_run_changes(existing, record)}
                    )
                continue

            with transaction.atomic():
                existing = VASA.all_objects.filter(instanceid=instanceid).first()
                raw_answers = _merged_raw_answers(existing, raw_values)
                if existing is not None:
                    for key, value in record.items():
                        setattr(existing, key, value)
                    existing.raw_answers = raw_answers
                    existing.save()
                    vasa = existing
                    summary["updated"] += 1
                else:
                    vasa = VASA.objects.create(**record, raw_answers=raw_answers)
                    summary["created"] += 1

                VASASocialAutopsy.objects.update_or_create(
                    vasa=vasa, defaults=sa_values
                )
                if conflicts:
                    _flag_conflict_issues(vasa, instanceid, conflicts)
                if decode_flags:
                    _flag_decode_issues(vasa, instanceid, decode_flags)
                flagged_checks[vasa.pk] = {c["kind"] for c in conflicts} | {
                    f["check"] for f in decode_flags
                }
                if _has_meaningful_value(vasa.key):
                    vasa_by_key[str(vasa.key).strip()] = vasa
                vasa_ids.append(vasa.pk)
        except Exception:
            summary["errors"] += 1
            logger.exception("Failed to load VASA submission")

    if not dry_run:
        try:
            _resolve_stale_loader_issues(flagged_checks)
        except Exception:
            logger.exception("Failed to resolve stale VASA loader issues")

    for group, df in (repeat_dfs or {}).items():
        if group not in REPEAT_MODELS:
            logger.warning("Unknown VASA repeat group %s; skipping", group)
            continue
        if dry_run:
            summary["repeats"][group] = {
                "skipped_dry_run": 0 if df is None else len(df)
            }
            continue
        if df is None or df.empty:
            summary["repeats"][group] = {"created": 0, "updated": 0, "skipped": 0}
            continue
        summary["repeats"][group] = _load_repeat(group, df, vasa_by_key)

    # VASA records arrive with a field-entered DeathID; validate them all
    # (format/checksum + existence in the death register) and flag problems as
    # DataQualityIssues. Never blocks or mutates the imported records.
    if not dry_run:
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
    from va_explorer.va_data_management.utils.matching import (
        matching_enabled,
        schedule_link_for_va_ids,
    )

    summary["linking"]["enabled"] = matching_enabled()
    if vasa_ids and not dry_run:
        schedule_link_for_va_ids(vasa_ids, model_name="vasa", source="pyodk_ingestion")
        if summary["linking"]["enabled"]:
            summary["linking"]["scheduled"] = len(vasa_ids)

    decode = summary["decode"]
    logger.info(
        "VASA decode summary (crosswalk %s): %s fields decoded across %s rows; "
        "%s unknown codes (%s records), %s fallback values (%s records)",
        decode["crosswalk_version"],
        decode["fields_decoded"],
        summary["rows"],
        decode["unknown_codes"],
        decode["records_with_unknown"],
        decode["fallback_values"],
        decode["records_with_fallback"],
    )
    return summary


def load_vasa_repeat_dataframe(group, df):
    """Load a single VASA repeat group pulled independently (e.g. via ODK OData).

    Parents are resolved from the DB by ``key`` (the main form is expected to
    have been loaded already).
    """
    if group not in REPEAT_MODELS or df is None or df.empty:
        return {"created": 0, "updated": 0, "skipped": 0}
    return _load_repeat(group, df, {})


def load_vasa_from_csv(main_csv, repeat_csvs=None, *, dry_run=False):
    """Convenience wrapper: read CSV paths into DataFrames and load them."""
    main_df = pd.read_csv(main_csv, dtype=str, low_memory=False).fillna("")
    repeat_dfs = {}
    for group, path in (repeat_csvs or {}).items():
        repeat_dfs[group] = pd.read_csv(path, dtype=str, low_memory=False).fillna("")
    return load_vasa_from_dataframes(main_df, repeat_dfs, dry_run=dry_run)
