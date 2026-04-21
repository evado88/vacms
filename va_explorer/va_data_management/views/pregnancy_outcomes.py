from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import (
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)
from django.views.generic.detail import SingleObjectMixin

from va_explorer.utils.mixins import CustomAuthMixin, LocationRestrictedQuerySetMixin
from va_explorer.va_data_management.filters import PregnancyOutcomeFilter
from va_explorer.va_data_management.forms import PregnancyOutcomeForm
from va_explorer.va_data_management.models import PregnancyOutcome


class PregnancyOutcomes(
    CustomAuthMixin, PermissionRequiredMixin, LocationRestrictedQuerySetMixin, ListView
):
    permission_required = "va_data_management.view_pregnancyoutcome"
    template_name = "va_data_management/pregnancy_outcomes.html"
    paginate_by = 15
    model = PregnancyOutcome

    _SORT_FIELDS = {
        "id": "id",
        "enumerator": "enumerator",
        "province": "province",
        "district": "district",
        "ward": "ward",
        "PO_41": "PO_41",
        "PO_46": "PO_46",
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        self.filterset = PregnancyOutcomeFilter(self.request.GET, queryset=queryset)
        order_by_raw = self.request.GET.get("order_by", "-id")
        direction = "-" if order_by_raw.startswith("-") else ""
        db_field = self._SORT_FIELDS.get(order_by_raw.lstrip("-"), "id")
        return self.filterset.qs.order_by(direction + db_field)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter"] = self.filterset
        return context


class PregnancyOutcomeAccessMixin(LocationRestrictedQuerySetMixin, SingleObjectMixin):
    def get_queryset(self):
        return super().get_queryset().prefetch_related("babies")


class PregnancyOutcomeDetail(
    CustomAuthMixin, PregnancyOutcomeAccessMixin, PermissionRequiredMixin, DetailView
):
    permission_required = "va_data_management.view_pregnancyoutcome"
    template_name = "va_data_management/pregnancy_outcome_detail.html"
    model = PregnancyOutcome
    pk_url_kwarg = "id"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["id"] = self.object.id
        context["form"] = PregnancyOutcomeForm(None, instance=self.object)
        context["history"] = self.object.history.all().reverse()
        return context


class PregnancyOutcomeEdit(
    CustomAuthMixin,
    PermissionRequiredMixin,
    PregnancyOutcomeAccessMixin,
    SuccessMessageMixin,
    UpdateView,
):
    permission_required = "va_data_management.change_pregnancyoutcome"
    template_name = "va_data_management/pregnancy_outcome_edit.html"
    form_class = PregnancyOutcomeForm
    model = PregnancyOutcome
    pk_url_kwarg = "id"
    success_message = "Pregnancy outcome record successfully updated!"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["include_pii"] = self.request.user.can_view_pii
        return kwargs

    def get_success_url(self):
        return reverse(
            "va_data_management:pregnancy_outcome_detail", kwargs={"id": self.object.id}
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["id"] = self.object.id
        return context


class PregnancyOutcomeDelete(
    CustomAuthMixin,
    PermissionRequiredMixin,
    LocationRestrictedQuerySetMixin,
    DeleteView,
):
    permission_required = "va_data_management.delete_pregnancyoutcome"
    model = PregnancyOutcome
    success_url = reverse_lazy("va_data_management:pregnancy_outcomes")
    success_message = "Pregnancy outcome %(id)s was deleted successfully"
    error_message = "Pregnancy outcome %(id)s could not be deleted. This record doesn't exist or you don't have access."

    def form_valid(self, form):
        obj = self.get_object()
        messages.success(self.request, self.success_message % obj.__dict__)
        return super().form_valid(form)

    def form_invalid(self, form):
        obj = self.get_object()
        messages.error(self.request, self.error_message % obj.__dict__)
        return redirect(self.success_url)


pregnancyoutcome_delete = PregnancyOutcomeDelete.as_view()


class PregnancyOutcomeDeleteAll(CustomAuthMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "va_data_management.bulk_delete"
    model = PregnancyOutcome
    success_url = reverse_lazy("va_data_management:pregnancy_outcomes")
    success_message = "All Pregnancy Outcomes successfully deleted!"
    template_name = "va_data_management/pregnancy_outcome_confirm_delete_all.html"

    def post(self, request, *args, **kwargs):
        PregnancyOutcome.objects.all().delete()
        messages.success(self.request, self.success_message)
        return redirect(self.success_url)


pregnancyoutcome_delete_all = PregnancyOutcomeDeleteAll.as_view()
