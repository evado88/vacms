from allauth.account.adapter import get_adapter
from allauth.account.forms import (
    PasswordField,
    PasswordVerificationMixin,
    SetPasswordField,
)
from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group, Permission
from django.forms import (
    ModelChoiceField,
    ModelMultipleChoiceField,
    RadioSelect,
    SelectMultiple,
)

from va_explorer.users.utils.location_mapping import map_srs_clusters_to_locations
from va_explorer.va_data_management.models import Location, SRSClusterLocation

from .constants import FEEDBACK_MODULE_FEATURES, PERM_FIELD_MAP
from .models import Feedback

# from allauth.account.utils import send_email_confirmation, setup_user_email


User = get_user_model()


def get_location_restrictions(cleaned_data):
    """
    Utility method to determine if the user locations are from the location_restrictions
    dropdown or facility restrictions dropdown (Field Worker role only)
    """
    if (
        "location_restrictions" in cleaned_data
        and len(cleaned_data.get("facility_restrictions", [])) == 0
    ):
        return cleaned_data["location_restrictions"]
    elif (
        "facility_restrictions" in cleaned_data
        and len(cleaned_data.get("location_restrictions", [])) == 0
    ):
        return cleaned_data["facility_restrictions"]
    else:
        return []


def validate_location_access(form, geographic_access, location_restrictions, group):
    """
    Custom form validations related to geographic access and location restrictions
    """
    # Note: the Field Worker role may not be used in VACMS; for now we
    # just ensure that the Field Worker role is treated the same as
    # other roles when considering location restrictions by disabling
    # the Field Workers conditional block
    if False:
        if len(location_restrictions) == 0 or geographic_access == "national":
            form._errors["facility_restrictions"] = form.error_class(
                ["You must add one or more facilities."]
            )
    else:
        if geographic_access == "location-specific" and len(location_restrictions) == 0:
            form._errors["location_restrictions"] = form.error_class(
                ["You must add one or more locations if access is location-specific."]
            )
        elif geographic_access == "national" and len(location_restrictions) > 0:
            form._errors["location_restrictions"] = form.error_class(
                ["You cannot add specific locations if access is national."]
            )


# core logic/steps to set user fields based on form data. Used in both UserCreation
# and ExtendedUserCreation forms
def process_user_data(user, cleaned_data):
    # set user location restrictions
    location_restrictions = get_location_restrictions(cleaned_data)

    if (
        "location_restrictions" in cleaned_data
        and cleaned_data["location_restrictions"]
    ):
        clusters = cleaned_data["location_restrictions"]
        mapped_locations = map_srs_clusters_to_locations(clusters)
        user.location_restrictions.set(mapped_locations)
    else:
        user.location_restrictions.set(location_restrictions)

    # set user group
    group = cleaned_data["group"]
    user.groups.set([group])

    if "is_superuser" in cleaned_data:
        user.is_superuser = bool(cleaned_data["is_superuser"])
        user.is_staff = user.is_superuser

    denied_permissions = set(user.denied_permissions or [])
    group_permissions = {
        f"{app_label}.{codename}"
        for app_label, codename in group.permissions.values_list(
            "content_type__app_label", "codename"
        )
    }

    # Handle individual permission checkboxes as per-user overrides:
    # - checked: ensure user effectively has permission
    # - unchecked: ensure user effectively does not have permission
    for field_name, (app_label, codename) in PERM_FIELD_MAP.items():
        if field_name not in cleaned_data:
            continue

        perm_name = f"{app_label}.{codename}"
        perm = Permission.objects.filter(
            content_type__app_label=app_label, codename=codename
        ).first()
        if not perm:
            continue

        if cleaned_data[field_name]:
            denied_permissions.discard(perm_name)
            # Keep user_permissions minimal: only persist explicit grants
            # when the assigned role does not already include this permission.
            if perm_name in group_permissions:
                user.user_permissions.remove(perm)
            else:
                user.user_permissions.add(perm)
        else:
            denied_permissions.add(perm_name)
            user.user_permissions.remove(perm)

    user.denied_permissions = sorted(denied_permissions)

    user.save()
    for cache_name in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
        user.__dict__.pop(cache_name, None)
    return user


class LocationRestrictionsSelectMultiple(SelectMultiple):
    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex, attrs
        )
        if value:
            # Use depth - 1 to account for root/country node
            option["attrs"]["data-depth"] = value.instance.depth - 1
            # Only query for descendants if there are any
            if value.instance.numchild > 0:
                option["attrs"][
                    "data-descendants"
                ] = value.instance.get_descendant_ids()
        return option


class GroupSelect(forms.Select):
    """
    Custom widget for group select that will have a 'data-view-pii' and
    'data-download-data' attributes.
    We can use this attribute in the HTML to determine whether or not to
    enable the 'Can View PII' checkbox.
    """

    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex, attrs
        )
        if value:
            if value.instance.permissions.filter(codename="view_pii").first():
                option["attrs"]["data-view-pii"] = 1
            if value.instance.permissions.filter(codename="download_data").first():
                option["attrs"]["data-download-data"] = 1
        return option


class UserCommonFields(forms.ModelForm):
    name = forms.CharField(required=True, max_length=100)
    group = ModelChoiceField(
        queryset=Group.objects.exclude(name="tmp_va_dbg_grp"),
        required=True,
        widget=GroupSelect,
    )
    location_restrictions = ModelMultipleChoiceField(
        # Don't include 'Unknown' or Root/Country node in options
        queryset=SRSClusterLocation.objects.filter(
            location_type__in=["province", "district"]
        )
        .exclude(name="Unknown")
        .order_by("path"),
        widget=LocationRestrictionsSelectMultiple(
            attrs={"class": "location-restrictions-select"}
        ),
        required=False,
    )
    facility_restrictions = ModelMultipleChoiceField(
        queryset=Location.objects.filter(location_type="facility").order_by("name"),
        widget=forms.SelectMultiple(attrs={"class": "facility-restrictions-select"}),
        required=False,
        help_text="Field Workers must be assigned to at least one facility.",
    )
    geographic_access = forms.ChoiceField(
        choices=(("national", "National"), ("location-specific", "Location-specific")),
        initial="location-specific",
        widget=RadioSelect(),
        required=True,
    )
    view_pii = forms.BooleanField(
        label="View PII",
        required=False,
    )
    download_data = forms.BooleanField(
        label="Download Data",
        required=False,
    )

    # Individual module permission fields
    perm_view_dashboard = forms.BooleanField(label="View Dashboard", required=False)
    perm_supervise_users = forms.BooleanField(label="Supervise Users", required=False)
    perm_add_user = forms.BooleanField(label="Add User", required=False)
    perm_view_user = forms.BooleanField(label="View Users", required=False)
    perm_change_user = forms.BooleanField(label="Edit User", required=False)
    perm_delete_user = forms.BooleanField(label="Delete User", required=False)
    perm_view_verbalautopsy = forms.BooleanField(
        label="View Verbal Autopsy", required=False
    )
    perm_change_verbalautopsy = forms.BooleanField(
        label="Edit Verbal Autopsy", required=False
    )
    perm_delete_verbalautopsy = forms.BooleanField(
        label="Delete Verbal Autopsy", required=False
    )
    perm_bulk_delete = forms.BooleanField(label="Bulk Delete", required=False)
    perm_view_household = forms.BooleanField(label="View Household", required=False)
    perm_change_household = forms.BooleanField(label="Edit Household", required=False)
    perm_view_pregnancy = forms.BooleanField(label="View Pregnancy", required=False)
    perm_change_pregnancy = forms.BooleanField(label="Edit Pregnancy", required=False)
    perm_view_pregnancyoutcome = forms.BooleanField(
        label="View Pregnancy Outcome", required=False
    )
    perm_change_pregnancyoutcome = forms.BooleanField(
        label="Edit Pregnancy Outcome", required=False
    )
    perm_view_death = forms.BooleanField(label="View Death", required=False)
    perm_change_death = forms.BooleanField(label="Edit Death", required=False)
    perm_view_datacleanup = forms.BooleanField(
        label="View Data Cleanup", required=False
    )
    perm_download_datacleanup = forms.BooleanField(label="Download", required=False)
    perm_bulk_download = forms.BooleanField(label="Bulk Download", required=False)


class ExtendedUserCreationForm(UserCommonFields, UserCreationForm):
    """
    Extends the built in UserCreationForm in several ways:

    * Name field is added.
    * Group model from django.contrib.auth.models is represented as a ModelChoiceField
    * Non-model field geographic_access added to toggle between national and
      location-specific access
    * Data not saved by the default behavior of UserCreationForm is saved.
    """

    password1 = None
    password2 = None

    class Meta:
        model = User
        fields = [
            "name",
            "email",
            "mobile1",
            "mobile2",
            "address",
            "password",
            "group",
            "view_pii",
            "download_data",
            "geographic_access",
            "location_restrictions",
            "facility_restrictions",
        ]

    def __init__(self, *args, **kwargs):
        """
        Set the request on the form class so that we can access the request when
        calling send_new_user_mail() in the save method below
        """
        self.request = kwargs.pop("request", None)

        super(UserCreationForm, self).__init__(*args, **kwargs)

        self.fields["group"].label = "Role"

    def clean(self, *args, **kwargs):
        """
        Normal cleanup
        """
        cleaned_data = super(UserCreationForm, self).clean(*args, **kwargs)

        if "geographic_access" and "group" in cleaned_data:
            location_restrictions = get_location_restrictions(cleaned_data)

            validate_location_access(
                self,
                cleaned_data["geographic_access"],
                location_restrictions,
                cleaned_data["group"],
            )

        return cleaned_data

    def save(self, commit=True, email_confirmation=True):
        """
        Saves the email and name properties after the normal
        save behavior is complete. Sets a random password, which the user must
        change upon initial login.

        Saves the location and group after the user object is saved.
        """
        user = super(UserCreationForm, self).save(commit=False)
        if user:
            user.email = self.cleaned_data["email"]
            user.name = self.cleaned_data["name"]
            user.mobile1 = self.cleaned_data["mobile1"]
            user.mobile2 = self.cleaned_data["mobile2"]
            user.address = self.cleaned_data["address"]
            user.password = self.cleaned_data["password"]

            # password = get_random_string(length=16)
            password = user.password
            user.set_password(password)

            # location_restrictions = get_location_restrictions(self.cleaned_data)
            # group = self.cleaned_data["group"]

            if commit:
                # Need to save again after setting password.
                user.save()
                user = process_user_data(user, self.cleaned_data)

            # TODO: Remove if we do not require email confirmation; we will no
            # longer need the lines below. See allauth:
            # https://github.com/pennersr/django-allauth/blob/c19a212c6ee786af1bb8bc1b07eb2aa8e2bf531b/allauth/account/utils.py
            # setup_user_email(self.request, user, [])
            # A workaround to run this script without a mail server.
            # If True, it will send email like normal.
            # If False, user credentials will just be printed to console.
            console_msg = (
                "" * 20
                + f"Created user with email {user.email} and temp. password {password}"
            )

            # prevent sending of emails
            email_confirmation = False

            if email_confirmation:
                try:
                    get_adapter().send_new_user_mail(self.request, user, password)
                except Exception as err:
                    print("WARNING: failed to send email. Printing credentials instead")
                    print(console_msg)
                    print(f"Failure source: {err}")

            else:
                print(console_msg)

        return user


class UserUpdateForm(UserCommonFields, forms.ModelForm):
    """
    Similar to UserCreationForm but adds is_active field to allow an administrator
    to mark a user account as inactive
    """

    class Meta:
        model = User
        fields = [
            "name",
            "email",
            "mobile1",
            "mobile2",
            "address",
            "is_active",
            "is_superuser",
            "group",
            "view_pii",
            "download_data",
            "geographic_access",
            "location_restrictions",
            "facility_restrictions",
        ]

    def __init__(self, *args, **kwargs):
        # TODO: Remove if we do not require email confirmation; we will no longer
        # need the lines below

        super().__init__(*args, **kwargs)
        self.fields["group"].label = "Role"

    def clean(self, *args, **kwargs):
        """
        Normal cleanup
        """
        cleaned_data = super(forms.ModelForm, self).clean()

        if "geographic_access" and "group" in cleaned_data:
            location_restrictions = get_location_restrictions(cleaned_data)

            validate_location_access(
                self,
                cleaned_data["geographic_access"],
                location_restrictions,
                cleaned_data["group"],
            )

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)

        if commit:
            user.save()
            # only run va matching logic when user is first created
            user = process_user_data(user, self.cleaned_data)

        return user


class UserPasswordUpdateForm(forms.Form):
    id = forms.CharField(
        label="User ID",
        required=True,
        widget=forms.TextInput(attrs={"readonly": "readonly"}),
    )
    name = forms.CharField(
        label="Name",
        required=True,
        widget=forms.TextInput(attrs={"readonly": "readonly"}),
    )
    email = forms.CharField(
        label="Email",
        required=True,
        widget=forms.TextInput(attrs={"readonly": "readonly"}),
    )
    mobile1 = forms.CharField(
        label="Mobile 1",
        required=False,
        widget=forms.TextInput(attrs={"readonly": "readonly"}),
    )
    mobile2 = forms.CharField(
        label="Mobile 2",
        required=False,
        widget=forms.TextInput(attrs={"readonly": "readonly"}),
    )
    password1 = SetPasswordField(
        label="New Password",
        help_text=password_validation.password_validators_help_text_html(),
    )
    password2 = PasswordField(label="New Password (again)")

    #
    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError("The provided passwords do not match")
        return cleaned_data


class UserSetPasswordForm(PasswordVerificationMixin, forms.Form):
    password1 = SetPasswordField(
        label="New Password",
        help_text=password_validation.password_validators_help_text_html(),
    )
    password2 = PasswordField(label="New Password (again)")

    def save(self, user):
        user.set_password(self.cleaned_data["password1"])
        user.has_valid_password = True
        user.save()
        return user


class UserChangePasswordForm(PasswordVerificationMixin, forms.Form):
    """
    Allows the user to change their password if they already have a valid
    (i.e., non-temporary) password. Requires the user to re-type their old
    password and type in their new password twice.

    See allauth:
        https://github.com/pennersr/django-allauth/blob/master/allauth/account/forms.py#L54
        If we do not want this dependency, we can write our own clean method to
        ensure the 2 typed-in passwords match.
    """

    current_password = PasswordField(label="Current Password")

    password1 = SetPasswordField(
        label="New Password",
        help_text=password_validation.password_validators_help_text_html(),
    )
    password2 = PasswordField(label="New Password (again)")

    def __init__(self, *args, **kwargs):
        """
        Set the current user on the form
        We need this to call user.check_password in clean_current_password
        """
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

    def clean_current_password(self):
        if not self.user.check_password(self.cleaned_data.get("current_password")):
            raise forms.ValidationError("Please type your current password.")
        return self.cleaned_data["current_password"]

    def save(self, user):
        user.set_password(self.cleaned_data["password1"])
        user.save()

        return user


class FeedbackForm(forms.ModelForm):
    report_type = forms.ChoiceField(
        choices=Feedback.ReportType.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    module = forms.ChoiceField(
        choices=Feedback.Module.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    feature = forms.ChoiceField(
        choices=(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    severity = forms.ChoiceField(
        choices=Feedback.Severity.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    attachment = forms.FileField(
        required=False,
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
    )

    class Meta:
        model = Feedback
        fields = [
            "subject",
            "report_type",
            "module",
            "feature",
            "severity",
            "description",
            "attachment",
        ]
        widgets = {
            "subject": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        module = self.initial.get("module") or getattr(
            self.instance, "module", Feedback.Module.DATA_MANAGEMENT
        )
        self.fields["report_type"].initial = Feedback.ReportType.BUG
        self.fields["severity"].initial = Feedback.Severity.LOW
        self._set_feature_choices(module)

    def _set_feature_choices(self, module):
        self.fields["feature"].choices = FEEDBACK_MODULE_FEATURES.get(module, [])
        if not self.fields["feature"].choices:
            self.fields["feature"].choices = [("", "---------")]

    def clean(self):
        cleaned = super().clean()
        module = cleaned.get("module")
        feature = cleaned.get("feature")
        if module and feature:
            valid_features = dict(FEEDBACK_MODULE_FEATURES.get(module, []))
            if feature not in valid_features:
                self.add_error(
                    "feature",
                    "Select a valid feature for the chosen module.",
                )
        return cleaned


class FeedbackStatusForm(forms.ModelForm):
    class Meta:
        model = Feedback
        fields = ["status"]
        widgets = {
            "status": forms.Select(attrs={"class": "form-select form-select-sm"})
        }


class UserImportForm(forms.Form):

    groups = forms.ModelChoiceField(
        queryset=Group.objects.all(),
        widget=forms.Select,
        required=False,
        label="Group (overwrites group in CSV file)",
    )
    file = forms.FileField()
