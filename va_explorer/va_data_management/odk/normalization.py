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

    duplicated = {
        target: originals
        for target, originals in collisions.items()
        if len(set(originals)) > 1
    }
    if duplicated:
        pieces = ", ".join(
            f"{target}: {', '.join(sorted(set(originals)))}"
            for target, originals in sorted(duplicated.items())
        )
        raise ODKColumnCollision(f"Column normalization collision detected for {pieces}")

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
