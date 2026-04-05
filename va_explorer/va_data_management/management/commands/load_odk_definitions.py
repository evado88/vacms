import argparse
import os
import re
from io import BytesIO

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from va_explorer.va_data_management.models import ODKFormChoice
from va_explorer.va_data_management.odk.config import resolve_odk_form_configs
from va_explorer.va_data_management.utils.loading import normalize_string, normalize_value
from va_explorer.va_data_management.utils.odk import make_pyodk_client

FORM_ID_MAP = {
    "BASELINE_CENSUS": "household",
    "E_PREGNANCY": "pregnancy",
    "E_PREGNANCY_OUTCOME": "pregnancy-outcome",
    "E_DEATH": "death",
    "CSA_DAILY_TRACKER": "csa_daily_tracker",
}

PRESET_FILES = [
    "va_explorer/static/data/HHC_BASELINE_CENSUS_ROSTER_Form.xlsx",
    "va_explorer/static/data/E_PREGNANCY.xlsx",
    "va_explorer/static/data/E_PREGNANCY_OUTCOME.xlsx",
    "va_explorer/static/data/E_DEATH.xlsx",
]


class Command(BaseCommand):
    """Load preset ODK XLSForm definitions (all-or-nothing, transactional)."""

    help = (
        "Load ODK form definitions with normalization (case preserved). "
        "Processes all preset forms sequentially inside a single transaction. "
        "If any file fails, the entire load is rolled back."
    )

    # ---------------------------
    # CLI
    # ---------------------------
    def add_arguments(self, parser: argparse.ArgumentParser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and report without writing to the database.",
        )
        parser.add_argument(
            "--only",
            type=str,
            help="Only import choices for this survey field name (e.g., HH_33).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Delete existing choices for each form before loading.",
        )
        parser.add_argument(
            "--no-verbose",
            action="store_true",
            help="Disable verbose logging (verbose is ON by default).",
        )
        # ODK Central live fetch options
        parser.add_argument(
            "--from-odk",
            action="store_true",
            help=(
                "Fetch the XLSForm definition directly from ODK Central instead of "
                "reading local preset files. Requires --form-id."
            ),
        )
        parser.add_argument(
            "--form-id",
            type=str,
            default=None,
            help="ODK xmlFormId to fetch when using --from-odk.",
        )
        parser.add_argument(
            "--project-id",
            type=int,
            default=None,
            help="ODK project ID (defaults to ODK_DEFAULT_PROJECT_ID setting).",
        )
        parser.add_argument(
            "--form-name",
            type=str,
            default=None,
            help=(
                "Explicit form name (e.g. csa_daily_tracker) when using --from-odk. "
                "If omitted the form is looked up from ODK_PULL_FORMS by form_id."
            ),
        )

    # ---------------------------
    # Helpers
    # ---------------------------
    @staticmethod
    def _canon_header(s: str) -> str:
        return str(s or "").strip().lower().replace(" ", "_")

    def _find_column(self, df: pd.DataFrame, target: str):
        """Find a column by canonicalized name (e.g., 'list_name' matches 'list name')."""
        target = self._canon_header(target)
        for col in df.columns:
            if self._canon_header(col) == target:
                return col
        return None

    def _derive_form_name(self, settings_df: pd.DataFrame, path_for_error: str) -> str:
        """Return normalized form_name via FORM_ID_MAP or raise CommandError."""
        form_id_col = None
        for col in settings_df.columns:
            if self._canon_header(col) == "form_id":
                form_id_col = col
                break
        if not form_id_col:
            raise CommandError(f"{path_for_error}: Could not find 'form_id' column in 'settings' sheet.")

        form_id = (settings_df.iloc[0][form_id_col] or "").strip()
        if not form_id:
            raise CommandError(f"{path_for_error}: Empty 'form_id' in 'settings' sheet.")

        form_name = FORM_ID_MAP.get(form_id.upper())
        if not form_name:
            raise CommandError(f"{path_for_error}: Form ID '{form_id}' is not recognized or mapped.")
        return normalize_string(form_name)

    # ---------------------------
    # Main
    # ---------------------------
    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        only_field = options.get("only")
        force = options.get("force", False)
        verbose = not options.get("no_verbose", False)  # verbose enabled by default
        from_odk = options.get("from_odk", False)

        if from_odk:
            self._handle_from_odk(
                form_id=options.get("form_id"),
                project_id=options.get("project_id"),
                form_name=options.get("form_name"),
                dry_run=dry_run,
                only_field=only_field,
                force=force,
                verbose=verbose,
            )
            return

        # ---- Local preset files path ----
        # Preflight: ensure all preset files exist before doing anything
        missing = [p for p in PRESET_FILES if not os.path.isfile(p)]
        if missing:
            missing_list = "\n  - ".join(missing)
            raise CommandError(f"The following preset files were not found:\n  - {missing_list}")

        # ---- DRY RUN path: parse & report only, no DB writes ----
        if dry_run:
            total_created = 0
            for path in PRESET_FILES:
                self.stdout.write(f"\n[INFO] [DRY-RUN] Processing file: {path}")
                created = self._process_single_file(
                    path, verbose=verbose, dry_run=True, only_field=only_field, force=False
                )
                total_created += created
            self.stdout.write(f"\n[SUMMARY] [DRY-RUN] Total choices that would be created/updated: {total_created}")
            return

        # ---- REAL LOAD path: single transaction, all-or-nothing ----
        try:
            with transaction.atomic():
                total_created = 0
                for path in PRESET_FILES:
                    self.stdout.write(f"\n[INFO] Processing file: {path}")
                    created = self._process_single_file(
                        path, verbose=verbose, dry_run=False, only_field=only_field, force=force
                    )
                    total_created += created
                self.stdout.write(
                    f"\n[SUCCESS] Loaded {total_created} choices across {len(PRESET_FILES)} forms (transaction committed)."
                )
        except CommandError as ce:
            # Known/expected validation failure: show friendly error and confirm rollback
            self.stderr.write(f"\n[ERROR] {ce}")
            self.stderr.write("[INFO] All changes have been rolled back; database remains unchanged.")
            raise
        except Exception as ex:
            # Unexpected failure: include path if available from message
            self.stderr.write(f"\n[ERROR] Unexpected failure: {ex}")
            self.stderr.write("[INFO] All changes have been rolled back; database remains unchanged.")
            raise CommandError("Aborted due to unexpected error; see logs above for details.") from ex

    def _handle_from_odk(self, *, form_id, project_id, form_name, dry_run, only_field, force, verbose):
        """Fetch an XLSForm definition from ODK Central and load its choices."""
        if not form_id:
            raise CommandError("--form-id is required when using --from-odk.")

        # Resolve form_name: explicit arg > ODK_PULL_FORMS lookup
        if not form_name:
            configs = resolve_odk_form_configs(form_ids=[form_id], project_id=project_id)
            form_name = configs[0].get("form_name") if configs else None
        if not form_name:
            raise CommandError(
                f"Could not determine form_name for '{form_id}'. "
                "Pass --form-name explicitly or add the form to ODK_PULL_FORMS in settings."
            )

        pid = project_id or getattr(settings, "ODK_DEFAULT_PROJECT_ID", None)
        if not pid:
            raise CommandError("project_id is required (pass --project-id or set ODK_DEFAULT_PROJECT_ID).")

        self.stdout.write(f"\n[INFO] Fetching XLSForm from ODK Central: form_id={form_id}, project_id={pid}")
        try:
            with make_pyodk_client(pid).open() as client:
                resp = client.get(f"projects/{pid}/forms/{form_id}.xlsx")
                resp.raise_for_status()
                content = resp.content
        except Exception as ex:
            raise CommandError(f"Failed to download XLSForm from ODK Central: {ex}") from ex

        self.stdout.write(f"[INFO] Downloaded {len(content):,} bytes; processing form '{form_name}'")
        try:
            with transaction.atomic():
                created = self._process_single_file(
                    content,
                    verbose=verbose,
                    dry_run=dry_run,
                    only_field=only_field,
                    force=force,
                    form_name=form_name,
                )
            prefix = "[DRY-RUN] " if dry_run else ""
            self.stdout.write(f"\n[SUCCESS] {prefix}Loaded {created} choices for form '{form_name}'.")
        except CommandError as ce:
            self.stderr.write(f"\n[ERROR] {ce}")
            self.stderr.write("[INFO] All changes have been rolled back; database remains unchanged.")
            raise
        except Exception as ex:
            self.stderr.write(f"\n[ERROR] Unexpected failure: {ex}")
            self.stderr.write("[INFO] All changes have been rolled back; database remains unchanged.")
            raise CommandError("Aborted due to unexpected error; see logs above for details.") from ex

    # ---------------------------
    # Process a single file (may raise CommandError)
    #
    # source: file path (str) for preset local files, or raw bytes fetched from ODK Central.
    # form_name: when provided (--from-odk path), skip reading the 'settings' sheet.
    # ---------------------------
    def _process_single_file(
        self,
        source,
        *,
        verbose: bool,
        dry_run: bool,
        only_field: str | None,
        force: bool,
        form_name: str | None = None,
    ) -> int:
        # pd.read_excel accepts both file paths and BytesIO. When source is raw bytes,
        # wrap in a fresh BytesIO for each call so the read position is always at 0.
        def _read_sheet(sheet_name):
            file_like = BytesIO(source) if isinstance(source, bytes) else source
            return pd.read_excel(
                file_like,
                sheet_name=sheet_name,
                dtype=str,
                engine="openpyxl",
                keep_default_na=False,
            )

        source_label = f"ODK Central:{form_name}" if isinstance(source, bytes) else source

        # --- Derive form_name from 'settings' sheet when not supplied directly ---
        if form_name is None:
            try:
                form_settings = _read_sheet("settings")
            except Exception as ex:
                raise CommandError(f"{source_label}: Error reading 'settings' sheet: {ex}") from ex
            form_name = self._derive_form_name(form_settings, source_label)

        norm_form_name = normalize_string(form_name)

        # --- Handle existing rows (force or bail) ---
        if force and not dry_run:
            deleted = ODKFormChoice.objects.filter(form_name=norm_form_name).delete()
            if verbose:
                self.stdout.write(f"[INFO] --force: deleted existing rows for form '{norm_form_name}': {deleted}")
        elif ODKFormChoice.objects.filter(form_name=norm_form_name).exists():
            raise CommandError(
                f"{source_label}: The form '{norm_form_name}' is already loaded. Use --force to reload."
            )

        # --- Read survey & choices ---
        try:
            survey = _read_sheet("survey")
            choices = _read_sheet("choices")
        except Exception as ex:
            raise CommandError(f"{source_label}: Error reading 'survey' or 'choices' sheets: {ex}") from ex

        # --- Detect label column (label or label::<lang>) ---
        label_col = next((c for c in choices.columns if str(c).lower().startswith("label")), None)
        if not label_col:
            raise CommandError(f"{source_label}: Could not find a 'label' column in 'choices' sheet.")

        # --- Detect list_name column robustly ---
        list_name_col = self._find_column(choices, "list_name")
        if not list_name_col:
            raise CommandError(
                f"{source_label}: Could not find 'list_name' column in 'choices' sheet. "
                f"Columns: {list(choices.columns)}"
            )

        # Precompute normalized list_name on choices
        choices["_list_name_norm"] = choices[list_name_col].apply(lambda x: normalize_string(x))

        created = 0
        missing_lists = []

        # --- Iterate survey rows, parse select_* and match list names robustly ---
        for _, srow in survey.iterrows():
            qtype = str(srow.get("type", "") or "").strip()
            m = re.match(r"select_(?:one|multiple)(?:_from_file)?\s+(.+)", qtype, flags=re.IGNORECASE)
            if not m:
                continue

            # Take only first token after select_* (ignore 'or_other' and other suffixes)
            list_name_raw = m.group(1).strip().split()[0]

            field = (srow.get("name") or "").strip()
            if not field:
                continue

            if only_field and field != only_field:
                continue

            norm_field = normalize_string(field)
            norm_list = normalize_string(list_name_raw)

            sub = choices[choices["_list_name_norm"] == norm_list]

            if verbose:
                self.stdout.write(
                    f"[DEBUG] form='{norm_form_name}' field='{field}' "
                    f"norm_field='{norm_field}' list_name_raw='{list_name_raw}' "
                    f"norm_list='{norm_list}' matches={len(sub)}"
                )

            if sub.empty:
                missing_lists.append((field, list_name_raw))
                continue

            # Upsert rows (or dry-run)
            for _, crow in sub.iterrows():
                raw_value = crow.get("name")
                if raw_value in (None, ""):
                    continue
                raw_label = crow.get(label_col, "")
                norm_value = normalize_value(raw_value)
                norm_label = str(raw_label).strip() if raw_label is not None else ""

                if dry_run:
                    if verbose:
                        self.stdout.write(
                            f"[DRY-RUN] would upsert: form='{norm_form_name}', field='{norm_field}', "
                            f"value='{norm_value}', label='{norm_label}'"
                        )
                    created += 1
                else:
                    ODKFormChoice.objects.update_or_create(
                        form_name=norm_form_name,
                        field_name=norm_field,
                        value=norm_value,
                        defaults={"label": norm_label},
                    )
                    created += 1

        # --- Diagnostics for missing lists ---
        if missing_lists:
            # Surface detailed info but do not fail hard: replicate previous behavior
            self.stderr.write(f"[WARN] {source_label}: Some select questions had no matching choices list:")
            unique_lists = sorted({ln for _, ln in missing_lists})
            self.stderr.write(
                "       Missing list_names (normalized): " + ", ".join(normalize_string(x) for x in unique_lists)
            )
            for field, ln in missing_lists:
                self.stderr.write(f"       field '{field}' expected list '{ln}'")

        self.stdout.write(
            f"Loaded {created} choices for form '{norm_form_name}' (normalized, case preserved)"
        )
        return created
