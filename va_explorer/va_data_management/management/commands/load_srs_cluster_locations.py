import argparse

import pandas as pd
from anytree import LevelOrderIter, Node, RenderTree
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from va_explorer.va_data_management.models import SRSClusterLocation

# === Configuration toggles ===
USE_UPPER_LOCATION_TYPES = (
    False  # set True if your model choices are uppercase (e.g., "PROVINCE")
)
PATH_SEPARATOR_FALLBACK = "/"  # used only if we cannot infer from an existing DB node


def _lt(val: str) -> str:
    """Normalize location_type to match model choices."""
    v = (val or "").strip()
    return v.upper() if USE_UPPER_LOCATION_TYPES else v.lower()


def _boolish(x) -> bool:
    """Robust boolean coercion for CSV flags."""
    s = str(x).strip().lower()
    return s in {"1", "true", "t", "yes", "y", "active"}


class Command(BaseCommand):
    help = "Loads hierarchical SRS cluster location data from a CSV."

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=argparse.FileType("r"))
        # Use store_true so '--delete_previous' works as a flag
        parser.add_argument(
            "--delete_previous",
            action="store_true",
            help="Delete existing SRSClusterLocation rows before load",
        )

    def handle(self, *args, **options):
        csv_file = options["csv_file"]
        delete_previous = options["delete_previous"]

        df = _process_cluster_csv(csv_file)

        # Fail fast if essential columns are missing
        required = {
            "province",
            "district",
            "constituency",
            "ward",
            "ea",
            "cluster",
            "is_active",
        }
        missing = required - set(df.columns)
        if missing:
            raise CommandError(
                f"CSV is missing required columns: {', '.join(sorted(missing))}"
            )

        tree = _treeify_clusters(df)
        _process_cluster_tree(tree, delete_previous)


def _process_cluster_csv(csv_file) -> pd.DataFrame:
    df = pd.read_csv(csv_file, dtype=str).rename(
        columns=lambda c: str(c).strip().lower()
    )
    df = df.applymap(lambda v: v.strip() if isinstance(v, str) else v)

    if "is_active" in df.columns and "status" not in df.columns:
        pass
    elif "status" in df.columns:
        df = df.rename(columns={"status": "is_active"})
    else:
        df["is_active"] = ""

    def _boolish(x):
        s = str(x).strip().lower()
        return s in {"1", "true", "t", "yes", "y", "active"}

    df["is_active"] = df["is_active"].map(_boolish)

    for col in [
        "country",
        "province",
        "district",
        "constituency",
        "ward",
        "ea",
        "cluster",
    ]:
        if col not in df.columns:
            df[col] = ""
    if not df.get("country", pd.Series(dtype="object")).any():
        df["country"] = "Zambia"

    # drop structurally incomplete rows
    for col in ["province", "district", "constituency", "ward", "ea"]:
        df = df[df[col] != ""]

    # Dedup ONLY on the admin path (one row per EA)
    df = df.sort_values(
        ["country", "province", "district", "constituency", "ward", "ea"]
    )
    df = df.drop_duplicates(
        subset=["country", "province", "district", "constituency", "ward", "ea"],
        keep="last",
    )

    return df.reset_index(drop=True)


def _treeify_clusters(df: pd.DataFrame) -> Node:
    root = Node("Zambia", location_type=_lt("country"))
    node_lookup = {}  # keys at each level to avoid duplicates

    for _, row in df.iterrows():
        prov = row["province"]
        dist = row["district"]
        const = row["constituency"]
        ward = row["ward"]
        ea = row["ea"]
        cluster_code = row["cluster"]
        status = bool(row["is_active"])

        # Province
        p_key = prov
        p_node = node_lookup.get(p_key)
        if not p_node:
            p_node = Node(prov, location_type=_lt("province"), parent=root)
            node_lookup[p_key] = p_node

        # District
        d_key = f"{prov}::{dist}"
        d_node = node_lookup.get(d_key)
        if not d_node:
            d_node = Node(dist, location_type=_lt("district"), parent=p_node)
            node_lookup[d_key] = d_node

        # Constituency
        c_key = f"{d_key}::{const}"
        c_node = node_lookup.get(c_key)
        if not c_node:
            c_node = Node(const, location_type=_lt("constituency"), parent=d_node)
            node_lookup[c_key] = c_node

        # Ward
        w_key = f"{c_key}::{ward}"
        w_node = node_lookup.get(w_key)
        if not w_node:
            w_node = Node(ward, location_type=_lt("ward"), parent=c_node)
            node_lookup[w_key] = w_node

        # EA (leaf; store cluster code & active flag here)
        ea_key = f"{w_key}::{ea}"
        if ea_key not in node_lookup:
            ea_node = Node(
                str(ea),
                location_type=_lt("ea"),
                code=str(cluster_code) if pd.notna(cluster_code) else "",
                is_active=status,
                parent=w_node,
            )
            node_lookup[ea_key] = ea_node

    if settings.DEBUG:
        for pre, _, node in RenderTree(root):
            print(f"{pre}{node.name}")

    return root


def _infer_path_separator() -> str:
    # Try to read one node and reverse-engineer its path_string separator.
    sample = SRSClusterLocation.objects.order_by("id").first()
    if sample and getattr(sample, "path_string", None):
        # Heuristic: if path_string starts with "/", keep "/"
        ps = sample.path_string
        if ps.startswith("/"):
            return "/"
        # Fall back to a safe default if it’s some other format
    return PATH_SEPARATOR_FALLBACK


def _build_path_from_anytree(node, sep: str) -> str:
    # Build "/Zambia/Province/..." style path to match path_string
    names = [str(n.name) for n in node.path]
    return sep + sep.join(names)


def _process_cluster_tree(tree: Node, delete_previous: bool = False):
    if delete_previous:
        confirm = input(
            "This will delete all SRSClusterLocation data. Continue? (yes/no): "
        )
        if confirm.strip().lower() not in {"yes", "y"}:
            print("Aborted.")
            return

    _persist_cluster_tree(tree, delete_previous)


@transaction.atomic
def _persist_cluster_tree(tree: Node, delete_previous: bool = False):
    """Reconcile a parsed hierarchy without leaving partial tree mutations."""
    if delete_previous:
        SRSClusterLocation.objects.all().delete()

    # Treebeard allocates a new child path from the parent's persisted
    # ``numchild`` value. An interrupted historical import can leave that value
    # stale, causing add_child() to reuse an occupied path. The safe/default
    # repair updates only depth and numchild; it does not rewrite node paths.
    SRSClusterLocation.fix_tree()

    db_fields = {f.name for f in SRSClusterLocation._meta.get_fields()}
    allowed_node_attrs = {"name", "location_type", "code", "is_active"} & db_fields

    # Build a quick map of existing roots by name to avoid re-creating the root
    roots_by_name = {r.name: r for r in SRSClusterLocation.get_root_nodes()}

    tree_nodes = list(LevelOrderIter(tree))
    added = updated = 0

    for node in tree_nodes:
        model_data = {
            k: getattr(node, k) for k in allowed_node_attrs if hasattr(node, k)
        }

        if node.is_root:
            # Root: get-or-create by name (and optionally location_type)
            existing_root = roots_by_name.get(model_data["name"])
            if existing_root:
                # Update root fields if needed
                for f, v in model_data.items():
                    setattr(existing_root, f, v)
                existing_root.save()
                updated += 1
                db_node = existing_root
            else:
                db_node = SRSClusterLocation.add_root(**model_data)
                roots_by_name[db_node.name] = db_node
                added += 1

            # Stash a back-reference so we can find parents quickly
            node._db_obj = db_node
            continue

        # Resolve the parent DB node that we created/updated in a prior iteration
        parent_db = getattr(node.parent, "_db_obj", None)
        if not parent_db:
            # If this happens, the parent wasn’t created/found; skip defensively
            print(f"Missing parent for {node.name}; skipping.")
            continue

        # --- KEY CHANGE: decide existence by parent->children, not by path_string ---
        qs = parent_db.get_children().filter(name=model_data["name"])
        if "location_type" in model_data:
            qs = qs.filter(location_type=model_data["location_type"])
        existing = qs.first()

        if existing:
            for f, v in model_data.items():
                setattr(existing, f, v)
            existing.save()
            updated += 1
            db_node = existing
        else:
            db_node = parent_db.add_child(**model_data)
            added += 1

        # keep handle for deeper descendants
        node._db_obj = db_node

    print(f"Added: {added}, Updated: {updated}")
