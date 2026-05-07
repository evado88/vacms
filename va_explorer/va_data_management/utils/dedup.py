# va_explorer/va_data_management/utils/dedup.py
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from django.db.models import QuerySet

from va_explorer.va_data_management.models import Household
from va_explorer.va_data_management.utils.date_parsing import parse_date  # if available

# ---- Helpers ----


def _norm(s: str | None) -> str:
    """Normalize strings for matching: None -> '', strip, lowercase."""
    if s is None:
        return ""
    return str(s).strip().lower()


def _pick_ts(hh: Household) -> datetime | None:
    """
    Pick the best timestamp for matching.
    Try: start -> end -> submission_date (all are TextFields).
    Use parse_date() if available; else try common formats.
    """
    for candidate in (hh.start, hh.end, hh.submission_date):
        if not candidate:
            continue
        # Prefer the project's parse_date if it returns datetime or date
        try:
            dt = parse_date(candidate)  # project helper
            if dt:
                # If parse_date returns date, convert to datetime at midnight
                if not isinstance(dt, datetime):
                    return datetime.combine(dt, datetime.min.time())
                return dt
        except Exception:
            pass

        # Fallback simple parsing
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M", "%m/%d/%Y"):
            try:
                dt = datetime.strptime(candidate.strip(), fmt)
                return dt
            except Exception:
                continue
    return None


@dataclass(frozen=True)
class DedupHit:
    household_id: int
    household_key: str
    matched_id: int
    matched_key: str
    reason: str  # "EA+HUN+HHN within Xh" or "ADMIN+HUN/HHN within Xh"
    time_diff_hours: float | None
    enumerator: str
    respondent: str


# ---- Core logic ----


def find_secondary_duplicates(
    qs: QuerySet | None = None,
    time_window_hours: int = 48,
) -> list[DedupHit]:
    """
    Return suspected duplicate pairs according to the rule:
      (EA + HUN + HHN) within time window OR
      (province, district, constituency, ward + HUN + HHN) within time window.
    """
    if qs is None:
        qs = Household.objects.all()

    # Pull minimal fields to memory (text parsing is simpler in Python here)
    records: list[
        tuple[Household, str, str, str, str, str, str, str, datetime | None]
    ] = []
    for hh in qs.iterator():
        ea = _norm(hh.ea)
        hun = _norm(hh.hun)
        hhn = _norm(hh.hhn)
        province = _norm(hh.province)
        district = _norm(hh.district)
        constituency = _norm(hh.constituency)
        ward = _norm(hh.ward)
        ts = _pick_ts(hh)

        # Skip if both HUN and HHN are missing (nothing to match)
        if not hun and not hhn:
            continue

        records.append((hh, ea, hun, hhn, province, district, constituency, ward, ts))

    # Grouping: (EA,HUN,HHN) and (ADMIN,HUN,HHN)
    by_ea_hun_hhn = defaultdict(list)  # key: (ea, hun, hhn)
    by_admin_hun_hhn = defaultdict(
        list
    )  # key: (province, district, constituency, ward, hun, hhn)

    for hh, ea, hun, hhn, prov, dist, cons, ward, ts in records:
        key1 = (ea, hun, hhn)
        key2 = (prov, dist, cons, ward, hun, hhn)
        by_ea_hun_hhn[key1].append((hh, ts))
        by_admin_hun_hhn[key2].append((hh, ts))

    window = timedelta(hours=time_window_hours)
    hits: list[DedupHit] = []

    def _scan_bucket(
        bucket: Iterable[tuple[Household, datetime | None]], reason_label: str
    ):
        # Sort by timestamp (None last) for meaningful neighbor comparisons
        bucket_sorted = sorted(
            bucket, key=lambda x: (x[1] is None, x[1] or datetime.min)
        )
        n = len(bucket_sorted)
        if n < 2:
            return
        # Compare every pair within the bucket; stop early when time diff exceeds window
        for i in range(n):
            hh_i, ts_i = bucket_sorted[i]
            for j in range(i + 1, n):
                hh_j, ts_j = bucket_sorted[j]
                # If either timestamp missing, we still record as possible duplicate (time diff unknown)
                if ts_i and ts_j:
                    td = abs(ts_j - ts_i)
                    if td > window:
                        # Because sorted by ts, later entries will only be farther in time
                        break
                    time_diff = td.total_seconds() / 3600.0
                else:
                    time_diff = None

                hits.append(
                    DedupHit(
                        household_id=hh_i.id,
                        household_key=hh_i.key,
                        matched_id=hh_j.id,
                        matched_key=hh_j.key,
                        reason=f"{reason_label} within {time_window_hours}h",
                        time_diff_hours=time_diff,
                        enumerator=_norm(hh_i.enumerator),
                        respondent=_norm(hh_i.respondent),
                    )
                )

    # Scan (EA+HUN+HHN)
    for key, bucket in by_ea_hun_hhn.items():
        ea, hun, hhn = key
        if not (ea and (hun or hhn)):
            continue
        _scan_bucket(bucket, "EA+HUN+HHN")

    # Scan (ADMIN+HUN/HHN) — EA excluded on purpose
    for key, bucket in by_admin_hun_hhn.items():
        prov, dist, cons, ward, hun, hhn = key
        if not (prov or dist or cons or ward):
            continue
        if not (hun or hhn):
            continue
        _scan_bucket(bucket, "ADMIN+HUN/HHN")

    return hits
