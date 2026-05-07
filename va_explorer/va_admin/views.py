import csv
import io
import json
import os
import re
import traceback
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.management import call_command
from django.http import HttpResponseForbidden, JsonResponse, StreamingHttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date as django_parse_date
from django.views.decorators.http import require_POST

from va_explorer.va_admin.command_registry import (
    ALLOWLIST,
    STANDARD_FILENAME_PLACEHOLDERS,
    data_dir,
    grouped_command_specs,
    is_safe_filename,
    resolve_data_file,
)
from va_explorer.va_admin.models import AdminPanelAlert
from va_explorer.vacms.models import AdminCommandRun

PRODUCTION_SETTINGS_MODULE = "config.settings.production"
DISABLED_IN_PRODUCTION = {"dummify_and_dump"}
MAX_ALERT_FEED_ITEMS = 60
ALERT_RETENTION_DAYS = 30


def _is_production_environment():
    return (
        os.environ.get("DJANGO_SETTINGS_MODULE") == PRODUCTION_SETTINGS_MODULE
        or not settings.DEBUG
    )


def _client_ip(request):
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return str(request.META.get("REMOTE_ADDR", "") or "")


def _alert_color_class(alert):
    now = timezone.now()
    age = now - alert.created_at
    if alert.severity >= AdminPanelAlert.HIGH and age <= timedelta(hours=12):
        return "alert-tag-red"
    if alert.severity >= AdminPanelAlert.MEDIUM and age <= timedelta(days=3):
        return "alert-tag-amber"
    return "alert-tag-yellow"


def _alert_rank_score(alert):
    age_minutes = max(int((timezone.now() - alert.created_at).total_seconds() / 60), 0)
    # Strongly prefer severe alerts, then recency.
    return (alert.severity * 100000) - age_minutes


def _serialize_alert(alert):
    return {
        "id": alert.id,
        "title": alert.title,
        "summary": alert.summary,
        "details": alert.details,
        "category": alert.get_category_display(),
        "severity": alert.get_severity_display(),
        "severity_level": alert.severity,
        "created_at": timezone.localtime(alert.created_at).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "color_class": _alert_color_class(alert),
    }


def _fetch_alert_feed(limit=MAX_ALERT_FEED_ITEMS):
    alerts = list(
        AdminPanelAlert.objects.select_related("user").order_by("-created_at")[
            : max(limit * 2, limit)
        ]
    )
    alerts.sort(key=_alert_rank_score, reverse=True)
    return alerts[:limit]


def _prune_old_alerts(retention_days=ALERT_RETENTION_DAYS):
    cutoff = timezone.now() - timedelta(days=retention_days)
    AdminPanelAlert.objects.filter(created_at__lt=cutoff).delete()


def _record_alert(
    *,
    request,
    category,
    severity,
    title,
    summary="",
    details="",
    context=None,
):
    try:
        _prune_old_alerts()
        AdminPanelAlert.objects.create(
            user=(
                request.user
                if getattr(request.user, "is_authenticated", False)
                else None
            ),
            category=category,
            severity=severity,
            title=title[:255],
            summary=(summary or "")[:512],
            details=(details or "")[:5000],
            context=context or {},
            path=str(request.path or "")[:512],
            ip_address=_client_ip(request)[:64],
            user_agent=str(request.headers.get("user-agent", "") or "")[:512],
        )
    except Exception:
        # Alerting should never break the main workflow.
        return


def user_can_run_admin_commands(user):
    return bool(getattr(user, "is_authenticated", False) and user.is_superuser)


@login_required
def admin_panel_view(request):
    if not user_can_run_admin_commands(request.user):
        _record_alert(
            request=request,
            category=AdminPanelAlert.SECURITY,
            severity=AdminPanelAlert.HIGH,
            title="Unauthorized admin panel access attempt",
            summary="User attempted to open admin panel without required privileges.",
        )
        return HttpResponseForbidden("You are not allowed to access this page.")
    _prune_old_alerts()
    command_groups = grouped_command_specs()
    command_groups_json = {}
    command_categories = []
    is_production = _is_production_environment()
    for category, specs in command_groups.items():
        if is_production:
            specs = [spec for spec in specs if spec.key not in DISABLED_IN_PRODUCTION]
        normal_specs = [spec for spec in specs if not spec.dangerous]
        danger_specs = [spec for spec in specs if spec.dangerous]
        command_categories.append(
            {
                "category": category,
                "normal": normal_specs,
                "danger": danger_specs,
            }
        )
        command_groups_json[category] = [
            {
                "key": spec.key,
                "management_command": spec.management_command,
                "category": spec.category,
                "description": spec.description,
                "safety": spec.safety,
                "timeout_hint": spec.timeout_hint,
                "favorite": spec.favorite,
                "dangerous": spec.dangerous,
                "inputs": [
                    {
                        "type": field.type,
                        "name": field.name,
                        "required": field.required,
                        "help": field.help,
                        "filename_pattern": field.filename_pattern,
                        "standardized_name": field.standardized_name,
                        "allowed_pattern": field.allowed_pattern,
                        "default": field.default,
                        "choices": list(field.choices),
                        "flag": field.flag,
                    }
                    for field in spec.inputs
                ],
            }
            for spec in specs
        ]
    context = {
        "command_groups": command_groups,
        "command_categories": command_categories,
        "command_groups_json": command_groups_json,
        "filename_placeholders": STANDARD_FILENAME_PLACEHOLDERS,
        "alerts_feed": [_serialize_alert(alert) for alert in _fetch_alert_feed()],
    }
    return render(request, "va_admin/admin_panel.html", context)


def _sanitize_input_value(field, raw_value):
    if field.type == "bool":
        return raw_value in (True, "true", "1", 1, "on", "yes")

    if field.type == "file":
        value = "" if raw_value is None else str(raw_value).strip()
        if not value:
            standardized = str(field.standardized_name or "").strip()
            if standardized:
                standardized_candidates = [
                    token.strip()
                    for token in re.split(r"\s+or\s+|\|", standardized)
                    if token.strip()
                ]
                for candidate in standardized_candidates:
                    resolved_candidate = resolve_data_file(candidate)
                    if resolved_candidate.exists():
                        value = candidate
                        break
                if not value:
                    expected = (
                        ", ".join(standardized_candidates)
                        if standardized_candidates
                        else standardized
                    )
                    raise ValueError(
                        f"Standardized file not found in static/data: {expected}"
                    )

        if not value:
            return None

        resolved_file = resolve_data_file(value)
        if not resolved_file.exists():
            raise ValueError(f"File not found in static/data: {value}")
        if field.allowed_pattern and not re.match(field.allowed_pattern, value):
            raise ValueError(
                f"Filename does not match allowed pattern for {field.name}"
            )
        return value

    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if not value:
        return None

    if field.type == "int":
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"Invalid integer for {field.name}") from exc

    if field.type == "date":
        if django_parse_date(value) is None:
            raise ValueError(f"Invalid date for {field.name}. Expected YYYY-MM-DD.")
        return value

    if field.type == "choice":
        if field.choices and value not in field.choices:
            raise ValueError(f"Invalid choice for {field.name}: {value}")
        return value

    return value


def _build_call_command_args(command_id, inputs):
    spec = ALLOWLIST[command_id]
    args = []
    safe_inputs = {}
    filenames = []

    for field in spec.inputs:
        raw_value = inputs.get(field.name)
        value = _sanitize_input_value(field, raw_value)

        if field.required and (value is None or value == ""):
            raise ValueError(f"Missing required input: {field.name}")

        if value is None:
            continue

        safe_inputs[field.name] = value
        if field.type == "bool":
            if value and field.flag:
                args.append(field.flag)
            continue

        if field.type == "file":
            filenames.append(value)
            value = str(resolve_data_file(value))

        if field.flag:
            args.extend([field.flag, str(value)])
        else:
            args.append(str(value))

    return args, safe_inputs, filenames


@login_required
@require_POST
def admin_panel_run_view(request):
    if not user_can_run_admin_commands(request.user):
        _record_alert(
            request=request,
            category=AdminPanelAlert.SECURITY,
            severity=AdminPanelAlert.HIGH,
            title="Unauthorized command execution attempt",
            summary="User attempted to run an admin command without required privileges.",
        )
        return HttpResponseForbidden("You are not allowed to run admin commands.")

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        _record_alert(
            request=request,
            category=AdminPanelAlert.SYSTEM,
            severity=AdminPanelAlert.MEDIUM,
            title="Invalid command payload",
            summary="Admin command run endpoint received invalid JSON payload.",
        )
        return JsonResponse(
            {"ok": False, "output": "Invalid JSON payload."}, status=400
        )

    command_id = str(payload.get("command_id") or payload.get("command") or "").strip()
    inputs = payload.get("inputs", {}) or {}

    if _is_production_environment() and command_id in DISABLED_IN_PRODUCTION:
        _record_alert(
            request=request,
            category=AdminPanelAlert.SECURITY,
            severity=AdminPanelAlert.HIGH,
            title="Blocked production command",
            summary=f"Attempted to run disabled command in production: {command_id}",
            context={"command_id": command_id},
        )
        return JsonResponse(
            {
                "ok": False,
                "output": f"Command '{command_id}' is disabled in production.",
            },
            status=403,
        )

    if command_id not in ALLOWLIST:
        _record_alert(
            request=request,
            category=AdminPanelAlert.SECURITY,
            severity=AdminPanelAlert.HIGH,
            title="Rejected non-allowlisted command",
            summary=f"Command '{command_id}' is not allowlisted.",
            context={"command_id": command_id},
        )
        return JsonResponse(
            {"ok": False, "output": "Command is not allowlisted."}, status=400
        )
    if not isinstance(inputs, dict):
        return JsonResponse(
            {"ok": False, "output": "inputs must be an object."}, status=400
        )

    try:
        cmd_args, safe_inputs, filenames = _build_call_command_args(command_id, inputs)
    except ValueError as exc:
        _record_alert(
            request=request,
            category=AdminPanelAlert.SECURITY,
            severity=AdminPanelAlert.MEDIUM,
            title="Command input validation failed",
            summary=f"Validation failed for command '{command_id}'.",
            details=str(exc),
            context={"command_id": command_id},
        )
        return JsonResponse({"ok": False, "output": str(exc)}, status=400)

    spec = ALLOWLIST[command_id]
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    started_at = timezone.now()
    ok = True

    try:
        call_command(
            spec.management_command,
            *cmd_args,
            stdout=stdout_buffer,
            stderr=stderr_buffer,
        )
    except Exception:
        ok = False
        stderr_buffer.write("\n")
        stderr_buffer.write(traceback.format_exc())

    finished_at = timezone.now()
    duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    output = f"{stdout_buffer.getvalue()}{stderr_buffer.getvalue()}"

    AdminCommandRun.objects.create(
        user=request.user,
        command_id=command_id,
        management_command=spec.management_command,
        inputs=safe_inputs,
        filenames=filenames,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        ok=ok,
        output_excerpt=output[:50000],
        output_full=output[:200000],
    )

    if ok:
        _record_alert(
            request=request,
            category=AdminPanelAlert.INTERACTION,
            severity=AdminPanelAlert.LOW,
            title=f"Command executed: {command_id}",
            summary=f"Completed in {duration_ms} ms.",
            context={"command_id": command_id, "duration_ms": duration_ms, "ok": ok},
        )
    else:
        _record_alert(
            request=request,
            category=AdminPanelAlert.SYSTEM,
            severity=AdminPanelAlert.CRITICAL,
            title=f"Command failed: {command_id}",
            summary=f"Execution failed after {duration_ms} ms.",
            details=output[-4000:],
            context={"command_id": command_id, "duration_ms": duration_ms, "ok": ok},
        )

    return JsonResponse(
        {
            "ok": ok,
            "output": output,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": duration_ms,
        }
    )


@login_required
@require_POST
def admin_panel_validate_file_view(request):
    if not user_can_run_admin_commands(request.user):
        _record_alert(
            request=request,
            category=AdminPanelAlert.SECURITY,
            severity=AdminPanelAlert.HIGH,
            title="Unauthorized file validation attempt",
            summary="User attempted to validate files without required privileges.",
        )
        return HttpResponseForbidden("You are not allowed to validate files.")

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        _record_alert(
            request=request,
            category=AdminPanelAlert.SYSTEM,
            severity=AdminPanelAlert.MEDIUM,
            title="Invalid file validation payload",
            summary="File validation endpoint received invalid JSON payload.",
        )
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    filename = str(payload.get("filename", "")).strip()
    try:
        resolved = resolve_data_file(filename)
    except ValueError as exc:
        _record_alert(
            request=request,
            category=AdminPanelAlert.SECURITY,
            severity=AdminPanelAlert.HIGH,
            title="Unsafe filename rejected",
            summary="Potential path traversal or invalid filename in validation request.",
            details=str(exc),
            context={"filename": filename},
        )
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "exists": resolved.exists(),
            "resolved": str(resolved),
        }
    )


@login_required
@require_POST
def admin_panel_upload_file_view(request):
    if not user_can_run_admin_commands(request.user):
        _record_alert(
            request=request,
            category=AdminPanelAlert.SECURITY,
            severity=AdminPanelAlert.HIGH,
            title="Unauthorized file upload attempt",
            summary="User attempted to upload files without required privileges.",
        )
        return HttpResponseForbidden("You are not allowed to upload files.")

    upload = request.FILES.get("file")
    if upload is None:
        _record_alert(
            request=request,
            category=AdminPanelAlert.SYSTEM,
            severity=AdminPanelAlert.MEDIUM,
            title="Upload rejected: missing file",
            summary="Upload endpoint called without a file payload.",
        )
        return JsonResponse({"ok": False, "error": "No file uploaded."}, status=400)

    requested_name = str(request.POST.get("filename") or upload.name or "").strip()
    if not requested_name:
        return JsonResponse({"ok": False, "error": "Filename is required."}, status=400)
    if not is_safe_filename(requested_name):
        _record_alert(
            request=request,
            category=AdminPanelAlert.SECURITY,
            severity=AdminPanelAlert.HIGH,
            title="Unsafe upload filename rejected",
            summary="Potential path traversal or invalid upload filename.",
            context={"filename": requested_name},
        )
        return JsonResponse(
            {"ok": False, "error": "Invalid filename. Use a basename only."},
            status=400,
        )

    target_dir = data_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = resolve_data_file(requested_name)

    overwrite_raw = request.POST.get("overwrite")
    if overwrite_raw is None:
        overwrite = True
    else:
        overwrite = str(overwrite_raw).lower() in {"1", "true", "yes", "on"}
    if target_path.exists() and not overwrite:
        _record_alert(
            request=request,
            category=AdminPanelAlert.INTERACTION,
            severity=AdminPanelAlert.MEDIUM,
            title="Upload conflict blocked",
            summary=f"File exists and overwrite disabled: {requested_name}",
            context={"filename": requested_name},
        )
        return JsonResponse(
            {"ok": False, "error": f"File already exists: {requested_name}"},
            status=400,
        )

    with open(target_path, "wb") as destination:
        for chunk in upload.chunks():
            destination.write(chunk)

    _record_alert(
        request=request,
        category=AdminPanelAlert.INTERACTION,
        severity=AdminPanelAlert.LOW,
        title="File uploaded",
        summary=f"Uploaded {requested_name}",
        context={"filename": requested_name, "size_bytes": target_path.stat().st_size},
    )

    return JsonResponse(
        {
            "ok": True,
            "filename": requested_name,
            "saved_to": str(target_path),
            "size_bytes": target_path.stat().st_size,
        }
    )


@login_required
def admin_panel_logs_view(request):
    if not user_can_run_admin_commands(request.user):
        return HttpResponseForbidden("You are not allowed to access command logs.")

    runs = AdminCommandRun.objects.select_related("user").order_by("-created_at")[:100]
    context = {"runs": runs}
    return render(request, "va_admin/admin_panel_logs.html", context)


@login_required
def admin_panel_csrf_token_view(request):
    if not user_can_run_admin_commands(request.user):
        return HttpResponseForbidden(
            "You are not allowed to access CSRF token endpoint."
        )
    return JsonResponse({"ok": True, "csrf_token": get_token(request)})


@login_required
def admin_panel_alerts_view(request):
    if not user_can_run_admin_commands(request.user):
        return HttpResponseForbidden("You are not allowed to access alerts.")
    _prune_old_alerts()
    alerts = [_serialize_alert(alert) for alert in _fetch_alert_feed()]
    return JsonResponse({"ok": True, "alerts": alerts})


@login_required
@require_POST
def admin_panel_alert_log_view(request):
    if not user_can_run_admin_commands(request.user):
        return HttpResponseForbidden("You are not allowed to log alerts.")
    _prune_old_alerts()

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    category = (
        str(payload.get("category") or AdminPanelAlert.INTERACTION).strip().lower()
    )
    if category not in {
        AdminPanelAlert.INTERACTION,
        AdminPanelAlert.SECURITY,
        AdminPanelAlert.SYSTEM,
    }:
        category = AdminPanelAlert.INTERACTION

    try:
        severity = int(payload.get("severity") or AdminPanelAlert.MEDIUM)
    except Exception:
        severity = AdminPanelAlert.MEDIUM
    severity = max(AdminPanelAlert.LOW, min(AdminPanelAlert.CRITICAL, severity))

    title = str(payload.get("title") or "Admin panel event").strip()[:255]
    summary = str(payload.get("summary") or "").strip()[:512]
    details = str(payload.get("details") or "").strip()[:5000]
    context = payload.get("context") if isinstance(payload.get("context"), dict) else {}

    _record_alert(
        request=request,
        category=category,
        severity=severity,
        title=title,
        summary=summary,
        details=details,
        context=context,
    )
    return JsonResponse({"ok": True})


# ── User Activity Log ──────────────────────────────────────────────────────────

_ACTIVITY_LOG_PAGE_SIZE = 100
_ACTIVITY_CSV_FIELDS = [
    "timestamp",
    "user_email",
    "action",
    "path",
    "method",
    "ip_address",
    "user_agent",
    "session_key",
    "extra",
]


def _build_activity_queryset(params):
    from va_explorer.users.models import UserActivity

    qs = UserActivity.objects.select_related("user").order_by("-timestamp")

    user_email = (params.get("user_email") or "").strip()
    if user_email:
        qs = qs.filter(user_email__icontains=user_email)

    action = (params.get("action") or "").strip()
    if action:
        qs = qs.filter(action=action)

    start = django_parse_date((params.get("start_date") or "").strip())
    if start:
        qs = qs.filter(timestamp__date__gte=start)

    end = django_parse_date((params.get("end_date") or "").strip())
    if end:
        qs = qs.filter(timestamp__date__lte=end)

    return qs


@login_required
def user_activity_log_view(request):
    if not user_can_run_admin_commands(request.user):
        return HttpResponseForbidden("You are not allowed to view the activity log.")

    from va_explorer.users.models import UserActivity

    qs = _build_activity_queryset(request.GET)

    try:
        page = max(1, int(request.GET.get("page") or 1))
    except (ValueError, TypeError):
        page = 1

    offset = (page - 1) * _ACTIVITY_LOG_PAGE_SIZE
    activities = qs[offset : offset + _ACTIVITY_LOG_PAGE_SIZE]
    total = qs.count()
    total_pages = max(
        1, (total + _ACTIVITY_LOG_PAGE_SIZE - 1) // _ACTIVITY_LOG_PAGE_SIZE
    )

    context = {
        "activities": activities,
        "action_choices": UserActivity.ACTION_CHOICES,
        "filters": request.GET,
        "page": page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
    }
    return render(request, "va_admin/user_activity_log.html", context)


@login_required
def user_activity_log_download_view(request):
    if not user_can_run_admin_commands(request.user):
        return HttpResponseForbidden(
            "You are not allowed to download the activity log."
        )

    qs = _build_activity_queryset(request.GET).values(*_ACTIVITY_CSV_FIELDS)

    def _rows():
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=_ACTIVITY_CSV_FIELDS)
        writer.writeheader()
        yield buf.getvalue()

        for row in qs.iterator():
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=_ACTIVITY_CSV_FIELDS)
            writer.writerow(
                {k: ("" if row[k] is None else row[k]) for k in _ACTIVITY_CSV_FIELDS}
            )
            yield buf.getvalue()

    filename = f"user_activity_{timezone.now().strftime('%Y%m%dT%H%M%SZ')}.csv"
    response = StreamingHttpResponse(_rows(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
