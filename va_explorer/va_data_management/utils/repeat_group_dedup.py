# va_explorer/va_data_management/utils/repeat_group_dedup.py
"""
Content-based duplicate detection for ODK repeat-group child records
(HouseholdMember, HouseholdDeath, PregnancyOutcomeBaby).

The load commands already dedupe by the ODK ``key`` (exact re-imports). This
module catches the harder case: the same person ingested under *different* keys
(re-collected forms, edits that mint a new UUID, or double entry). Two child
rows are considered the same when they share the same parent and identical
identity fields (name + DOB + sex), compared case-insensitively and trimmed.

For each duplicate set the most-complete row is kept canonical (fewest blank
fields, ties broken by lowest id); the rest are flagged ``is_duplicate=True``
with ``duplicate_of`` pointing at the canonical row, so every ``.canonical()``
count shows one row per set. A ``DataQualityIssue(DUPLICATE)`` is upserted per
set for audit/visibility, reusing the same stable-signature, idempotent,
auto-resolving machinery as the parent-model DQ runner. Re-runs are no-ops and
self-heal (a row that is no longer a duplicate is un-flagged).
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from va_explorer.va_data_management.models.data_quality import DataQualityIssue
from va_explorer.va_data_management.utils.dq import (
    _norm_key,
    resolve_missing_issues,
    upsert_issue,
)


@dataclass
class ChildDedupSpec:
    model: type
    label: str
    slug: str
    parent_field: str  # FK id column, e.g. "household_id"
    identity_fields: Sequence[str]  # all must be non-blank to group
    dq_title: str


# Bookkeeping columns that must not influence the most-complete canonical pick
# (otherwise a row's own duplicate flag would shift the choice on re-runs).
_NON_CONTENT_FIELDS = frozenset({"id", "is_duplicate", "duplicate_of_id"})


def _blank_count(row: Dict[str, Any], parent_field: str) -> int:
    """Number of blank/placeholder content values in a row (drives canonical pick)."""
    ignore = _NON_CONTENT_FIELDS | {parent_field}
    return sum(1 for k, v in row.items() if k not in ignore and _norm_key(v) is None)


def run_dedup_for_model(
    spec: ChildDedupSpec,
    *,
    resolve_missing: bool = True,
) -> Dict[str, Any]:
    """Detect, flag, and record content duplicates for one child model."""
    model = spec.model
    rows = list(model.objects.values())
    summary: Dict[str, Any] = {
        "label": spec.label,
        "total_rows": len(rows),
        "groups": 0,
        "flagged": 0,
        "unflagged": 0,
        "resolved": 0,
    }
    if not rows:
        if resolve_missing:
            summary["resolved"] = resolve_missing_issues(
                DataQualityIssue.DUPLICATE, model, []
            )
        return summary

    # Group by (parent, normalized identity tuple); skip rows with any blank
    # identity field so incomplete rows are never grouped.
    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for row in rows:
        identity = tuple(_norm_key(row.get(f)) for f in spec.identity_fields)
        if any(v is None for v in identity):
            continue
        parent = row.get(spec.parent_field)
        if parent is None:
            continue
        groups[(parent, identity)].append(row)

    active_signatures: List[str] = []
    desired_duplicate_to_canonical: Dict[int, int] = {}

    for (_parent, identity), members in groups.items():
        if len(members) < 2:
            continue
        summary["groups"] += 1

        # Canonical = most complete (fewest blanks), ties broken by lowest id.
        canonical = min(
            members, key=lambda r: (_blank_count(r, spec.parent_field), r["id"])
        )
        canonical_id = canonical["id"]
        all_ids = sorted(r["id"] for r in members)
        dup_ids = sorted(i for i in all_ids if i != canonical_id)

        for dup_id in dup_ids:
            desired_duplicate_to_canonical[dup_id] = canonical_id

        issue = upsert_issue(
            issue_type=DataQualityIssue.DUPLICATE,
            model_class=model,
            member_ids=all_ids,
            title=spec.dq_title,
            keys=dict(zip(spec.identity_fields, identity)),
            details={
                "subtype": "child_content_match",
                "canonical_id": canonical_id,
                "duplicate_ids": dup_ids,
                "count": str(len(all_ids)),
            },
        )
        active_signatures.append(issue.signature)

    # Apply flags. Group duplicates by canonical so each update() sets a single
    # duplicate_of value across the affected rows.
    canonical_to_dups: Dict[int, List[int]] = defaultdict(list)
    for dup_id, canonical_id in desired_duplicate_to_canonical.items():
        canonical_to_dups[canonical_id].append(dup_id)

    for canonical_id, dup_ids in canonical_to_dups.items():
        # Only touch rows not already flagged against this canonical so a stable
        # re-run reports zero new flags.
        summary["flagged"] += (
            model.objects.filter(id__in=dup_ids)
            .exclude(is_duplicate=True, duplicate_of_id=canonical_id)
            .update(is_duplicate=True, duplicate_of_id=canonical_id)
        )

    # Self-heal: any row currently flagged that is no longer a duplicate.
    summary["unflagged"] = (
        model.objects.filter(is_duplicate=True)
        .exclude(id__in=desired_duplicate_to_canonical.keys())
        .update(is_duplicate=False, duplicate_of=None)
    )

    if resolve_missing:
        summary["resolved"] = resolve_missing_issues(
            DataQualityIssue.DUPLICATE, model, active_signatures
        )

    return summary


def _build_specs() -> Dict[str, ChildDedupSpec]:
    # Imported lazily to avoid import-time model loading order issues.
    from va_explorer.va_data_management.models import (
        HouseholdDeath,
        HouseholdMember,
        PregnancyOutcomeBaby,
    )

    return {
        "members": ChildDedupSpec(
            model=HouseholdMember,
            label="Household Member",
            slug="household_members",
            parent_field="household_id",
            identity_fields=["first_name", "last_name", "HH_07", "HH_05"],
            dq_title="Household member possible duplicate",
        ),
        "deaths": ChildDedupSpec(
            model=HouseholdDeath,
            label="Household Death",
            slug="household_deaths",
            parent_field="household_id",
            identity_fields=["deathname", "sirname", "HH_48E", "HH_48F"],
            dq_title="Household death possible duplicate",
        ),
        "babies": ChildDedupSpec(
            model=PregnancyOutcomeBaby,
            label="Pregnancy Outcome Baby",
            slug="pregnancy_outcome_babies",
            parent_field="pregnancy_outcome_id",
            identity_fields=["PO_49B", "PO_49C"],
            dq_title="Pregnancy outcome baby possible duplicate",
        ),
    }


# target name -> spec
CHILD_DEDUP_SPECS: Dict[str, ChildDedupSpec] = _build_specs()
