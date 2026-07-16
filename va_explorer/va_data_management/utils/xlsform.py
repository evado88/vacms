"""Shared XLSForm parsing helpers.

Used by the artifact generators (``generate_vasa_mapping``,
``generate_linkage_crosswalk``) so both read survey/choices sheets, normalise
choice labels and render generated modules identically.
"""

import hashlib
import json
import re
from pathlib import Path

import pandas as pd

GROUP_MARKERS = ("begin group", "begin_group", "end group", "end_group")
REPEAT_BEGIN = ("begin repeat", "begin_repeat")
REPEAT_END = ("end repeat", "end_repeat")


def base_type(raw_type: str) -> str:
    raw = str(raw_type or "").strip()
    return raw.split()[0].lower() if raw else ""


def select_list_name(raw_type: str):
    """Return the choice-list name of a select field, or None.

    Tolerates the nonstandard ``select one <list>`` (space) spelling that
    appears in the E_DEATH form alongside the usual ``select_one <list>``.
    """
    parts = str(raw_type or "").strip().split()
    if not parts:
        return None
    head = parts[0].lower()
    if head in {"select_one", "select_multiple"} and len(parts) >= 2:
        return parts[1]
    if head == "select" and len(parts) >= 3 and parts[1].lower() in {"one", "multiple"}:
        return parts[2]
    return None


def find_label_col(columns, prefix="label"):
    """Return the first English label column (header spelling varies per form)."""
    lowered = {str(c).strip().lower(): c for c in columns}
    for key, col in lowered.items():
        if key.startswith(f"{prefix}::english"):
            return col
    for key, col in lowered.items():
        if key.startswith(prefix):
            return col
    return None


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def norm_label(text: str) -> str:
    """Normalise a choice label for cross-form matching (case/punct/alias)."""
    norm = str(text or "").lower().replace("'", "")
    norm = re.sub(r"[^a-z0-9]+", " ", norm).strip()
    # Reconcile common wording differences between the forms' labels.
    norm = norm.replace("doesnt", "dont")  # "doesn't know" vs "don't know"
    norm = norm.replace("ambigous", "ambiguous")  # VASA typo vs WHO spelling
    return norm


def choice_maps(xlsform: Path):
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


def survey_fields(xlsform: Path) -> list:
    """Parse a survey sheet into data-field dicts, skipping structure rows.

    Each dict: ``name``, ``type`` (raw), ``base_type``, ``list_name`` (selects),
    ``label`` (English), ``who_name`` (empty unless the form has the column),
    ``in_repeat`` (bool). ``note``/group rows are skipped; repeat contents are
    kept but flagged so callers can exclude them.
    """
    survey = pd.read_excel(xlsform, sheet_name="survey", dtype=str).fillna("")
    cols = {str(c).strip().lower(): c for c in survey.columns}
    label_col = find_label_col(survey.columns)
    who_col = cols.get("who_name")

    fields = []
    repeat_depth = 0
    for _, row in survey.iterrows():
        raw_type = str(row[cols["type"]]).strip()
        low_type = raw_type.lower()
        name = str(row[cols["name"]]).strip()
        if low_type.startswith(REPEAT_BEGIN):
            repeat_depth += 1
            continue
        if low_type.startswith(REPEAT_END):
            repeat_depth = max(0, repeat_depth - 1)
            continue
        bt = base_type(raw_type)
        if low_type.startswith(GROUP_MARKERS) or bt == "note" or not name:
            continue
        fields.append(
            {
                "name": name,
                "type": raw_type,
                "base_type": bt,
                "list_name": select_list_name(raw_type),
                "label": str(row[label_col]).strip() if label_col else "",
                "who_name": str(row[who_col]).strip() if who_col else "",
                "in_repeat": repeat_depth > 0,
            }
        )
    return fields


def as_lists(obj):
    """Normalise tuples->lists so committed (JSON) and computed data compare equal."""
    if isinstance(obj, (list, tuple)):
        return [as_lists(x) for x in obj]
    if isinstance(obj, dict):
        return {k: as_lists(v) for k, v in obj.items()}
    return obj


def render_module(
    name: str, summary: str, data: dict, extra: dict | None = None, *, command: str
) -> str:
    header = (
        f'"""AUTO-GENERATED by `manage.py {command}` — DO NOT EDIT.\n\n'
        f"{summary}\n\n"
        f"Regenerate after any XLSForm revision; the generator is idempotent\n"
        f'and `--check` fails if this file drifts from the form."""\n\n'
    )
    body = []
    for key, value in (extra or {}).items():
        body.append(f"{key} = {json.dumps(value)}\n")
    for key, value in data.items():
        body.append(f"{key} = {json.dumps(value, indent=4, sort_keys=True)}\n")
    return header + "\n".join(body)
