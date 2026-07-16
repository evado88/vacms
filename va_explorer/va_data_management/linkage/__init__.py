"""Death <-> VA linkage crosswalk package.

``crosswalk_rules``  — hand-maintained seed table, overrides and category maps
                       (the human-confirmation record for the crosswalk).
``linkage_map``      — AUTO-GENERATED artifact (run ``generate_linkage_crosswalk``).
``linkage_crosswalk.csv`` — the reviewable artifact emitted alongside it.
"""


def active_concepts():
    """Concepts confirmed to drive linking (status == 'confirmed')."""
    from va_explorer.va_data_management.linkage import linkage_map

    return {
        concept: spec
        for concept, spec in linkage_map.CONCEPTS.items()
        if spec.get("status") == "confirmed"
    }
