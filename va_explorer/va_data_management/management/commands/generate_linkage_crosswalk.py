"""Generate the version-controlled Death<->VA linkage crosswalk artifacts.

Reads the E_DEATH and WHO-VA XLSForms (plus the VASA form for hashing and the
facility-VA CSV for column verification), verifies the hand-maintained seed
table in :mod:`va_explorer.va_data_management.linkage.crosswalk_rules` against
the forms via label analysis, and writes:

* ``linkage/linkage_map.py``        — CONCEPTS, CHOICE_CATEGORY_MAPS,
                                      GEO_CODE_MAPS, hashes (drives the engine)
* ``linkage/linkage_crosswalk.csv`` — the reviewable artifact (every DE field
                                      with its mapping, confidence and status)

Usage::

    python manage.py generate_linkage_crosswalk           # regenerate + report
    python manage.py generate_linkage_crosswalk --check   # verify, write nothing

The XLSForms are authoritative: where the seed table and the forms disagree the
command flags the discrepancy (and demotes seeds whose Death-side field is
missing to ``absent_in_form``).
"""

import csv
import io
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from fuzzywuzzy import fuzz

from va_explorer.va_data_management.linkage import crosswalk_rules as rules
from va_explorer.va_data_management.models import Death
from va_explorer.va_data_management.utils.xlsform import (
    choice_maps,
    norm_label,
    render_module,
    sha256_file,
    survey_fields,
)

LINKAGE_PACKAGE_DIR = Path(__file__).resolve().parents[2] / "linkage"
DEFAULT_DATA_DIR = Path(settings.APPS_DIR) / "static" / "data"
DEFAULT_E_DEATH = DEFAULT_DATA_DIR / "E_DEATH.xlsx"
DEFAULT_WHO = DEFAULT_DATA_DIR / "znphi_va_who_v1_5_2_7.xlsx"
DEFAULT_VASA = DEFAULT_DATA_DIR / "znphifacilityvasa_02_final.xlsx"
DEFAULT_FACILITY_CSV = DEFAULT_DATA_DIR / "zm_va_who_v1_5_2_7.csv"

_GEO_LISTS = {
    "province": "lstprovince",
    "district": "lstdistrict",
    "constituency": "lstconstituency",
    "ward": "lstward",
    "ea": "lstea",
}
_GEO_FIELDS = set(_GEO_LISTS)
# Facility-VA geography columns worth verifying against the facility CSV header.
_FACILITY_GEO = {
    "province",
    "district",
    "constituency",
    "ward",
    "ea",
    "area",
    "hospital",
}
# E_DEATH admin/machinery fields never analysed for a WHO mapping.
_DE_ADMIN = {
    "deviceid",
    "today",
    "start",
    "supervisor",
    "enumerator",
    "consent",
    "informant",
}

_DE_PREFIX_RE = re.compile(r"^de[\s_-]*\d+[a-z]?\b", flags=re.IGNORECASE)
_WHO_PREFIX_RE = re.compile(r"^\(Id\d+(_[a-z])?\)\s*", flags=re.IGNORECASE)
_REF_RE = re.compile(r"\$\{[^}]*\}")
_PAREN_RE = re.compile(r"\([^)]*\)")


def _norm_de_label(label: str) -> str:
    text = _REF_RE.sub(" ", str(label or ""))
    text = norm_label(text)
    return _DE_PREFIX_RE.sub("", text).strip()


def _norm_who_label(label: str) -> str:
    text = str(label or "").strip()
    text = _WHO_PREFIX_RE.sub("", text)
    text = text.strip("[]")
    text = _REF_RE.sub(" ", text)
    text = _PAREN_RE.sub(" ", text)  # drop interviewer notes / "(s)he" artifacts
    return norm_label(text)


def _label_similarity(de_label: str, who_label: str) -> float:
    a, b = _norm_de_label(de_label), _norm_who_label(who_label)
    if not a or not b:
        return 0.0
    return float(fuzz.token_set_ratio(a, b))


def _keyword_boost(concept: str, de_label: str, who_label: str) -> float:
    keywords = rules.CONCEPT_KEYWORDS.get(concept, [])
    if not keywords:
        return 0.0
    tokens = set(_norm_de_label(de_label).split()) & set(
        _norm_who_label(who_label).split()
    )
    hits = sum(1 for k in keywords if k in tokens)
    return min(10.0, 5.0 * hits)


def _de_attr(de_field: str):
    """XLSForm field name -> Death model attribute name."""
    if de_field is None:
        return None
    return de_field.replace("-", "_")


def _tokens_subset(pattern: str, label_tokens: set) -> bool:
    return set(pattern.split()) <= label_tokens


def _categorise(label: str, category_patterns: dict):
    tokens = set(norm_label(label).split())
    for category, patterns in category_patterns.items():
        if any(_tokens_subset(p, tokens) for p in patterns):
            return category
    return None


class Command(BaseCommand):
    help = "Generate (or --check) the Death<->VA linkage crosswalk artifacts."

    def add_arguments(self, parser):
        parser.add_argument("--e-death-xlsform", default=str(DEFAULT_E_DEATH))
        parser.add_argument("--who-xlsform", default=str(DEFAULT_WHO))
        parser.add_argument("--vasa-xlsform", default=str(DEFAULT_VASA))
        parser.add_argument("--facility-csv", default=str(DEFAULT_FACILITY_CSV))
        parser.add_argument(
            "--check",
            action="store_true",
            help="Verify committed artifacts match the forms; write nothing.",
        )
        parser.add_argument("--quiet", action="store_true")

    def handle(self, *args, **options):
        artifacts = self.build_artifacts(
            Path(options["e_death_xlsform"]),
            Path(options["who_xlsform"]),
            Path(options["vasa_xlsform"]),
            Path(options["facility_csv"]),
        )
        if not options["quiet"]:
            self.stdout.write(artifacts["report"])
        if options["check"]:
            self._check(artifacts)
        else:
            self._write(artifacts)
            if not options["quiet"]:
                self.stdout.write(
                    self.style.SUCCESS("Wrote linkage crosswalk artifacts.")
                )

    # ------------------------------------------------------------------ build
    def build_artifacts(
        self, e_death: Path, who: Path, vasa: Path, facility_csv: Path
    ) -> dict:
        for path in (e_death, who, vasa):
            if not path.exists():
                raise CommandError(f"XLSForm not found: {path}")

        de_fields = {f["name"]: f for f in survey_fields(e_death)}
        who_fields = {f["name"]: f for f in survey_fields(who) if not f["in_repeat"]}
        who_to_vasa = self._who_to_vasa_names()

        death_attrs = {f.name for f in Death._meta.fields}

        concepts: dict = {}
        warnings: list = []
        seeded_de_fields: set = set()

        seeds = {**rules.SEED_CROSSWALK}
        for concept, override in rules.MANUAL_OVERRIDES.items():
            seeds[concept] = {**seeds.get(concept, {}), **override}

        for concept, seed in seeds.items():
            de_field = seed.get("de_field")
            who_ids = list(seed.get("who_ids", []))
            status = seed.get("status", "proposed")
            attr = _de_attr(de_field)

            if de_field is not None:
                seeded_de_fields.add(de_field)
                if de_field not in de_fields:
                    status = "absent_in_form"
                elif attr not in death_attrs:
                    warnings.append(
                        f"{concept}: E_DEATH has '{de_field}' but the Death "
                        f"model has no '{attr}' column — loader gap?"
                    )

            missing_who = [w for w in who_ids if w not in who_fields]
            if missing_who and status == "confirmed":
                warnings.append(
                    f"{concept}: WHO form is missing {missing_who} — verify"
                )

            label_de = de_fields.get(de_field, {}).get("label", "") if de_field else ""
            who_labelled = [w for w in who_ids if who_fields.get(w, {}).get("label")]
            label_who = who_fields[who_labelled[0]]["label"] if who_labelled else ""
            confidence = 0.0
            if label_de and who_labelled:
                confidence = max(
                    _label_similarity(label_de, who_fields[w]["label"])
                    + _keyword_boost(concept, label_de, who_fields[w]["label"])
                    for w in who_labelled
                )
                confidence = round(min(confidence, 100.0), 1)
                if status == "confirmed" and confidence < 25.0:
                    warnings.append(
                        f"{concept}: confirmed seed but label similarity is only "
                        f"{confidence} ('{label_de}' vs '{label_who}')"
                    )

            vasa_names = sorted(
                {name for w in who_ids for name in who_to_vasa.get(w, [])}
            )
            concepts[concept] = {
                # "" (not None) so the generated module stays JSON-renderable.
                "de_field": attr or "",
                "who_ids": who_ids,
                "vasa_names": vasa_names,
                "label_de": label_de,
                "label_who": label_who,
                "confidence": confidence,
                "rule": seed.get("rule", ""),
                "status": status,
            }

        proposals = self._propose_unseeded(de_fields, who_fields, seeded_de_fields)
        choice_category_maps = self._choice_category_maps(concepts, e_death, who)
        geo_code_maps = self._geo_code_maps(e_death, who)
        facility_columns, facility_header_sha = self._facility_columns(facility_csv)

        missing_facility_geo = sorted(
            g for g in ("province", "area", "hospital") if g not in facility_columns
        )
        if missing_facility_geo:
            warnings.append(
                f"facility CSV header lacks expected columns: {missing_facility_geo}"
            )

        hashes = {
            "e_death": sha256_file(e_death),
            "who": sha256_file(who),
            "vasa": sha256_file(vasa),
            "facility_csv_header": facility_header_sha,
        }
        linkage_map = {
            "CONCEPTS": concepts,
            "CHOICE_CATEGORY_MAPS": choice_category_maps,
            "GEO_CODE_MAPS": geo_code_maps,
            "FACILITY_VA_COLUMNS": sorted(facility_columns & _FACILITY_GEO),
            "XLSFORM_HASHES": hashes,
            "CROSSWALK_VERSION": (
                f"{hashes['e_death'][:8]}-{hashes['who'][:8]}-{hashes['vasa'][:8]}"
            ),
        }
        csv_text = self._render_csv(concepts, proposals)
        report = self._format_report(
            concepts=concepts,
            proposals=proposals,
            warnings=warnings,
            choice_category_maps=choice_category_maps,
            geo_code_maps=geo_code_maps,
            facility_columns=facility_columns,
            version=linkage_map["CROSSWALK_VERSION"],
        )
        return {"linkage_map": linkage_map, "csv": csv_text, "report": report}

    def _who_to_vasa_names(self) -> dict:
        """Invert the existing VASA crosswalk (never re-derived from the form)."""
        from va_explorer.va_data_management.vasa import field_map

        who_to_vasa: dict = {}
        for pseudo, who in field_map.PSEUDO_TO_WHO.items():
            who_to_vasa.setdefault(who, []).append(pseudo)
        for pseudo, targets in field_map.COMBINED_WHO.items():
            for who in targets:
                who_to_vasa.setdefault(who, [])
                if pseudo not in who_to_vasa[who]:
                    who_to_vasa[who].append(pseudo)
        return who_to_vasa

    def _propose_unseeded(self, de_fields, who_fields, seeded) -> list:
        """Label-analysis proposals for DE fields no seed/exclusion covers."""
        who_labelled = [
            (name, f["label"]) for name, f in who_fields.items() if f["label"]
        ]
        proposals = []
        for name, f in de_fields.items():
            if (
                name in seeded
                or name in rules.EXCLUDED_DE_FIELDS
                or name in _DE_ADMIN
                or name in _GEO_FIELDS
                or f["base_type"] in {"image", "geopoint"}
                or not f["label"]
            ):
                continue
            best_who, best_label, best_score = None, "", 0.0
            for who_name, who_label in who_labelled:
                score = _label_similarity(f["label"], who_label)
                if score > best_score:
                    best_who, best_label, best_score = who_name, who_label, score
            proposals.append(
                {
                    "de_field": _de_attr(name),
                    "who_ids": [best_who] if best_who and best_score >= 45 else [],
                    "label_de": f["label"],
                    "label_who": best_label if best_score >= 45 else "",
                    "confidence": round(best_score, 1),
                    "status": "proposed" if best_score >= 45 else "unmapped",
                }
            )
        return proposals

    def _choice_category_maps(self, concepts, e_death: Path, who: Path) -> dict:
        de_field_list, de_list_choices, _ = choice_maps(e_death)
        who_field_list, who_list_choices, _ = choice_maps(who)

        maps: dict = {}
        for concept, patterns in rules.CATEGORY_MAPS.items():
            spec = concepts.get(concept)
            if spec is None:
                continue
            death_map: dict = {}
            de_field = spec["de_field"]
            # Model attr back to form spelling for the survey lookup.
            form_name = de_field.replace("_", "-") if de_field else None
            list_name = de_field_list.get(form_name) if form_name else None
            for code, label in de_list_choices.get(list_name, {}).items():
                category = _categorise(label, patterns)
                if category:
                    death_map[code] = category
            who_map: dict = {}
            for who_id in spec["who_ids"]:
                list_name = who_field_list.get(who_id)
                if not list_name:
                    continue
                for code, label in who_list_choices.get(list_name, {}).items():
                    category = _categorise(label, patterns)
                    if category:
                        # WHO choice *names* are the stored VA values.
                        who_map[code] = category
                break  # first select-typed WHO id carries the concept
            if death_map or who_map:
                maps[concept] = {"death": death_map, "who": who_map}
        return maps

    def _geo_code_maps(self, e_death: Path, who: Path) -> dict:
        """code -> name maps for each geo level, merged across both forms.

        Codes are stored stripped of leading zeros (both forms zero-pad, the VA
        records don't always); lookups must normalise the same way.
        """
        maps: dict = {level: {} for level in _GEO_LISTS}
        for form in (who, e_death):  # E_DEATH last: its labels win on conflict
            _, list_choices, _ = choice_maps(form)
            for level, list_name in _GEO_LISTS.items():
                for code, label in list_choices.get(list_name, {}).items():
                    key = str(code).strip().lstrip("0")
                    if key and label:
                        maps[level][key] = str(label).strip()
        return maps

    def _facility_columns(self, facility_csv: Path):
        """Base column names from the facility-VA export header (schema check).

        The file may contain data rows; only the header line is read (and only
        the header is hashed, so appended data never causes artifact drift).
        ODK flattening prefixes group paths — the base name is the last
        ``-``-separated segment.
        """
        if not facility_csv.exists():
            return set(), ""
        with facility_csv.open("r", encoding="utf-8-sig", errors="replace") as f:
            header_line = f.readline().strip()
        columns = next(csv.reader(io.StringIO(header_line)), [])
        base_names = {c.split("-")[-1].strip() for c in columns if c}
        import hashlib

        return base_names, hashlib.sha256(header_line.encode()).hexdigest()

    # ------------------------------------------------------------------ write
    def _write(self, artifacts: dict) -> None:
        (LINKAGE_PACKAGE_DIR / "linkage_map.py").write_text(
            render_module(
                "linkage_map",
                "Death<->VA linkage concepts, choice category maps, geo code maps",
                artifacts["linkage_map"],
                command="generate_linkage_crosswalk",
            )
        )
        (LINKAGE_PACKAGE_DIR / "linkage_crosswalk.csv").write_text(artifacts["csv"])

    def _check(self, artifacts: dict) -> None:
        try:
            from va_explorer.va_data_management.linkage import linkage_map
        except ImportError as exc:
            raise CommandError(
                "linkage_map.py missing; run 'generate_linkage_crosswalk'."
            ) from exc

        mismatches = [
            f"linkage_map.{key}"
            for key, expected in artifacts["linkage_map"].items()
            if getattr(linkage_map, key, None) != expected
        ]
        csv_path = LINKAGE_PACKAGE_DIR / "linkage_crosswalk.csv"
        if not csv_path.exists() or csv_path.read_text() != artifacts["csv"]:
            mismatches.append("linkage_crosswalk.csv")
        if mismatches:
            raise CommandError(
                "Linkage crosswalk artifacts are stale; run "
                f"'generate_linkage_crosswalk'. Mismatched: {', '.join(mismatches)}"
            )
        self.stdout.write(
            self.style.SUCCESS("Linkage crosswalk artifacts are up to date.")
        )

    # ----------------------------------------------------------------- report
    def _render_csv(self, concepts: dict, proposals: list) -> str:
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            # "\n" so the written file round-trips through read_text() in --check.
            lineterminator="\n",
            fieldnames=[
                "concept",
                "de_field",
                "who_ids",
                "vasa_names",
                "label_de",
                "label_who",
                "confidence",
                "rule",
                "status",
            ],
        )
        writer.writeheader()
        for concept, spec in concepts.items():
            writer.writerow(
                {
                    "concept": concept,
                    "de_field": spec["de_field"] or "",
                    "who_ids": ";".join(spec["who_ids"]),
                    "vasa_names": ";".join(spec["vasa_names"]),
                    "label_de": spec["label_de"],
                    "label_who": spec["label_who"],
                    "confidence": spec["confidence"],
                    "rule": spec["rule"],
                    "status": spec["status"],
                }
            )
        for row in proposals:
            writer.writerow(
                {
                    "concept": "",
                    "de_field": row["de_field"],
                    "who_ids": ";".join(row["who_ids"]),
                    "vasa_names": "",
                    "label_de": row["label_de"],
                    "label_who": row["label_who"],
                    "confidence": row["confidence"],
                    "rule": "label_match",
                    "status": row["status"],
                }
            )
        for de_field, reason in sorted(rules.EXCLUDED_DE_FIELDS.items()):
            writer.writerow(
                {
                    "concept": "",
                    "de_field": _de_attr(de_field),
                    "who_ids": "",
                    "vasa_names": "",
                    "label_de": "",
                    "label_who": "",
                    "confidence": "",
                    "rule": reason,
                    "status": "excluded",
                }
            )
        return buf.getvalue()

    def _format_report(self, **kw) -> str:
        concepts = kw["concepts"]
        by_status: dict = {}
        for spec in concepts.values():
            by_status[spec["status"]] = by_status.get(spec["status"], 0) + 1
        lines = [
            "=== Linkage crosswalk report ===",
            f"Concepts:                      {len(concepts)} {by_status}",
            f"  confirmed (drive linking):   "
            f"{sum(1 for s in concepts.values() if s['status'] == 'confirmed')}",
            f"  absent_in_form (dormant):    "
            f"{[c for c, s in concepts.items() if s['status'] == 'absent_in_form']}",
            f"  proposed (review only):      "
            f"{[c for c, s in concepts.items() if s['status'] == 'proposed']}",
            f"Unseeded DE-field proposals:   "
            f"{[(p['de_field'], p['confidence']) for p in kw['proposals']]}",
            f"Excluded DE fields:            {sorted(rules.EXCLUDED_DE_FIELDS)}",
            "  NOTE: DE_08 is the DECEASED's NRC (maps to Id10073); DE_23 is the",
            "  informant's NRC and is excluded from deceased-identity matching.",
            f"Choice category maps:          {sorted(kw['choice_category_maps'])}",
            f"Geo code maps:                 "
            f"{ {k: len(v) for k, v in kw['geo_code_maps'].items()} }",
            f"Facility CSV geo columns:      "
            f"{sorted(kw['facility_columns'] & _FACILITY_GEO)}",
            f"Crosswalk version:             {kw['version']}",
        ]
        if kw["warnings"]:
            lines.append("Warnings:")
            lines.extend(f"  - {w}" for w in kw["warnings"])
        return "\n".join(lines)
