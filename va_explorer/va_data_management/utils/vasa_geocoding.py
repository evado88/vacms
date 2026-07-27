"""Resolve a VASA record's coded geography to the location trees.

VASA submissions carry geography as raw census codes (``province='02'``,
``district='0205'``, ``ward='0205107'``, ``ea='02051072009'``) and, unlike
:class:`VerbalAutopsy`, arrive with **no** ``location`` or ``cluster`` foreign key.
Every location-scoped query in the app matches on names / FKs, so an unresolved
VASA record silently drops out of exports (both the form's location filter and a
user's ``location_restrictions``) and out of the location-scoped dashboards.

This module turns those codes into FK links so a VASA record behaves like a VA
record everywhere:

* ``cluster`` (``SRSClusterLocation``) — drives the export **form** filter. The
  EA-level node's ``name`` *is* the 11-digit geoid, so ``VASA.ea`` joins to it
  directly. The SRS tree is the surveillance subset; a record outside it simply
  gets no cluster (it legitimately can't be filtered to a tree it isn't in).
* ``location`` (``Location``) — drives ``location_restrictions`` scoping. The
  national Location tree only descends to district, so we decode the VASA
  province/district codes to names via :class:`SRSEAPopulationTarget` (the
  national census crosswalk) and match them into the tree, preferring the
  deepest (district) level.

Census names and Location-tree names diverge in format
(``'CENTRAL'`` vs ``'Central Province'``, ``'KAPIRI MPOSHI'`` vs
``'Kapirimposhi District'``), so matching runs through :func:`_normalize`.
Genuine spelling mismatches leave ``location`` unset and are reported via
``unresolved_level`` so the loader can flag — never silently mismap.

The lookup tables are built once per :class:`VasaGeocoder` instance (reference
data is static for the duration of a load run / backfill), never captured at
import time.
"""

import re
from dataclasses import dataclass

from ..models import Location, SRSClusterLocation, SRSEAPopulationTarget

# Coarse-to-fine so the deepest resolvable level wins.
_LOCATION_LEVELS = ("province", "district")
_ALL_LEVELS = ("province", "district", "constituency", "ward", "ea")

# The coded geography fields carried on a VASA record / loader record dict.
GEO_CODE_FIELDS = _ALL_LEVELS


def geo_codes(source):
    """Pull the geography codes from a loader record dict or a VASA instance."""
    if isinstance(source, dict):
        return {level: source.get(level) for level in GEO_CODE_FIELDS}
    return {level: getattr(source, level, None) for level in GEO_CODE_FIELDS}


_SUFFIX_RE = re.compile(r"\s+(province|district)\s*$", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")


def _normalize(name):
    """Collapse a place name to a comparison key across the naming schemes.

    Strips a trailing "Province"/"District" word, casefolds, and drops all
    non-alphanumerics so ``'KAPIRI MPOSHI'`` and ``'Kapirimposhi District'`` and
    ``'North_Western Province'``/``'NORTH WESTERN'`` compare equal.
    """
    if not name:
        return ""
    text = _SUFFIX_RE.sub("", str(name).strip())
    return _NON_ALNUM_RE.sub("", text.casefold())


@dataclass(frozen=True)
class GeoResolution:
    """Result of resolving one VASA record's coded geography.

    ``unresolved_level`` is the finest level for which the record carried a code
    but no ``location`` link could be made (``None`` when a link was made or the
    record carried no geography at all).
    """

    location_id: int | None = None
    cluster_id: int | None = None
    unresolved_level: str | None = None


class VasaGeocoder:
    """Builds the code/name lookup tables once, then resolves records."""

    def __init__(self):
        self._ea_to_cluster = {
            str(name): pk
            for pk, name in SRSClusterLocation.objects.filter(
                location_type="ea"
            ).values_list("pk", "name")
            if name
        }

        # {(level, census_code): census_name} from the national census table.
        self._code_to_name = {}
        target_fields = (
            ("province", "province_code", "province_name"),
            ("district", "district_code", "district_name"),
            ("constituency", "constituency_code", "constituency_name"),
            ("ward", "ward_code", "ward_name"),
        )
        rows = SRSEAPopulationTarget.objects.values(
            *(field for _level, code, name in target_fields for field in (code, name))
        ).distinct()
        for row in rows:
            for level, code_field, name_field in target_fields:
                code = str(row.get(code_field) or "").strip()
                name = str(row.get(name_field) or "").strip()
                if code and name:
                    self._code_to_name.setdefault((level, code), name)

        # {(level, normalized_name): [Location pk, ...]} — a list so ambiguous
        # names (same district name under two provinces) can be detected.
        self._name_to_location = {}
        for pk, level, name in Location.objects.filter(
            location_type__in=_LOCATION_LEVELS
        ).values_list("pk", "location_type", "name"):
            key = (str(level).lower(), _normalize(name))
            if key[1]:
                self._name_to_location.setdefault(key, []).append(pk)

    def _location_for(self, level, code):
        """Resolve one (level, census code) to a unique Location pk, or None."""
        name = self._code_to_name.get((level, str(code).strip()))
        if not name:
            return None
        matches = self._name_to_location.get((level, _normalize(name)))
        # Skip ambiguous matches (name appears under multiple parents); the
        # coarser level will still resolve, and the record is flagged if not.
        if matches and len(matches) == 1:
            return matches[0]
        return None

    def resolve(self, codes):
        """Resolve a mapping of level -> raw code to FK links.

        ``codes`` accepts any subset of ``province/district/constituency/ward/ea``.
        """
        codes = {level: str(codes.get(level) or "").strip() for level in _ALL_LEVELS}

        cluster_id = self._ea_to_cluster.get(codes["ea"]) if codes["ea"] else None

        location_id = None
        for level in reversed(_LOCATION_LEVELS):  # district first, then province
            if codes[level]:
                location_id = self._location_for(level, codes[level])
                if location_id is not None:
                    break

        unresolved_level = None
        if location_id is None:
            # Flag the finest level the record actually carried a code for.
            for level in reversed(_ALL_LEVELS):
                if codes[level]:
                    unresolved_level = level
                    break

        return GeoResolution(
            location_id=location_id,
            cluster_id=cluster_id,
            unresolved_level=unresolved_level,
        )
