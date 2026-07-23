from collections import OrderedDict

from django.contrib.contenttypes.models import ContentType
from django.db.models import Subquery
from django.urls import reverse

from va_explorer.va_data_management.models.data_quality import (
    DataQualityIssue,
    DataQualityIssueMember,
)

DETAIL_ROUTES = {
    "household": ("va_data_management:household_detail", "Household"),
    "death": ("va_data_management:death_detail", "Death"),
    "pregnancy": ("va_data_management:pregnancy_detail", "Pregnancy"),
    "pregnancyoutcome": (
        "va_data_management:pregnancy_outcome_detail",
        "Pregnancy outcome",
    ),
    "verbalautopsy": ("va_data_management:show", "VA"),
    "vasa": ("va_data_management:vasa_show", "VASA"),
}


def _record_links(record_ids, route_name, label):
    return [
        {
            "id": record_id,
            "label": f"{label} #{record_id}",
            "url": reverse(route_name, kwargs={"id": record_id}),
        }
        for record_id in record_ids
    ]


def get_duplicate_issue_groups(record, accessible_queryset):
    """Return open duplicate groups with links the current user may access.

    Duplicate memberships are loaded in one query. Candidate records are then
    reduced to IDs through the detail view's access-restricted queryset, avoiding
    both generic-foreign-key N+1 queries and disclosure of inaccessible records.
    """
    route = DETAIL_ROUTES.get(record._meta.model_name)
    if route is None:
        return []

    route_name, label = route
    content_type = ContentType.objects.get_for_model(record, for_concrete_model=False)
    current_issue_ids = DataQualityIssueMember.objects.filter(
        content_type=content_type,
        object_id=record.pk,
        issue__target_model=content_type,
        issue__issue_type=DataQualityIssue.DUPLICATE,
        issue__status=DataQualityIssue.OPEN,
    ).values("issue_id")
    other_members = (
        DataQualityIssueMember.objects.filter(
            content_type=content_type,
            issue_id__in=Subquery(current_issue_ids),
        )
        .exclude(object_id=record.pk)
        .select_related("issue")
        .only("object_id", "issue_id", "issue__title", "issue__detected_at")
        .order_by("-issue__detected_at", "issue_id", "object_id")
    )

    grouped_ids = OrderedDict()
    issues_by_id = {}
    for member in other_members:
        issues_by_id[member.issue_id] = member.issue
        grouped_ids.setdefault(member.issue_id, []).append(member.object_id)

    candidate_ids = {object_id for ids in grouped_ids.values() for object_id in ids}
    accessible_ids = set()
    if candidate_ids:
        accessible_ids = set(
            accessible_queryset.filter(pk__in=candidate_ids)
            .order_by()
            .values_list("pk", flat=True)
        )

    groups = []
    linked_ids = set()
    for issue_id, object_ids in grouped_ids.items():
        visible_ids = [
            object_id for object_id in object_ids if object_id in accessible_ids
        ]
        if not visible_ids:
            continue
        linked_ids.update(visible_ids)
        issue = issues_by_id[issue_id]
        groups.append(
            {
                "id": issue_id,
                "title": issue.title or "Possible duplicate",
                "records": _record_links(visible_ids, route_name, label),
            }
        )

    # VerbalAutopsy predates DataQualityIssue and records duplicate groups with a
    # shared identifier plus a flag. Keep that established workflow visible while
    # using the same access-aware output as newer duplicate checks.
    if (
        record._meta.model_name == "verbalautopsy"
        and record.duplicate
        and record.unique_va_identifier
    ):
        legacy_ids = list(
            accessible_queryset.filter(unique_va_identifier=record.unique_va_identifier)
            .exclude(pk=record.pk)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        legacy_ids = [
            record_id for record_id in legacy_ids if record_id not in linked_ids
        ]
        if legacy_ids:
            groups.append(
                {
                    "id": None,
                    "title": "VA possible duplicate",
                    "records": _record_links(legacy_ids, route_name, label),
                }
            )

    return groups
