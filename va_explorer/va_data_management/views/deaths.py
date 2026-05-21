import csv

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import (
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)

from va_explorer.utils.mixins import (
    CsvDownloadPermissionMixin,
    CustomAuthMixin,
    LocationRestrictedQuerySetMixin,
)
from va_explorer.va_data_management.constants import DEATH_PII_FIELDS, REDACTED_STRING
from va_explorer.va_data_management.filters import DeathFilter
from va_explorer.va_data_management.forms import DeathForm
from va_explorer.va_data_management.models import Death
from va_explorer.va_data_management.utils.location_access import (
    restrict_queryset_to_user_locations,
)
from va_explorer.vacms.permissions import user_can_manage_va_schedule


class Deaths(
    CustomAuthMixin, PermissionRequiredMixin, LocationRestrictedQuerySetMixin, ListView
):
    permission_required = "va_data_management.view_death"
    model = Death
    template_name = "va_data_management/deaths.html"
    paginate_by = 15

    _SORT_FIELDS = {
        "id": "id",
        "enumerator": "enumerator",
        "province": "province",
        "district": "district",
        "ward": "ward",
        "DE_03": "DE_03",
        "DE_06": "DE_06",
    }

    def get_queryset(self):
        queryset = super().get_queryset().filter(eventid__isnull=True)
        self.filter = DeathFilter(self.request.GET, queryset=queryset)
        order_by_raw = self.request.GET.get("order_by", "-id")
        direction = "-" if order_by_raw.startswith("-") else ""
        db_field = self._SORT_FIELDS.get(order_by_raw.lstrip("-"), "id")
        return self.filter.qs.order_by(direction + db_field)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Remove filter fields that would let non-PII users search by PII values.
        if not self.request.user.can_view_pii:
            for field in ("deceased", "start_date", "end_date"):
                self.filter.form.fields.pop(field, None)
        context["filter"] = self.filter
        return context


class DeathsCsvExport(CsvDownloadPermissionMixin, Deaths):
    """Download the (filtered) list of unscheduled VAs as a CSV.

    Reuses the Deaths queryset (location scoping + ``eventid__isnull=True`` +
    DeathFilter), so the export matches the on-screen list. Restricted to Data
    Managers/Admins via CsvDownloadPermissionMixin; PII columns are redacted for
    users without ``can_view_pii``.
    """

    paginate_by = None

    def get(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        redact = not request.user.can_view_pii
        field_names = [field.name for field in Death._meta.fields]
        filename = timezone.now().strftime("unscheduled_vas_%Y%m%d_%H%M%S.csv")
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        writer = csv.writer(response)
        writer.writerow(field_names)
        for death in queryset.iterator():
            row = []
            for name in field_names:
                if redact and name in DEATH_PII_FIELDS:
                    row.append(REDACTED_STRING)
                    continue
                value = getattr(death, name)
                row.append("" if value is None else value)
            writer.writerow(row)
        return response


class DeathDetail(
    CustomAuthMixin,
    PermissionRequiredMixin,
    LocationRestrictedQuerySetMixin,
    DetailView,
):
    permission_required = "va_data_management.view_death"
    model = Death
    pk_url_kwarg = "id"
    template_name = "va_data_management/death_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["id"] = self.object.id
        context["form"] = DeathForm(None, instance=self.object)
        context["user_can_manage_schedule"] = user_can_manage_va_schedule(
            self.request.user
        )
        history = self.object.history.all().reverse()
        context["history"] = history
        return context


class DeathEdit(
    CustomAuthMixin,
    PermissionRequiredMixin,
    LocationRestrictedQuerySetMixin,
    SuccessMessageMixin,
    UpdateView,
):
    permission_required = "va_data_management.change_death"
    model = Death
    form_class = DeathForm
    template_name = "va_data_management/death_edit.html"
    pk_url_kwarg = "id"
    success_message = "Death record successfully updated."

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["include_pii"] = self.request.user.can_view_pii
        return kwargs

    def get_success_url(self):
        return reverse("va_data_management:death_detail", kwargs={"id": self.object.id})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["id"] = self.object.id
        return context


class DeathDelete(
    CustomAuthMixin,
    PermissionRequiredMixin,
    LocationRestrictedQuerySetMixin,
    DeleteView,
):
    permission_required = "va_data_management.delete_death"
    model = Death
    success_url = reverse_lazy("va_data_management:deaths")
    template_name = "va_data_management/death_confirm_delete.html"
    success_message = "Death record %(id)s was deleted successfully."

    def form_valid(self, form):
        messages.success(
            self.request, self.success_message % self.get_object().__dict__
        )
        return super().form_valid(form)


class DeathDeleteAll(CustomAuthMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "va_data_management.bulk_delete"
    template_name = "va_data_management/death_confirm_delete_all.html"
    success_message = "All death records successfully deleted."

    def post(self, request, *args, **kwargs):
        qs = restrict_queryset_to_user_locations(Death.objects.all(), request.user)
        qs.delete()
        messages.success(request, self.success_message)
        return redirect(reverse("va_data_management:deaths"))
