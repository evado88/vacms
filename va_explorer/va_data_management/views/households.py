from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.contenttypes.models import ContentType
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
from va_explorer.va_data_management.filters import HouseholdFilter
from va_explorer.va_data_management.forms import HouseholdForm
from va_explorer.va_data_management.models import Household
from va_explorer.va_data_management.models.data_quality import (
    DataQualityIssue,
)
from va_explorer.va_data_management.utils.location_access import (
    restrict_queryset_to_user_locations,
)


class HouseholdAccessMixin(LocationRestrictedQuerySetMixin, SingleObjectMixin):
    pass


class Households(
    CustomAuthMixin, PermissionRequiredMixin, LocationRestrictedQuerySetMixin, ListView
):
    permission_required = "va_data_management.view_household"
    model = Household
    template_name = "va_data_management/households.html"
    paginate_by = 15
    location_field_mapping = {
        "province": "province",
        "district": "district",
        "constituency": "constituency",
        "ward": "ward",
    }

    _SORT_FIELDS = {
        "id": "id",
        "enumerator": "enumerator",
        "province": "province",
        "district": "district",
        "ward": "ward",
        "locality_name": "locality_name",
        "respondent": "respondent",
    }

    def get_queryset(self):
        base_qs = super().get_queryset()
        self.filter = HouseholdFilter(self.request.GET, queryset=base_qs)
        order_by_raw = self.request.GET.get("order_by", "-id")
        direction = "-" if order_by_raw.startswith("-") else ""
        db_field = self._SORT_FIELDS.get(order_by_raw.lstrip("-"), "id")
        return self.filter.qs.order_by(direction + db_field)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter"] = self.filter
        return context


class HouseholdDetail(
    CustomAuthMixin, PermissionRequiredMixin, HouseholdAccessMixin, DetailView
):
    permission_required = "va_data_management.view_household"
    model = Household
    pk_url_kwarg = "id"
    template_name = "va_data_management/household_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["id"] = self.object.id
        history = self.object.history.all().reverse()
        context["history"] = history
        context["form"] = HouseholdForm(instance=self.object)

        # Fetch issues via reverse relation through members
        ct = ContentType.objects.get_for_model(Household)
        dq_issues = (
            DataQualityIssue.objects.filter(
                members__content_type=ct, members__object_id=self.object.id
            )
            .order_by("-detected_at")
            .distinct()
        )
        context["dq_issues"] = list(dq_issues)
        context["dq_issues_counts"] = {
            "open": dq_issues.filter(status="open").count(),
            "resolved": dq_issues.filter(status="resolved").count(),
            "muted": dq_issues.filter(status="muted").count(),
        }
        return context


class HouseholdEdit(
    CustomAuthMixin,
    PermissionRequiredMixin,
    HouseholdAccessMixin,
    SuccessMessageMixin,
    UpdateView,
):
    permission_required = "va_data_management.change_household"
    model = Household
    form_class = HouseholdForm
    template_name = "va_data_management/household_edit.html"
    pk_url_kwarg = "id"
    success_message = "Household successfully updated."

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["include_pii"] = self.request.user.can_view_pii
        return kwargs

    def get_success_url(self):
        return reverse(
            "va_data_management:household_detail", kwargs={"id": self.object.id}
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["id"] = self.object.id
        return context


class HouseholdDelete(
    CustomAuthMixin,
    PermissionRequiredMixin,
    LocationRestrictedQuerySetMixin,
    DeleteView,
):
    permission_required = "va_data_management.delete_household"
    model = Household
    success_url = reverse_lazy("va_data_management:households")
    template_name = "va_data_management/household_confirm_delete.html"
    success_message = "Household %(id)s was deleted successfully."

    def form_valid(self, form):
        messages.success(
            self.request, self.success_message % self.get_object().__dict__
        )
        return super().form_valid(form)


class HouseholdDeleteAll(CustomAuthMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "va_data_management.bulk_delete"
    template_name = "va_data_management/household_confirm_delete_all.html"
    success_message = "All households successfully deleted."

    def post(self, request, *args, **kwargs):
        qs = restrict_queryset_to_user_locations(
            Household.objects.all(),
            request.user,
            field_mapping={
                "province": "province",
                "district": "district",
                "constituency": "constituency",
                "ward": "ward",
            },
        )
        qs.delete()
        messages.success(request, self.success_message)
        return redirect(reverse("va_data_management:households"))
