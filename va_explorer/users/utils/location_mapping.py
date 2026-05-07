from django.db.models import Q

from va_explorer.va_data_management.models import Location


def map_srs_clusters_to_locations(clusters):
    """Map SRSClusterLocation nodes to the corresponding Location queryset."""

    if not clusters:
        return Location.objects.none()

    cluster_nodes = []
    for cluster in clusters:
        cluster_nodes.append(cluster)
        cluster_nodes.extend(cluster.get_descendants())

    if not cluster_nodes:
        return Location.objects.none()

    query = Q()
    for node in cluster_nodes:
        if getattr(node, "name", None) and getattr(node, "location_type", None):
            query |= Q(name__iexact=node.name, location_type__iexact=node.location_type)

    if not query:
        return Location.objects.none()

    matched_locations = Location.objects.filter(query).distinct()
    if not matched_locations.exists():
        return Location.objects.none()

    location_ids = set()
    for location in matched_locations:
        descendant_ids = list(location.get_descendants().values_list("pk", flat=True))
        descendant_ids.append(location.pk)
        location_ids.update(descendant_ids)

    if not location_ids:
        return Location.objects.none()

    return Location.objects.filter(pk__in=location_ids)
