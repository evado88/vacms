import argparse

import pandas as pd
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from va_explorer.va_data_management.models import Location, VerbalAutopsy
from va_explorer.va_data_management.utils.loading import (
    build_srs_location_maps,
    normalize_community_va_value,
    normalize_dataframe_columns,
    normalize_string,
    resolve_srs_cluster_from_row,
)
from va_explorer.va_data_management.utils.location_assignment import assign_va_location


def _clean_value(value):
    if pd.isnull(value):
        return None
    text = str(value).strip()
    return text if text else None


class Command(BaseCommand):
    help = (
        "Update existing VerbalAutopsy location fields from CSV, matched by instanceid. "
        "Supports community location fields (province, district, constituency, ward, ea)."
    )

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=argparse.FileType("r"))
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without writing to the database.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Bulk update batch size (default: 1000).",
        )

    def handle(self, *args, **options):
        _ = args  # unused
        csv_file = options["csv_file"]
        dry_run = bool(options["dry_run"])
        batch_size = max(int(options["batch_size"]), 1)

        df = pd.read_csv(csv_file)

        if "instanceID" in df.columns and "instanceid" not in df.columns:
            df = df.rename(columns={"instanceID": "instanceid"})

        # Normalize header names/casing and keep only VA model fields.
        df = normalize_dataframe_columns(df, VerbalAutopsy)
        if "instanceid" not in df.columns:
            raise CommandError("CSV must include 'instanceid' (or 'instanceID').")

        # Drop empty keys and de-duplicate updates by instanceid (last row wins).
        before = len(df)
        df = df[
            df["instanceid"].notna() & (df["instanceid"].astype(str).str.strip() != "")
        ]
        dropped_blank = before - len(df)
        before_dedupe = len(df)
        df = df.sort_values("instanceid").drop_duplicates(
            subset=["instanceid"], keep="last"
        )
        deduped = before_dedupe - len(df)

        update_columns = [
            field
            for field in (
                "province",
                "district",
                "constituency",
                "ward",
                "ea",
                "area",
                "hospital",
                "community_va",
            )
            if field in df.columns
        ]

        rows = df.to_dict(orient="records")
        instanceids = [str(row["instanceid"]).strip() for row in rows]
        existing = VerbalAutopsy.objects.filter(instanceid__in=instanceids)
        existing_map = {va.instanceid: va for va in existing}

        # Build maps used for resolving updated relationships.
        srs_maps = build_srs_location_maps()
        hospitals = {
            normalize_string(row.get("hospital")).strip()
            for row in rows
            if row.get("hospital") is not None
            and str(row.get("hospital")).strip() != ""
        }
        location_map = {
            key_name_pair[0]: key_name_pair[1]
            for key_name_pair in Location.objects.filter(key__in=hospitals)
            .only("name", "key")
            .values_list("key", "name")
        }

        updated_objects = []
        matched = 0
        unmatched = 0
        changed = 0

        for row in rows:
            instanceid = str(row["instanceid"]).strip()
            va = existing_map.get(instanceid)
            if not va:
                unmatched += 1
                continue
            matched += 1

            has_changes = False

            # Apply direct location field updates.
            for field in update_columns:
                new_value = _clean_value(row.get(field))
                if getattr(va, field, None) != new_value:
                    setattr(va, field, new_value)
                    has_changes = True

            # Enforce community_va semantics based on latest values.
            normalized_community_va = normalize_community_va_value(
                va.community_va,
                hospital=va.hospital,
                area=va.area,
                district=va.district,
                constituency=va.constituency,
                ward=va.ward,
                ea=va.ea,
            )
            if va.community_va != normalized_community_va:
                va.community_va = normalized_community_va
                has_changes = True

            # Resolve cluster for community VAs; clear for facility VAs.
            community_mode = va.community_va_normalized
            if community_mode == "yes":
                cluster_candidate = resolve_srs_cluster_from_row(
                    {
                        "province": va.province,
                        "district": va.district,
                        "constituency": va.constituency,
                        "ward": va.ward,
                        "ea": va.ea,
                    },
                    srs_maps,
                )
                if va.cluster != cluster_candidate:
                    va.cluster = cluster_candidate
                    has_changes = True
            elif community_mode == "no":
                if va.cluster_id is not None:
                    va.cluster = None
                    has_changes = True
                old_location = va.location
                assign_va_location(va, location_mapper=location_map)
                if va.location != old_location:
                    has_changes = True

            if has_changes:
                changed += 1
                updated_objects.append(va)

        if not dry_run and updated_objects:
            with transaction.atomic():
                VerbalAutopsy.objects.bulk_update(
                    updated_objects,
                    [
                        "province",
                        "district",
                        "constituency",
                        "ward",
                        "ea",
                        "area",
                        "hospital",
                        "community_va",
                        "cluster",
                        "location",
                    ],
                    batch_size=batch_size,
                )

        action = "Would update" if dry_run else "Updated"
        self.stdout.write(
            f"{action} {changed} verbal autopsy row(s); "
            f"matched={matched}, unmatched={unmatched}, "
            f"dropped_blank_instanceid={dropped_blank}, deduped_rows={deduped}."
        )
