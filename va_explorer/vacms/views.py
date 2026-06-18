import csv
import json
import subprocess
import sys
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import (
    HttpResponse,
    HttpResponseForbidden,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    UpdateView,
)  # new

from va_explorer.utils.mixins import AjaxListTableMixin, CsvDownloadPermissionMixin
from va_explorer.va_data_management.models import Death
from va_explorer.vacms.admin_panel import (
    ADMINS_GROUP_NAME,
    ALLOWLIST,
    STANDARD_FILENAME_PLACEHOLDERS,
    grouped_command_specs,
    resolve_data_file,
)

# Create your views here.
from va_explorer.vacms.cmsmodels.events import Event
from va_explorer.vacms.forms.forms import ScheduleDeathForm, VAInterviewStatusForm
from va_explorer.vacms.notifications import (
    ensure_va_schedule_message,
    remove_va_schedule_message,
)
from va_explorer.vacms.permissions import (
    user_can_manage_va_schedule,
    user_can_view_va_schedule,
)


def user_can_access_admin_panel(user):
    return bool(
        getattr(user, "is_authenticated", False)
        and (user.is_superuser or user.groups.filter(name=ADMINS_GROUP_NAME).exists())
    )


# event
@method_decorator(login_required, name="dispatch")
class EventListView(AjaxListTableMixin, ListView):
    model = Event
    template_name = "va_cms/event_list.html"
    list_table_template = "va_cms/partials/_event_list_table.html"
    component_template = "va_cms/partials/_event_list_component.html"
    paginate_by = 25

    _SORT_FIELDS = {
        "id": "id",
        "province": "province",
        "district": "district",
        "va_interview_status": "va_interview_status",
        "interview_scheduled_date": "interview_scheduled_date",
        "interviewer": "va_interview_staff__id",
    }

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .select_related(
                "death",
                "pregnancy",
                "pregnancy_outcome",
                "household",
                "va_interview_staff",
            )
        )
        queryset = self._apply_filters(queryset)
        order_by_raw = self.request.GET.get("order_by", "interview_scheduled_date")
        direction = "-" if order_by_raw.startswith("-") else ""
        db_field = self._SORT_FIELDS.get(
            order_by_raw.lstrip("-"), "interview_scheduled_date"
        )
        return queryset.order_by(direction + db_field, "id")

    def dispatch(self, request, *args, **kwargs):
        if not user_can_view_va_schedule(request.user):
            return HttpResponseForbidden("You are not allowed to view VA schedules.")
        return super().dispatch(request, *args, **kwargs)

    #: status tabs shown on the event list, in display order
    STATUS_TABS = (
        (Event.VAInterviewStatus.SCHEDULED, "Scheduled"),
        (Event.VAInterviewStatus.COMPLETED, "Completed"),
        (Event.VAInterviewStatus.NOT_DONE, "Not Done"),
        (Event.VAInterviewStatus.POSTPONED, "Postponed"),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filters"] = self._filters_context
        context["user_can_manage_schedule"] = user_can_manage_va_schedule(
            self.request.user
        )
        # Per-tab counts respect the geo/interviewer filters but not the status
        # itself, so each tab shows its own total under the active filters.
        base = self._apply_geo_interviewer(Event.objects.all(), self._filters_context)
        counts = {
            row["va_interview_status"]: row["c"]
            for row in base.values("va_interview_status").annotate(c=Count("id"))
        }
        active_status = self._filters_context["interview_status"]
        context["status_tabs"] = [
            {
                "value": value,
                "label": label,
                "count": counts.get(value, 0),
                "active": value == active_status,
            }
            for value, label in self.STATUS_TABS
        ]
        context["active_status"] = active_status
        return context

    def _apply_geo_interviewer(self, queryset, filters):
        if filters["province"]:
            queryset = queryset.filter(
                Q(death__province__icontains=filters["province"])
                | Q(pregnancy__province__icontains=filters["province"])
                | Q(pregnancy_outcome__province__icontains=filters["province"])
                | Q(household__province__icontains=filters["province"])
            )
        if filters["district"]:
            queryset = queryset.filter(
                Q(death__district__icontains=filters["district"])
                | Q(pregnancy__district__icontains=filters["district"])
                | Q(pregnancy_outcome__district__icontains=filters["district"])
                | Q(household__district__icontains=filters["district"])
            )
        if filters["interviewer"]:
            queryset = queryset.filter(
                Q(va_interview_staff__name__icontains=filters["interviewer"])
                | Q(va_interview_staff__email__icontains=filters["interviewer"])
            )
        return queryset

    def _apply_filters(self, queryset):
        params = self.request.GET
        # Default to the Scheduled tab so completed/not-done/postponed events
        # drop off the default list once their status changes.
        filters = {
            "province": params.get("province", "").strip(),
            "district": params.get("district", "").strip(),
            "interview_status": params.get("interview_status", "").strip()
            or Event.VAInterviewStatus.SCHEDULED,
            "interviewer": params.get("interviewer", "").strip(),
        }

        queryset = self._apply_geo_interviewer(queryset, filters)

        if filters["interview_status"] == "unscheduled":
            queryset = queryset.filter(
                Q(event_status__lt=Event.EventStatus.VA_INTERVIEW_SCHEDULED)
                | Q(event_status__isnull=True)
            )
        else:
            queryset = queryset.filter(va_interview_status=filters["interview_status"])

        self._filters_context = filters
        return queryset


class EventListCsvExport(CsvDownloadPermissionMixin, EventListView):
    """Download the (filtered) event list as a CSV.

    Subclasses EventListView so it reuses the same permission check
    (dispatch), filtering (_apply_filters), and sort handling — keeping the
    export exactly in sync with what the list page shows.
    """

    _CSV_COLUMNS = (
        ("id", "ID"),
        ("deceased_name", "Deceased Name"),
        ("death_id", "Death ID"),
        ("key", "Key"),
        ("interviewer", "Interviewer"),
        ("province", "Province"),
        ("district", "District"),
        ("event_type", "Event Type"),
        ("event_status", "Event Status"),
        ("va_interview_status", "VA Interview Status"),
        ("interview_scheduled_date", "VA Interview Scheduled Date"),
        ("interview_complete_date", "VA Interview Complete Date"),
        ("supervisor", "Supervisor"),
        ("respondent_name", "Respondent Name"),
        ("respondent_phone", "Respondent Phone"),
        ("va_not_done_reason", "Not Done Reason"),
    )

    def get(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        filename = timezone.now().strftime("scheduled_vas_%Y%m%d_%H%M%S.csv")
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        writer = csv.writer(response)
        writer.writerow([label for _, label in self._CSV_COLUMNS])
        for event in queryset.iterator():
            writer.writerow(self._row_for(event))
        return response

    @staticmethod
    def _row_for(event):
        staff = event.va_interview_staff
        interviewer = (
            (staff.name or staff.get_full_name() or staff.email)
            if staff
            else "Unassigned"
        )
        death = event.death
        supervisor = event.supervisor or (death.supervisor if death else "") or ""
        respondent_name = event.respondent_name or (death.DE_19 if death else "") or ""
        respondent_phone = (
            event.respondent_phone or (death.DE_26 if death else "") or ""
        )
        return [
            event.id,
            death.DE_03 if death else "",
            event.death_id or "",
            death.key if death else "",
            interviewer,
            event.record_province or "",
            event.record_district or "",
            event.get_event_type_display() if event.event_type is not None else "",
            event.get_event_status_display() if event.event_status is not None else "",
            event.get_va_interview_status_display() or "",
            event.interview_scheduled_date or "",
            event.interview_complete_date or "",
            supervisor,
            respondent_name,
            respondent_phone,
            (
                event.get_va_not_done_reason_display()
                if event.va_not_done_reason
                else ""
            ),
        ]


class EventListScheduledView(ListView):
    model = Event
    template_name = "va_cms/event_scheduled_list.html"

    def get_queryset(self):
        queryset = super().get_queryset()  # Start with the default queryset
        queryset = queryset.filter(event_status=1)

        return queryset


class EventListCompletedView(ListView):
    model = Event
    template_name = "va_cms/event_completed_list.html"

    def get_queryset(self):
        queryset = super().get_queryset()  # Start with the default queryset
        queryset = queryset.filter(event_status=5)

        return queryset


class EventCreateView(CreateView):
    model = Event
    fields = [
        "event_type",
        "event_status",
        "data_collection_staff",
        "data_collect_proposed_date",
        "data_collect_contact_name",
        "data_collect_contact_tel",
        "data_collect_comments",
    ]
    template_name = "va_cms/event_create.html"


@login_required
def EventCreateDeathView(request, death):
    if not user_can_manage_va_schedule(request.user):
        return HttpResponseForbidden("You are not allowed to manage VA schedules.")

    if request.method == "POST":
        form = ScheduleDeathForm(request.POST)

        if form.is_valid():
            # Process the valid data from form.cleaned_data
            minterview_scheduled_date = form.cleaned_data["interview_scheduled_date"]
            mva_interview_staff = form.cleaned_data["va_interview_staff"]
            minterview_contact_name = form.cleaned_data["interview_contact_name"]
            minterview_contact_tel = form.cleaned_data["interview_contact_tel"]
            minterview_comments = form.cleaned_data["interview_comments"]

            deathDetail = Death.objects.get(id=death)

            newEvent = Event.objects.create(
                death=deathDetail,
                event_type=3,
                event_status=4,
                interview_scheduled_date=minterview_scheduled_date,
                va_interview_staff=mva_interview_staff,
                interview_contact_name=minterview_contact_name,
                interview_contact_tel=minterview_contact_tel,
                interview_comments=minterview_comments,
            )

            newEvent.save()

            ensure_va_schedule_message(newEvent, request)

            deathDetail.eventid = newEvent.id
            deathDetail.save()

            return redirect(reverse("va_data_management:deaths"))

            """
              return HttpResponse(
                json.dumps(context), status=200, content_type="application/json"
            )

            """
            # return render(request, "va_cms/event_death_create.html", context)
        else:
            # Form is invalid, re-render with errors
            return render(request, "va_cms/event_death_create.html", {"form": form})
    else:
        initials = {"id": death}

        try:
            deathDetail = Death.objects.get(id=death)
            initials = {
                "id": death,
                "name": deathDetail.DE_03,
                "dob": deathDetail.DE_04,
                "sex": deathDetail.DE_05,
                "dod": deathDetail.DE_06,
                # Prepopulate contact name and phone from DE_28 and DE_29
                "interview_contact_name": deathDetail.DE_28 or "",
                "interview_contact_tel": deathDetail.DE_29 or "",
            }
        except Death.DoesNotExist:
            initials = {}

        return render(
            request,
            "va_cms/event_death_create.html",
            {"form": ScheduleDeathForm(initial=initials)},
        )


class EventUpdateView(UpdateView):
    model = Event
    fields = [
        "event_type",
        "respondent_name",
        "respondent_phone",
        "comment",
        "deviceid",
        "instanceid",
        "today",
        "start",
        "province",
        "district",
        "constituency",
        "ward",
        "ea",
        "supervisor",
        "enumerator",
        "form_version",
    ]
    template_name = "va_cms/event_update.html"


class EventDetailView(DetailView):
    model = Event
    fields = "__all__"
    template_name = "va_cms/event_detail.html"

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.method.lower() == "post" and not self._can_update_status(
            self.object
        ):
            return HttpResponseForbidden("You are not allowed to update this event.")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.object = getattr(self, "object", self.get_object())
        form = VAInterviewStatusForm(request.POST, instance=self.object)
        if form.is_valid():
            updated_event = form.save()
            if updated_event.va_interview_status in (
                Event.VAInterviewStatus.COMPLETED,
                Event.VAInterviewStatus.NOT_DONE,
            ):
                remove_va_schedule_message(updated_event)
            messages.success(request, "VA interview status updated.")
            return redirect("cms-event-detail", pk=self.object.pk)

        context = self.get_context_data(status_form=form)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event = self.object
        death_record = event.death
        context["death_record"] = death_record
        context["is_death_event"] = (
            bool(death_record) and event.event_type == Event.EventType.DEATH
        )
        interviewer = event.va_interview_staff
        context["interviewer"] = interviewer
        if interviewer:
            context["interviewer_name"] = (
                interviewer.name or interviewer.get_full_name() or interviewer.email
            )
            context["interviewer_contact"] = (
                interviewer.mobile1 or interviewer.mobile2 or interviewer.email
            )
            context["interviewer_scheduled_count"] = Event.objects.filter(
                va_interview_staff=interviewer,
                va_interview_status=Event.VAInterviewStatus.SCHEDULED,
            ).count()
        else:
            context["interviewer_name"] = None
            context["interviewer_contact"] = None
            context["interviewer_scheduled_count"] = None
        context["status_form"] = kwargs.get(
            "status_form", VAInterviewStatusForm(instance=event)
        )
        context["can_update_va_status"] = self._can_update_status(event)
        context["user_can_manage_schedule"] = user_can_manage_va_schedule(
            self.request.user
        )
        return context

    def _can_update_status(self, event):
        user = self.request.user
        if not user.is_authenticated:
            return False
        if event.va_interview_staff_id and user.id == event.va_interview_staff_id:
            return True
        return user_can_manage_va_schedule(user)


class EventScheduleDataCollectionView(UpdateView):
    model = Event
    fields = [
        "event_type",
        "event_status",
        "household",
        "data_collection_staff",
        "data_collect_proposed_date",
        "data_collect_scheduled_date",
        "data_collect_contact_name",
        "data_collect_contact_tel",
        "data_collect_comments",
    ]
    template_name = "va_cms/event_schedule_data.html"

    def form_valid(self, form):
        form.instance.event_status = 1

        # Call the parent's form_valid to save the object and handle redirection
        response = super().form_valid(form)

        return response


@method_decorator(login_required, name="dispatch")
class VACMSAdminPanelView(View):
    template_name = "va_cms/admin_panel.html"

    def get(self, request):
        if not user_can_access_admin_panel(request.user):
            return HttpResponseForbidden("You are not allowed to access this page.")

        command_groups = grouped_command_specs()
        context = {
            "command_groups": command_groups,
            "filename_placeholders": STANDARD_FILENAME_PLACEHOLDERS,
        }
        return render(request, self.template_name, context)


def _build_command_arguments(command_name, inputs):
    spec = ALLOWLIST[command_name]
    args = []

    for field in spec.inputs:
        raw_value = inputs.get(field.name)

        if field.type == "bool":
            if raw_value in (True, "true", "1", 1, "on") and field.flag:
                args.append(field.flag)
            continue

        if field.required and not raw_value:
            raise ValueError(f"Missing required input: {field.name}")

        if not raw_value:
            continue

        value = str(raw_value).strip()
        if field.choices and value not in field.choices:
            raise ValueError(f"Invalid value for {field.name}: {value}")

        if field.type == "file":
            resolved_file = resolve_data_file(value)
            if not resolved_file.exists():
                raise ValueError(f"File not found in static/data: {value}")
            value = str(resolved_file)

        if field.flag:
            args.extend([field.flag, value])
        else:
            args.append(value)
    return args


@login_required
@require_POST
def vacms_admin_command_stream(request):
    if not user_can_access_admin_panel(request.user):
        return HttpResponseForbidden("You are not allowed to run admin commands.")

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    command_name = str(payload.get("command", "")).strip()
    inputs = payload.get("inputs", {}) or {}
    if not isinstance(inputs, dict):
        return JsonResponse({"error": "inputs must be a JSON object."}, status=400)

    if command_name not in ALLOWLIST:
        return JsonResponse({"error": "Command is not allowlisted."}, status=400)

    try:
        cmd_args = _build_command_arguments(command_name, inputs)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    python_exec = sys.executable
    base_dir = Path(settings.BASE_DIR)
    manage_py = str(base_dir / "manage.py")
    spec = ALLOWLIST[command_name]
    cmd = [python_exec, manage_py, spec.management_command, *cmd_args]

    def stream_output():
        yield f"$ {' '.join(cmd)}\n"
        yield "-" * 80 + "\n"

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(base_dir),
            bufsize=1,
        )

        try:
            assert process.stdout is not None
            yield from process.stdout
        finally:
            process.wait()
            yield "-" * 80 + "\n"
            yield f"[exit_code={process.returncode}] {spec.management_command}\n"

    response = StreamingHttpResponse(
        stream_output(), content_type="text/plain; charset=utf-8"
    )
    response["X-Accel-Buffering"] = "no"
    return response


@method_decorator(login_required, name="dispatch")
class EventScheduleVAInterviewView(UpdateView):
    model = Event
    fields = [
        "household",
        "interview_scheduled_date",
        "va_interview_staff",
        "interview_contact_name",
        "interview_contact_tel",
        "interview_comments",
    ]
    template_name = "va_cms/event_schedule_va.html"

    def dispatch(self, request, *args, **kwargs):
        if not user_can_manage_va_schedule(request.user):
            return HttpResponseForbidden("You are not allowed to manage VA schedules.")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.event_status = 4
        form.instance.va_interview_status = Event.VAInterviewStatus.SCHEDULED
        form.instance.va_not_done_reason = None
        form.instance.va_not_done_other = None

        # Call the parent's form_valid to save the object and handle redirection
        response = super().form_valid(form)

        return response


class EventLinkNone(forms.ModelForm):
    class Meta:
        model = Event
        fields = [
            "data_collect_complete_date",
            "data_collect_contact_name",
            "data_collect_contact_tel",
            "data_collect_comments",
        ]


class EventLinkDeath(forms.ModelForm):
    class Meta:
        model = Event
        fields = [
            "data_collect_complete_date",
            "data_collect_contact_name",
            "data_collect_contact_tel",
            "data_collect_comments",
            "death",
        ]


class EventLinkPregnancy(forms.ModelForm):
    class Meta:
        model = Event
        fields = [
            "data_collect_complete_date",
            "data_collect_contact_name",
            "data_collect_contact_tel",
            "data_collect_comments",
            "pregnancy",
        ]


class EventLinkPregnancyOutcome(forms.ModelForm):
    class Meta:
        model = Event
        fields = [
            "data_collect_complete_date",
            "data_collect_contact_name",
            "data_collect_contact_tel",
            "data_collect_comments",
            "pregnancy_outcome",
        ]


class EventLinkDataView(UpdateView):
    model = Event
    template_name = "va_cms/event_link.html"

    def form_valid(self, form):
        form.instance.event_status = 2

        # close event if its not a death
        if self.object.event_type != 3:
            form.instance.completion_date = timezone.now().strftime("%Y-%m-%d")

        # Call the parent's form_valid to save the object and handle redirection
        response = super().form_valid(form)

        return response

    def get_form_class(self):
        obj = self.get_object()  # Get the instance being updated

        if obj.event_type == 1:
            return EventLinkPregnancy

        elif obj.event_type == 2:
            return EventLinkPregnancyOutcome

        elif obj.event_type == 3:
            return EventLinkDeath
        else:
            return EventLinkNone


class EventLinkVA(UpdateView):
    model = Event
    fields = [
        "interview_complete_date",
        "va",
        "interview_comments",
    ]

    template_name = "va_cms/event_complete.html"

    def form_valid(self, form):
        form.instance.event_status = 5
        form.instance.va_interview_status = Event.VAInterviewStatus.COMPLETED
        form.instance.va_not_done_reason = None
        form.instance.va_not_done_other = None
        form.instance.completion_date = timezone.now().strftime("%Y-%m-%d")
        # Call the parent's form_valid to save the object and handle redirection
        response = super().form_valid(form)

        return response
