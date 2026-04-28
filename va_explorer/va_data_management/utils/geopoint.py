import math
import re

GEOPOINT_COMPONENT_RE = re.compile(
    r"^geopoint[-_.](latitude|longitude|altitude|accuracy)$",
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
