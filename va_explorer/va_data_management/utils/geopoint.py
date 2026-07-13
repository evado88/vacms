import math
import re

GEOPOINT_COMPONENT_RE = re.compile(
    r"^geopoint[-_.](latitude|longitude|altitude|accuracy)$",
    re.IGNORECASE,
)

_PREFIX_COMPONENT_RE = re.compile(
    r"^(.+)[-_.](?P<component>latitude|longitude|altitude|accuracy)$",
    re.IGNORECASE,
)

_BARE_COMPONENT_RE = re.compile(
    r"^(?P<component>latitude|longitude|altitude|accuracy)$",
    re.IGNORECASE,
)


def geopoint_component_name(column):
    match = GEOPOINT_COMPONENT_RE.match(str(column))
    if not match:
        return None
    return match.group(1).lower()


def find_geopoint_columns(columns):
    geopoint_columns = {}
    for column in columns:
        component = geopoint_component_name(column)
        if component and component not in geopoint_columns:
            geopoint_columns[component] = column
    return geopoint_columns


def _has_meaningful_value(value):
    if value is None:
        return False

    try:
        if isinstance(value, float) and math.isnan(value):
            return False
    except TypeError:
        return False

    normalized = str(value).strip().replace("-", "_").lower()
    return normalized not in {"", "nan", "none", "null", "<na>", "nat"}


def _format_geopoint_number(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Geopoint components must be finite numbers.")
    if number.is_integer():
        return str(int(number))
    return str(number)


def compose_geopoint(row):
    try:
        geopoint_columns = find_geopoint_columns(row.keys())
        latitude = row.get(geopoint_columns.get("latitude"))
        longitude = row.get(geopoint_columns.get("longitude"))

        if not (_has_meaningful_value(latitude) and _has_meaningful_value(longitude)):
            return None

        values = [
            _format_geopoint_number(latitude),
            _format_geopoint_number(longitude),
        ]
        for component in ("altitude", "accuracy"):
            value = row.get(geopoint_columns.get(component))
            values.append(
                _format_geopoint_number(value) if _has_meaningful_value(value) else ""
            )
        return " ".join(values).rstrip()
    except Exception:
        return None


def parse_geopoint(value):
    """Parse an ODK geopoint string into structured coordinates.

    Accepts ``"lat lon"``, ``"lat lon alt"`` or ``"lat lon alt acc"`` (whitespace-separated).
    Returns ``{"latitude": float, "longitude": float, "altitude": float|None, "accuracy": float|None}``
    on success, or ``None`` for empty/invalid input or out-of-range coordinates.
    """
    if not _has_meaningful_value(value):
        return None
    try:
        tokens = str(value).strip().split()
    except Exception:
        return None
    if len(tokens) < 2:
        return None
    try:
        lat = float(tokens[0])
        lon = float(tokens[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None
    result = {"latitude": lat, "longitude": lon, "altitude": None, "accuracy": None}
    if len(tokens) >= 3:
        try:
            alt = float(tokens[2])
            result["altitude"] = alt if math.isfinite(alt) else None
        except (TypeError, ValueError):
            result["altitude"] = None
    if len(tokens) >= 4:
        try:
            acc = float(tokens[3])
            result["accuracy"] = acc if math.isfinite(acc) else None
        except (TypeError, ValueError):
            result["accuracy"] = None
    return result


def find_geopoint_columns_for_prefix(columns, source_prefix):
    """Return {component: column_name} for GPS split columns matching source_prefix.

    Falls back to bare component names (``Latitude``/``Longitude``/...) when no
    prefixed columns match: the ODK pull strips group prefixes from column names
    (``PE-25-Latitude`` -> ``Latitude``) before the geopoint merge runs. A form
    with two geopoints would be ambiguous after that strip, so the fallback
    assumes at most one geopoint per form.
    """
    target = source_prefix.lower().replace("_", "-")
    found = {}
    for col in columns:
        match = _PREFIX_COMPONENT_RE.match(str(col))
        if not match:
            continue
        prefix_norm = match.group(1).lower().replace("_", "-")
        if prefix_norm != target:
            continue
        component = match.group("component").lower()
        if component not in found:
            found[component] = str(col)
    if found:
        return found
    for col in columns:
        match = _BARE_COMPONENT_RE.match(str(col))
        if not match:
            continue
        component = match.group("component").lower()
        if component not in found:
            found[component] = str(col)
    return found


def compose_geopoint_for_prefix(row, source_prefix):
    """Compose an ODK geopoint string from split columns for an arbitrary prefix."""
    cols = find_geopoint_columns_for_prefix(row.keys(), source_prefix)
    if not cols:
        return None
    lat_col = cols.get("latitude")
    lon_col = cols.get("longitude")
    lat = row.get(lat_col) if lat_col else None
    lon = row.get(lon_col) if lon_col else None
    if not (_has_meaningful_value(lat) and _has_meaningful_value(lon)):
        return None
    try:
        values = [_format_geopoint_number(lat), _format_geopoint_number(lon)]
        for component in ("altitude", "accuracy"):
            col = cols.get(component)
            val = row.get(col) if col else None
            values.append(
                _format_geopoint_number(val) if _has_meaningful_value(val) else ""
            )
        return " ".join(values).rstrip()
    except Exception:
        return None


def merge_geopoint_into(df, *, source_prefix, target_field):
    """Merge split GPS component columns into a single ODK-format target field.

    Only fills target_field where it is currently empty (anti-clobber).
    Drops the source component columns from the returned DataFrame.
    """
    cols = find_geopoint_columns_for_prefix(df.columns, source_prefix)
    if not cols:
        return df

    df = df.copy()
    if target_field not in df.columns:
        df[target_field] = None

    composed = df.apply(
        lambda r: compose_geopoint_for_prefix(r.to_dict(), source_prefix),
        axis=1,
    )

    needs_fill = df[target_field].apply(lambda v: not _has_meaningful_value(v))
    has_new_value = composed.notna()
    df.loc[needs_fill & has_new_value, target_field] = composed[
        needs_fill & has_new_value
    ]

    return df.drop(columns=list(cols.values()))
