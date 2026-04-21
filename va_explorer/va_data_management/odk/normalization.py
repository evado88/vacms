import re
from collections import defaultdict
from typing import Dict, Iterable, Iterator, Mapping, Optional


class ODKColumnCollision(ValueError):
    """Raised when multiple input columns normalize to the same target field."""


def iter_odk_column_aliases(column_name: object) -> Iterator[str]:
    raw = str(column_name).strip()
    if not raw:
        return

    seen = set()

    def emit(value: str):
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            yield cleaned

    for candidate in emit(raw):
        yield candidate

    underscore = raw.replace("-", "_")
    for candidate in emit(underscore):
        yield candidate

    parts = [part for part in re.split(r"[./]", raw) if part]
    if parts:
        tail = parts[-1]
        for candidate in emit(tail):
            yield candidate
        for candidate in emit(tail.replace("-", "_")):
            yield candidate

    dash_tail = raw.rsplit("-", 1)[-1]
    for candidate in emit(dash_tail):
        yield candidate
    for candidate in emit(dash_tail.replace("-", "_")):
        yield candidate

    composite_tail_parts = [part for part in re.split(r"[./]", dash_tail) if part]
    if composite_tail_parts:
        composite_tail = composite_tail_parts[-1]
        for candidate in emit(composite_tail):
            yield candidate
        for candidate in emit(composite_tail.replace("-", "_")):
            yield candidate


def build_odk_rename_map(
    columns: Iterable[object],
    *,
    expected_fields: Iterable[str],
) -> Dict[object, str]:
    expected_by_lower = {field.lower(): field for field in expected_fields}
    rename_map: Dict[object, str] = {}
    collisions = defaultdict(list)

    for column in columns:
        target = resolve_odk_column_name(column, expected_by_lower)
        if not target:
            continue
        rename_map[column] = target
        collisions[target].append(str(column))

    truly_duplicated = {}
    for target, originals in collisions.items():
        unique_originals = list(dict.fromkeys(originals))  # preserve order, deduplicate
        if len(unique_originals) <= 1:
            continue
        # If all colliding columns are case-variants of each other, resolve by
        # keeping the best match: exact match to target field, then lowercase
        # match, then first occurrence. ODK Central exports both `deviceid`
        # (form field) and `DeviceID` (metadata column); this handles that case.
        if len({o.lower() for o in unique_originals}) == 1:
            # Pick the column that exactly equals the target, else the lowercase one,
            # else the first in iteration order
            preferred = next(
                (
                    col
                    for col in rename_map
                    if str(col) in unique_originals and str(col) == target
                ),
                next(
                    (
                        col
                        for col in rename_map
                        if str(col) in unique_originals and str(col) == target.lower()
                    ),
                    next(col for col in rename_map if str(col) in unique_originals),
                ),
            )
            for col in list(rename_map):
                if str(col) in unique_originals and col != preferred:
                    del rename_map[col]
        else:
            truly_duplicated[target] = unique_originals

    if truly_duplicated:
        pieces = ", ".join(
            f"{target}: {', '.join(sorted(set(originals)))}"
            for target, originals in sorted(truly_duplicated.items())
        )
        raise ODKColumnCollision(
            f"Column normalization collision detected for {pieces}"
        )

    return rename_map


def resolve_odk_column_name(
    column: object,
    expected_by_lower: Mapping[str, str],
) -> Optional[str]:
    for alias in iter_odk_column_aliases(column):
        match = expected_by_lower.get(alias.lower())
        if match:
            return match
    return None
