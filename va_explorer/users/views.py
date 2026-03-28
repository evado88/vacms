import json

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import (
    LoginRequiredMixin,
    PermissionRequiredMixin,
    UserPassesTestMixin,
)
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import (
    CreateView,
    DetailView,
    FormView,
    ListView,
    RedirectView,
    UpdateView,
)

from ..utils.mixins import CustomAuthMixin, UserDetailViewMixin
from .constants import PERM_FIELD_MAP
from .utils.user_form_backend import parse_users_from_file, save_users_from_data
from .forms import (
    ExtendedUserCreationForm,
    FeedbackForm,
    FeedbackStatusForm,
    UserChangePasswordForm,
    UserImportForm,
    UserPasswordUpdateForm,
    UserSetPasswordForm,
    UserUpdateForm,
)
from .models import Feedback

User = get_user_model()


def get_group_permissions_json():
    from django.contrib.auth.models import Group

    group_permissions = {
        group.name: list(group.permissions.values_list("codename", flat=True))
        for group in Group.objects.prefetch_related("permissions").all()
    }
    return json.dumps(group_permissions)


def user_is_system_admin(user):
    return getattr(user, "is_authenticated", False) and (
        user.is_superuser or user.groups.filter(name="Admins").exists()
    )


class SystemAdminRequiredMixin(UserPassesTestMixin):
    raise_exception = True

    def test_func(self):
        return user_is_system_admin(self.request.user)

class UserIndexView(CustomAuthMixin, PermissionRequiredMixin, ListView):
    # https://github.com/pennersr/django-allauth/blob/c19a212c6ee786af1bb8bc1b07eb2aa8e2bf531b/allauth/account/urls.py
    login_url = reverse_lazy("account_login")
    permission_required = "users.view_user"
    model = User
    paginate_by = 10
    queryset = User.objects.all().order_by("name")


user_index_view = UserIndexView.as_view()


class UserCreateView(
    CustomAuthMixin, PermissionRequiredMixin, SuccessMessageMixin, CreateView
):
    login_url = reverse_lazy("account_login")
    permission_required = "users.add_user"
    form_class = ExtendedUserCreationForm
    template_name = "users/user_create.html"
    success_message = "User successfully created!"

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["request"] = self.request  # the trick!
        return kw

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["group_permissions_json"] = get_group_permissions_json()
        context["apply_role_defaults_on_load"] = self.request.method == "GET"
        return context

    def form_valid(self, form):
        return super().form_valid(form)


user_create_view = UserCreateView.as_view()


@login_required(login_url=reverse_lazy("account_login"))
@permission_required("users.add_user", raise_exception=True)
def UserImportView(request):

    if request.method == "POST":
        
        if "confirm_import" in request.POST:
            # Step 2: Confirmation
            valid_users = request.session.get("valid_users_import")
            if not valid_users:
                messages.error(request, "No user data found to import or session expired.")
                return redirect(reverse("users:import"))
            
            saved_users = save_users_from_data(valid_users)
            messages.success(request, f"Successfully imported {len(saved_users)} users.")
            
            # Clean up session
            del request.session["valid_users_import"]
            
            return redirect(reverse("users:index"))

        else:
            # Step 1: Upload and Review
            form = UserImportForm(request.POST, request.FILES)

            if form.is_valid():
                uploaded_file = form.cleaned_data['file']
                group = form.cleaned_data.get("groups")
                
                if not uploaded_file.name.endswith('.csv'):
                    messages.error(request, 'File is not a CSV type.')
                    return render(request, "users/user_import.html", {"form": form})

                # Parse the users
                valid_users_raw, valid_users_display, invalid_users = parse_users_from_file(uploaded_file, default_group=group)
                
                # Store valid users in session for the confirmation step
                request.session["valid_users_import"] = valid_users_raw
                
                # Render the review page
                return render(request, "users/user_import_review.html", {
                    "valid_users": valid_users_display,
                    "headers": list(valid_users_display[0].keys()) if valid_users_display else [],
                    "invalid_users": invalid_users,
                    "total_valid": len(valid_users_raw),
                    "total_invalid": len(invalid_users)
                })

            else:
                # Form is invalid, re-render with errors
                return render(request, "users/user_import.html", {"form": form})
    else:
        # GET request
        # Clear any stale import data
        if "valid_users_import" in request.session:
            del request.session["valid_users_import"]

        return render(
            request,
            "users/user_import.html",
            {"form": UserImportForm()},
        )


@login_required(login_url=reverse_lazy("account_login"))
@permission_required("users.change_user", raise_exception=True)
def UserPasswordUpdateView(request, pk):

    if request.method == "POST":

        form = UserPasswordUpdateForm(request.POST)

        if form.is_valid():
            # Process the valid data from form.cleaned_data
            password1 = form.cleaned_data["password1"]
        
            try:
                userDetail = User.objects.get(id=pk)

                if userDetail.is_superuser and not request.user.is_superuser:
                    messages.error(request, "You cannot edit the password for this user.")
                    return redirect(reverse("users:index"))
                
                userDetail.set_password(password1)
                userDetail.has_valid_password = True
                userDetail.save()
                
                messages.success(request, 'The user password has been updated successfully.')
                
            except User.DoesNotExist:
                messages.error(request, 'The specified user cannot be found!')
                
            return redirect(reverse("users:index"))


        else:
            # Form is invalid, re-render with errors
            return render(request, "users/user_update_password.html", {"form": form})
    else:

        initials = {"id": pk}

        try:
            userDetail = User.objects.get(id=pk)
            initials = {
                "id": pk,
                "name": userDetail.name,
                "email": userDetail.email,
                "mobile1": userDetail.mobile1,
                "mobile2": userDetail.mobile2,
            }
            
            if userDetail.is_superuser and not request.user.is_superuser:
                messages.error(request, "You cannot edit the password for this user.")
                return redirect(reverse("users:index"))
        except User.DoesNotExist:
            initials = {}

        return render(
            request,
            "users/user_update_password.html",
            {"form": UserPasswordUpdateForm(initial=initials)},
        )

        
class UserDetailView(CustomAuthMixin, UserDetailViewMixin, DetailView):
    login_url = reverse_lazy("account_login")
    model = User


user_detail_view = UserDetailView.as_view()


class UserUpdateView(
    CustomAuthMixin, PermissionRequiredMixin, SuccessMessageMixin, UpdateView
):
    login_url = reverse_lazy("account_login")
    permission_required = "users.change_user"
    form_class = UserUpdateForm
    template_name = "users/user_update.html"
    success_message = "User successfully updated!"

    def get_success_url(self):
        return reverse("users:detail", kwargs={"pk": self.kwargs["pk"]})

    def dispatch(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.is_superuser and not request.user.is_superuser:
            messages.error(request, "You do not have permission to modify this user.")
            return redirect(reverse("users:index"))
        return super().dispatch(request, *args, **kwargs)

    def get_object(self):
        if hasattr(self, "_cached_object"):
            return self._cached_object
        self._cached_object = User.objects.get(pk=self.kwargs["pk"])
        return self._cached_object

    def form_valid(self, form):
        # refresh the updated users session to apply their new role permissions
        user = User.objects.get(pk=self.kwargs["pk"])
        update_session_auth_hash(self.request, user)
        return super().form_valid(form)

    def get_initial(self):
        """
        Initializes the user's group on the form, which in our case is not done
        by default even though we are using a model-bound form. (Note that Group
        is a model from django.contrib.auth that is m2m with User.) We want to
        permit a user to be assigned to one Group only and thus must manually
        initialize the UpdateForm since we are imposing a change on the relation
        through how we allow the user to be assigned to groups.

        Initializes the user's geographic access on the form:
            (1) Set the national or location-specific geographic access. If
            there are any locations restrictions associated with the user in the
            database, they have location-specific access; else national access.
            (2) Set the facilities restrictions associated with the user, if any
        """
        initial = super().get_initial()

        initial["group"] = self.get_object().groups.first()
        initial["geographic_access"] = (
            "location-specific"
            if self.get_object().location_restrictions.exists()
            else "national"
        )
        initial["facility_restrictions"] = (
            self.get_object().location_restrictions.filter(location_type="facility")
        )

        initial["view_pii"] = self.get_object().can_view_pii
        initial["download_data"] = self.get_object().can_download_data
        initial["is_superuser"] = self.get_object().is_superuser

        user = self.get_object()
        for field_name, (app_label, codename) in PERM_FIELD_MAP.items():
            initial[field_name] = user.has_perm(f"{app_label}.{codename}")

        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["group_permissions_json"] = get_group_permissions_json()
        context["apply_role_defaults_on_load"] = False
        return context

    # TODO: Remove if we do not require email confirmation; we will no longer
    # need the lines below
    # def get_form_kwargs(self):
    #     kw = super(UserUpdateView, self).get_form_kwargs()
    #     kw["request"] = self.request  # the trick!
    #     return kw


user_update_view = UserUpdateView.as_view()


class UserMessageListView(CustomAuthMixin, ListView):
    login_url = reverse_lazy("account_login")
    template_name = "users/mailbox_list.html"
    context_object_name = "messages"
    paginate_by = 20

    def get_queryset(self):
        return self.request.user.messages.all()


user_message_list_view = UserMessageListView.as_view()


class UserMessageDetailView(CustomAuthMixin, DetailView):
    login_url = reverse_lazy("account_login")
    template_name = "users/mailbox_detail.html"
    context_object_name = "message"

    def get_queryset(self):
        return self.request.user.messages.all()

    def get_object(self, queryset=None):
        message = super().get_object(queryset)
        if message.read_at is None:
            message.mark_read()
        return message

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        message = context["message"]
        metadata = message.metadata or {}

        message_body = message.body
        cleaned_body = message_body

        event_id = metadata.get("event_id") if isinstance(metadata, dict) else None

        if event_id:
            event_detail_url = reverse("cms-event-detail", kwargs={"pk": event_id})
            context["event_detail_url"] = event_detail_url

            absolute_event_url = self.request.build_absolute_uri(event_detail_url)
            body_lines = [
                line
                for line in message_body.splitlines()
                if event_detail_url not in line and absolute_event_url not in line
            ]

            cleaned_candidate = "\n".join(body_lines).strip()
            if cleaned_candidate:
                cleaned_body = cleaned_candidate

        context["message_body"] = cleaned_body

        return context


user_message_detail_view = UserMessageDetailView.as_view()


class FeedbackSubmitView(CustomAuthMixin, SuccessMessageMixin, CreateView):
    login_url = reverse_lazy("account_login")
    form_class = FeedbackForm
    template_name = "users/feedback_form.html"
    success_message = "Thank you for your feedback. Our team will review it shortly."
    success_url = reverse_lazy("users:feedback_submit")

    def form_valid(self, form):
        form.instance.submitted_by = self.request.user
        form.instance.metadata = Feedback.collect_system_metadata(self.request)
        return super().form_valid(form)

    def get_initial(self):
        initial = super().get_initial()
        initial.setdefault("module", Feedback.Module.DATA_MANAGEMENT)
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["feature_map"] = Feedback.module_feature_map()
        return context


feedback_submit_view = FeedbackSubmitView.as_view()


class FeedbackMailboxListView(
    CustomAuthMixin, SystemAdminRequiredMixin, ListView
):
    login_url = reverse_lazy("account_login")
    model = Feedback
    template_name = "users/feedback_list.html"
    context_object_name = "feedback_list"
    paginate_by = 25

    def get_queryset(self):
        qs = super().get_queryset()
        status = self.request.GET.get("status")
        if status:
            qs = qs.filter(status=status)
        severity = self.request.GET.get("severity")
        if severity:
            qs = qs.filter(severity=severity)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["selected_status"] = self.request.GET.get("status", "")
        context["selected_severity"] = self.request.GET.get("severity", "")
        context["status_choices"] = Feedback.Status.choices
        context["severity_choices"] = Feedback.Severity.choices
        return context


feedback_mailbox_view = FeedbackMailboxListView.as_view()


class FeedbackMailboxDetailView(
    CustomAuthMixin, SystemAdminRequiredMixin, DetailView
):
    login_url = reverse_lazy("account_login")
    model = Feedback
    template_name = "users/feedback_detail.html"
    context_object_name = "feedback"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status_form"] = kwargs.get(
            "status_form", FeedbackStatusForm(instance=self.object)
        )
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = FeedbackStatusForm(request.POST, instance=self.object)
        if form.is_valid():
            form.save()
            messages.success(request, "Feedback status updated.")
            return redirect("users:feedback_detail", pk=self.object.pk)
        context = self.get_context_data(status_form=form)
        return self.render_to_response(context)


feedback_detail_view = FeedbackMailboxDetailView.as_view()


class UserRedirectView(LoginRequiredMixin, RedirectView):
    login_url = reverse_lazy("account_login")
    permanent = False

    def get_redirect_url(self):
        if self.request.user.has_valid_password:
            return reverse("home:index")
        return reverse_lazy("users:set_password")


user_redirect_view = UserRedirectView.as_view()


class UserSetPasswordView(FormView, LoginRequiredMixin, SuccessMessageMixin):
    """
    Allows the user to set a password of their choosing after logging in with a
    system-defined random password.

    If the user already has valid password, the system will redirect from this view

    Note: This URL is not linked anywhere in the application. Rather, a user is
    redirected to it if they do not have a valid password via the CustomAuthMixin.
    The redirect in the dispatch is set up in case the user types the URL in manually
    """

    login_url = reverse_lazy("account_login")
    form_class = UserSetPasswordForm
    template_name = "users/user_set_password.html"
    success_url = "/about"

    def dispatch(self, request, *args, **kwargs):
        if request.user.has_valid_password:
            messages.add_message(
                request, messages.INFO, "User has already set password."
            )
            # TODO: change redirect to something like a "home" page
            return redirect("/about")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.save(self.request.user)
        # See django docs:
        # https://docs.djangoproject.com/en/dev/topics/auth/default/#django.contrib.auth.update_session_auth_hash
        update_session_auth_hash(self.request, self.request.user)
        messages.success(self.request, "Password successfully set!")
        return super().form_valid(form)


user_set_password_view = UserSetPasswordView.as_view()


class UserChangePasswordView(FormView, LoginRequiredMixin, SuccessMessageMixin):
    """
    Allows the user to change their password if they already have a valid
    (i.e., non-temporary) password.
    """

    login_url = reverse_lazy("account_login")
    form_class = UserChangePasswordForm
    template_name = "users/user_change_password.html"
    success_message = "Password successfully changed!"
    # TODO: change success_url to something like a "home" page
    success_url = "/about"
    model = User

    # Sending user object to the form
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.update({"user": self.request.user})
        return kwargs

    def form_valid(self, form):
        form.save(self.request.user)
        # See django docs:
        # https://docs.djangoproject.com/en/dev/topics/auth/default/#django.contrib.auth.update_session_auth_hash
        update_session_auth_hash(self.request, self.request.user)
        messages.success(self.request, "Password successfully changed!")
        return super().form_valid(form)


user_change_password_view = UserChangePasswordView.as_view()
