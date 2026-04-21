from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.core.management import get_commands, load_command_class

ADMINS_GROUP_NAME = "Admins"
SAFE_FILENAME_CHARS = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
CATEGORY_ORDER = (
    "Favorites",
    "Data Loading",
    "Data Quality",
    "Analytics",
    "Maintenance",
    "User/Admin",
    "Backups/Exports",
    "Integrations",
)


@dataclass(frozen=True)
class CommandInputSpec:
    type: str  # file | text | choice | date | bool | int
    name: str
    required: bool = False
    help: str = ""
    filename_pattern: str = ""
    allowed_pattern: str = ""
    standardized_name: str = ""
    default: str = ""
    choices: tuple[str, ...] = ()
    flag: str | None = None
    placeholder_key: str | None = None


@dataclass(frozen=True)
class CommandRegistryItem:
    key: str
    management_command: str
    category: str
    description: str
    inputs: tuple[CommandInputSpec, ...] = field(default_factory=tuple)
    safety: str = "mutating"  # read_only | mutating
    timeout_hint: int | None = None
    favorite: bool = False
    dangerous: bool = False


STANDARD_FILENAME_PLACEHOLDERS: dict[str, str] = {
    # Keep blank by default so filenames can be standardized later.
    "users_csv": "",
    "households_csv": "",
    "members_csv": "",
    "pregnancies_csv": "",
    "pregnancy_outcomes_csv": "",
    "deaths_csv": "",
    "va_csv": "",
    "locations_csv": "",
    "cluster_locations_csv": "",
    "dhis_cod_codes_csv": "",
}


def data_dir() -> Path:
    return Path(settings.BASE_DIR) / "va_explorer" / "static" / "data"


def is_safe_filename(filename: str) -> bool:
    if not filename or filename.strip() != filename:
        return False
    if "/" in filename or "\\" in filename or ".." in filename:
        return False
    return all(char in SAFE_FILENAME_CHARS for char in filename)


def resolve_data_file(filename: str) -> Path:
    if not is_safe_filename(filename):
        raise ValueError(
            "Invalid filename. Only safe filenames are allowed (no paths)."
        )
    base = data_dir().resolve()
    resolved = (base / filename).resolve()
    if base not in resolved.parents and resolved != base:
        raise ValueError("Filename must resolve inside va_explorer/static/data/")
    return resolved


def _description_for(command_name: str) -> str:
    try:
        app_name = get_commands().get(command_name)
        if app_name:
            command = load_command_class(app_name, command_name)
            if getattr(command, "help", None):
                return str(command.help).strip()
    except Exception:
        pass
    return command_name.replace("_", " ").capitalize()


def _command_specs() -> list[CommandRegistryItem]:
    csv_input = CommandInputSpec(
        type="file",
        name="csv_file",
        required=True,
        help="CSV filename located in va_explorer/static/data/",
        filename_pattern="DATA_FILE.csv",
        allowed_pattern=r"^[A-Za-z0-9._-]+\.csv$",
        standardized_name="DATA_FILE.csv",
    )
    return [
        CommandRegistryItem(
            key="bulk_load_users",
            management_command="bulk_load_users",
            category="User/Admin",
            description=_description_for("bulk_load_users"),
            inputs=(
                CommandInputSpec(
                    type="file",
                    name="user_list_file",
                    required=True,
                    help="Users roster for bulk user account creation.",
                    filename_pattern="USERS.csv",
                    placeholder_key="users_csv",
                    standardized_name="USERS.csv",
                    allowed_pattern=r"^USERS\.csv$",
                ),
                CommandInputSpec(
                    type="bool",
                    name="email_confirmation",
                    required=False,
                    help="Send account confirmation emails.",
                    flag="--email_confirmation",
                ),
            ),
            safety="mutating",
            timeout_hint=300,
            favorite=True,
        ),
        CommandRegistryItem(
            "get_user_form_template",
            "get_user_form_template",
            "User/Admin",
            _description_for("get_user_form_template"),
            safety="read_only",
        ),
        CommandRegistryItem(
            "export_user_info",
            "export_user_info",
            "Backups/Exports",
            _description_for("export_user_info"),
            safety="read_only",
        ),
        CommandRegistryItem(
            "initialize_groups",
            "initialize_groups",
            "User/Admin",
            _description_for("initialize_groups"),
            safety="mutating",
        ),
        CommandRegistryItem(
            key="seed_admin_user",
            management_command="seed_admin_user",
            category="User/Admin",
            description=_description_for("seed_admin_user"),
            inputs=(
                CommandInputSpec(
                    type="text",
                    name="email",
                    required=True,
                    help="Admin email address.",
                ),
                CommandInputSpec(
                    type="text",
                    name="password",
                    required=False,
                    help="Optional password.",
                    flag="--password",
                ),
            ),
            safety="mutating",
        ),
        CommandRegistryItem(
            "seed_demo_users",
            "seed_demo_users",
            "User/Admin",
            _description_for("seed_demo_users"),
            safety="mutating",
        ),
        CommandRegistryItem(
            "load_household_csv",
            "load_household_csv",
            "Data Loading",
            _description_for("load_household_csv"),
            inputs=(
                CommandInputSpec(
                    **{
                        **csv_input.__dict__,
                        "filename_pattern": "BASELINE_CENSUS_ROSTER or BASELINE_CENSUS_ROSTER.csv",
                        "placeholder_key": "households_csv",
                        "standardized_name": "BASELINE_CENSUS_ROSTER",
                        "allowed_pattern": r"^(BASELINE_CENSUS_ROSTER|BASELINE_CENSUS_ROSTER\.csv)$",
                        "help": "Baseline census household roster file.",
                    }
                ),
            ),
            safety="mutating",
            timeout_hint=600,
            favorite=True,
        ),
        CommandRegistryItem(
            "load_household_members",
            "load_household_members",
            "Data Loading",
            _description_for("load_household_members"),
            inputs=(
                CommandInputSpec(
                    **{
                        **csv_input.__dict__,
                        "filename_pattern": "HHC_BASELINE_CENSUS_ROSTER-member.csv",
                        "placeholder_key": "members_csv",
                        "standardized_name": "HHC_BASELINE_CENSUS_ROSTER-member.csv",
                        "allowed_pattern": r"^HHC_BASELINE_CENSUS_ROSTER-member\.csv$",
                        "help": "Household members extracted from baseline census roster.",
                    }
                ),
            ),
            safety="mutating",
            timeout_hint=600,
            favorite=True,
        ),
        CommandRegistryItem(
            "load_pregnancy_csv",
            "load_pregnancy_csv",
            "Data Loading",
            _description_for("load_pregnancy_csv"),
            inputs=(
                CommandInputSpec(
                    **{
                        **csv_input.__dict__,
                        "filename_pattern": "E_PREGNANCY.csv",
                        "placeholder_key": "pregnancies_csv",
                        "standardized_name": "E_PREGNANCY.csv",
                        "help": "Pregnancy events extract.",
                    }
                ),
            ),
            safety="mutating",
            timeout_hint=600,
            favorite=True,
        ),
        CommandRegistryItem(
            "load_pregnancy_outcome_csv",
            "load_pregnancy_outcome_csv",
            "Data Loading",
            _description_for("load_pregnancy_outcome_csv"),
            inputs=(
                CommandInputSpec(
                    **{
                        **csv_input.__dict__,
                        "filename_pattern": "E_PREGNANCY_OUTCOME.csv",
                        "placeholder_key": "pregnancy_outcomes_csv",
                        "standardized_name": "E_PREGNANCY_OUTCOME.csv",
                        "help": "Pregnancy outcomes extract.",
                    }
                ),
            ),
            safety="mutating",
            timeout_hint=600,
            favorite=True,
        ),
        CommandRegistryItem(
            "load_death_csv",
            "load_death_csv",
            "Data Loading",
            _description_for("load_death_csv"),
            inputs=(
                CommandInputSpec(
                    **{
                        **csv_input.__dict__,
                        "filename_pattern": "E_DEATH.csv",
                        "placeholder_key": "deaths_csv",
                        "standardized_name": "E_DEATH.csv",
                        "help": "Death events extract.",
                    }
                ),
            ),
            safety="mutating",
            timeout_hint=600,
            favorite=True,
        ),
        CommandRegistryItem(
            "load_va_csv",
            "load_va_csv",
            "Data Loading",
            _description_for("load_va_csv"),
            inputs=(
                CommandInputSpec(
                    **{
                        **csv_input.__dict__,
                        "filename_pattern": "znphi_va_who_v1_5_2_7.csv | zm_va_who_v1_5_2_7.csv",
                        "placeholder_key": "va_csv",
                        "standardized_name": "znphi_va_who_v1_5_2_7.csv or zm_va_who_v1_5_2_7.csv",
                        "allowed_pattern": r"^(znphi_va_who_v1_5_2_7\.csv|zm_va_who_v1_5_2_7\.csv)$",
                        "help": "WHO VA export file for VA import pipeline.",
                    }
                ),
            ),
            safety="mutating",
            timeout_hint=900,
        ),
        CommandRegistryItem(
            "load_locations",
            "load_locations",
            "Data Loading",
            _description_for("load_locations"),
            inputs=(
                CommandInputSpec(
                    **{
                        **csv_input.__dict__,
                        "filename_pattern": "LOCATIONS.csv",
                        "placeholder_key": "locations_csv",
                        "standardized_name": "LOCATIONS.csv",
                        "help": "Location hierarchy/facility file.",
                    }
                ),
            ),
            safety="mutating",
        ),
        CommandRegistryItem(
            "load_srs_cluster_locations",
            "load_srs_cluster_locations",
            "Data Loading",
            _description_for("load_srs_cluster_locations"),
            inputs=(
                CommandInputSpec(
                    **{
                        **csv_input.__dict__,
                        "filename_pattern": "cluster_map.csv",
                        "placeholder_key": "cluster_locations_csv",
                        "standardized_name": "cluster_map.csv",
                        "allowed_pattern": r"^cluster_map\.csv$",
                        "help": "Cluster map hierarchy file (province->district->constituency->ward->ea).",
                    }
                ),
            ),
            safety="mutating",
        ),
        CommandRegistryItem(
            "load_dhis_cod_codes",
            "load_dhis_cod_codes",
            "Data Loading",
            _description_for("load_dhis_cod_codes"),
            inputs=(
                CommandInputSpec(
                    **{
                        **csv_input.__dict__,
                        "filename_pattern": "DHIS_COD_CODES.csv",
                        "placeholder_key": "dhis_cod_codes_csv",
                        "standardized_name": "DHIS_COD_CODES.csv",
                        "help": "DHIS COD code mapping file.",
                    }
                ),
            ),
            safety="mutating",
        ),
        CommandRegistryItem(
            "load_csa_tracker",
            "load_csa_tracker",
            "Data Loading",
            _description_for("load_csa_tracker"),
            inputs=(
                CommandInputSpec(
                    **{
                        **csv_input.__dict__,
                        "filename_pattern": "CSA_DAILY_TRACKER or CSA_DAILY_TRACKER.csv",
                        "standardized_name": "CSA_DAILY_TRACKER",
                        "allowed_pattern": r"^(CSA_DAILY_TRACKER|CSA_DAILY_TRACKER\.csv)$",
                        "help": "CSA daily tracker feed for operations metrics.",
                    }
                ),
            ),
            safety="mutating",
        ),
        CommandRegistryItem(
            key="load_form_csv",
            management_command="load_form_csv",
            category="Data Loading",
            description=_description_for("load_form_csv"),
            inputs=(
                CommandInputSpec(
                    type="choice",
                    name="form_name",
                    required=True,
                    help="Target form name.",
                    choices=(
                        "household",
                        "pregnancy",
                        "pregnancyoutcome",
                        "death",
                        "verbalautopsy",
                    ),
                ),
                csv_input,
            ),
            safety="mutating",
        ),
        CommandRegistryItem(
            "load_odk_definitions",
            "load_odk_definitions",
            "Data Loading",
            _description_for("load_odk_definitions"),
            inputs=(
                CommandInputSpec(
                    type="bool",
                    name="dry_run",
                    required=False,
                    help="Parse definitions and report only.",
                    flag="--dry-run",
                ),
            ),
            safety="mutating",
        ),
        CommandRegistryItem(
            "dq_households",
            "dq_households",
            "Data Quality",
            _description_for("dq_households"),
            safety="read_only",
        ),
        CommandRegistryItem(
            "mark_vas_as_duplicate",
            "mark_vas_as_duplicate",
            "Data Quality",
            _description_for("mark_vas_as_duplicate"),
            safety="mutating",
        ),
        CommandRegistryItem(
            "run_coding_algorithms",
            "run_coding_algorithms",
            "Analytics",
            _description_for("run_coding_algorithms"),
            inputs=(
                CommandInputSpec(
                    type="bool",
                    name="overwrite",
                    flag="--overwrite",
                    help="Overwrite existing coding.",
                ),
            ),
            safety="mutating",
        ),
        CommandRegistryItem(
            "assign_death_ids",
            "assign_death_ids",
            "Maintenance",
            _description_for("assign_death_ids"),
            inputs=(
                CommandInputSpec(
                    type="bool",
                    name="dry_run",
                    required=False,
                    help="Preview generated death IDs only.",
                    flag="--dry-run",
                ),
            ),
            safety="mutating",
        ),
        CommandRegistryItem(
            "generate_ward_codes",
            "generate_ward_codes",
            "Maintenance",
            _description_for("generate_ward_codes"),
            safety="mutating",
        ),
        CommandRegistryItem(
            "refresh_locations",
            "refresh_locations",
            "Maintenance",
            _description_for("refresh_locations"),
            safety="mutating",
        ),
        CommandRegistryItem(
            "update_va_locations",
            "update_va_locations",
            "Maintenance",
            _description_for("update_va_locations"),
            inputs=(
                csv_input,
                CommandInputSpec(
                    type="bool",
                    name="dry_run",
                    required=False,
                    help="Preview updates without saving.",
                    flag="--dry-run",
                ),
            ),
            safety="mutating",
        ),
        CommandRegistryItem(
            "fake_current_va_dates",
            "fake_current_va_dates",
            "Maintenance",
            _description_for("fake_current_va_dates"),
            safety="mutating",
            dangerous=True,
        ),
        CommandRegistryItem(
            "randomize_va_dates",
            "randomize_va_dates",
            "Maintenance",
            _description_for("randomize_va_dates"),
            safety="mutating",
            dangerous=True,
        ),
        CommandRegistryItem(
            "purge_admin_panel_alerts",
            "purge_admin_panel_alerts",
            "Maintenance",
            _description_for("purge_admin_panel_alerts"),
            inputs=(
                CommandInputSpec(
                    type="int",
                    name="days",
                    required=False,
                    help="Delete alerts older than N days (default: 30).",
                    flag="--days",
                    default="30",
                ),
                CommandInputSpec(
                    type="bool",
                    name="dry_run",
                    required=False,
                    help="Preview delete count without deleting.",
                    flag="--dry-run",
                ),
            ),
            safety="mutating",
        ),
        CommandRegistryItem(
            "dummify_and_dump",
            "dummify_and_dump",
            "Backups/Exports",
            _description_for("dummify_and_dump"),
            safety="mutating",
            dangerous=True,
        ),
        CommandRegistryItem(
            "backup_admin_panel_alerts",
            "backup_admin_panel_alerts",
            "Backups/Exports",
            _description_for("backup_admin_panel_alerts"),
            inputs=(
                CommandInputSpec(
                    type="text",
                    name="output_dir",
                    required=True,
                    help="Backup destination directory (external disk path supported).",
                    flag="--output-dir",
                ),
                CommandInputSpec(
                    type="text",
                    name="prefix",
                    required=False,
                    help="Optional backup filename prefix.",
                    flag="--prefix",
                    default="admin_panel_alerts",
                ),
            ),
            safety="read_only",
        ),
        CommandRegistryItem(
            "export_locations",
            "export_locations",
            "Backups/Exports",
            _description_for("export_locations"),
            safety="read_only",
        ),
        CommandRegistryItem(
            "run_dhis",
            "run_dhis",
            "Integrations",
            _description_for("run_dhis"),
            safety="mutating",
        ),
        CommandRegistryItem(
            "import_from_kobo",
            "import_from_kobo",
            "Integrations",
            _description_for("import_from_kobo"),
            safety="mutating",
        ),
        CommandRegistryItem(
            "import_from_odk",
            "import_from_odk",
            "Integrations",
            _description_for("import_from_odk"),
            inputs=(
                CommandInputSpec(
                    type="text",
                    name="form_id",
                    required=False,
                    help="Optional ODK xmlFormId to pull; may be supplied multiple times through repeated runs.",
                    flag="--form-id",
                ),
                CommandInputSpec(
                    type="bool",
                    name="dry_run",
                    required=False,
                    help="Fetch data without writing to DB.",
                    flag="--dry-run",
                ),
                CommandInputSpec(
                    type="bool",
                    name="full_refresh",
                    required=False,
                    help="Ignore saved watermark state and refetch all submissions.",
                    flag="--full-refresh",
                ),
            ),
            safety="mutating",
        ),
    ]


REGISTRY_ITEMS: tuple[CommandRegistryItem, ...] = tuple(_command_specs())
ALLOWLIST: dict[str, CommandRegistryItem] = {item.key: item for item in REGISTRY_ITEMS}


def grouped_command_specs() -> dict[str, list[CommandRegistryItem]]:
    groups: dict[str, list[CommandRegistryItem]] = {}
    for item in REGISTRY_ITEMS:
        groups.setdefault(item.category, []).append(item)

    ordered: dict[str, list[CommandRegistryItem]] = {}
    for category in CATEGORY_ORDER:
        if category == "Favorites":
            favorites = [item for item in REGISTRY_ITEMS if item.favorite]
            if favorites:
                ordered["Favorites"] = sorted(favorites, key=lambda value: value.key)
            continue
        if category in groups:
            ordered[category] = sorted(groups[category], key=lambda value: value.key)

    for category in sorted(groups.keys()):
        if category not in ordered:
            ordered[category] = sorted(groups[category], key=lambda value: value.key)
    return ordered
