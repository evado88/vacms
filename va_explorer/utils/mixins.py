from django.contrib import messages
from django.contrib.auth.mixins import AccessMixin, UserPassesTestMixin
from django.http import HttpResponseForbidden
from django.http.response import HttpResponseRedirect
from django.urls import reverse

from va_explorer.va_data_management.utils.location_access import (
    restrict_queryset_to_user_locations,
)


class CustomAuthMixin(AccessMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            # messages.error(request, "You must be signed in to view this page.")
            return self.handle_no_permission()
        elif not self.user_has_valid_password(request):
            messages.error(
                request, "You must set a new password before you can view this page."
            )
            return HttpResponseRedirect(reverse("users:set_password"))
        return super().dispatch(request, *args, **kwargs)

    def user_has_valid_password(self, request):
        return request.user.has_valid_password


class CsvDownloadPermissionMixin:
    """Return 403 for authenticated users who aren't Data Managers/Admins.

    Place leftmost in the MRO so that authenticated-but-unauthorized users get
    a 403, while anonymous users fall through to the downstream auth mixin's
    login redirect.
    """

    def dispatch(self, request, *args, **kwargs):
        user = request.user
        if getattr(user, "is_authenticated", False) and (
            not user.can_download_csv_exports
        ):
            return HttpResponseForbidden("You are not allowed to download this data.")
        return super().dispatch(request, *args, **kwargs)


class AjaxListTableMixin:
    """Render only the table+pagination partial on the AJAX component refresh.

    When the request carries ``?component=1`` (issued by list_table.js) the view
    returns just the table fragment; otherwise it renders the full page, which
    embeds the same fragment on first paint. Subclasses set
    ``list_table_template`` to their per-model table partial.
    """

    list_table_template = None
    component_template = "partials/_list_table_component.html"

    def is_component_request(self):
        return self.request.GET.get("component") == "1"

    def get_template_names(self):
        if self.is_component_request():
            return [self.component_template]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["list_table_template"] = self.list_table_template
        return context


class LocationRestrictedQuerySetMixin:
    """Applies ``restrict_queryset_to_user_locations`` in ``get_queryset``.

    Place before concrete view base classes (ListView, DetailView, etc.) in MRO
    so that ``super().get_queryset()`` reaches the view's default manager call
    first, then this mixin wraps the result with location scoping.

    Override ``location_field_mapping`` on a subclass when the model's geography
    columns differ from the default province/district/constituency/ward/ea mapping.
    """

    location_field_mapping = None

    def get_queryset(self):
        qs = super().get_queryset()
        return restrict_queryset_to_user_locations(
            qs, self.request.user, self.location_field_mapping
        )


class UserDetailViewMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.has_perm(
            "users.view_user"
        ) or self.request.user.pk == self.kwargs.get("pk")
