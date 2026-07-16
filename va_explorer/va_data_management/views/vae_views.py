import re

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, OuterRef, Subquery, TextField
from django.db.models import Value as V
from django.db.models.functions import Concat
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import (
    DeleteView,
    DetailView,
    ListView,
    RedirectView,
    TemplateView,
    UpdateView,
)
from django.views.generic.detail import SingleObjectMixin

from va_explorer.utils.mixins import CustomAuthMixin
from va_explorer.va_data_management.filters import VAFilter, VASAFilter
from va_explorer.va_data_management.forms import VASAForm, VerbalAutopsyForm
from va_explorer.va_data_management.models import (
    VASA,
    CauseCodingIssue,
    Location,
    ODKFormChoice,
    VerbalAutopsy,
)
from va_explorer.va_data_management.tasks import run_coding_algorithms
from va_explorer.va_data_management.utils.date_parsing import parse_date
from va_explorer.va_data_management.utils.loading import get_va_summary_stats
from va_explorer.va_data_management.utils.validate import validate_vas_for_dashboard
from va_explorer.va_data_management.vasa.value_decode import sa_display_value

_VA_GEOGRAPHY_FORM_NAME = "pregnancy"
_VA_GEOGRAPHY_FIELDS = ("province", "district", "constituency", "ward")


def _normalize_odk_code(value):
    """Normalize ODK choice values so equivalent numeric codes map together."""
    text = str(value or "").strip()
    if re.fullmatch(r"\d+", text):
        return str(int(text))
    if re.fullmatch(r"\d+\.0+", text):
        return str(int(text.split(".", 1)[0]))
    return text


def _build_odk_label_map(field_name, form_name=_VA_GEOGRAPHY_FORM_NAME):
    lookup = {}
    choice_rows = (
        ODKFormChoice.objects.filter(form_name=form_name, field_name=field_name)
        .order_by("id")
        .values("value", "label")
    )
    for row in choice_rows:
        key = _normalize_odk_code(row["value"])
        if key and key not in lookup:
            lookup[key] = (row["label"] or "").strip()
    return lookup


def _decode_odk_field(label_map, raw_value):
    text = str(raw_value or "").strip()
    if not text:
        return text
    return label_map.get(_normalize_odk_code(text), text)


class Index(CustomAuthMixin, PermissionRequiredMixin, ListView):
    permission_required = "va_data_management.view_verbalautopsy"
    template_name = "va_data_management/index.html"
    paginate_by = 15

    # Columns selected for both the VA and VASA legs — MUST match exactly (name
    # and order) for the .union() below.
    _LIST_VALUES = (
        "id",
        "province",
        "district",
        "constituency",
        "ward",
        "area",
        "causes__cause",
        "Id10023",
        "Id10010",
        "Id10012",
        "deceased",
        "errors",
        "record_source",
    )

    def _source_values_qs(self, base_qs, cod_fk_field, source_label):
        """Annotate a VA/VASA base queryset for the line list.

        ``deceased``/``errors`` mirror the original VA list; ``record_source``
        (VA/VASA) drives the Source column and detail-link routing. A distinct
        alias is used because VASA already has a model field named ``source``.
        ``cod_fk_field`` selects the correct CauseCodingIssue FK
        (``verbalautopsy`` or ``vasa``).
        """
        errors_subq = (
            CauseCodingIssue.objects.filter(
                **{cod_fk_field: OuterRef("pk")}, severity="error"
            )
            .values(cod_fk_field)
            .annotate(c=Count("id"))
            .values("c")
        )
        return base_qs.annotate(
            deceased=Concat("Id10017", V(" "), "Id10018", output_field=TextField()),
            errors=Subquery(errors_subq),
            record_source=V(source_label, output_field=TextField()),
        ).values(*self._LIST_VALUES)

    def get_queryset(self):
        # The line list merges VerbalAutopsy and VASA rows: VASA records are
        # verbal autopsies too. Each leg is filtered independently (a filterset
        # per model) and then combined with .union() — post-union filtering is
        # unsupported, so all filtering happens before the union.
        va_values = self._source_values_qs(
            self.request.user.verbal_autopsies(), "verbalautopsy", "VA"
        )
        vasa_values = self._source_values_qs(
            self.request.user.vasa_records(), "vasa", "VASA"
        )

        self.filterset = VAFilter(data=self.request.GET or None, queryset=va_values)
        self.vasa_filterset = VASAFilter(
            data=self.request.GET or None, queryset=vasa_values
        )
        # Keep both filter forms in sync with the user's visibility.
        for filterset in (self.filterset, self.vasa_filterset):
            if self.request.user.is_fieldworker():
                filterset.form.fields.pop("interviewer", None)
            if not self.request.user.can_view_pii:
                for field in ("deceased", "start_date", "end_date"):
                    filterset.form.fields.pop(field, None)

        # sort by chosen field (default is interview date)
        sort_key_raw = self.request.GET.get("order_by", "-interviewed")
        sort_key = sort_key_raw.lstrip("-")
        sort_key_to_field = {
            "id": "id",
            "interviewer": "Id10010",
            "dod": "Id10023",
            "province": "province",
            "district": "district",
            "cause": "causes__cause",
            "interviewed": "Id10012",
            "deceased": "deceased",
        }
        sort_field = sort_key_to_field.get(sort_key, sort_key)
        if sort_key_raw.startswith("-") and not sort_field.startswith("-"):
            sort_field = "-" + sort_field

        combined = self.filterset.qs.union(self.vasa_filterset.qs).order_by(sort_field)
        return combined

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        context["filterset"] = self.filterset

        # ids for va download — VerbalAutopsy only. VASA export is not yet wired
        # into va_export, and VA/VASA ids collide, so mixing them would corrupt
        # the export. VASA rows are simply not offered for CSV/API download here.
        download_ids = [str(i) for i in self.filterset.qs.values_list("id", flat=True)]
        if len(download_ids) > 0 and user.can_download_data:
            context["download_url"] = (
                reverse("va_export:va_api") + "?ids=" + ",".join(download_ids)
            )  # self.request.get_host() +
        else:
            # filter returned no results or user isn't allowed to download;
            # render button useless
            context["download_url"] = ""

        geography_label_maps = {
            field_name: _build_odk_label_map(field_name)
            for field_name in _VA_GEOGRAPHY_FIELDS
        }

        context["object_list"] = [
            {
                "id": va["id"],
                "deceased": va["deceased"],
                "interviewer": va["Id10010"],
                "interviewed": parse_date(va["Id10012"]),
                "dod": (
                    parse_date(va["Id10023"]) if (va["Id10023"] != "dk") else "Unknown"
                ),
                "province": _decode_odk_field(
                    geography_label_maps["province"], va["province"]
                ),
                "district": (
                    _decode_odk_field(geography_label_maps["district"], va["district"])
                    or va["area"]
                ),
                "constituency": _decode_odk_field(
                    geography_label_maps["constituency"], va["constituency"]
                ),
                "ward": _decode_odk_field(geography_label_maps["ward"], va["ward"]),
                "cause": va["causes__cause"],
                "errors": va["errors"],
                # Drives the Source column and detail-link routing (VA vs VASA).
                "source": va.get("record_source", "VA"),
            }
            for va in context["object_list"]
        ]

        # Unified verbal-autopsy summary (VA + VASA) for the stats banner.
        context.update(
            get_va_summary_stats(
                self.request.user.verbal_autopsies(),
                cache_key="va_summary_stats_with_vasa",
                extra_qs=self.request.user.vasa_records(),
            )
        )
        return context


# Mixin just for the individual verbal autopsy data management views to
# restrict access based on user
class AccessRestrictionMixin(SingleObjectMixin):
    def get_queryset(self):
        # Restrict to VAs this user can access
        return self.request.user.verbal_autopsies()


class Show(
    CustomAuthMixin, AccessRestrictionMixin, PermissionRequiredMixin, DetailView
):
    permission_required = "va_data_management.view_verbalautopsy"
    template_name = "va_data_management/show.html"
    model = VerbalAutopsy
    pk_url_kwarg = "id"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["id"] = self.object.id
        context["form"] = VerbalAutopsyForm(None, instance=self.object)

        coding_issues = self.object.coding_issues.all()
        context["warnings"], context["algo_warnings"] = self.filter_warnings(
            [issue for issue in coding_issues if issue.severity == "warning"]
        )
        context["errors"] = [
            issue for issue in coding_issues if issue.severity == "error"
        ]

        # TODO: date in diff info should be formatted in local time
        history = self.object.history.all().reverse()
        history_pairs = zip(history, history[1:])  # noqa: B905
        diffs = []
        for old, new in history_pairs:
            delta = new.diff_against(
                old, excluded_fields=["unique_va_identifier", "duplicate"]
            )
            for change in delta.changes:
                if change.field == "location":
                    old_id = change.old
                    new_id = change.new
                    change.old = Location.objects.get(pk=new_id).name
                    change.new = Location.objects.get(pk=old_id).name
            diffs.append(delta)
        context["diffs"] = diffs

        context["duplicate"] = self.object.duplicate

        return context

    # this function uses regex to filter out user warnings and algorithm
    # warnings based on an observed pattern
    @staticmethod
    def filter_warnings(warnings):
        user_warnings = []
        algo_warnings = []
        for warning in warnings:
            if re.search(r"^W\d{6}[-]", str(warning)):
                algo_warnings.append(warning)
            else:
                user_warnings.append(warning)
        return user_warnings, algo_warnings


class VASAAccessRestrictionMixin(SingleObjectMixin):
    """Restrict individual VASA views to records the user can access."""

    def get_queryset(self):
        return self.request.user.vasa_records()


def _serialize_model_fields(instance, skip=(), value_fn=None):
    """Return [{name, label, value}] for a model instance's concrete fields.

    ``value_fn(name, value)`` optionally transforms values for display (e.g.
    decoding stored SA/repeat select codes to their choice labels).
    """
    rows = []
    for field in instance._meta.fields:
        if field.name in skip:
            continue
        value = getattr(instance, field.name)
        if value_fn is not None:
            value = value_fn(field.name, value)
        rows.append(
            {
                "name": field.name,
                "label": field.verbose_name,
                "value": value,
            }
        )
    return rows


class VASAShow(
    CustomAuthMixin, VASAAccessRestrictionMixin, PermissionRequiredMixin, DetailView
):
    """Detail page for a VASA record.

    Tabs: Record (WHO columns, shared with VA), Social Autopsy (linked SA model),
    one instrument-appropriate Care-Seeking tab, Coding Issues / Data Quality,
    and Change History. Social-autopsy / repeat data live on separate models and
    never enter the coding pipeline.
    """

    permission_required = "va_data_management.view_verbalautopsy"
    template_name = "va_data_management/vasa_show.html"
    model = VASA
    pk_url_kwarg = "id"

    _REPEAT_SKIP = ("vasa", "id", "created", "updated")
    _SA_SKIP = ("vasa", "created", "updated")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context["id"] = self.object.id
        context["instrument"] = self.object.get_instrument_display()
        context["form"] = VASAForm(
            None, instance=self.object, include_pii=self.request.user.can_view_pii
        )

        # Coding issues (data quality) — same warning/error split as the VA page.
        coding_issues = self.object.coding_issues.all()
        context["warnings"], context["algo_warnings"] = Show.filter_warnings(
            [issue for issue in coding_issues if issue.severity == "warning"]
        )
        context["errors"] = [
            issue for issue in coding_issues if issue.severity == "error"
        ]

        # Linked social autopsy (OneToOne; may be absent).
        try:
            social_autopsy = self.object.social_autopsy
        except ObjectDoesNotExist:
            social_autopsy = None
        # SA/repeat select answers are stored as raw codes; decode to their
        # choice labels for display only (never for coding input).
        context["social_autopsy_fields"] = (
            _serialize_model_fields(
                social_autopsy, skip=self._SA_SKIP, value_fn=sa_display_value
            )
            if social_autopsy is not None
            else None
        )

        # Care-seeking repeat data is instrument-specific.  Keep one tab in
        # the UI, but select the corresponding repeat model here so fields
        # belonging to the other instruments can never be rendered.  Maternal
        # records use the adult care-seeking repeat form in the VASA export.
        repeat_group = {
            "neonate": "neonate",
            "child": "child",
            "adult": "adult",
            "maternal": "adult",
        }.get(self.object.instrument)
        care_seeking_records = []
        if repeat_group:
            care_seeking_records = [
                _serialize_model_fields(
                    row, skip=self._REPEAT_SKIP, value_fn=sa_display_value
                )
                for row in getattr(self.object, f"care_seeking_{repeat_group}").all()
            ]
        context["care_seeking_groups"] = [
            {
                "key": "all",
                "label": "Care-Seeking",
                "records": care_seeking_records,
            }
        ]

        # Change history diffs.
        history = self.object.history.all().reverse()
        history_pairs = zip(history, history[1:])  # noqa: B905
        context["diffs"] = [new.diff_against(old) for old, new in history_pairs]

        return context


class Edit(
    CustomAuthMixin,
    PermissionRequiredMixin,
    AccessRestrictionMixin,
    SuccessMessageMixin,
    UpdateView,
):
    permission_required = "va_data_management.change_verbalautopsy"
    template_name = "va_data_management/edit.html"
    form_class = VerbalAutopsyForm
    model = VerbalAutopsy
    pk_url_kwarg = "id"
    success_message = "Verbal Autopsy successfully updated!"

    def get_success_url(self):
        # update the validation errors
        validate_vas_for_dashboard([self.object])
        return reverse("va_data_management:show", kwargs={"id": self.object.id})

    def get_form_kwargs(self):
        # Tell form to include PII fields if user is able.
        kwargs = super().get_form_kwargs()
        kwargs["include_pii"] = self.request.user.can_view_pii
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["id"] = self.object.id
        return context


class Reset(
    CustomAuthMixin, PermissionRequiredMixin, AccessRestrictionMixin, DetailView
):
    permission_required = "va_data_management.change_verbalautopsy"
    model = VerbalAutopsy
    pk_url_kwarg = "id"
    success_message = "Verbal Autopsy changes successfully reverted to original!"

    def render_to_response(self, context):
        _ = context  # unused
        earliest = self.object.history.earliest()
        latest = self.object.history.latest()
        if (
            earliest
            and len(
                latest.diff_against(
                    earliest, excluded_fields=["unique_va_identifier", "duplicate"]
                ).changes
            )
            > 0
        ):
            earliest.instance.save()
            # update the validation errors
            validate_vas_for_dashboard([earliest])
        messages.success(self.request, self.success_message)
        return redirect("va_data_management:show", id=self.object.id)


class RevertLatest(
    CustomAuthMixin, PermissionRequiredMixin, AccessRestrictionMixin, DetailView
):
    permission_required = "va_data_management.change_verbalautopsy"
    model = VerbalAutopsy
    pk_url_kwarg = "id"
    success_message = "Verbal Autopsy changes successfully reverted to previous!"

    def render_to_response(self, context):
        _ = context  # unused
        # TODO: Should record automatically be recoded?
        if self.object.history.count() > 1:
            previous = self.object.history.all()[1]
            latest = self.object.history.latest()
            if (
                len(
                    latest.diff_against(
                        previous, excluded_fields=["unique_va_identifier", "duplicate"]
                    ).changes
                )
                > 0
            ):
                previous.instance.save()
                # update the validation errors
                validate_vas_for_dashboard([previous])
        messages.success(self.request, self.success_message)
        return redirect("va_data_management:show", id=self.object.id)


class RunCodingAlgorithm(RedirectView, PermissionRequiredMixin):
    permission_required = "va_data_management.change_verbalautopsy"
    pattern_name = "home:index"

    def post(self, request, *args, **kwargs):
        try:
            run_coding_algorithms.apply_async()
            messages.success(request, "Successfully started background coding process")
            return super().post(request, *args, **kwargs)
        except Exception as error:
            messages.error(request, f"Unable to start background process: {error!s}")
            return super().post(request, *args, **kwargs)


class Delete(CustomAuthMixin, PermissionRequiredMixin, DeleteView):
    permission_required = "va_data_management.delete_verbalautopsy"
    model = VerbalAutopsy
    success_url = reverse_lazy("va_data_cleanup:index")
    success_message = "Verbal Autopsy %(id)s was deleted successfully"
    error_message = (
        "Verbal Autopsy %(id)s could not be deleted. This Verbal Autopsy doesn't "
        "exist or you don't have access to delete it."
    )

    def form_valid(self, request, *args, **kwargs):
        obj = self.get_object()
        # Check that VA passed in is indeed a duplicate & is VA that the user can access
        # Guards against a user manually passing in an arbitrary VA ID to
        # va_data_management/delete/:id
        if (
            self.request.user.verbal_autopsies()
            .filter(id=obj.id, duplicate=True)
            .exists()
        ):
            messages.success(self.request, self.success_message % obj.__dict__)
            return super().delete(request, *args, **kwargs)
        else:
            messages.error(self.request, self.error_message % obj.__dict__)
            return redirect("va_data_cleanup:index")


delete = Delete.as_view()


class DeleteAll(CustomAuthMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "va_data_management.bulk_delete"
    model = VerbalAutopsy
    success_url = reverse_lazy("va_data_cleanup:index")
    success_message = "Duplicate Verbal Autopsies successfully deleted!"
    template_name = "va_data_management/verbalautopsy_confirm_delete_all.html"

    def post(self, request, *args, **kwargs):
        self.request.user.verbal_autopsies().filter(duplicate=True).delete()
        messages.success(self.request, self.success_message)
        return redirect(reverse("va_data_cleanup:index"))


delete_all = DeleteAll.as_view()
