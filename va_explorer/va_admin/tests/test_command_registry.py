from pathlib import Path

from va_explorer.va_admin.command_registry import (
    ALLOWLIST,
    REGISTRY_ITEMS,
    grouped_command_specs,
    requires_password_confirmation,
)

# The Favorites tab must include at least these commands (docs/admin_panel.md req #5).
REQUIRED_FAVORITES = {
    "load_pregnancy_csv",
    "load_pregnancy_outcome_csv",
    "load_death_csv",
    "load_va_csv",
    "run_coding_algorithms",
    "load_csa_tracker",
}


def test_registry_keys_are_unique():
    # A duplicate key would be silently dropped by the ALLOWLIST dict.
    assert len(REGISTRY_ITEMS) == len(ALLOWLIST)


def test_load_va_csv_appears_once():
    matches = [item for item in REGISTRY_ITEMS if item.key == "load_va_csv"]
    assert len(matches) == 1


def test_every_project_management_command_is_registered():
    command_files = Path("va_explorer").glob("*/management/commands/*.py")
    command_names = {path.stem for path in command_files if path.stem != "__init__"}
    registered = {item.management_command for item in REGISTRY_ITEMS}

    assert command_names - registered == set()


def test_vasa_loader_exposes_each_repeat_group():
    spec = ALLOWLIST["load_vasa_csv"]
    repeat_inputs = [field for field in spec.inputs if field.flag == "--repeat"]

    assert {field.name for field in repeat_inputs} == {
        "neonate_repeat",
        "child_repeat",
        "adult_repeat",
    }


def test_va_vasa_export_repairs_are_available_as_maintenance_commands():
    groups = grouped_command_specs()
    maintenance = {spec.key for spec in groups["Maintenance"]}

    assert {
        "backfill_va_submitted_at",
        "resolve_vasa_locations",
    }.issubset(maintenance)

    backfill = ALLOWLIST["backfill_va_submitted_at"]
    assert {field.flag for field in backfill.inputs} == {
        "--dry-run",
        "--batch-size",
    }

    location_backfill = ALLOWLIST["resolve_vasa_locations"]
    assert {field.flag for field in location_backfill.inputs} == {"--dry-run"}


def test_favorites_include_required_commands():
    favorites = {item.key for item in REGISTRY_ITEMS if item.favorite}
    assert REQUIRED_FAVORITES.issubset(favorites)


def test_no_command_appears_in_more_than_one_category():
    groups = grouped_command_specs()
    seen = {}
    for category, specs in groups.items():
        for spec in specs:
            seen.setdefault(spec.key, []).append(category)
    duplicated = {key: cats for key, cats in seen.items() if len(cats) > 1}
    assert duplicated == {}


def test_favorites_not_repeated_in_other_categories():
    groups = grouped_command_specs()
    favorite_keys = {item.key for item in REGISTRY_ITEMS if item.favorite}
    for category, specs in groups.items():
        if category == "Favorites":
            continue
        leaked = {spec.key for spec in specs} & favorite_keys
        assert leaked == set(), f"Favorites leaked into {category}: {leaked}"


def test_favorites_tab_contains_all_favorites():
    groups = grouped_command_specs()
    favorite_keys = {item.key for item in REGISTRY_ITEMS if item.favorite}
    favorites_tab = {spec.key for spec in groups.get("Favorites", [])}
    assert favorites_tab == favorite_keys


def test_password_required_for_non_favorite_mutating_only():
    for item in REGISTRY_ITEMS:
        expected = item.safety == "mutating" and not item.favorite
        assert requires_password_confirmation(item) is expected
        # Favorites are always exempt.
        if item.favorite:
            assert requires_password_confirmation(item) is False
