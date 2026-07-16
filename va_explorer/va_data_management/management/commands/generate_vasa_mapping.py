"""Generate the version-controlled VASA field-mapping artifacts from the XLSForm.

Reads the VASA survey sheet (``znphifacilityvasa_02_final.xlsx``) and the WHO VA
survey sheet (for the coverage report), applies the explicit rule table in
:mod:`va_explorer.va_data_management.vasa.variant_rules`, and writes two
generated modules:

* ``vasa/field_map.py``    — pseudo-name -> WHO-field map + section/repeat indexes
* ``vasa/model_fields.py`` — typed field defs for VASASocialAutopsy + repeats

Usage::

    python manage.py generate_vasa_mapping            # regenerate + report
    python manage.py generate_vasa_mapping --check     # verify, do not write
"""

import hashlib
import json
import re
from pathlib import Path

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from va_explorer.va_data_management.models import VerbalAutopsy
from va_explorer.va_data_management.vasa import variant_rules

VASA_PACKAGE_DIR = Path(__file__).resolve().parents[2] / "vasa"
DEFAULT_DATA_DIR = Path(settings.APPS_DIR) / "static" / "data"
DEFAULT_XLSFORM = DEFAULT_DATA_DIR / "znphifacilityvasa_02_final.xlsx"
DEFAULT_WHO_XLSFORM = DEFAULT_DATA_DIR / "znphi_va_who_v1_5_2_7.xlsx"

VASA_FORM_ID = "znphi_vasa_who_2016"

# Structural / reserved names a generated model field must not collide with.
_RESERVED_FIELD_NAMES = frozenset(
    {
        "id",
        "pk",
        "vasa",
        "key",
        "parent_key",
        "created",
        "updated",
        "deleted_at",
        "history",
        "objects",
        "all_objects",
        "instrument",
        "source",
    }
)

_GROUP_MARKERS = ("begin group", "begin_group", "end group", "end_group")
_REPEAT_BEGIN = ("begin repeat", "begin_repeat")
_REPEAT_END = ("end repeat", "end_repeat")


def _base_type(raw_type: str) -> str:
    raw = str(raw_type or "").strip()
    return raw.split()[0].lower() if raw else ""


def _instrument_section(name: str) -> str:
    if name.startswith("N2"):
        return "neonate"
    if name.startswith("C3"):
        return "child"
    if name.startswith("A4"):
        return "adult"
    if name.startswith("Q1"):
        return "common"
    return "admin"


def _sanitize_field_name(column: str, used: set) -> str:
    """Return a unique, valid Django field name for a VASA pseudo-name column."""
    candidate = re.sub(r"[^0-9A-Za-z_]", "_", column)
    if not candidate or candidate[0].isdigit():
        candidate = f"f_{candidate}"
    if candidate.lower() in _RESERVED_FIELD_NAMES:
        candidate = f"{candidate}_field"
    base = candidate
    counter = 2
    while candidate in used:
        candidate = f"{base}_{counter}"
        counter += 1
    used.add(candidate)
    return candidate


class Command(BaseCommand):
    help = "Generate (or --check) the VASA field-mapping artifacts from the XLSForm."

    def add_arguments(self, parser):
        parser.add_argument("--xlsform", default=str(DEFAULT_XLSFORM))
        parser.add_argument("--who-xlsform", default=str(DEFAULT_WHO_XLSFORM))
        parser.add_argument(
            "--check",
            action="store_true",
            help="Verify committed artifacts match the XLSForm; write nothing.",
        )
        parser.add_argument("--quiet", action="store_true")

    def handle(self, *args, **options):
        artifacts = self.build_artifacts(
            Path(options["xlsform"]), Path(options["who_xlsform"])
        )

        if not options["quiet"]:
            self.stdout.write(artifacts["report"])

        if options["check"]:
            self._check(artifacts)
        else:
            self._write(artifacts)
            if not options["quiet"]:
                self.stdout.write(self.style.SUCCESS("Wrote VASA mapping artifacts."))

    # ------------------------------------------------------------------ build
    def build_artifacts(self, xlsform: Path, who_xlsform: Path) -> dict:
        if not xlsform.exists():
            raise CommandError(f"VASA XLSForm not found: {xlsform}")
        survey = pd.read_excel(xlsform, sheet_name="survey", dtype=str).fillna("")
        cols = {str(c).strip().lower(): c for c in survey.columns}
        type_col, name_col, who_col = cols["type"], cols["name"], cols["who_name"]
        label_col = cols.get("label::english(en)")

        va_lookup = {f.name.lower(): f.name for f in VerbalAutopsy._meta.fields}

        pseudo_to_who: dict = {}
        combined_who: dict = {}
        instrument_section: dict = {}
        repeat_groups: dict = {}
        sa_defs: list = []
        repeat_field_defs: dict = {}
        admin_meta: list = []
        missing_from_model: list = []
        unresolved: list = []
        covered_who: set = set()
        used_names: set = set()
        repeat_used_names: dict = {}

        current_repeat = None
        for _, row in survey.iterrows():
            raw_type = str(row[type_col]).strip()
            low_type = raw_type.lower()
            name = str(row[name_col]).strip()
            who = str(row[who_col]).strip()
            label = str(row[label_col]).strip() if label_col else ""
            base_type = _base_type(raw_type)

            if low_type.startswith(_REPEAT_BEGIN):
                current_repeat = name
                repeat_groups.setdefault(current_repeat, [])
                repeat_field_defs.setdefault(current_repeat, [])
                repeat_used_names.setdefault(current_repeat, set())
                continue
            if low_type.startswith(_REPEAT_END):
                current_repeat = None
                continue
            if low_type.startswith(_GROUP_MARKERS) or base_type == "note" or not name:
                continue

            # Repeat-group contents are captured as social-autopsy repeat data.
            if current_repeat is not None:
                repeat_groups[current_repeat].append(name)
                field_name = _sanitize_field_name(
                    name, repeat_used_names[current_repeat]
                )
                repeat_field_defs[current_repeat].append(
                    [name, field_name, label or name]
                )
                continue

            resolution = variant_rules.classify_field(
                name=name,
                base_type=base_type,
                who_name=who,
                va_field_lookup=va_lookup,
            )
            instrument_section[name] = _instrument_section(name)

            # Track WHO-Id coverage for the report (exact + combined components).
            for token in _who_ids_in(who):
                covered_who.add(token)

            if resolution.classification == "va":
                pseudo_to_who[name] = resolution.targets[0]
                if resolution.kind == "combined":
                    combined_who[name] = resolution.targets
            elif resolution.classification == "admin_meta":
                admin_meta.append(name)
            else:  # sa
                field_name = _sanitize_field_name(name, used_names)
                sa_defs.append([name, field_name, label or name])
                if who:
                    unresolved.append([name, who, resolution.kind])
                if resolution.kind == "exact_missing_from_model":
                    missing_from_model.append(who)

        who_form_ids = _who_form_ids(who_xlsform)
        not_covered = sorted(who_form_ids - covered_who)
        in_vasa_not_who = sorted(covered_who - who_form_ids)

        value_crosswalk, fallback_codes, select_types = _build_value_crosswalk(
            xlsform, who_xlsform, pseudo_to_who
        )
        sa_value_labels = _build_sa_value_labels(xlsform, sa_defs, repeat_field_defs)
        vasa_sha, who_sha = _sha256(xlsform), _sha256(who_xlsform)

        field_map = {
            "PSEUDO_TO_WHO": pseudo_to_who,
            "COMBINED_WHO": combined_who,
            "INSTRUMENT_SECTION": instrument_section,
            "REPEAT_GROUPS": repeat_groups,
            "VALUE_CROSSWALK": value_crosswalk,
            "SELECT_TYPES": select_types,
            "FALLBACK_CODES": fallback_codes,
            "XLSFORM_HASHES": {"vasa": vasa_sha, "who": who_sha},
            "CROSSWALK_VERSION": f"{vasa_sha[:8]}-{who_sha[:8]}",
        }
        model_fields = {
            "SA_FIELD_DEFS": sa_defs,
            "REPEAT_FIELD_DEFS": repeat_field_defs,
            "SA_VALUE_LABELS": sa_value_labels,
        }
        report = self._format_report(
            pseudo_to_who=pseudo_to_who,
            combined_who=combined_who,
            covered_who=covered_who,
            sa_defs=sa_defs,
            admin_meta=admin_meta,
            repeat_groups=repeat_groups,
            missing_from_model=sorted(set(missing_from_model)),
            not_covered=not_covered,
            in_vasa_not_who=in_vasa_not_who,
            unresolved=unresolved,
            value_crosswalk=value_crosswalk,
            fallback_codes=fallback_codes,
            sa_value_labels=sa_value_labels,
            crosswalk_version=field_map["CROSSWALK_VERSION"],
        )
        return {
            "field_map": field_map,
            "model_fields": model_fields,
            "report": report,
        }

    # ------------------------------------------------------------------ write
    def _write(self, artifacts: dict) -> None:
        (VASA_PACKAGE_DIR / "field_map.py").write_text(
            _render_module(
                "field_map",
                "pseudo-name -> WHO-field map, instrument sections, repeat groups",
                artifacts["field_map"],
                extra={"VASA_FORM_ID": VASA_FORM_ID},
            )
        )
        (VASA_PACKAGE_DIR / "model_fields.py").write_text(
            _render_module(
                "model_fields",
                "typed field definitions for VASASocialAutopsy + repeat models",
                artifacts["model_fields"],
            )
        )

    def _check(self, artifacts: dict) -> None:
        from va_explorer.va_data_management.vasa import field_map, model_fields

        mismatches = []
        for key, expected in artifacts["field_map"].items():
            if getattr(field_map, key, None) != expected:
                mismatches.append(f"field_map.{key}")
        for key, expected in artifacts["model_fields"].items():
            actual = getattr(model_fields, key, None)
            if _as_lists(actual) != _as_lists(expected):
                mismatches.append(f"model_fields.{key}")
        if mismatches:
            raise CommandError(
                "VASA mapping artifacts are stale; run 'generate_vasa_mapping'. "
                f"Mismatched: {', '.join(mismatches)}"
            )
        self.stdout.write(self.style.SUCCESS("VASA mapping artifacts are up to date."))

    # ----------------------------------------------------------------- report
    def _format_report(self, **kw) -> str:
        lines = [
            "=== VASA mapping validation report ===",
            f"VA fields mapped (pseudo -> WHO):   {len(kw['pseudo_to_who'])}",
            f"Distinct WHO Ids covered by VASA:   {len(kw['covered_who'])}",
            f"Combined WHO_names (split):         {len(kw['combined_who'])} "
            f"{list(kw['combined_who'].items())}",
            f"Social-autopsy input questions:     {len(kw['sa_defs'])}",
            f"Admin/meta (machinery, not stored): {len(kw['admin_meta'])}",
            f"Repeat groups:                      {len(kw['repeat_groups'])} "
            f"{ {k: len(v) for k, v in kw['repeat_groups'].items()} }",
            f"WHO_names missing from VA model:    {kw['missing_from_model']}",
            f"WHO Ids in VASA but not WHO form:   {kw['in_vasa_not_who']}",
            f"WHO Ids in WHO form not in VASA:    {len(kw['not_covered'])}",
            f"Unresolved WHO_names -> SA:         {len(kw['unresolved'])}",
            f"Value-crosswalked select fields:    {len(kw['value_crosswalk'])} "
            f"({sum(len(c) for c in kw['fallback_codes'].values())} fallback codes "
            f"on {len(kw['fallback_codes'])} fields)",
            f"SA/repeat display-label fields:     {len(kw['sa_value_labels'])}",
            f"Crosswalk version (XLSForm hashes): {kw['crosswalk_version']}",
        ]
        return "\n".join(lines)


def _who_ids_in(who_name: str) -> list:
    who_name = (who_name or "").strip()
    if re.match(r"^Id\d+$", who_name):
        return [who_name]
    combined = re.match(r"^Id(\d+)/(\d+)$", who_name)
    if combined:
        prefix, num = combined.group(1), combined.group(2)
        right = "Id" + (prefix[: -len(num)] + num if len(num) < len(prefix) else num)
        return [f"Id{prefix}", right]
    return []


def _norm_label(text: str) -> str:
    """Normalise a choice label for cross-form matching (case/punct/alias)."""
    norm = str(text or "").lower().replace("'", "")
    norm = re.sub(r"[^a-z0-9]+", " ", norm).strip()
    # Reconcile common wording differences between the two forms' labels.
    norm = norm.replace("doesnt", "dont")  # "doesn't know" vs "don't know"
    norm = norm.replace("ambigous", "ambiguous")  # VASA typo vs WHO spelling
    return norm


def _choice_maps(xlsform: Path):
    """Return (field -> list_name, list_name -> {code: label},
    field -> select_one|select_multiple) for an XLSForm."""
    xl = pd.ExcelFile(xlsform)
    survey = xl.parse("survey", dtype=str).fillna("")
    choices = xl.parse("choices", dtype=str).fillna("")
    sc = {str(c).strip().lower(): c for c in survey.columns}
    cc = {str(c).strip().lower(): c for c in choices.columns}
    label_col = next((cc[k] for k in cc if k.startswith("label")), None)

    field_list: dict = {}
    field_kind: dict = {}
    for _, row in survey.iterrows():
        raw_type = str(row[sc["type"]]).strip()
        parts = raw_type.split()
        name = str(row[sc["name"]]).strip()
        if len(parts) >= 2 and parts[0].startswith("select"):
            field_list[name] = parts[1]
            field_kind[name] = (
                "select_multiple"
                if parts[0].lower().startswith("select_multiple")
                else "select_one"
            )

    list_choices: dict = {}
    for _, row in choices.iterrows():
        list_name = str(row[cc["list_name"]]).strip()
        code = str(row[cc["name"]]).strip()
        label = str(row[label_col]).strip() if label_col else ""
        if list_name:
            list_choices.setdefault(list_name, {})[code] = label
    return field_list, list_choices, field_kind


def _build_value_crosswalk(
    xlsform: Path, who_xlsform: Path, pseudo_to_who: dict
) -> tuple:
    """Map each VASA select code to the WHO value the coding pipeline expects.

    VASA select choices are numeric codes with human labels (1 -> "Yes"); the VA
    value-space is WHO choice *names* ("yes"). For every VASA select field mapped
    to a WHO field, match the VASA label to the WHO field's choice label and emit
    ``{code: who_value}``. Where no WHO label matches, fall back to the
    normalised VASA label so nothing is silently lost; those codes are recorded
    in ``fallback_codes`` so the loader can surface them (coding may not
    recognise a fallback value).

    Returns ``(crosswalk, fallback_codes, select_types)`` where ``select_types``
    marks every crosswalked field ``select_one`` or ``select_multiple``.
    """
    vasa_field_list, vasa_list_choices, vasa_field_kind = _choice_maps(xlsform)
    who_field_list, who_list_choices, _ = _choice_maps(who_xlsform)

    who_value_by_label: dict = {}
    for who_field, list_name in who_field_list.items():
        who_value_by_label[who_field] = {
            _norm_label(label): code
            for code, label in who_list_choices.get(list_name, {}).items()
        }

    crosswalk: dict = {}
    fallback_codes: dict = {}
    for pseudo, who in pseudo_to_who.items():
        list_name = vasa_field_list.get(pseudo)
        if not list_name:
            continue
        codes = vasa_list_choices.get(list_name, {})
        who_values = who_value_by_label.get(who, {})
        mapped = {}
        fallbacks = []
        for code, vasa_label in codes.items():
            who_value = who_values.get(_norm_label(vasa_label))
            if who_value:
                mapped[code] = who_value
            else:
                mapped[code] = _norm_label(vasa_label).replace(" ", "_")
                fallbacks.append(code)
        if mapped:
            crosswalk[pseudo] = mapped
            if fallbacks:
                fallback_codes[pseudo] = sorted(fallbacks)
    select_types = {pseudo: vasa_field_kind[pseudo] for pseudo in crosswalk}
    return crosswalk, fallback_codes, select_types


def _build_sa_value_labels(
    xlsform: Path, sa_defs: list, repeat_field_defs: dict
) -> dict:
    """Display-only choice labels for social-autopsy / repeat select columns.

    Keyed by generated model field name. Stored SA/repeat values remain the raw
    codes; the UI decodes them at render time only (never the coding pipeline).
    """
    field_list, list_choices, field_kind = _choice_maps(xlsform)
    defs = list(sa_defs)
    for group_defs in repeat_field_defs.values():
        defs.extend(group_defs)
    value_labels: dict = {}
    for column, field_name, _label in defs:
        list_name = field_list.get(column)
        choices = list_choices.get(list_name, {}) if list_name else {}
        if choices:
            value_labels[field_name] = {
                "kind": field_kind[column],
                "labels": choices,
            }
    return value_labels


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _who_form_ids(who_xlsform: Path) -> set:
    if not who_xlsform.exists():
        return set()
    survey = pd.read_excel(who_xlsform, sheet_name="survey", dtype=str).fillna("")
    cols = {str(c).strip().lower(): c for c in survey.columns}
    name_col = cols["name"]
    return {
        str(n).strip() for n in survey[name_col] if re.match(r"^Id\d+$", str(n).strip())
    }


def _as_lists(obj):
    """Normalise tuples->lists so committed (JSON) and computed data compare equal."""
    if isinstance(obj, (list, tuple)):
        return [_as_lists(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _as_lists(v) for k, v in obj.items()}
    return obj


def _render_module(
    name: str, summary: str, data: dict, extra: dict | None = None
) -> str:
    header = (
        f'"""AUTO-GENERATED by `manage.py generate_vasa_mapping` — DO NOT EDIT.\n\n'
        f"{summary}\n\n"
        f"Regenerate after any VASA XLSForm revision; the generator is idempotent\n"
        f'and `--check` fails if this file drifts from the form."""\n\n'
    )
    body = []
    for key, value in (extra or {}).items():
        body.append(f"{key} = {json.dumps(value)}\n")
    for key, value in data.items():
        body.append(f"{key} = {json.dumps(value, indent=4, sort_keys=True)}\n")
    return header + "\n".join(body)
