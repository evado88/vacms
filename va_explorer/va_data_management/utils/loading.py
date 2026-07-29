import logging
import random
from datetime import datetime

import numpy as np
import pandas as pd
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db.models import Count, Max, Q
from simple_history.utils import bulk_create_with_history, bulk_update_with_history

from va_explorer.users.utils.demo_users import make_field_workers_for_facilities
from va_explorer.va_data_management.models import (
    Location,
    SRSClusterLocation,
    VerbalAutopsy,
)
from va_explorer.va_data_management.odk.normalization import build_odk_rename_map
from va_explorer.va_data_management.utils.date_parsing import parse_date
from va_explorer.va_data_management.utils.geopoint import (
    compose_geopoint,
    find_geopoint_columns,
)
from va_explorer.va_data_management.utils.location_assignment import (
    assign_va_location,
)
from va_explorer.va_data_management.utils.validate import validate_vas_for_dashboard

from ..constants import _checkbox_choices

User = get_user_model()


def normalize_string(s):
    """Strip whitespace, replace hyphens with underscores, remove surrounding quotes."""
    if pd.isnull(s):
        return ""
    s = str(s).strip().replace("-", "_")
    if s.startswith(("'", '"')):
        s = s[1:]
    if s.endswith(("'", '"')):
        s = s[:-1]
    return s


def normalize_value(val):
    """Remove leading zeros, normalize .0 floats to int, preserve case."""
    if pd.isnull(val):
        return ""
    # Handle float/int conversion
    try:
        if isinstance(val, float) and val.is_integer():
            val = int(val)
        s = str(val).strip()
        # If string looks like '1.0', '02.0', etc.
        if s.replace(".", "", 1).isdigit():
            float_val = float(s)
            if float_val.is_integer():
                s = str(int(float_val))
        # Remove leading zeros for digit codes (but keep for non-numeric)
        if s.isdigit():
            s = str(int(s))
        return normalize_string(s)
    except Exception:
        return normalize_string(val)


def _has_meaningful_value(value):
    if pd.isnull(value):
        return False
    normalized = normalize_string(value).lower()
    return normalized not in {"", "nan", "none", "null"}


def clean_import_value(value, blank=None):
    """Convert a pandas missing value to a blank before it reaches a model field.

    A blank CSV cell arrives from ``read_csv`` as ``float('nan')``. The form
    models store answers in TextFields, so Django stringifies that float and
    persists the literal characters "nan" -- ``null=True`` never gets a chance
    to fire. Callers must route every value through this on the way into
    ``model(**row)``.

    ``blank`` is what an absent answer becomes. It defaults to None, but
    NOT NULL columns (Pregnancy, PregnancyOutcome and Household each have a
    handful) need "" instead -- see ``blank_values_for_model``.

    Mirrors ``odk.service._clean_import_value``, which already protects the ODK
    pull path.
    """
    if pd.isna(value):
        return blank
    if isinstance(value, str) and (not value.strip() or value.strip().lower() == "nan"):
        return blank
    return value


def blank_values_for_model(model):
    """Map each concrete field name to the blank value its column accepts.

    None where the column is nullable, "" where it is NOT NULL. Before the NaN
    fix a missing answer reached the DB as the string "nan", which happened to
    satisfy NOT NULL; writing None there would now raise IntegrityError.
    """
    return {field.name: (None if field.null else "") for field in model._meta.fields}


def read_form_csv(source, **kwargs):
    """Read an ODK/form CSV without pandas coercing identifiers or blanks.

    Form answers are text. Letting pandas infer numeric columns turns values
    such as ``00123`` into ``123``/``123.0`` and represents empty cells as
    floating-point NaN. Callers may override these defaults explicitly when a
    non-form CSV genuinely needs type inference.
    """
    options = {
        "dtype": str,
        "keep_default_na": False,
        "low_memory": False,
    }
    options.update(kwargs)
    return pd.read_csv(source, **options)


def merge_geopoint_columns(df):
    geopoint_columns = find_geopoint_columns(df.columns)
    if not geopoint_columns:
        return df

    df = df.copy()
    derived_geopoint = df.apply(compose_geopoint, axis=1)

    if "geopoint" not in df.columns:
        df["geopoint"] = ""

    existing_geopoint_empty = ~df["geopoint"].apply(_has_meaningful_value)
    should_set_geopoint = (
        derived_geopoint.apply(_has_meaningful_value) & existing_geopoint_empty
    )
    df.loc[should_set_geopoint, "geopoint"] = derived_geopoint[should_set_geopoint]
    df.loc[~df["geopoint"].apply(_has_meaningful_value), "geopoint"] = ""

    return df.drop(columns=list(geopoint_columns.values()))


def normalize_community_va_value(
    value,
    *,
    hospital=None,
    area=None,
    district=None,
    constituency=None,
    ward=None,
    ea=None,
):
    """
    ODK-first community/facility tagging rules:
    1) If community_va is explicitly provided, use it.
    2) Else if any community admin markers exist, infer community ("yes").
    3) Else if a hospital/facility marker exists, infer facility ("no").
    4) Else treat area as the legacy community geography fallback ("yes").
    5) Else unknown (None).
    """
    if not pd.isnull(value):
        normalized = normalize_string(value).lower()
        if normalized not in {"", "nan", "none", "null"}:
            if normalized in {"yes", "y", "true", "1"}:
                return "yes"
            if normalized in {"no", "n", "false", "0"}:
                return "no"

    if any(_has_meaningful_value(v) for v in (district, constituency, ward, ea)):
        return "yes"

    if _has_meaningful_value(hospital):
        return "no"
    if _has_meaningful_value(area):
        return "yes"

    return None


def _normalized_geo_token(value):
    return normalize_string(value).strip().lower()


def build_srs_location_maps():
    """
    Build lightweight lookup maps for SRSClusterLocation by level and token.
    Token uses normalized name and normalized code.
    """
    maps = {
        "ea": {},
        "ward": {},
        "constituency": {},
        "district": {},
        "province": {},
        "all_by_name": {},
        "all_by_code": {},
        "all_by_id": {},
        "lineage_cache": {},
    }
    for loc in SRSClusterLocation.objects.only(
        "id", "name", "code", "location_type"
    ).iterator():
        level = _normalized_geo_token(loc.location_type)
        if level not in maps:
            continue
        name_token = _normalized_geo_token(loc.name)
        if name_token:
            maps[level].setdefault(name_token, []).append(loc)
            maps["all_by_name"].setdefault(name_token, []).append(loc)
        code_token = _normalized_geo_token(loc.code)
        if code_token:
            maps[level].setdefault(code_token, []).append(loc)
            maps["all_by_code"].setdefault(code_token, []).append(loc)
        maps["all_by_id"][str(loc.id)] = loc
    return maps


def _get_row_tokens_by_level(row):
    tokens = {}
    field_to_level = (
        ("province", "province"),
        ("district", "district"),
        ("constituency", "constituency"),
        ("ward", "ward"),
        # Historical VA payloads can carry ward-like names in "area".
        ("area", "ward"),
        ("ea", "ea"),
    )
    for field_name, level in field_to_level:
        token = _normalized_geo_token(row.get(field_name))
        if token:
            tokens.setdefault(level, set()).add(token)
    return tokens


def _get_location_lineage_tokens(location, lineage_cache):
    cached = lineage_cache.get(location.id)
    if cached is not None:
        return cached

    lineage = {}
    nodes = list(location.get_ancestors()) + [location]
    for node in nodes:
        level = _normalized_geo_token(node.location_type)
        if not level:
            continue
        lineage[level] = {
            "name": _normalized_geo_token(node.name),
            "code": _normalized_geo_token(node.code),
        }
    lineage_cache[location.id] = lineage
    return lineage


def _select_best_cluster_candidate(candidates, row_tokens, lineage_cache):
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    valid = []
    for candidate in candidates:
        lineage = _get_location_lineage_tokens(candidate, lineage_cache)
        contradiction = False
        score = 0
        for level, provided_tokens in row_tokens.items():
            level_tokens = lineage.get(level)
            if not level_tokens:
                continue
            matches_level = (
                level_tokens.get("name") in provided_tokens
                or level_tokens.get("code") in provided_tokens
            )
            if matches_level:
                score += 1
            else:
                contradiction = True
                break
        if not contradiction:
            valid.append((score, candidate))

    if not valid:
        return None

    valid.sort(key=lambda item: item[0], reverse=True)
    best_score = valid[0][0]
    best = [candidate for score, candidate in valid if score == best_score]
    return best[0] if len(best) == 1 else None


def resolve_srs_cluster_from_row(row, srs_maps):
    row_tokens = _get_row_tokens_by_level(row)
    lineage_cache = srs_maps.get("lineage_cache", {})

    # Preferred direct cluster hints.
    direct_cluster_fields = (
        "cluster_id",
        "cluster_code",
        "cluster_identifier",
        "clusterid",
        "clustercode",
        "cluster",
    )
    for field_name in direct_cluster_fields:
        token = _normalized_geo_token(row.get(field_name))
        if not token:
            continue

        direct_id_match = srs_maps.get("all_by_id", {}).get(token)
        if direct_id_match:
            return direct_id_match

        direct_code_candidates = srs_maps.get("all_by_code", {}).get(token, [])
        direct_match = _select_best_cluster_candidate(
            direct_code_candidates, row_tokens, lineage_cache
        )
        if direct_match:
            return direct_match

        direct_name_candidates = srs_maps.get("all_by_name", {}).get(token, [])
        direct_match = _select_best_cluster_candidate(
            direct_name_candidates, row_tokens, lineage_cache
        )
        if direct_match:
            return direct_match

    # deepest-to-broadest precedence
    for field_name, level in (
        ("ea", "ea"),
        ("ward", "ward"),
        ("area", "ward"),
        ("constituency", "constituency"),
        ("district", "district"),
        ("province", "province"),
    ):
        token = _normalized_geo_token(row.get(field_name))
        if not token:
            continue
        match = _select_best_cluster_candidate(
            srs_maps.get(level, {}).get(token, []),
            row_tokens,
            lineage_cache,
        )
        if match:
            return match
    return None


def normalize_dataframe_columns(df, model):
    """
    Rename and filter DataFrame columns to align with model fields.
    - Map column names ignoring case
    - Replace only hyphens (-) with underscores (_), without affecting existing underscores
    - Only keep columns that exist in the model
    """
    model_fields = [f.name for f in model._meta.get_fields()]
    model_fields_set = set(model_fields)
    rename_map = build_odk_rename_map(df.columns, expected_fields=model_fields)

    df = df.rename(columns=rename_map)
    df.columns = df.columns.str.strip()
    df = df[[col for col in df.columns if col in model_fields_set]]

    return df


def load_odk_csv_to_model(
    df, model, odk_choices_queryset, odk_map_columns, verbose=False
):
    """
    Generic loader for ODK CSV to Django model, using ODKFormChoice for value-to-label mapping.
    """
    # Build odk_map (all normalized but case preserved)
    odk_map = {}
    for choice in odk_choices_queryset:
        field_name = normalize_string(choice.field_name)
        value = normalize_value(choice.value)
        if field_name not in odk_map:
            odk_map[field_name] = {}
        odk_map[field_name][value] = choice.label

    def lookup_label(col, v):
        v_norm = normalize_value(v)
        if v_norm in odk_map.get(col, {}):
            return odk_map[col][v_norm]
        # A blank cell has no label to look up; keep it blank rather than letting
        # the NaN survive to be stringified as "nan" on the model field.
        if pd.isnull(v) or str(v).lower() == "nan":
            return None
        return v_norm

    # Map ODK code values to labels
    for col in odk_map_columns:
        if col in df.columns and col in odk_map:
            df[col] = df[col].map(lambda v, c=col: lookup_label(c, v))
        elif col not in df.columns and verbose:
            print(f"Column '{col}' not in DataFrame")
        elif col not in odk_map and verbose:
            print(f"Column '{col}' not in odk_map")

    # Remove duplicate columns after renaming
    df = df.loc[:, ~df.columns.duplicated()]

    # Only keep columns that are model fields
    model_fields = {f.name for f in model._meta.get_fields()}
    df = df[[col for col in df.columns if col in model_fields]]

    # Handle ForeignKey: cluster → SRSClusterLocation
    if "cluster" in df.columns:
        cluster_map = {
            str(loc.code): loc
            for loc in SRSClusterLocation.objects.exclude(code__isnull=True)
        }

        def get_cluster_instance(code):
            return cluster_map.get(str(code).strip())

        df["cluster"] = df["cluster"].apply(get_cluster_instance)

    blanks = blank_values_for_model(model)
    objects = [
        model(
            **{
                key: clean_import_value(value, blanks.get(key))
                for key, value in row.items()
            }
        )
        for row in df.to_dict(orient="records")
    ]
    return objects


def _gps_text(value):
    return "" if not _has_meaningful_value(value) else str(value).strip()


def upsert_gps_for_existing(*, model, df, key_field, gps_field, dry_run=False):
    """Update one GPS field for existing rows matched by key_field."""
    stats = {
        "updated": 0,
        "skipped_no_change": 0,
        "skipped_no_value": 0,
        "sample": [],
    }
    if df.empty or key_field not in df.columns:
        return stats

    incoming = {}
    for row in df.to_dict(orient="records"):
        key = row.get(key_field)
        if not _has_meaningful_value(key):
            continue
        incoming[str(key).strip()] = row.get(gps_field) if gps_field in row else None

    if not incoming:
        return stats

    updates = []
    existing_qs = model.objects.filter(**{f"{key_field}__in": list(incoming.keys())})
    for obj in existing_qs:
        key = str(getattr(obj, key_field)).strip()
        incoming_value = incoming.get(key)
        if not _has_meaningful_value(incoming_value):
            stats["skipped_no_value"] += 1
            continue

        existing_value = getattr(obj, gps_field, None)
        incoming_text = _gps_text(incoming_value)
        existing_text = _gps_text(existing_value)
        if existing_text == incoming_text:
            stats["skipped_no_change"] += 1
            continue

        if len(stats["sample"]) < 10:
            stats["sample"].append(
                {
                    "key": key,
                    "existing": existing_text,
                    "incoming": incoming_text,
                }
            )
        setattr(obj, gps_field, incoming_text)
        updates.append(obj)

    stats["updated"] = len(updates)
    if updates and not dry_run:
        bulk_update_with_history(
            updates,
            model,
            fields=[gps_field],
            batch_size=500,
        )
    return stats


# load VA records into django database
def load_records_from_dataframe(
    record_df,
    random_locations=False,
    debug=False,
    *,
    upsert_gps=False,
    dry_run=False,
):
    logger = None if not debug else logging.getLogger("debug")
    if logger:
        header = "=" * 10 + "DATA INGEST" + "=" * 10
        logger.info(header)

    record_df = merge_geopoint_columns(record_df)

    # CSV can prefix column names with a strings and a dash or more. Examples:
    #     presets-Id10004
    #     respondent-backgr-Id10008
    #     -Id10008
    # Remove everything up to and including last dash.
    record_df = record_df.rename(columns=lambda c: c.rsplit("-", 1)[-1])

    # Figure out the common field names across the CSV and our model
    model_field_names = pd.Index([f.name for f in VerbalAutopsy._meta.get_fields()])

    # But first, account for case differences in csv columns
    # (i.e. ensure id10041 maps to Id10041)
    field_case_mapper = {field.lower(): field for field in model_field_names}
    record_df = record_df.rename(columns=lambda c: field_case_mapper.get(c.lower(), c))

    # instanceid is an essential identifier. Normalize input if needed and
    # attempt some known workarounds for supported integrations if such an
    # important field happens to be missing.
    if "instanceID" in record_df.columns:
        record_df = record_df.rename(columns={"instanceID": "instanceid"})
    if "instanceid" not in record_df.columns and "key" in record_df.columns:
        record_df = record_df.rename(columns={"key": "instanceid"})  # ODK
    elif "instanceid" not in record_df.columns and "_uuid" in record_df.columns:
        record_df = record_df.rename(columns={"_uuid": "instanceid"})  # Kobo

    # similar story for instancename. normalized, less known workarounds. if record
    # does not at least have first/last name for deceased (Id10017 + Id10018) and an
    # interview date (Id10012), consider it unrecoverable and drop from import
    invalid_vas = []
    if "instanceName" in record_df.columns:
        record_df = record_df.rename(columns={"instanceName": "instancename"})
    elif "instancename" not in record_df.columns:
        record_df["instancename"] = None

    unrecoverable = record_df[
        record_df["Id10017"].isnull()
        | record_df["Id10018"].isnull()
        | record_df["Id10012"].isnull()
    ]
    invalid_vas.extend(unrecoverable.iterrows())
    record_df.drop(labels=unrecoverable.index.values, axis=0)

    missing_instancenames = record_df[record_df["instancename"].isnull()]
    corrected_vas = []
    for index, record in missing_instancenames.iterrows():
        instancename = f"_Dec---{record['Id10017']} {record['Id10018']}_D.o.I---{record['Id10012']}"  # noqa: E501
        record_df.at[index, "instancename"] = instancename
        corrected_vas.append(index)
    record_df["instancename"] = record_df["instancename"].str.casefold()

    # Patch VAs that are missing Id10010 (Interviewer Name) with custom field fallbacks
    if "Id10010" not in record_df.columns and "SubmitterName" in record_df.columns:
        record_df = record_df.rename(columns={"SubmitterName": "Id10010"})  # ODK
    elif "Id10010" not in record_df.columns and "_submitted_by" in record_df.columns:
        record_df = record_df.rename(columns={"_submitted_by": "Id10010"})  # Kobo

    # Kobo provides an indicator for which records are invalid. Check if this
    # column is present, and if so, drop these from import consideration plus
    # attempt to remove them from existing VA Explorer records
    invalid_vas = []
    if "_validation_status" in record_df.columns:
        filtered_df = record_df[
            record_df["_validation_status"].apply(
                lambda x: isinstance(x, dict) and len(x) != 0
            )
        ]  # Assume blank/not indicated is fine
        invalid = filtered_df[
            filtered_df["_validation_status"].apply(
                lambda x: x["label"] in (["On Hold", "Not Approved"])
            )
        ]
        invalid_uuids = invalid["instanceid"].to_list() if len(invalid) > 0 else []
        to_remove = VerbalAutopsy.objects.filter(instanceid__in=invalid_uuids)
        for va in to_remove:
            invalid_vas.append(va)
        if not dry_run:
            to_remove.delete()
        record_df = record_df.drop(labels=invalid.index.values, axis=0)

    print("de-duplicating fields...")
    # collapse fields ending with _other for their normal counterparts
    # (e.x. Id10010_other, Id10010)
    record_df = deduplicate_columns(record_df)

    # Normalize community_va before dropping non-model columns so we can use
    # transient import fields such as ward.
    if "community_va" not in record_df.columns:
        record_df["community_va"] = None
    record_df["community_va"] = record_df.apply(
        lambda row: normalize_community_va_value(
            row.get("community_va"),
            hospital=row.get("hospital"),
            area=row.get("area"),
            district=row.get("district"),
            constituency=row.get("constituency"),
            ward=row.get("ward"),
            ea=row.get("ea"),
        ),
        axis=1,
    )

    full_record_df = record_df.copy()
    csv_field_names = full_record_df.columns
    common_field_names = csv_field_names.intersection(model_field_names)

    # Only keep fields in CSV that we have columns for in our VerbalAutopsy model
    if logger:
        missing_field_names = model_field_names.difference(common_field_names)
        logger.debug("Missing fields: %s", missing_field_names)
        extra_field_names = csv_field_names.difference(common_field_names)
        logger.debug("Extra fields: %s", extra_field_names)
    record_df = full_record_df[common_field_names]

    # For each row, check to see if there is an instanceid.
    # If there is instanceid, try to find existing VA with that instanceid.
    # If row does not have an instanceid, it will create a new VA.
    # Build a list of VAs to create and a list of VAs to ignore (that already exist).
    ignored_vas = []
    outdated_vas = []
    created_vas = []
    enriched_vas = []
    enriched_instanceids = set()
    gps_upsert_stats = {
        "updated": 0,
        "skipped_no_change": 0,
        "skipped_no_value": 0,
        "sample": [],
    }
    location_map = {}

    # build location mapper to map csv locations to known db locations
    if "hospital" in record_df.columns:
        hospitals = record_df["hospital"].unique().tolist()
        location_map = {
            key_name_pair[0]: key_name_pair[1]
            for key_name_pair in Location.objects.filter(key__in=hospitals)
            .only("name", "key")
            .values_list("key", "name")
        }
    srs_maps = build_srs_location_maps()

    # if random locations, assign random locations via a random field worker.
    if random_locations:
        field_workers = list(User.objects.filter(groups__name="Field Workers"))
        if len(field_workers) <= 1:
            if dry_run:
                print(
                    "WARNING: no field workers in system. "
                    "Skipping random location assignment for dry run..."
                )
                field_workers = []
            else:
                print(
                    "WARNING: no field workers in system. \
                    Generating random ones now..."
                )
                make_field_workers_for_facilities()
                field_workers = list(User.objects.filter(groups__name="Field Workers"))

    # pull in all existing VA instanceIDs from db for de-duping purposes
    print("pulling in instance ids...")
    va_instance_ids = set(VerbalAutopsy.objects.values_list("instanceid", flat=True))
    va_instance_names = set(
        VerbalAutopsy.objects.values_list("instancename", flat=True)
    )
    incoming_instance_ids = [
        instanceid
        for instanceid in record_df["instanceid"].dropna().unique().tolist()
        if _has_meaningful_value(instanceid)
    ]
    existing_by_instanceid = {
        va.instanceid: va
        for va in VerbalAutopsy.objects.filter(instanceid__in=incoming_instance_ids)
    }

    if debug:
        print(
            f"# of VAs: {record_df.shape[0]}, \
            # of instanceIDs: {record_df.instanceid.nunique()}, \
            # of instanceNames: {record_df.instancename.nunique()}"
        )

    print("creating new VAs...")
    model_rows = record_df.to_dict(orient="records")
    raw_rows = full_record_df.to_dict(orient="records")
    for i, row in enumerate(model_rows):
        raw_row = raw_rows[i]
        format_multi_select_fields(row)

        va = VerbalAutopsy(**row)
        # only import VA if its instanceId doesn't already exist
        if row["instanceid"]:
            va_exists = row["instanceid"] in va_instance_ids
            if va_exists:
                existing_va = existing_by_instanceid.get(row["instanceid"])
                incoming_geopoint = row.get("geopoint")
                if row["instanceid"] in enriched_instanceids:
                    ignored_vas.append(va)
                    continue
                if not _has_meaningful_value(incoming_geopoint):
                    gps_upsert_stats["skipped_no_value"] += 1
                elif existing_va is not None:
                    incoming_text = _gps_text(incoming_geopoint)
                    existing_text = _gps_text(existing_va.geopoint)
                    should_update_geopoint = not _has_meaningful_value(
                        existing_va.geopoint
                    ) or (upsert_gps and existing_text != incoming_text)
                    if existing_text == incoming_text:
                        gps_upsert_stats["skipped_no_change"] += 1
                    elif should_update_geopoint:
                        if len(gps_upsert_stats["sample"]) < 10:
                            gps_upsert_stats["sample"].append(
                                {
                                    "key": row["instanceid"],
                                    "existing": existing_text,
                                    "incoming": incoming_text,
                                }
                            )
                        existing_va.geopoint = incoming_text
                        enriched_instanceids.add(row["instanceid"])
                        gps_upsert_stats["updated"] += 1
                        enriched_vas.append(existing_va)
                        continue

                ignored_vas.append(va)
                continue
            else:
                # If VA "doesn't exist", it's still possible we have an edited VA,
                # since kobo will change the uuid for an edited VA. Check if a
                # previous instancename for this "new" VA exists and if so, delete
                # the old one in favor of this new one
                if row["instancename"] in va_instance_names:
                    outdated_va = VerbalAutopsy.objects.filter(
                        instancename=row["instancename"]
                    )
                    if not _has_meaningful_value(va.geopoint):
                        existing_geopoint = next(
                            (
                                geopoint
                                for geopoint in outdated_va.values_list(
                                    "geopoint", flat=True
                                )
                                if _has_meaningful_value(geopoint)
                            ),
                            None,
                        )
                        if existing_geopoint:
                            va.geopoint = existing_geopoint
                    outdated_vas.append(outdated_va)
                    if not dry_run:
                        outdated_va.delete()

                va_instance_ids.add(row["instanceid"])

        # If we got here, we have a new, legit VA on our hands.
        va_id = row.get("instanceid", f"{i} of {record_df.shape[0]}")

        # Try to parse date of death as as datetime. Otherwise, record string and
        # add record issue during validation
        parsed_date = parse_date(va.Id10023, strict=False)
        if logger:
            logger.info(
                "va_id: %s - Parsed %s for Date of Death from %s",
                va_id,
                parse_date,
                va.Id10023,
            )
        va.Id10023 = parsed_date

        # Try to parse interview date as as datetime. Otherwise, record string and
        # add record issue during validation
        parsed_sub_date = parse_date(va.Id10012, strict=False)
        if logger:
            logger.info(
                "va_id: %s - Parsed %s as Interview Date from %s",
                va_id,
                parsed_sub_date,
                va.Id10012,
            )
        va.Id10012 = parsed_sub_date

        # ``bulk_create_with_history`` bypasses model ``save()``; populate the
        # canonical ODK timestamp explicitly so the live and new history rows
        # are synchronized from first insertion. A malformed value must not sink
        # the batch — callers wrap this loader in a transaction, so raising here
        # would roll back an entire ODK pull over one bad row. Load the record
        # with a null timestamp; validate_vas_for_dashboard() below raises a
        # CauseCodingIssue for it, exactly as it does for an unparseable
        # Id10023.
        try:
            va.synchronize_submitted_at()
        except ValidationError:
            va.submitted_at = None

        # if random_locations, assign random field worker to VA which can be used
        # to determine location.
        # Otherwise, try assigning location based on hospital field.
        if random_locations and field_workers:
            user = random.choice(field_workers)
            va.location = user.location_restrictions.first()
        else:
            community_mode = va.community_va_normalized
            if community_mode == "yes":
                va.cluster = resolve_srs_cluster_from_row(raw_row, srs_maps)
            elif community_mode == "no":
                assign_va_location(va, location_map)
            if "hospital" in row and logger:
                logger.info(
                    "va_id: %s - Matched hospital %s to %s location in DB",
                    va_id,
                    row["hospital"],
                    va.location,
                )

        # Generate a unique_identifier_hash for each VA if the application is
        # configured to detect duplicate VAs
        if VerbalAutopsy.auto_detect_duplicates():
            va.generate_unique_identifier_hash()
        created_vas.append(va)

    print("populating DB...")
    if dry_run:
        new_vas = created_vas
    else:
        new_vas = bulk_create_with_history(created_vas, VerbalAutopsy)
    if enriched_vas and not dry_run:
        bulk_update_with_history(
            enriched_vas,
            VerbalAutopsy,
            fields=["geopoint"],
            batch_size=500,
        )

    if not dry_run:
        print("Validating VAs...")
        # Add any errors to the db
        validate_vas_for_dashboard(new_vas)

    # Mark duplicate VAs if the application is configured to do so
    if not dry_run and VerbalAutopsy.auto_detect_duplicates():
        print("Marking VAs as duplicate...")
        VerbalAutopsy.mark_duplicates()

    # Auto-link newly ingested VAs to Death records (no-op unless the feature
    # flag is enabled; runs after commit so the matcher sees committed rows).
    if not dry_run and new_vas:
        from va_explorer.va_data_management.utils.matching import (
            schedule_link_for_va_ids,
        )

        schedule_link_for_va_ids(
            [va.pk for va in new_vas],
            model_name="verbalautopsy",
            source="pyodk_ingestion",
        )

    return {
        "ignored": ignored_vas,
        "outdated": outdated_vas,
        "created": created_vas,
        "enriched": enriched_vas,
        "gps_upsert": gps_upsert_stats,
        "corrected": corrected_vas,
        "removed": invalid_vas,
    }


# Change the response format of Multiselect questions from ODK (space-separated)
# into the format that we expect for rendering in the UI (comma-separated)
def format_multi_select_fields(row):
    for multi_select_question in _checkbox_choices:
        if multi_select_question in row and isinstance(row[multi_select_question], str):
            # Convert to comma-separated
            row[multi_select_question] = ",".join(
                response for response in row[multi_select_question].split()
            )


# combine fields ending with _other for their normal counterparts
# (e.x. Id10010_other, Id10010).
# in Zambia data, often either the normal or _other field has a value but not both.
# NOTE: currently using 'cleaned' version of other field (called filtered_<field>_other)
# and discarding <field>-other values. verify that this is kosher.
def deduplicate_columns(record_df, drop_duplicates=True):
    other_cols = record_df.filter(regex=r"\_other$", axis=1).columns
    # get original columns that other columns are derived from
    original_cols = list(
        set(
            other_cols.str.replace(r"^filtered\_", "", regex=True)
            .str.replace(r"_other$", "", regex=True)
            .tolist()
        )
    )
    for original_col in original_cols:
        # If original column exists, combine values from original and _other
        # columns into a single column
        if original_col in record_df.columns:
            record_df[original_col] = (
                record_df.filter(regex=original_col, axis=1)
                .replace("other", np.nan)
                .bfill(axis=1)
                .iloc[:, 0]
            )
            # record_df[original_col] = record_df.apply(lambda row: row[other_col]
            # f pd.isna(row[original_col]) else row[original_col], axis=1)
        else:
            print(
                f"WARNING: couldn't find {original_col} but \
                {original_col}_other in columns"
            )
    if drop_duplicates:
        record_df = record_df.drop(columns=other_cols)
    return record_df


def _merge_summary_aggregates(primary, extra):
    """Combine two summary aggregates (VA + VASA) into one.

    Totals add; the Max() columns take whichever is larger (``None`` last).
    ``last_interview`` / ``_max_submissiondate`` are ISO text so lexicographic
    max equals chronological max for well-formed values.
    """

    def _max(a, b):
        if a is None:
            return b
        if b is None:
            return a
        return max(a, b)

    return {
        "last_created": _max(primary.get("last_created"), extra.get("last_created")),
        "last_interview": _max(
            primary.get("last_interview"), extra.get("last_interview")
        ),
        "total_vas": (primary.get("total_vas") or 0) + (extra.get("total_vas") or 0),
        "_max_submissiondate": _max(
            primary.get("_max_submissiondate"), extra.get("_max_submissiondate")
        ),
    }


def get_va_summary_stats(
    vas, filter_fields=False, cache_key="va_summary_stats", extra_qs=None
):
    # if vas.count() > 0 code is the slowest SQL query

    # ``extra_qs`` folds a second BaseVerbalAutopsy queryset (VASA) into the same
    # summary so dashboards/line-list show one unified verbal-autopsy figure.

    # if filter_fields=True, filter down to only relevant fields
    _summary_only_fields = (
        "created",
        "id",
        "location",
        "cluster",
        "community_va",
        "Id10023",
        "submissiondate",
        "Id10012",
    )
    if filter_fields:
        vas = vas.only(*_summary_only_fields)
        if extra_qs is not None:
            extra_qs = extra_qs.only(*_summary_only_fields)

    # check cache for va summary stats and set it if not already there
    stats = cache.get(cache_key) if cache_key else None
    if not stats:
        _summary_aggregate = {
            "last_created": Max("created"),
            "last_interview": Max("Id10012"),
            "total_vas": Count("id"),
            # Max() on ISO text dates is lexicographic, which equals chronological
            # for well-formed YYYY-MM-DD* strings — avoids loading all rows to Python.
            "_max_submissiondate": Max("submissiondate"),
        }
        agg = vas.aggregate(**_summary_aggregate)
        if extra_qs is not None:
            agg = _merge_summary_aggregates(
                agg, extra_qs.aggregate(**_summary_aggregate)
            )
        raw_sd = agg.get("_max_submissiondate")
        if raw_sd:
            parsed_sd = parse_date(str(raw_sd))
            if parsed_sd and parsed_sd not in {"dk", "nan", "none", "null"}:
                try:
                    latest_submission = datetime.strptime(parsed_sd, "%Y-%m-%d").date()
                except ValueError:
                    latest_submission = None
            else:
                latest_submission = None
        else:
            latest_submission = None

        # Facility VAs are expected to resolve a facility Location; community VAs
        # are expected to resolve an SRS cluster.
        _ineligible_q = (
            Q(Id10023__in=["DK", "dk"])
            | Q(Id10023__isnull=True)
            | (Q(community_va__iexact="yes") & Q(cluster__isnull=True))
            | (~Q(community_va__iexact="yes") & Q(location__isnull=True))
        )
        ineligible_count = vas.filter(_ineligible_q).count()
        if extra_qs is not None:
            ineligible_count += extra_qs.filter(_ineligible_q).count()

        stats = {
            "last_update": latest_submission or agg.get("last_created"),
            "last_interview": agg.get("last_interview"),
            "total_vas": agg.get("total_vas"),
            "ineligible_vas": ineligible_count,
        }
        if cache_key:
            cache.set(cache_key, stats, timeout=60 * 60)

    # clean up dates if non-null
    if stats["last_update"] and not isinstance(stats["last_update"], str):
        stats["last_update"] = stats["last_update"].strftime("%Y-%m-%d")

    if stats["last_interview"]:
        # Handle datetimes and Text (which can occur since interview is TextField)
        if not isinstance(stats["last_interview"], str):
            stats["last_interview"] = stats["last_interview"].strftime("%Y-%m-%d")
        else:
            stats["last_interview"] = parse_date(stats["last_interview"])
    return stats
    # TODO is it likely or possible to return no VAs? if yes, return empty stats
