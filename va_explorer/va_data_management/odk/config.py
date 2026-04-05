from typing import Any, Dict, Iterable, List, Optional

from django.conf import settings


def normalize_form_config(
    config: Dict[str, Any],
    *,
    default_project_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    form_id = config.get("form_id")
    if not form_id:
        return None

    project_id = config.get("project_id") or default_project_id
    return {
        "form_id": form_id,
        "form_name": config.get("form_name"),
        "project_id": project_id,
        "enabled": config.get("enabled", True),
        "schedule_key": config.get("schedule_key", "default"),
        "fetch_expand": config.get("fetch_expand"),
        "watermark_field": config.get("watermark_field"),
    }


def get_odk_form_configs(
    *,
    default_project_id: Optional[int] = None,
    configs: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    source = configs if configs is not None else getattr(settings, "ODK_PULL_FORMS", [])
    normalized = []
    for config in source:
        item = normalize_form_config(
            config, default_project_id=default_project_id or getattr(settings, "ODK_DEFAULT_PROJECT_ID", None)
        )
        if item:
            normalized.append(item)
    return normalized


def resolve_odk_form_configs(
    *,
    form_ids: Optional[Iterable[str]] = None,
    project_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    default_project_id = project_id or getattr(settings, "ODK_DEFAULT_PROJECT_ID", None)
    if form_ids:
        # Look up the full config from ODK_PULL_FORMS so that form_name, fetch_expand etc.
        # are preserved when the same form_id is passed explicitly (e.g. from the CLI).
        known = {
            c["form_id"]: c
            for c in get_odk_form_configs(default_project_id=default_project_id)
        }
        return [
            known.get(form_id) or {
                "form_id": form_id,
                "form_name": None,
                "project_id": default_project_id,
                "enabled": True,
                "schedule_key": "manual",
                "fetch_expand": None,
                "watermark_field": None,
            }
            for form_id in form_ids
            if form_id
        ]

    return get_odk_form_configs(default_project_id=default_project_id)
