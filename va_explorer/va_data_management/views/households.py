from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.urls import reverse_lazy, reverse
from django.views.generic import ListView, DetailView, UpdateView, DeleteView, TemplateView
from django.views.generic.detail import SingleObjectMixin
from django.shortcuts import redirect

from va_explorer.utils.mixins import CustomAuthMixin
from va_explorer.va_data_management.models import Household
from va_explorer.va_data_management.utils.location_access import (
    restrict_queryset_to_user_locations,
)
from va_explorer.va_data_management.forms import HouseholdForm
from va_explorer.va_data_management.filters import HouseholdFilter
from django.contrib.contenttypes.models import ContentType
from va_explorer.va_data_management.models.data_quality import (
    DataQualityIssue,
    DataQualityIssueMember,
)


class HouseholdAccessMixin(SingleObjectMixin):
    def get_queryset(self):
        return restrict_queryset_to_user_locations(
            Household.objects.all(),
            self.request.user,
        )


class Households(CustomAuthMixin, PermissionRequiredMixin, ListView):
    permission_required = "va_data_management.view_household"
    model = Household
    template_name = "va_data_management/households.html"
    paginate_by = 15

    def get_queryset(self):
        # Set up filter with GET params
        base_qs = Household.objects.all()
        base_qs = restrict_queryset_to_user_locations(
            base_qs,
            self.request.user,
            {
                "province": "province",
            },
        )
        self.filter = HouseholdFilter(
            self.request.GET,
            queryset=base_qs.order_by("-id"),
        )
        return self.filter.qs.order_by("-id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter'] = self.filter
        return context


class HouseholdDetail(CustomAuthMixin, PermissionRequiredMixin, HouseholdAccessMixin, DetailView):
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
            DataQualityIssue.objects
            .filter(members__content_type=ct, members__object_id=self.object.id)
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

class HouseholdEdit(CustomAuthMixin, PermissionRequiredMixin, HouseholdAccessMixin, SuccessMessageMixin, UpdateView):
    permission_required = "va_data_management.change_household"
    model = Household
    form_class = HouseholdForm
    template_name = "va_data_management/household_edit.html"
    pk_url_kwarg = "id"
    success_message = "Household successfully updated."

    def get_success_url(self):
        return reverse("va_data_management:household_detail", kwargs={"id": self.object.id})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["id"] = self.object.id
        return context


class HouseholdDelete(CustomAuthMixin, PermissionRequiredMixin, DeleteView):
    permission_required = "va_data_management.delete_household"
    model = Household
    success_url = reverse_lazy("va_data_management:households")
    template_name = "va_data_management/household_confirm_delete.html"
    success_message = "Household %(id)s was deleted successfully."

    def get_queryset(self):
        return restrict_queryset_to_user_locations(
            Household.objects.all(),
            self.request.user,
        )

    def form_valid(self, form):
        messages.success(self.request, self.success_message % self.get_object().__dict__)
        return super().form_valid(form)


class HouseholdDeleteAll(CustomAuthMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "va_data_management.bulk_delete"
    template_name = "va_data_management/household_confirm_delete_all.html"
    success_message = "All households successfully deleted."

    def post(self, request, *args, **kwargs):
        Household.objects.all().delete()
        messages.success(request, self.success_message)
        return redirect(reverse("va_data_management:households"))
