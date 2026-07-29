# va_explorer/va_data_management/utils/dq_specs.py
"""
Declarative data-quality (DQ) specifications and a single generic runner shared by
Household, Pregnancy, PregnancyOutcome and Death.

Each model is described by a ``DQSpec``; ``run_dq_for_model`` executes every applicable
check (duplicates, duration, timeliness, completeness, consent, validity), upserts the
results through the existing ``DataQualityIssue`` framework, auto-resolves issues that no
longer apply, and writes a CSV/JSON report. ``run_dq_for_form`` is the entry point used by
the ODK import path (keyed by ODK form name).
"""

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from django.db import models as dj_models

from va_explorer.va_data_management.models import (
    Death,
    Household,
    Pregnancy,
    PregnancyOutcome,
)
from va_explorer.va_data_management.models.data_quality import DataQualityIssue
from va_explorer.va_data_management.utils.dq import (
    date_in_future,
    date_order_violation,
    detect_exact_duplicate_groups,
    nonneg_int_violation,
    parse_when,
    resolve_missing_issues,
    to_count,
    upsert_completeness_issue,
    upsert_consent_issue_if_invalid,
    upsert_duration_issue_if_short,
    upsert_household_completeness_issue,
    upsert_household_consent_issue_if_invalid,
    upsert_issue,
    upsert_timeliness_issue_if_late,
    upsert_validity_issue,
)

# Defaults shared across datasets
DEFAULT_MIN_DURATION = timedelta(minutes=15)
DEFAULT_MAX_DELAY = timedelta(days=2)
HOUSEHOLD_DUP_WINDOW = timedelta(days=1)


def _norm_key(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if s == "" or s.lower() in {"nan", "none", "null", "n/a"}:
        return None
    return s.lower()


def _yes(value: Optional[str]) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"yes", "y", "true", "1", "01"}


@dataclass
class DupFinding:
    subtype: str
    member_ids: List[int]
    title: str
    keys: Dict[str, Any]
    details: Dict[str, Any]


@dataclass
class DQSpec:
    model: type
    label: str
    slug: str
    # Field-name map: keys among {start,end,today,submissiondate,submit_time}
    time_fields: Dict[str, Optional[str]] = field(default_factory=dict)
    consent_field: Optional[str] = None
    consent_gate_field: Optional[str] = None
    completeness_requires_consent: bool = False
    required_fields: Sequence[Any] = ()  # (field, label) pairs
    duplicate_groups: Sequence[Tuple[str, Sequence[str]]] = ()
    duplicate_title: str = ""
    validity_rules: Sequence[Callable[[dict], List[str]]] = ()
    validity_key_fields: Sequence[str] = ()
    min_duration: timedelta = DEFAULT_MIN_DURATION
    max_delay: timedelta = DEFAULT_MAX_DELAY
    # Optional hooks (Household supplies these to keep its richer logic)
    duplicate_fn: Optional[Callable[[List[dict]], List[DupFinding]]] = None
    completeness_fn: Optional[Callable[[type, int, dict], Any]] = None
    consent_fn: Optional[Callable[[type, int, dict], Any]] = None


# ---------------------------------------------------------------------------
# Household-specific logic (ported from the original dq_households command)
# ---------------------------------------------------------------------------

_HH_FIELDS_CACHE: Optional[List[str]] = None


def _household_hh_fields() -> List[str]:
    global _HH_FIELDS_CACHE
    if _HH_FIELDS_CACHE is None:
        _HH_FIELDS_CACHE = [
            f.name
            for f in Household._meta.get_fields()
            if isinstance(f, dj_models.Field) and f.name.startswith("HH_")
        ]
    return _HH_FIELDS_CACHE


def _household_duplicate_findings(rows: List[dict]) -> List[DupFinding]:
    """Exact-field, EA+HUN+HHN-within-window, and admin+HUN/HHN duplicate groups."""
    window = HOUSEHOLD_DUP_WINDOW
    normed = []
    for r in rows:
        normed.append(
            {
                "id": r["id"],
                "province": _norm_key(r.get("province")),
                "district": _norm_key(r.get("district")),
                "constituency": _norm_key(r.get("constituency")),
                "ward": _norm_key(r.get("ward")),
                "ea": _norm_key(r.get("ea")),
                "hun": _norm_key(r.get("hun")),
                "hhn": _norm_key(r.get("hhn")),
                "start": _norm_key(r.get("start")),
                "end": _norm_key(r.get("end")),
                "enumerator": _norm_key(r.get("enumerator")),
                "respondent": _norm_key(r.get("respondent")),
                "start_dt": parse_when(r.get("start")),
                "end_dt": parse_when(r.get("end")),
            }
        )

    findings: List[DupFinding] = []

    # --- exact-field duplicates ---
    exact_groups: Dict[Tuple[Optional[str], ...], List[int]] = defaultdict(list)
    exact_cols = [
        "province",
        "district",
        "constituency",
        "ward",
        "ea",
        "hun",
        "hhn",
        "start",
        "end",
        "enumerator",
        "respondent",
    ]
    for row in normed:
        exact_groups[tuple(row[c] for c in exact_cols)].append(row["id"])
    for key_tuple, ids in exact_groups.items():
        if len(ids) > 1:
            findings.append(
                DupFinding(
                    subtype="exact_fields",
                    member_ids=sorted(ids),
                    title="Household possible duplicate",
                    keys=dict(zip(exact_cols, key_tuple)),
                    details={"count": str(len(ids))},
                )
            )

    # --- EA+HUN+HHN within a time window ---
    tri_groups: Dict[Tuple, List[dict]] = defaultdict(list)
    for row in normed:
        tri_groups[(row["ea"], row["hun"], row["hhn"])].append(row)

    for (ea, hun, hhn), members in tri_groups.items():
        if not (ea and hun and hhn):
            continue

        def best_dt(r):
            return r["start_dt"] or r["end_dt"]

        members = [m for m in members if best_dt(m) is not None]
        if len(members) < 2:
            continue
        members.sort(key=best_dt)

        cluster_ids: List[int] = []
        current_cluster: List[int] = []
        i = 0
        while i < len(members) - 1:
            a, b = members[i], members[i + 1]
            t_a, t_b = best_dt(a), best_dt(b)
            if t_a and t_b and (t_b - t_a) <= window:
                if not current_cluster:
                    current_cluster = [a["id"], b["id"]]
                elif b["id"] not in current_cluster:
                    current_cluster.append(b["id"])
            else:
                if current_cluster:
                    cluster_ids.extend(current_cluster)
                    current_cluster = []
            i += 1
        if current_cluster:
            cluster_ids.extend(current_cluster)

        cluster_ids = sorted(set(cluster_ids))
        if len(cluster_ids) >= 2:
            times = [best_dt(m) for m in members if m["id"] in cluster_ids]
            min_t = min(times) if times else None
            max_t = max(times) if times else None
            findings.append(
                DupFinding(
                    subtype="ea_hun_hhn_time",
                    member_ids=cluster_ids,
                    title="Household possible duplicate",
                    keys={"ea": ea, "hun": hun, "hhn": hhn},
                    details={
                        "window": str(window),
                        "first_seen": min_t.isoformat() if min_t else "",
                        "last_seen": max_t.isoformat() if max_t else "",
                        "count": str(len(cluster_ids)),
                    },
                )
            )

    # --- admin + HUN/HHN ---
    admin_groups: Dict[Tuple, List[int]] = defaultdict(list)
    admin_cols = ["province", "district", "constituency", "ward", "hun", "hhn"]
    for row in normed:
        if (row["hun"] and row["hhn"]) and (
            row["province"] or row["district"] or row["constituency"] or row["ward"]
        ):
            admin_groups[tuple(row[c] for c in admin_cols)].append(row["id"])
    for key_tuple, ids in admin_groups.items():
        if len(ids) > 1:
            findings.append(
                DupFinding(
                    subtype="admin_hun_hhn",
                    member_ids=sorted(ids),
                    title="Household possible duplicate",
                    keys=dict(zip(admin_cols, key_tuple)),
                    details={"count": str(len(ids))},
                )
            )

    # Prioritise and collapse overlapping groups (first claim wins).
    priority = {"exact_fields": 0, "ea_hun_hhn_time": 1, "admin_hun_hhn": 2}
    findings.sort(key=lambda f: priority.get(f.subtype, 999))
    claimed: set = set()
    collapsed: List[DupFinding] = []
    for f in findings:
        if any(hid in claimed for hid in f.member_ids):
            continue
        collapsed.append(f)
        claimed.update(f.member_ids)
    return collapsed


def _household_completeness_fn(model, oid, row):
    hh_fields = {name: row.get(name) for name in _household_hh_fields()}
    return upsert_household_completeness_issue(
        model_class=model,
        object_id=oid,
        hh01_value=row.get("HH_01"),
        hh02_value=row.get("HH_02"),
        household_value=row.get("household"),
        enumerator_value=row.get("enumerator"),
        result_list_value=row.get("result_list"),
        result_other_value=row.get("result_other"),
        hh_fields=hh_fields,
    )


def _household_consent_fn(model, oid, row):
    return upsert_household_consent_issue_if_invalid(
        model_class=model,
        object_id=oid,
        household_value=row.get("household"),
        consent_value=row.get("consent"),
    )


# ---------------------------------------------------------------------------
# Validity rules (best-practice plausibility checks per model)
# ---------------------------------------------------------------------------


def _death_validity(row: dict) -> List[str]:
    v: List[str] = []
    if date_in_future(row.get("DE_06")):
        v.append("date_of_death_in_future")
    if date_order_violation(row.get("DE_04"), row.get("DE_06")):
        v.append("date_of_birth_after_date_of_death")
    sex = str(row.get("DE_05")).strip().lower() if row.get("DE_05") else ""
    male = sex in {"male", "m", "1", "01"}
    if male:
        for fld, label in (
            ("DE_16", "died_pregnant"),
            ("DE_17", "died_during_childbirth"),
            ("DE_18", "died_within_42_days_of_pregnancy"),
        ):
            if _yes(row.get(fld)):
                v.append(f"{label}_but_sex_recorded_male")
    return v


def _pregnancy_validity(row: dict) -> List[str]:
    v: List[str] = []
    if date_in_future(row.get("PE_09A")):
        v.append("last_menstrual_period_in_future")
    if date_order_violation(row.get("PE_09A"), row.get("PE_10A")):
        v.append("lmp_after_expected_delivery_date")
    if nonneg_int_violation(row.get("PE_22")):
        v.append("antenatal_visit_count_invalid")
    return v


def _outcome_validity(row: dict) -> List[str]:
    v: List[str] = []
    if date_in_future(row.get("PO_41")):
        v.append("delivery_date_in_future")
    if nonneg_int_violation(row.get("PO_47")):
        v.append("born_alive_count_invalid")
    if nonneg_int_violation(row.get("PO_47A")):
        v.append("born_dead_count_invalid")
    alive = to_count(row.get("PO_47")) or 0
    dead = to_count(row.get("PO_47A")) or 0
    if not _yes(row.get("PO_45")) and (alive + dead) > 1:
        v.append("multiple_birth_count_without_multiple_flag")
    outcome = str(row.get("PO_46")).strip().lower() if row.get("PO_46") else ""
    if "dead" in outcome and alive > 0:
        v.append("born_dead_outcome_but_alive_count_positive")
    return v


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

DQ_SPECS: Dict[type, DQSpec] = {
    Household: DQSpec(
        model=Household,
        label="Household",
        slug="households",
        time_fields={
            "start": "start",
            "end": "end",
            "today": "today",
            "submissiondate": "submissiondate",
            "submit_time": "submit_time",
        },
        duplicate_fn=_household_duplicate_findings,
        completeness_fn=_household_completeness_fn,
        consent_fn=_household_consent_fn,
    ),
    Pregnancy: DQSpec(
        model=Pregnancy,
        label="Pregnancy",
        slug="pregnancy",
        time_fields={
            "start": "start",
            "end": "end",
            "today": "today",
            "submissiondate": "submissiondate",
        },
        consent_field="PE_03",
        required_fields=(
            ("PE_06", "Name of pregnant woman"),
            ("PE_04", "Household reference"),
            ("enumerator", "Enumerator"),
        ),
        duplicate_groups=(("woman_household_lmp", ["ea", "PE_04", "PE_06", "PE_09A"]),),
        duplicate_title="Pregnancy possible duplicate",
        validity_rules=(_pregnancy_validity,),
        validity_key_fields=("PE_06", "PE_09A", "PE_10A", "PE_22"),
    ),
    PregnancyOutcome: DQSpec(
        model=PregnancyOutcome,
        label="Pregnancy Outcome",
        slug="pregnancy_outcome",
        time_fields={
            "start": "start",
            "end": "end",
            "today": "today",
            "submissiondate": "submissiondate",
        },
        consent_field="consent",
        required_fields=(
            ("PO_04", "Name of mother"),
            ("PO_02", "Household reference"),
            ("PO_41", "Date of delivery / pregnancy end"),
            ("PO_46", "Outcome (born alive/dead/lost)"),
            ("enumerator", "Enumerator"),
        ),
        duplicate_groups=(
            ("mother_household_delivery", ["ea", "PO_02", "PO_04", "PO_41"]),
        ),
        duplicate_title="Pregnancy outcome possible duplicate",
        validity_rules=(_outcome_validity,),
        validity_key_fields=("PO_04", "PO_41", "PO_45", "PO_46", "PO_47", "PO_47A"),
    ),
    Death: DQSpec(
        model=Death,
        label="Death",
        slug="death",
        time_fields={
            "start": "start",
            "today": "today",
            "submissiondate": "submissiondate",
        },
        consent_field="consent",
        completeness_requires_consent=True,
        required_fields=(
            ("DE_03", "Name of deceased"),
            ("DE_05", "Sex of deceased"),
            ("DE_06", "Date of death"),
            ("DE_01", "Household reference"),
            ("enumerator", "Enumerator"),
        ),
        duplicate_groups=(
            (
                "deceased_household_dod",
                ["province", "district", "ward", "ea", "DE_01", "DE_03", "DE_06"],
            ),
        ),
        duplicate_title="Death possible duplicate",
        validity_rules=(_death_validity,),
        validity_key_fields=(
            "DE_03",
            "DE_04",
            "DE_05",
            "DE_06",
            "DE_16",
            "DE_17",
            "DE_18",
        ),
    ),
}

# ODK form name -> spec (keys mirror FORM_MODEL_MAP in load_form_csv).
FORM_DQ_SPECS: Dict[str, DQSpec] = {
    "household": DQ_SPECS[Household],
    "pregnancy": DQ_SPECS[Pregnancy],
    "pregnancy_outcome": DQ_SPECS[PregnancyOutcome],
    "death": DQ_SPECS[Death],
}


# ---------------------------------------------------------------------------
# Generic runner
# ---------------------------------------------------------------------------


def _generic_duplicate_findings(spec: DQSpec, rows: List[dict]) -> List[DupFinding]:
    findings: List[DupFinding] = []
    for subtype, fields in spec.duplicate_groups:
        for ids, keys in detect_exact_duplicate_groups(rows, fields):
            findings.append(
                DupFinding(
                    subtype=subtype,
                    member_ids=ids,
                    title=spec.duplicate_title or f"{spec.label} possible duplicate",
                    keys=keys,
                    details={"count": str(len(ids))},
                )
            )
    # Collapse overlapping groups across rules (first claim wins).
    claimed: set = set()
    collapsed: List[DupFinding] = []
    for f in findings:
        if any(i in claimed for i in f.member_ids):
            continue
        collapsed.append(f)
        claimed.update(f.member_ids)
    return collapsed


def _validity_for(spec: DQSpec, row: dict) -> Tuple[List[str], Dict[str, Any]]:
    violations: List[str] = []
    for rule in spec.validity_rules:
        violations.extend(rule(row) or [])
    if not violations:
        return [], {}
    keys = {f: row.get(f) for f in spec.validity_key_fields}
    return violations, keys


def run_dq_for_model(
    spec: DQSpec,
    *,
    resolve_missing: bool = True,
    write_reports: bool = True,
) -> Dict[str, Any]:
    """Run every applicable DQ check for ``spec.model`` and persist the results."""
    model = spec.model
    rows = list(model.objects.all().values())
    summary: Dict[str, Any] = {
        "model": spec.label,
        "total_rows": len(rows),
        "duplicate": 0,
        "duration": 0,
        "timeliness": 0,
        "incomplete": 0,
        "consent": 0,
        "validity": 0,
        "resolved": {},
    }
    if not rows:
        return summary

    report_rows: List[dict] = []
    active: Dict[str, List[str]] = defaultdict(list)
    tf = spec.time_fields

    def record(issue, member_ids):
        if issue is None:
            return False
        report_rows.append(
            {
                "issue_type": issue.issue_type,
                "subtype": (issue.details or {}).get("subtype", ""),
                "member_ids": list(member_ids),
                "keys": issue.keys,
                "details": issue.details,
            }
        )
        return True

    # ---- Duplicates ----
    if spec.duplicate_fn is not None:
        dup_findings = spec.duplicate_fn(rows)
    else:
        dup_findings = _generic_duplicate_findings(spec, rows)
    for f in dup_findings:
        issue = upsert_issue(
            issue_type=DataQualityIssue.DUPLICATE,
            model_class=model,
            member_ids=f.member_ids,
            title=f.title,
            keys=f.keys,
            details={"subtype": f.subtype, **f.details},
        )
        active[DataQualityIssue.DUPLICATE].append(issue.signature)
        record(issue, f.member_ids)
        summary["duplicate"] += 1

    has_duration = bool(tf.get("start") and tf.get("end"))
    has_timeliness = bool(
        tf.get("today") or tf.get("submissiondate") or tf.get("start")
    )

    # ---- Per-row checks ----
    for row in rows:
        oid = row["id"]

        if has_duration:
            issue = upsert_duration_issue_if_short(
                model_class=model,
                object_id=oid,
                start_value=row.get(tf["start"]),
                end_value=row.get(tf["end"]),
                min_duration=spec.min_duration,
            )
            if record(issue, [oid]):
                active[DataQualityIssue.DURATION].append(issue.signature)
                summary["duration"] += 1

        if has_timeliness:
            issue = upsert_timeliness_issue_if_late(
                model_class=model,
                object_id=oid,
                start_value=row.get(tf["start"]) if tf.get("start") else None,
                today_value=row.get(tf["today"]) if tf.get("today") else None,
                submission_date_value=(
                    row.get(tf["submissiondate"]) if tf.get("submissiondate") else None
                ),
                submit_time_value=(
                    row.get(tf["submit_time"]) if tf.get("submit_time") else None
                ),
                max_delay=spec.max_delay,
            )
            if record(issue, [oid]):
                active[DataQualityIssue.TIMELINESS].append(issue.signature)
                summary["timeliness"] += 1

        # Completeness
        consent_blocks_completeness = (
            spec.completeness_requires_consent
            and spec.consent_field
            and not _yes(row.get(spec.consent_field))
        )
        if consent_blocks_completeness:
            issue = None
        elif spec.completeness_fn is not None:
            issue = spec.completeness_fn(model, oid, row)
        elif spec.required_fields:
            issue = upsert_completeness_issue(
                model_class=model,
                object_id=oid,
                field_values=row,
                required=spec.required_fields,
            )
        else:
            issue = None
        if record(issue, [oid]):
            active[DataQualityIssue.INCOMPLETE].append(issue.signature)
            summary["incomplete"] += 1

        # Consent
        if spec.consent_fn is not None:
            issue = spec.consent_fn(model, oid, row)
        elif spec.consent_field:
            issue = upsert_consent_issue_if_invalid(
                model_class=model,
                object_id=oid,
                consent_value=row.get(spec.consent_field),
                gate_value=(
                    row.get(spec.consent_gate_field)
                    if spec.consent_gate_field
                    else None
                ),
            )
        else:
            issue = None
        if record(issue, [oid]):
            active[DataQualityIssue.CONSENT].append(issue.signature)
            summary["consent"] += 1

        # Validity
        if spec.validity_rules:
            violations, vkeys = _validity_for(spec, row)
            issue = upsert_validity_issue(
                model_class=model,
                object_id=oid,
                violations=violations,
                keys=vkeys,
            )
            if record(issue, [oid]):
                active[DataQualityIssue.VALIDITY].append(issue.signature)
                summary["validity"] += 1

    # ---- Resolve stale issues ----
    if resolve_missing:
        run_types = {DataQualityIssue.DUPLICATE}
        if has_duration:
            run_types.add(DataQualityIssue.DURATION)
        if has_timeliness:
            run_types.add(DataQualityIssue.TIMELINESS)
        if spec.completeness_fn or spec.required_fields:
            run_types.add(DataQualityIssue.INCOMPLETE)
        if spec.consent_fn or spec.consent_field:
            run_types.add(DataQualityIssue.CONSENT)
        if spec.validity_rules:
            run_types.add(DataQualityIssue.VALIDITY)
        for issue_type in run_types:
            n = resolve_missing_issues(issue_type, model, active.get(issue_type, []))
            if n:
                summary["resolved"][issue_type] = n

    if write_reports:
        _write_reports(spec, report_rows)

    return summary


def _write_reports(spec: DQSpec, report_rows: List[dict]) -> None:
    out_csv = Path(f"reports/dq_{spec.slug}.csv")
    out_json = Path(f"reports/dq_{spec.slug}.json")
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["issue_type", "subtype", "member_ids", "keys", "details"])
        for r in report_rows:
            writer.writerow(
                [
                    r["issue_type"],
                    r["subtype"],
                    ";".join(str(x) for x in r["member_ids"]),
                    json.dumps(r["keys"], ensure_ascii=False),
                    json.dumps(r["details"], ensure_ascii=False),
                ]
            )

    with out_json.open("w", encoding="utf-8") as fh:
        json.dump(report_rows, fh, ensure_ascii=False, indent=2, default=str)


def run_dq_for_form(form_name: str, **kwargs) -> Optional[Dict[str, Any]]:
    """Run DQ for an ODK form name; returns ``None`` when no spec is registered."""
    spec = FORM_DQ_SPECS.get(form_name)
    if spec is None:
        return None
    return run_dq_for_model(spec, **kwargs)
