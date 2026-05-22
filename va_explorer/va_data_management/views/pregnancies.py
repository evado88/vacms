from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import (
    DeleteView,
    DetailView,
    TemplateView,
    UpdateView,
)
from django_filters.views import FilterView

from va_explorer.utils.mixins import (
    AjaxListTableMixin,
    CustomAuthMixin,
    LocationRestrictedQuerySetMixin,
)
from va_explorer.va_data_management.filters import PregnancyFilter
from va_explorer.va_data_management.forms import PregnancyForm
from va_explorer.va_data_management.models import Pregnancy
from va_explorer.va_data_management.utils.list_cleaning import (
    exclude_rows_with_invalid_display_values,
)
from va_explorer.va_data_management.utils.location_access import (
    restrict_queryset_to_user_locations,
)


class Pregnancies(
    AjaxListTableMixin,
    CustomAuthMixin,
    PermissionRequiredMixin,
    LocationRestrictedQuerySetMixin,
    FilterView,
):
    permission_required = "va_data_management.view_pregnancy"
    template_name = "va_data_management/pregnancies.html"
    list_table_template = "partials/_pregnancy_table.html"
    filterset_class = PregnancyFilter
    paginate_by = 15
    model = Pregnancy

    _SORT_FIELDS = {
        "id": "id",
        "enumerator": "enumerator",
        "province": "province",
        "district": "district",
        "ward": "ward",
        "pregnant_woman": "PE_06",
        "dob": "PE_09A",
        "lmp_date": "PE_09A",
    }

    display_validation_fields = (
        "enumerator",
        "province",
        "district",
        "ward",
        "PE_06",
        "PE_09A",
    )

    def get_queryset(self):
        queryset = exclude_rows_with_invalid_display_values(
            super().get_queryset(), self.display_validation_fields
        )
        self.filterset = PregnancyFilter(self.request.GET, queryset=queryset)
        order_by_raw = self.request.GET.get("order_by", "-id")
        direction = "-" if order_by_raw.startswith("-") else ""
        db_field = self._SORT_FIELDS.get(order_by_raw.lstrip("-"), "id")
        return self.filterset.qs.order_by(direction + db_field)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter"] = self.filterset
        return context


class PregnancyDetail(
    CustomAuthMixin,
    PermissionRequiredMixin,
    LocationRestrictedQuerySetMixin,
    DetailView,
):
    permission_required = "va_data_management.view_pregnancy"
    template_name = "va_data_management/pregnancy_detail.html"
    model = Pregnancy
    pk_url_kwarg = "id"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["id"] = self.object.id
        context["form"] = PregnancyForm(None, instance=self.object)
        context["history"] = self.object.history.all().reverse()
        return context


class PregnancyEdit(
    CustomAuthMixin,
    PermissionRequiredMixin,
    LocationRestrictedQuerySetMixin,
    SuccessMessageMixin,
    UpdateView,
):
    permission_required = "va_data_management.change_pregnancy"
    template_name = "va_data_management/pregnancy_edit.html"
    form_class = PregnancyForm
    model = Pregnancy
    pk_url_kwarg = "id"
    success_message = "Pregnancy record successfully updated!"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["include_pii"] = self.request.user.can_view_pii
        return kwargs

    def get_success_url(self):
        return reverse(
            "va_data_management:pregnancy_detail", kwargs={"id": self.object.id}
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["id"] = self.object.id
        return context


class PregnancyDelete(
    CustomAuthMixin,
    PermissionRequiredMixin,
    LocationRestrictedQuerySetMixin,
    DeleteView,
):
    permission_required = "va_data_management.delete_pregnancy"
    model = Pregnancy
    success_url = reverse_lazy("va_data_management:pregnancy_list")
    success_message = "Pregnancy %(id)s was deleted successfully"
    error_message = "Pregnancy %(id)s could not be deleted. This record doesn't exist or you don't have access."

    def form_valid(self, form):
        obj = self.get_object()
        messages.success(self.request, self.success_message % obj.__dict__)
        return super().form_valid(form)

    def form_invalid(self, form):
        obj = self.get_object()
        messages.error(self.request, self.error_message % obj.__dict__)
        return redirect(self.success_url)


pregnancy_delete = PregnancyDelete.as_view()


class PregnancyDeleteAll(CustomAuthMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "va_data_management.bulk_delete"
    model = Pregnancy
    success_url = reverse_lazy("va_data_management:pregnancy_list")
    success_message = "All Pregnancy records successfully deleted!"
    template_name = "va_data_management/pregnancy_confirm_delete_all.html"

    def post(self, request, *args, **kwargs):
        qs = restrict_queryset_to_user_locations(Pregnancy.objects.all(), request.user)
        qs.delete()
        messages.success(self.request, self.success_message)
        return redirect(self.success_url)


pregnancy_delete_all = PregnancyDeleteAll.as_view()
