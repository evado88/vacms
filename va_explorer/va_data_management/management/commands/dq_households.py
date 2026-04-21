# va_explorer/va_data_management/management/commands/dq_households.py

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from django.core.management.base import BaseCommand
from django.db import models as dj_models
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from va_explorer.va_data_management.models import Household
from va_explorer.va_data_management.models.data_quality import DataQualityIssue
from va_explorer.va_data_management.utils.dq import (
    resolve_missing_issues,
    upsert_duration_issue_if_short,
    upsert_household_completeness_issue,
    upsert_household_consent_issue_if_invalid,  # NEW
    upsert_issue,
    upsert_timeliness_issue_if_late,
)


# ---------- Helpers ----------
def _norm(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    ss = str(s).strip()
    if ss == "" or ss.lower() in {"nan", "none", "null", "n/a"}:
        return None
    return ss.lower()


def _parse_when(s: Optional[str]) -> Optional[timezone.datetime]:
    if not s:
        return None
    s = s.strip()
    dt = parse_datetime(s)
    if dt is None:
        d = parse_date(s)
        if d is not None:
            dt = timezone.datetime(d.year, d.month, d.day)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


@dataclass
class Finding:
    kind: str  # "exact_fields" | "ea_hun_hhn_time" | "admin_hun_hhn" | "short_duration" | "submission_timeliness" | "mvr_completeness" | "consent"
    household_ids: List[int]
    keys: Dict[str, Optional[str]]
    details: Dict[str, str]


class Command(BaseCommand):
    help = (
        "Run data quality checks on Household entries and persist results.\n"
        "- Duplicate checks: exact-field, EA+HUN+HHN within window, admin+HUN/HHN\n"
        "- Duration: short interviews\n"
        "- Timeliness: late submissions\n"
        "- Completeness (MVR): HH_01, HH_02, enumerator for household=YES; blank HH_* + valid result for household=NO\n"
        "- Consent: when household=YES, consent must be affirmative\n"
    )

    # -------- Fixed constants --------
    TIME_WINDOW = timedelta(days=1)  # duplicates EA+HUN+HHN within 1 day
    MIN_DURATION = timedelta(minutes=15)  # short interviews < 15m
    MAX_DELAY = timedelta(days=2)  # late submission > 2 days
    OUT_CSV = Path("reports/dq_households.csv")
    OUT_JSON = Path("reports/dq_households.json")
    LIMIT = 0  # 0 = no limit
    RESOLVE_MISSING = True  # resolve stale issues automatically

    def add_arguments(self, parser):
        # No user-provided arguments
        pass

    def handle(self, *args, **opts):
        window = self.TIME_WINDOW
        min_duration = self.MIN_DURATION
        max_delay = self.MAX_DELAY
        out_csv = self.OUT_CSV
        out_json = self.OUT_JSON
        limit = self.LIMIT
        resolve_missing = self.RESOLVE_MISSING

        # Collect HH_* field names dynamically
        hh_fields_all: List[str] = []
        for f in Household._meta.get_fields():
            if isinstance(f, dj_models.Field) and f.name.startswith("HH_"):
                hh_fields_all.append(f.name)

        value_fields = [
            "id",
            "key",
            "province",
            "district",
            "constituency",
            "ward",
            "ea",
            "hun",
            "hhn",
            "start",
            "end",
            "today",
            "submission_date",
            "submit_time",
            "enumerator",
            "respondent",
            # MVR / consent fields
            "HH_01",
            "HH_02",
            "household",
            "result_list",
            "result_other",
            "consent",
            *hh_fields_all,
        ]
        value_fields = list(
            dict.fromkeys(value_fields)
        )  # de-dup while preserving order

        qs = Household.objects.all().values(*value_fields)
        if limit > 0:
            qs = qs[:limit]
        rows = list(qs)
        if not rows:
            self.stdout.write(self.style.WARNING("No Household rows found."))
            return

        # Normalize and parse time fields
        normed = []
        for r in rows:
            start_dt = _parse_when(r.get("start"))
            end_dt = _parse_when(r.get("end"))
            today_dt = _parse_when(r.get("today"))
            submission_date_dt = _parse_when(r.get("submission_date"))
            submit_time_dt = _parse_when(r.get("submit_time"))

            entry = {
                "id": r["id"],
                "key": r.get("key"),
                "province": _norm(r.get("province")),
                "district": _norm(r.get("district")),
                "constituency": _norm(r.get("constituency")),
                "ward": _norm(r.get("ward")),
                "ea": _norm(r.get("ea")),
                "hun": _norm(r.get("hun")),
                "hhn": _norm(r.get("hhn")),
                "start": _norm(r.get("start")),
                "end": _norm(r.get("end")),
                "today": _norm(r.get("today")),
                "submission_date": _norm(r.get("submission_date")),
                "submit_time": _norm(r.get("submit_time")),
                "enumerator": _norm(r.get("enumerator")),
                "respondent": _norm(r.get("respondent")),
                "start_dt": start_dt,
                "end_dt": end_dt,
                "today_dt": today_dt,
                "submission_date_dt": submission_date_dt,
                "submit_time_dt": submit_time_dt,
                # Originals for MVR & consent
                "HH_01_raw": r.get("HH_01"),
                "HH_02_raw": r.get("HH_02"),
                "household_raw": r.get("household"),
                "enumerator_raw": r.get("enumerator"),
                "result_list_raw": r.get("result_list"),
                "result_other_raw": r.get("result_other"),
                "consent_raw": r.get("consent"),
            }
            hh_values = {}
            for fname in hh_fields_all:
                hh_values[fname] = r.get(fname)
            entry["HH_ALL_raw"] = hh_values
            normed.append(entry)

        findings: List[Finding] = []

        # -------- Duplicate checks --------
        exact_groups: Dict[Tuple[Optional[str], ...], List[int]] = defaultdict(list)

        def exact_key(row) -> Tuple[Optional[str], ...]:
            return (
                row["province"],
                row["district"],
                row["constituency"],
                row["ward"],
                row["ea"],
                row["hun"],
                row["hhn"],
                row["start"],
                row["end"],
                row["enumerator"],
                row["respondent"],
            )

        for row in normed:
            exact_groups[exact_key(row)].append(row["id"])

        for key_tuple, ids in exact_groups.items():
            if len(ids) > 1:
                keys_dict = {
                    "province": key_tuple[0],
                    "district": key_tuple[1],
                    "constituency": key_tuple[2],
                    "ward": key_tuple[3],
                    "ea": key_tuple[4],
                    "hun": key_tuple[5],
                    "hhn": key_tuple[6],
                    "start": key_tuple[7],
                    "end": key_tuple[8],
                    "enumerator": key_tuple[9],
                    "respondent": key_tuple[10],
                }
                findings.append(
                    Finding(
                        kind="exact_fields",
                        household_ids=sorted(ids),
                        keys=keys_dict,
                        details={"count": str(len(ids))},
                    )
                )

        tri_groups: Dict[
            Tuple[Optional[str], Optional[str], Optional[str]], List[dict]
        ] = defaultdict(list)
        for row in normed:
            tri_groups[(row["ea"], row["hun"], row["hhn"])].append(row)

        for tri_key, members in tri_groups.items():
            ea, hun, hhn = tri_key
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
                    else:
                        if b["id"] not in current_cluster:
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
                    Finding(
                        kind="ea_hun_hhn_time",
                        household_ids=cluster_ids,
                        keys={"ea": ea, "hun": hun, "hhn": hhn},
                        details={
                            "window": str(window),
                            "first_seen": min_t.isoformat() if min_t else "",
                            "last_seen": max_t.isoformat() if max_t else "",
                            "count": str(len(cluster_ids)),
                        },
                    )
                )

        admin_groups: Dict[
            Tuple[
                Optional[str],
                Optional[str],
                Optional[str],
                Optional[str],
                Optional[str],
                Optional[str],
            ],
            List[int],
        ] = defaultdict(list)

        def admin_key(row):
            return (
                row["province"],
                row["district"],
                row["constituency"],
                row["ward"],
                row["hun"],
                row["hhn"],
            )

        for row in normed:
            if (row["hun"] and row["hhn"]) and (
                row["province"] or row["district"] or row["constituency"] or row["ward"]
            ):
                admin_groups[admin_key(row)].append(row["id"])

        for k, ids in admin_groups.items():
            if len(ids) > 1:
                keys_dict = {
                    "province": k[0],
                    "district": k[1],
                    "constituency": k[2],
                    "ward": k[3],
                    "hun": k[4],
                    "hhn": k[5],
                }
                findings.append(
                    Finding(
                        kind="admin_hun_hhn",
                        household_ids=sorted(ids),
                        keys=keys_dict,
                        details={"count": str(len(ids))},
                    )
                )

        # Prioritize & collapse overlapping duplicate groups
        PRIORITY = {"exact_fields": 0, "ea_hun_hhn_time": 1, "admin_hun_hhn": 2}
        findings.sort(key=lambda f: PRIORITY.get(f.kind, 999))

        claimed = set()
        filtered_dupe_groups: List[Finding] = []
        for f in findings:
            if any(hid in claimed for hid in f.household_ids):
                continue
            filtered_dupe_groups.append(f)
            claimed.update(f.household_ids)
        findings = filtered_dupe_groups

        active_duplicate_signatures: List[str] = []
        for f in findings:
            issue = upsert_issue(
                issue_type=DataQualityIssue.DUPLICATE,
                model_class=Household,
                member_ids=f.household_ids,
                title="Household possible duplicate",
                keys=f.keys,
                details={"subtype": f.kind, **f.details},
            )
            active_duplicate_signatures.append(issue.signature)

        # -------- Duration --------
        duration_findings: List[Finding] = []
        active_duration_signatures: List[str] = []
        for row in normed:
            issue = upsert_duration_issue_if_short(
                model_class=Household,
                object_id=row["id"],
                start_value=row["start"],
                end_value=row["end"],
                min_duration=min_duration,
            )
            if issue:
                active_duration_signatures.append(issue.signature)
                if row["start_dt"] and row["end_dt"]:
                    dur = row["end_dt"] - row["start_dt"]
                    duration_findings.append(
                        Finding(
                            kind="short_duration",
                            household_ids=[row["id"]],
                            keys={"start": row["start"], "end": row["end"]},
                            details={
                                "duration_minutes": f"{dur.total_seconds() / 60:.2f}",
                                "threshold": str(min_duration),
                            },
                        )
                    )

        # -------- Timeliness --------
        timeliness_findings: List[Finding] = []
        active_timeliness_signatures: List[str] = []
        for row in normed:
            issue = upsert_timeliness_issue_if_late(
                model_class=Household,
                object_id=row["id"],
                start_value=row["start"],
                today_value=row["today"],
                submission_date_value=row["submission_date"],
                submit_time_value=row["submit_time"],
                max_delay=max_delay,
            )
            if issue:
                active_timeliness_signatures.append(issue.signature)
                interview_dt = row["start_dt"] or row["today_dt"]
                submitted_dt = row["submit_time_dt"] or row["submission_date_dt"]
                if interview_dt and submitted_dt:
                    delay = submitted_dt - interview_dt
                    timeliness_findings.append(
                        Finding(
                            kind="submission_timeliness",
                            household_ids=[row["id"]],
                            keys={
                                "start": row["start"],
                                "today": row["today"],
                                "submission_date": row["submission_date"],
                                "submit_time": row["submit_time"],
                            },
                            details={
                                "delay_hours": f"{delay.total_seconds()/3600:.2f}",
                                "threshold": str(max_delay),
                            },
                        )
                    )

        # -------- Completeness (MVR) --------
        completeness_findings: List[Finding] = []
        active_incomplete_signatures: List[str] = []
        for row in normed:
            issue = upsert_household_completeness_issue(
                model_class=Household,
                object_id=row["id"],
                hh01_value=row.get("HH_01_raw"),
                hh02_value=row.get("HH_02_raw"),
                household_value=row.get("household_raw"),
                enumerator_value=row.get("enumerator_raw"),
                result_list_value=row.get("result_list_raw"),
                result_other_value=row.get("result_other_raw"),
                hh_fields=row.get("HH_ALL_raw") or {},
            )
            if issue:
                active_incomplete_signatures.append(issue.signature)
                completeness_findings.append(
                    Finding(
                        kind="mvr_completeness",
                        household_ids=[row["id"]],
                        keys={
                            "HH_01": row.get("HH_01_raw"),
                            "HH_02": row.get("HH_02_raw"),
                            "household": row.get("household_raw"),
                            "enumerator": row.get("enumerator_raw"),
                            "result_list": row.get("result_list_raw"),
                            "result_other": row.get("result_other_raw"),
                        },
                        details={"note": "Minimum Viable Record rule violated"},
                    )
                )

        # -------- Consent (NEW) --------
        consent_findings: List[Finding] = []
        active_consent_signatures: List[str] = []
        for row in normed:
            issue = upsert_household_consent_issue_if_invalid(
                model_class=Household,
                object_id=row["id"],
                household_value=row.get("household_raw"),
                consent_value=row.get("consent_raw"),
            )
            if issue:
                active_consent_signatures.append(issue.signature)
                consent_findings.append(
                    Finding(
                        kind="consent",
                        household_ids=[row["id"]],
                        keys={
                            "household": row.get("household_raw"),
                            "consent": row.get("consent_raw"),
                        },
                        details={"subtype": "household_consent_missing_or_invalid"},
                    )
                )

        # -------- Resolve stale issues (optional) --------
        if resolve_missing:
            n_resolved_dupes = resolve_missing_issues(
                issue_type=DataQualityIssue.DUPLICATE,
                model_class=Household,
                active_signatures=active_duplicate_signatures,
            )
            if n_resolved_dupes:
                self.stdout.write(
                    self.style.WARNING(
                        f"Marked {n_resolved_dupes} stale duplicate groups as resolved."
                    )
                )

            n_resolved_duration = resolve_missing_issues(
                issue_type=DataQualityIssue.DURATION,
                model_class=Household,
                active_signatures=active_duration_signatures,
            )
            if n_resolved_duration:
                self.stdout.write(
                    self.style.WARNING(
                        f"Marked {n_resolved_duration} stale short-duration issues as resolved."
                    )
                )

            n_resolved_timeliness = resolve_missing_issues(
                issue_type=DataQualityIssue.TIMELINESS,
                model_class=Household,
                active_signatures=active_timeliness_signatures,
            )
            if n_resolved_timeliness:
                self.stdout.write(
                    self.style.WARNING(
                        f"Marked {n_resolved_timeliness} stale timeliness issues as resolved."
                    )
                )

            n_resolved_incomplete = resolve_missing_issues(
                issue_type=DataQualityIssue.INCOMPLETE,
                model_class=Household,
                active_signatures=active_incomplete_signatures,
            )
            if n_resolved_incomplete:
                self.stdout.write(
                    self.style.WARNING(
                        f"Marked {n_resolved_incomplete} stale completeness issues as resolved."
                    )
                )

            n_resolved_consent = resolve_missing_issues(
                issue_type=DataQualityIssue.CONSENT,
                model_class=Household,
                active_signatures=active_consent_signatures,
            )
            if n_resolved_consent:
                self.stdout.write(
                    self.style.WARNING(
                        f"Marked {n_resolved_consent} stale consent issues as resolved."
                    )
                )

        # -------- Output / Reports --------
        kinds = defaultdict(int)
        for group in (
            findings,
            duration_findings,
            timeliness_findings,
            completeness_findings,
            consent_findings,
        ):
            for f in group:
                kinds[f.kind] += 1

        self.stdout.write(self.style.SUCCESS("Household data-quality checks complete."))
        if not any(
            [
                findings,
                duration_findings,
                timeliness_findings,
                completeness_findings,
                consent_findings,
            ]
        ):
            self.stdout.write(
                self.style.SUCCESS(
                    "No duplicate, duration, timeliness, completeness, or consent issues found."
                )
            )
        else:
            self.stdout.write("Groups by category:")
            for kind, n in kinds.items():
                self.stdout.write(f"  - {kind}: {n}")

        # Always emit CSV/JSON to fixed paths
        if out_csv:
            out_csv.parent.mkdir(parents=True, exist_ok=True)
            with out_csv.open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["kind", "household_ids", "keys", "details"])
                for f in (
                    findings
                    + duration_findings
                    + timeliness_findings
                    + completeness_findings
                    + consent_findings
                ):
                    w.writerow(
                        [
                            f.kind,
                            ";".join(str(x) for x in f.household_ids),
                            json.dumps(f.keys, ensure_ascii=False),
                            json.dumps(
                                {"subtype": f.kind, **f.details}, ensure_ascii=False
                            ),
                        ]
                    )
            self.stdout.write(self.style.SUCCESS(f"Wrote CSV report → {out_csv}"))

        if out_json:
            out_json.parent.mkdir(parents=True, exist_ok=True)
            with out_json.open("w", encoding="utf-8") as fh:
                json.dump(
                    [
                        *[
                            {
                                "kind": f.kind,
                                "household_ids": f.household_ids,
                                "keys": f.keys,
                                "details": {"subtype": f.kind, **f.details},
                            }
                            for f in (
                                findings
                                + duration_findings
                                + timeliness_findings
                                + completeness_findings
                                + consent_findings
                            )
                        ],
                    ],
                    fh,
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            self.stdout.write(self.style.SUCCESS(f"Wrote JSON report → {out_json}"))
