from django.db.models import CharField, F, Value
from django.db.models.functions import Cast, Coalesce, Lower, Trim

INVALID_LIST_DISPLAY_VALUES = frozenset({"", "-", "not listed", "unknown"})


def exclude_rows_with_invalid_display_values(queryset, field_names):
    """
    Exclude rows whose displayed table fields resolve to placeholder values.

    Values are normalized with trim + lowercase so blank/whitespace-only
    content and case variants are treated consistently.
    """

    unique_field_names = tuple(dict.fromkeys(field_names))
    annotations = {}

    for field_name in unique_field_names:
        annotation_name = (
            f"_listclean_{field_name.lower().replace('__', '_').replace('.', '_')}"
        )
        annotations[annotation_name] = Lower(
            Trim(
                Coalesce(
                    Cast(F(field_name), output_field=CharField()),
                    Value(""),
                )
            )
        )

    if annotations:
        queryset = queryset.annotate(**annotations)

    for annotation_name in annotations:
        queryset = queryset.exclude(
            **{f"{annotation_name}__in": INVALID_LIST_DISPLAY_VALUES}
        )

    return queryset
