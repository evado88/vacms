import re

from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Q, TextField
from django.db.models import Value as V
from django.db.models.functions import Concat
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

from django_filters.views import FilterView

from va_explorer.utils.mixins import CustomAuthMixin
from va_explorer.va_data_management.forms import PregnancyForm
from va_explorer.va_data_management.models import Pregnancy
from va_explorer.va_data_management.utils.location_access import (
    restrict_queryset_to_user_locations,
)
from va_explorer.va_data_management.filters import PregnancyFilter

# You can implement PregnancyFilter for advanced search/filtering if needed
# from va_explorer.va_data_management.filters import PregnancyFilter


class Pregnancies(CustomAuthMixin, PermissionRequiredMixin, FilterView):
    permission_required = "va_data_management.view_pregnancy"
    template_name = "va_data_management/pregnancies.html"
    filterset_class = PregnancyFilter
    paginate_by = 15
    model = Pregnancy

    def get_queryset(self):
        queryset = restrict_queryset_to_user_locations(
            Pregnancy.objects.all(),
            self.request.user,
        ).order_by("-created", "-id")
        self.filterset = PregnancyFilter(self.request.GET, queryset=queryset)
        return self.filterset.qs.order_by("-created", "-id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter"] = self.filterset
        return context


class PregnancyAccessMixin(SingleObjectMixin):
    def get_queryset(self):
        return restrict_queryset_to_user_locations(
            Pregnancy.objects.all(),
            self.request.user,
        )


class PregnancyDetail(
    CustomAuthMixin, PregnancyAccessMixin, PermissionRequiredMixin, DetailView
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
    PregnancyAccessMixin,
    SuccessMessageMixin,
    UpdateView,
):
    permission_required = "va_data_management.change_pregnancy"
    template_name = "va_data_management/pregnancy_edit.html"
    form_class = PregnancyForm
    model = Pregnancy
    pk_url_kwarg = "id"
    success_message = "Pregnancy record successfully updated!"

    def get_success_url(self):
        return reverse("va_data_management:pregnancy_detail", kwargs={"id": self.object.id})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["id"] = self.object.id
        return context


class PregnancyDelete(CustomAuthMixin, PermissionRequiredMixin, DeleteView):
    permission_required = "va_data_management.delete_pregnancy"
    model = Pregnancy
    success_url = reverse_lazy("va_data_management:pregnancy_list")
    success_message = "Pregnancy %(id)s was deleted successfully"
    error_message = (
        "Pregnancy %(id)s could not be deleted. This record doesn't exist or you don't have access."
    )

    def get_queryset(self):
        return restrict_queryset_to_user_locations(
            Pregnancy.objects.all(),
            self.request.user,
        )

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
        Pregnancy.objects.all().delete()
        messages.success(self.request, self.success_message)
        return redirect(self.success_url)


pregnancy_delete_all = PregnancyDeleteAll.as_view()
