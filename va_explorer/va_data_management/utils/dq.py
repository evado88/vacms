# va_explorer/va_data_management/utils/dq.py
import hashlib
import json
from datetime import timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from va_explorer.va_data_management.models.data_quality import (
    DataQualityIssue,
    DataQualityIssueMember,
)

# ---------- Internal helpers ----------


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


def _stable_signature(
    issue_type: str,
    target_ct: ContentType,
    member_ids: Sequence[int],
    keys: Optional[dict],
) -> str:
    payload = {
        "issue_type": issue_type,
        "model": f"{target_ct.app_label}.{target_ct.model}",
        "members": sorted(int(x) for x in member_ids),
        "keys": keys or {},
    }
    j = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(j.encode("utf-8")).hexdigest()


def _norm(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    ss = str(s).strip()
    if ss == "" or ss.lower() in {"nan", "none", "null", "n/a"}:
        return None
    return ss


def _to_bool_yesno(v: Optional[str]) -> Optional[bool]:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in {"yes", "y", "true", "1", "01"}:
        return True
    if s in {"no", "n", "false", "0", "02"}:
        return False
    return None


def _is_blank(v: Any) -> bool:
    return _norm(v) is None


# ---------- Public API ----------


@transaction.atomic
def upsert_issue(
    issue_type: str,
    model_class,
    member_ids: Iterable[int],
    *,
    title: str = "",
    keys: Optional[dict] = None,
    details: Optional[dict] = None,
) -> DataQualityIssue:
    ct = ContentType.objects.get_for_model(model_class)
    member_ids = sorted({int(x) for x in member_ids})
    sig = _stable_signature(issue_type, ct, member_ids, keys)

    issue, created = DataQualityIssue.objects.get_or_create(
        signature=sig,
        defaults={
            "issue_type": issue_type,
            "target_model": ct,
            "title": title[:255],
            "keys": keys or {},
            "details": details or {},
            "status": DataQualityIssue.OPEN,
        },
    )

    changed = False
    if not created:
        meta_updates = {}
        if title and issue.title != title[:255]:
            meta_updates["title"] = title[:255]
        if keys is not None and issue.keys != keys:
            meta_updates["keys"] = keys
        if details is not None and issue.details != details:
            meta_updates["details"] = details
        if issue.status == DataQualityIssue.RESOLVED:
            meta_updates["status"] = DataQualityIssue.OPEN
            meta_updates["resolved_at"] = None
            meta_updates["resolved_by"] = None
        if meta_updates:
            for k, v in meta_updates.items():
                setattr(issue, k, v)
            issue.save(update_fields=[*meta_updates.keys(), "updated"])
            changed = True

    existing = set(issue.members.values_list("object_id", flat=True))
    desired = set(member_ids)

    to_add = desired - existing
    to_rm = existing - desired

    if to_add:
        DataQualityIssueMember.objects.bulk_create(
            [
                DataQualityIssueMember(issue=issue, content_type=ct, object_id=oid)
                for oid in sorted(to_add)
            ],
            ignore_conflicts=True,
        )
        changed = True

    if to_rm:
        DataQualityIssueMember.objects.filter(
            issue=issue, content_type=ct, object_id__in=sorted(to_rm)
        ).delete()
        changed = True

    if changed:
        issue.refresh_from_db()

    return issue


def resolve_missing_issues(
    issue_type: str,
    model_class,
    active_signatures: Iterable[str],
) -> int:
    ct = ContentType.objects.get_for_model(model_class)
    qs = DataQualityIssue.objects.filter(
        issue_type=issue_type,
        target_model=ct,
        status=DataQualityIssue.OPEN,
    ).exclude(signature__in=set(active_signatures))
    n = qs.count()
    if n:
        qs.update(status=DataQualityIssue.RESOLVED)
    return n


# ---------- Duration checks ----------


def upsert_duration_issue_if_short(
    *,
    model_class,
    object_id: int,
    start_value: Optional[str],
    end_value: Optional[str],
    min_duration: timedelta = timedelta(minutes=15),
    extra_keys: Optional[Dict[str, Any]] = None,
    extra_details: Optional[Dict[str, Any]] = None,
) -> Optional[DataQualityIssue]:
    start_dt = _parse_when(start_value)
    end_dt = _parse_when(end_value)

    if not (start_dt and end_dt):
        return None

    dur = end_dt - start_dt
    if dur >= min_duration:
        return None

    keys = {"start": start_value, "end": end_value}
    if extra_keys:
        keys.update(extra_keys)

    details = {
        "duration_minutes": f"{dur.total_seconds() / 60:.2f}",
        "threshold": str(min_duration),
        "subtype": "short_duration",
    }
    if extra_details:
        details.update(extra_details)

    return upsert_issue(
        issue_type=DataQualityIssue.DURATION,
        model_class=model_class,
        member_ids=[object_id],
        title="Short interview duration",
        keys=keys,
        details=details,
    )


def bulk_upsert_duration_issues_if_short(
    *,
    model_class,
    rows: Iterable[Dict[str, Any]],
    start_field: str = "start",
    end_field: str = "end",
    id_field: str = "id",
    min_duration: timedelta = timedelta(minutes=15),
) -> List[Tuple[int, str]]:
    signatures: List[Tuple[int, str]] = []
    for row in rows:
        obj_id = int(row.get(id_field))
        start_val = row.get(start_field)
        end_val = row.get(end_field)

        issue = upsert_duration_issue_if_short(
            model_class=model_class,
            object_id=obj_id,
            start_value=start_val,
            end_value=end_val,
            min_duration=min_duration,
        )
        if issue:
            signatures.append((obj_id, issue.signature))
    return signatures


# ---------- Submission timeliness checks ----------


def upsert_timeliness_issue_if_late(
    *,
    model_class,
    object_id: int,
    start_value: Optional[str],
    today_value: Optional[str],
    submission_date_value: Optional[str],
    submit_time_value: Optional[str],
    max_delay: timedelta = timedelta(days=2),
    extra_keys: Optional[Dict[str, Any]] = None,
    extra_details: Optional[Dict[str, Any]] = None,
) -> Optional[DataQualityIssue]:
    interview_dt = _parse_when(start_value) or _parse_when(today_value)
    submission_dt = _parse_when(submit_time_value) or _parse_when(submission_date_value)

    if not (interview_dt and submission_dt):
        return None

    delay = submission_dt - interview_dt
    if delay <= max_delay:
        return None

    keys = {
        "start": start_value,
        "today": today_value,
        "submission_date": submission_date_value,
        "submit_time": submit_time_value,
    }
    if extra_keys:
        keys.update(extra_keys)

    details = {
        "delay_hours": f"{delay.total_seconds()/3600:.2f}",
        "threshold": str(max_delay),
        "subtype": "submission_timeliness",
    }
    if extra_details:
        details.update(extra_details)

    return upsert_issue(
        issue_type=DataQualityIssue.TIMELINESS,
        model_class=model_class,
        member_ids=[object_id],
        title="Late submission (> allowed delay)",
        keys=keys,
        details=details,
    )


# ---------- Household completeness (Minimum Viable Record) ----------


def upsert_household_completeness_issue(
    *,
    model_class,
    object_id: int,
    hh01_value: Optional[str],
    hh02_value: Optional[str],
    household_value: Optional[str],
    enumerator_value: Optional[str],
    result_list_value: Optional[str],
    result_other_value: Optional[str],
    hh_fields: Dict[str, Any],
) -> Optional[DataQualityIssue]:
    hh01 = _norm(hh01_value)
    hh02 = _norm(hh02_value)
    enumerator = _norm(enumerator_value)
    household_bool = _to_bool_yesno(household_value)
    result_list = _norm(result_list_value)
    result_other = _norm(result_other_value)

    violations: List[str] = []
    details: Dict[str, Any] = {"subtype": "minimum_viable_record"}

    if household_bool is None:
        violations.append("household_missing_or_unknown")

    if household_bool is True:
        if hh01 is None:
            violations.append("HH_01_missing")
        if hh02 is None:
            violations.append("HH_02_missing")
        if enumerator is None:
            violations.append("enumerator_missing")

    if household_bool is False:
        unexpected = [fname for fname, val in hh_fields.items() if not _is_blank(val)]
        if unexpected:
            violations.append("HH_fields_should_be_blank_when_household_no")
            details["nonblank_HH_fields"] = sorted(unexpected)
        if result_list is None:
            violations.append("result_list_missing")
        else:
            rl = str(result_list).strip().lower()
            if (
                rl in {"other", "others", "other_specify", "96", "99"}
                and result_other is None
            ):
                violations.append("result_other_missing_for_other_result_list")
        if enumerator is None:
            violations.append("enumerator_missing")

    if not violations:
        return None

    keys = {
        "HH_01": hh01_value,
        "HH_02": hh02_value,
        "household": household_value,
        "enumerator": enumerator_value,
        "result_list": result_list_value,
        "result_other": result_other_value,
    }
    details["violations"] = violations

    return upsert_issue(
        issue_type=DataQualityIssue.INCOMPLETE,
        model_class=model_class,
        member_ids=[object_id],
        title="Minimum Viable Record (MVR) completeness issue",
        keys=keys,
        details=details,
    )


# ---------- NEW: Consent checks for Household ----------


def _is_yes_consent(v: Optional[str]) -> bool:
    """
    Treat these as affirmative consent: yes, y, true, 1, 01
    """
    if v is None:
        return False
    return str(v).strip().lower() in {"yes", "y", "true", "1", "01"}


def upsert_household_consent_issue_if_invalid(
    *,
    model_class,
    object_id: int,
    household_value: Optional[str],
    consent_value: Optional[str],
) -> Optional[DataQualityIssue]:
    """
    If household == YES, consent must be affirmative.
    If missing or not affirmative, create a CONSENT issue.
    """
    household_bool = _to_bool_yesno(household_value)

    # Only enforce consent when household == YES (interview conducted)
    if household_bool is not True:
        return None

    if not _is_yes_consent(consent_value):
        return upsert_issue(
            issue_type=DataQualityIssue.CONSENT,
            model_class=model_class,
            member_ids=[object_id],
            title="Consent missing or not affirmative for household = YES",
            keys={"household": household_value, "consent": consent_value},
            details={"subtype": "household_consent_missing_or_invalid"},
        )
    return None
