from va_explorer.va_data_management.models import Location

_ADMIN_LEVELS = ("province", "district", "constituency", "ward", "ea")


def _tree_lineage(node, nodes_by_path, step_length):
    lineage = {}
    for prefix_length in range(step_length, len(node["path"]) + 1, step_length):
        ancestor = nodes_by_path.get(node["path"][:prefix_length])
        if not ancestor:
            continue
        level = str(ancestor["location_type"] or "").lower()
        name = str(ancestor["name"] or "").strip()
        if level and name:
            lineage[level] = name.casefold()
    return lineage


def map_srs_clusters_to_locations(clusters):
    """Map SRSClusterLocation nodes to the corresponding Location queryset."""

    clusters = list(clusters)
    if not clusters:
        return Location.objects.none()

    cluster_model = type(clusters[0])
    all_cluster_nodes = list(
        cluster_model.objects.values("id", "path", "name", "location_type")
    )
    selected_paths = [cluster.path for cluster in clusters]
    cluster_nodes = [
        node
        for node in all_cluster_nodes
        if any(node["path"].startswith(path) for path in selected_paths)
    ]

    location_nodes = list(
        Location.objects.values("id", "path", "name", "location_type")
    )
    location_by_path = {node["path"]: node for node in location_nodes}
    locations_by_name_and_type = {}
    for node in location_nodes:
        key = (
            str(node["location_type"] or "").lower(),
            str(node["name"] or "").strip().casefold(),
        )
        locations_by_name_and_type.setdefault(key, []).append(node)

    cluster_by_path = {node["path"]: node for node in all_cluster_nodes}

    matched_paths = set()
    for cluster in cluster_nodes:
        cluster_lineage = _tree_lineage(cluster, cluster_by_path, cluster_model.steplen)
        key = (
            str(cluster["location_type"] or "").lower(),
            str(cluster["name"] or "").strip().casefold(),
        )

        for candidate in locations_by_name_and_type.get(key, []):
            location_lineage = _tree_lineage(
                candidate, location_by_path, Location.steplen
            )
            if any(
                cluster_lineage.get(level) != location_lineage.get(level)
                for level in _ADMIN_LEVELS
                if cluster_lineage.get(level) and location_lineage.get(level)
            ):
                continue
            matched_paths.add(candidate["path"])

    location_ids = {
        node["id"]
        for node in location_nodes
        if any(node["path"].startswith(path) for path in matched_paths)
    }

    if not location_ids:
        return Location.objects.none()

    return Location.objects.filter(pk__in=location_ids)
