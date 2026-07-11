# va_explorer/va_data_management/utils/death_ids.py
"""Death ID generation, validation, and assignment.

A Death ID is a 10-character code ``NNNNCCSSSS``:

- chars 1-4: 4-digit cluster base, first digit 1-9
- chars 5-6: two checksum letters derived from the base digits
- chars 7-10: 4-digit per-cluster sequence, 0001-0999

Death records are registered without an ID (the community E_DEATH form has no
Death ID field); VACMS mints IDs here. VASA records arrive from the facility
form carrying a ``DeathID`` the interviewer entered — those are only validated
(see :func:`validate_vasa_death_ids`), never generated or overwritten.

This module is the single source of truth shared by the ``assign_death_ids``
management command, the Celery task, the death CSV loaders, and
``generate_ward_codes`` — so the checksum and format cannot drift between
call sites or the ODK form constraint.
"""

import logging
import re
from collections import defaultdict
from datetime import date
from typing import Dict, Iterable, List, Optional

from dateutil import parser as dateutil_parser
from django.db import connection, transaction
from django.db.models import Q

logger = logging.getLogger(__name__)

# Index = mod-19 checksum value. Mirrors the ODK forms' letter table.
CHECKSUM_LETTERS = "RGABKYNCLSHZDEJPFTM"

# Canonical full-string Death ID pattern. The ODK form constraint must stay
# equivalent to this (see docs/death_id_assignment.md, "Updating the ODK form").
DEATH_ID_REGEX = r"^[1-9][0-9]{3}[RGABKYNCLSHZDEJPFTM]{2}0[0-9]{3}$"
_DEATH_ID_RE = re.compile(DEATH_ID_REGEX)
_BASE_RE = re.compile(r"^[1-9][0-9]{3}$")

MAX_SEQUENCE = 999

# Arbitrary-but-fixed key for the PostgreSQL advisory lock that serialises
# concurrent assignment runs (signal-triggered task, periodic task, command).
_ASSIGN_LOCK_KEY = 0x0DEAD1D


def checksum_letters(base4: str) -> str:
    """Two checksum letters for a 4-digit base (``2026`` -> ``TL``)."""
    if not _BASE_RE.match(str(base4)):
        raise ValueError(f"Invalid Death ID base {base4!r}: must match [1-9][0-9]{{3}}")
    d1, d2, d3, d4 = (int(c) for c in str(base4))
    first = (d1 * 3 + d2 * 5 + d3 * 7 + d4 * 9) % 19
    second = (d1 * 9 + d2 * 7 + d3 * 5 + d4 * 3) % 19
    return CHECKSUM_LETTERS[first] + CHECKSUM_LETTERS[second]


def build_prefix(base4: str) -> str:
    """Full 6-character cluster prefix ``NNNNCC`` for a 4-digit base."""
    return f"{base4}{checksum_letters(base4)}"


def is_valid_cluster_code(code) -> bool:
    """True when ``code`` is a 6-char prefix whose letters match its digits."""
    code = str(code or "").strip()
    if len(code) != 6 or not _BASE_RE.match(code[:4]):
        return False
    return code[4:] == checksum_letters(code[:4])


def is_valid_death_id(value) -> bool:
    """Full validation: pattern, checksum recomputation, and suffix 0001-0999."""
    value = str(value or "").strip()
    if not _DEATH_ID_RE.match(value):
        return False
    if value[4:6] != checksum_letters(value[:4]):
        return False
    return int(value[6:]) >= 1


def format_death_id(cluster_code: str, sequence: int) -> str:
    """Build ``{cluster_code}{sequence:04d}``, refusing anything ODK rejects."""
    cluster_code = str(cluster_code or "").strip()
    if not is_valid_cluster_code(cluster_code):
        raise ValueError(f"Invalid cluster code {cluster_code!r}")
    if not 1 <= int(sequence) <= MAX_SEQUENCE:
        raise ValueError(
            f"Death ID sequence {sequence} out of range 1-{MAX_SEQUENCE} "
            f"for cluster {cluster_code}"
        )
    return f"{cluster_code}{int(sequence):04d}"


def extract_sequence(death_id) -> Optional[int]:
    """Numeric sequence of a valid Death ID; None for malformed values."""
    value = str(death_id or "").strip()
    if not is_valid_death_id(value):
        return None
    return int(value[6:])


def _parse_de06(value) -> Optional[date]:
    """Best-effort parse of the DE_06 occurrence-date text field."""
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    try:
        return dateutil_parser.parse(text, dayfirst=False).date()
    except (ValueError, OverflowError):
        return None


def _resolve_cluster_code(ward_name: str):
    """Ward name -> cluster code via ClusterLocationCodes.

    Deaths store ward *names*, so match the ``ward`` column first; fall back to
    ``ward_code`` for deployments that store codes in Death.ward.
    """
    from va_explorer.va_data_management.models import ClusterLocationCodes

    row = (
        ClusterLocationCodes.objects.filter(ward__iexact=ward_name)
        .exclude(Q(cluster_code__isnull=True) | Q(cluster_code=""))
        .first()
    ) or (
        ClusterLocationCodes.objects.filter(ward_code__iexact=ward_name)
        .exclude(Q(cluster_code__isnull=True) | Q(cluster_code=""))
        .first()
    )
    return row.cluster_code.strip() if row and row.cluster_code else None


def assign_death_ids(
    *,
    dry_run: bool = False,
    death_ids: Optional[Iterable[int]] = None,
) -> Dict:
    """Assign Death IDs to Death records that don't have one.

    Shared service behind the management command, the Celery task, and the
    import hooks. Idempotent: only rows with blank ``death_id`` are touched;
    existing IDs (valid or not) are never overwritten. ``death_ids`` restricts
    the run to specific PKs (used at ingest). Non-dry runs are serialised via
    a PostgreSQL advisory lock so concurrent callers cannot mint duplicate
    sequences.
    """
    if dry_run:
        return _assign(dry_run=True, death_ids=death_ids)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_ASSIGN_LOCK_KEY])
        return _assign(dry_run=False, death_ids=death_ids)


def _assign(*, dry_run: bool, death_ids) -> Dict:
    from va_explorer.va_data_management.models import Death

    summary: Dict = {
        "total_missing": 0,
        "assigned": 0,
        "skipped_wards": {},  # ward -> {"reason": ..., "count": n}
        "malformed_existing": [],  # existing death_ids ignored for sequencing
        "overflow_wards": [],
        "assignments": [],  # (ward, death_id, deceased name)
        "dry_run": dry_run,
    }

    qs = Death.objects.filter(Q(death_id__isnull=True) | Q(death_id=""))
    if death_ids is not None:
        pks = [pk for pk in death_ids if pk is not None]
        if not pks:
            return summary
        qs = qs.filter(id__in=pks)

    missing = list(qs.only("id", "ward", "death_id", "DE_03", "DE_06"))
    summary["total_missing"] = len(missing)
    if not missing:
        return summary

    # Group by *normalized* ward (cluster resolution is case-insensitive, so
    # "CHAMUKA" and "Chamuka" must share one sequence, not race for it).
    deaths_by_ward: Dict[str, List] = defaultdict(list)
    ward_display: Dict[str, str] = {}
    no_ward = 0
    for death in missing:
        ward = (death.ward or "").strip()
        if ward:
            ward_key = ward.lower()
            deaths_by_ward[ward_key].append(death)
            ward_display.setdefault(ward_key, ward)
        else:
            no_ward += 1
    if no_ward:
        summary["skipped_wards"]["(blank ward)"] = {
            "reason": "no ward on record",
            "count": no_ward,
        }

    to_update: List = []
    # Per-cluster next-sequence state for the whole run, so two ward groups
    # resolving to the same cluster code can never mint the same ID.
    next_seq_by_prefix: Dict[str, int] = {}
    for ward_key in sorted(deaths_by_ward):
        deaths_in_ward = deaths_by_ward[ward_key]
        ward_name = ward_display[ward_key]
        cluster_code = _resolve_cluster_code(ward_name)
        if not cluster_code:
            summary["skipped_wards"][ward_name] = {
                "reason": "no cluster code",
                "count": len(deaths_in_ward),
            }
            continue
        if not is_valid_cluster_code(cluster_code):
            summary["skipped_wards"][ward_name] = {
                "reason": f"cluster code {cluster_code!r} fails checksum",
                "count": len(deaths_in_ward),
            }
            continue

        # Continue from the highest *valid* existing sequence for this cluster
        # prefix. Malformed IDs are reported and excluded so they cannot poison
        # the sequence; they are never overwritten.
        if cluster_code not in next_seq_by_prefix:
            existing = Death.objects.filter(death_id__startswith=cluster_code).exclude(
                Q(death_id__isnull=True) | Q(death_id="")
            )
            max_seq = 0
            for existing_id in existing.values_list("death_id", flat=True):
                seq = extract_sequence(existing_id)
                if seq is None:
                    summary["malformed_existing"].append(existing_id)
                else:
                    max_seq = max(max_seq, seq)
            next_seq_by_prefix[cluster_code] = max_seq + 1

        # Deterministic order: death occurrence date (DE_06), then id;
        # unparseable/blank dates sort last.
        ordered = sorted(
            deaths_in_ward,
            key=lambda d: (
                (_parse_de06(d.DE_06) is None),
                _parse_de06(d.DE_06) or date.max,
                d.id,
            ),
        )

        next_seq = next_seq_by_prefix[cluster_code]
        for position, death in enumerate(ordered):
            if next_seq > MAX_SEQUENCE:
                remaining = len(ordered) - position
                summary["overflow_wards"].append(
                    {"ward": ward_name, "cluster_code": cluster_code}
                )
                summary["skipped_wards"][ward_name] = {
                    "reason": f"sequence exhausted (max {MAX_SEQUENCE})",
                    "count": remaining,
                }
                logger.error(
                    "Death ID sequence exhausted for ward %s (cluster %s); "
                    "%d record(s) left unassigned",
                    ward_name,
                    cluster_code,
                    remaining,
                )
                break
            death.death_id = format_death_id(cluster_code, next_seq)
            to_update.append(death)
            summary["assignments"].append(
                (ward_name, death.death_id, death.DE_03 or "Unknown")
            )
            next_seq += 1
        next_seq_by_prefix[cluster_code] = next_seq

    summary["assigned"] = len(to_update)
    if to_update and not dry_run:
        Death.objects.bulk_update(to_update, ["death_id"], batch_size=1000)
    return summary


# ---------------------------------------------------------------------------
# VASA-side validation (VASA records carry a DeathID entered in the field)
# ---------------------------------------------------------------------------

VASA_DEATH_ID_INVALID_TITLE = "VASA Death ID invalid"
VASA_DEATH_ID_UNMATCHED_TITLE = "VASA Death ID not found in death register"


def validate_vasa_death_ids() -> Dict:
    """Validate every VASASocialAutopsy.DeathID; flag problems as DQ issues.

    Two checks per non-blank DeathID (never mutated, import always proceeds):

    - format/checksum failure -> VALIDITY issue "VASA Death ID invalid"
    - well-formed but no matching ``Death.death_id`` -> VALIDITY issue
      "VASA Death ID not found in death register"

    Runs over the whole table so re-runs are idempotent and self-healing:
    issues whose DeathID has been corrected (or whose Death record has since
    been assigned) auto-resolve via ``resolve_missing_issues``.
    """
    from va_explorer.va_data_management.models import Death, VASASocialAutopsy
    from va_explorer.va_data_management.models.data_quality import DataQualityIssue
    from va_explorer.va_data_management.utils.dq import (
        resolve_missing_issues,
        upsert_issue,
    )

    summary = {"checked": 0, "invalid": 0, "unmatched": 0, "resolved": 0}

    # VASASocialAutopsy's primary key is its OneToOne to VASA (vasa_id).
    rows = list(
        VASASocialAutopsy.objects.exclude(
            Q(DeathID__isnull=True) | Q(DeathID="")
        ).values_list("pk", "DeathID")
    )
    summary["checked"] = len(rows)

    registered = {
        str(v).strip().upper()
        for v in Death.objects.exclude(
            Q(death_id__isnull=True) | Q(death_id="")
        ).values_list("death_id", flat=True)
    }

    active_signatures: List[str] = []
    for sa_id, raw_death_id in rows:
        death_id = str(raw_death_id).strip()
        if not death_id:
            continue
        if not is_valid_death_id(death_id):
            summary["invalid"] += 1
            issue = upsert_issue(
                issue_type=DataQualityIssue.VALIDITY,
                model_class=VASASocialAutopsy,
                member_ids=[sa_id],
                title=VASA_DEATH_ID_INVALID_TITLE,
                keys={"death_id": death_id, "check": "format"},
                details={"expected": DEATH_ID_REGEX},
            )
            active_signatures.append(issue.signature)
        elif death_id.upper() not in registered:
            summary["unmatched"] += 1
            issue = upsert_issue(
                issue_type=DataQualityIssue.VALIDITY,
                model_class=VASASocialAutopsy,
                member_ids=[sa_id],
                title=VASA_DEATH_ID_UNMATCHED_TITLE,
                keys={"death_id": death_id, "check": "register"},
                details={},
            )
            active_signatures.append(issue.signature)

    summary["resolved"] = resolve_missing_issues(
        DataQualityIssue.VALIDITY, VASASocialAutopsy, active_signatures
    )
    return summary
