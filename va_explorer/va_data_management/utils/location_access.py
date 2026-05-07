from typing import Dict, List, Optional, Set

from django.core.cache import cache
from django.db.models import Q
from django.db.models.functions import Lower

_LOCATION_TREE_CACHE_VERSION_KEY = "location_tree:version"
_LOCATION_TREE_NODE_CACHE_TTL = 86400  # 24 hours


def _location_tree_version() -> int:
    v = cache.get(_LOCATION_TREE_CACHE_VERSION_KEY)
    if v is None:
        v = 1
        cache.set(_LOCATION_TREE_CACHE_VERSION_KEY, v, timeout=None)
    return int(v)


def bump_location_tree_version() -> int:
    """Invalidate all cached location-tree node lists. Called by Location signals."""
    v = cache.get(_LOCATION_TREE_CACHE_VERSION_KEY)
    next_v = int(v) + 1 if v is not None else 1
    cache.set(_LOCATION_TREE_CACHE_VERSION_KEY, next_v, timeout=None)
    return next_v


def _collect_related_nodes(location) -> List:
    """Return the location, its descendants, and ancestors (cached per location.pk)."""
    version = _location_tree_version()
    cache_key = f"location_tree:nodes:{location.pk}:v{version}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    nodes = [location]
    nodes.extend(location.get_descendants())
    nodes.extend(location.get_ancestors())

    cache.set(cache_key, nodes, timeout=_LOCATION_TREE_NODE_CACHE_TTL)
    return nodes


def _allowed_location_names(user) -> Optional[Dict[str, Set[str]]]:
    if not user or not getattr(user, "is_authenticated", False):
        return None

    # Per-request memoization: avoids recomputing on the same request
    cached_on_user = getattr(user, "_allowed_location_names_cache", None)
    if cached_on_user is not None:
        return cached_on_user if cached_on_user else None

    locations = getattr(user, "location_restrictions", None)
    if not locations:
        user._allowed_location_names_cache = {}
        return None

    locations = locations.all()
    if not locations.exists():
        user._allowed_location_names_cache = {}
        return None

    allowed: Dict[str, Set[str]] = {
        "province": set(),
        "district": set(),
        "constituency": set(),
        "ward": set(),
        "ea": set(),
    }

    for location in locations:
        for node in _collect_related_nodes(location):
            loc_type = (getattr(node, "location_type", "") or "").lower()
            name = getattr(node, "name", "")
            if loc_type in allowed and name:
                allowed[loc_type].add(name.strip())

    if not any(allowed.values()):
        user._allowed_location_names_cache = {}
        return None

    user._allowed_location_names_cache = allowed
    return allowed


def restrict_queryset_to_user_locations(queryset, user, field_mapping=None):
    """
    Limit queryset rows to the geographic locations assigned to a user with
    location restrictions. If the user has no location assignments, the queryset
    is returned unchanged.
    """

    allowed = _allowed_location_names(user)
    if not allowed:
        return queryset

    default_mapping = {
        "province": "province",
        "district": "district",
        "constituency": "constituency",
        "ward": "ward",
        "ea": "ea",
    }
    mapping = field_mapping or default_mapping
    locations_qs = user.location_restrictions.all()
    has_non_province_assignments = locations_qs.exclude(
        location_type__iexact="province"
    ).exists()

    # Annotate once with Lower() for each field we need to filter on.
    # This lets us use a single __in lookup per field instead of a chain of
    # __iexact OR conditions, reducing the Q-object tree depth considerably.
    active_fields = {
        field_name for loc_type, field_name in mapping.items() if allowed.get(loc_type)
    }
    annotations = {f"_rloc_{fn}": Lower(fn) for fn in active_fields}
    if annotations:
        queryset = queryset.annotate(**annotations)

    # Anchor by province when available to avoid cross-province leakage when
    # lower-level names overlap between provinces.
    combined_q = Q()
    has_any_filter = False
    province_field = mapping.get("province")
    province_names = allowed.get("province") or set()
    if province_field and province_names:
        ann_key = f"_rloc_{province_field}"
        combined_q &= Q(**{f"{ann_key}__in": [n.lower() for n in province_names]})
        has_any_filter = True

    lower_level_q = Q()
    for loc_type, field_name in mapping.items():
        if loc_type == "province":
            continue
        names = allowed.get(loc_type)
        if not names:
            continue
        ann_key = f"_rloc_{field_name}"
        lower_level_q |= Q(**{f"{ann_key}__in": [n.lower() for n in names]})

    if lower_level_q.children:
        has_any_filter = True
        if province_names and has_non_province_assignments:
            combined_q &= lower_level_q
        elif not province_names:
            combined_q = lower_level_q

    if not has_any_filter:
        return queryset

    return queryset.filter(combined_q)


def restrict_va_queryset_to_user_locations(queryset, user):
    """
    VerbalAutopsy-specific restriction that supports both:
    - facility VA access via Location tree relation (location FK), and
    - community VA access via persisted admin text fields.
    """
    allowed = _allowed_location_names(user)
    if not allowed:
        return queryset

    locations = getattr(user, "location_restrictions", None)
    if not locations:
        return queryset

    locations_qs = locations.all()
    if not locations_qs.exists():
        return queryset

    # Facility path scoping using location tree nodes.
    location_nodes = set()
    for location in locations_qs:
        for node in _collect_related_nodes(location):
            location_nodes.add(node.pk)
    facility_q = Q(location__in=location_nodes)

    # Community/admin path scoping using text geography fields.
    # Annotate with Lower() once per field so we can use __in instead of
    # a chain of __iexact OR conditions.
    text_fields = {loc_type for loc_type, names in allowed.items() if names}
    if text_fields:
        queryset = queryset.annotate(**{f"_rloc_{f}": Lower(f) for f in text_fields})
    text_q = Q()
    for loc_type, names in allowed.items():
        if not names:
            continue
        text_q |= Q(**{f"_rloc_{loc_type}__in": [n.lower() for n in names]})

    combined_q = facility_q
    if text_q.children:
        combined_q |= text_q

    return queryset.filter(combined_q).distinct()
