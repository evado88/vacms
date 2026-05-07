import os
import re

import pandas as pd
from django.db.models import F
from django.db.models.query import QuerySet
from django.forms import BooleanField
from django.forms.models import ModelMultipleChoiceField as MMCField
from django.utils.crypto import get_random_string
from pandas.core.frame import DataFrame

from va_explorer.users.forms import ExtendedUserCreationForm
from va_explorer.users.management.commands.initialize_groups import GROUPS_PERMISSIONS
from va_explorer.users.models import User
from va_explorer.va_data_management.utils.location_assignment import fuzzy_match


# get table with basic info for a list of users. By default, exports results
# for all users but admin. No PII included in result
def get_anonymized_user_info(user_list_file=None):
    # By default, include all users but admins.
    user_objects = User.objects.exclude(groups__name="Admins").exclude(
        email="admin@example.com"
    )
    # If user_list provided, filter list down to users w matching emails
    if user_list_file:
        if not os.path.isfile(user_list_file):
            raise FileNotFoundError(f"Couldn't find user file {user_list_file}")
        with open(user_list_file) as f:
            emails = [
                x.strip().replace(",", "")
                for x in filter(lambda x: "@" in x, f.readlines())
            ]
        if len(emails) > 0:
            filtered_users = user_objects.filter(email__in=emails)
            if len(filtered_users) == 0:
                print(f"WARNING: couldn't find any user emails matching {emails}")
            else:
                found_emails = set(filtered_users.values_list("email", flat=True))
                missing_emails = set(emails).difference(found_emails)
                if len(missing_emails) > 0:
                    print(
                        f"WARNING: couldn't find users for following emails: \
                        {missing_emails}"
                    )
                user_objects = filtered_users
        else:
            print("WARNING no valid emails found in file. Exporting info for all users")

    # export user data in way that is consistent with user form
    user_data = (
        user_objects.select_related("location_restrictions")
        .select_related("groups")
        .select_related("user_permissions")
        .values(
            "uuid",
            "is_active",
            "is_staff",
            role=F("groups__name"),
            locations=F("location_restrictions__name"),
            permissions=F("user_permissions__codename"),
        )
    )
    user_df = pd.DataFrame.from_records(user_data).rename(
        columns={"locations": "location_restrictions"}
    )
    user_perms = (
        user_df[["uuid", "permissions"]]
        .pivot_table(
            index="uuid",
            columns="permissions",
            values="uuid",
            aggfunc=lambda x: len(x) > 0,
        )
        .fillna(False)
        .reset_index()
    )
    user_df = (
        user_df.drop(columns="permissions")
        .drop_duplicates()
        .merge(user_perms, how="left")
    )
    return user_df


def parse_users_from_file(user_list_file, default_group=None, debug=False):
    """
    Parses a CSV file of users, validates them using the user creation form logic,
    and returns lists of valid and invalid user data.
    """
    try:
        user_df = pd.read_csv(user_list_file).fillna("")
    except Exception as e:
        return [], [
            {
                "row": 0,
                "email": "N/A",
                "name": "N/A",
                "errors": {"file": [f"Failed to read CSV: {str(e)}"]},
            }
        ]

    valid_users_raw = []
    valid_users_display = []
    invalid_users = []

    for i, row in user_df.iterrows():
        user_data = row.to_dict()

        # Apply default group override if provided
        if default_group:
            if hasattr(default_group, "name"):
                user_data["group"] = default_group.name
            else:
                user_data["group"] = str(default_group)

        # Generate random password if missing
        if not user_data.get("password"):
            user_data["password"] = get_random_string(12)

        try:
            user_form = fill_user_form_data(user_data, debug=debug)

            if user_form.is_valid():
                # Store raw data for saving (contains IDs)
                valid_users_raw.append(user_form.data.copy())

                # Create display version of the data
                display_data = {}
                for field in ExtendedUserCreationForm.Meta.fields:
                    if field == "password":
                        continue

                    label = user_form.fields[field].label
                    if not label:
                        label = field.replace("_", " ").title()

                    val = user_form.cleaned_data.get(field)

                    if field == "group" and val:
                        display_data[label] = val.name
                    elif field in ["location_restrictions", "facility_restrictions"]:
                        display_data[label] = (
                            "; ".join(str(location) for location in val) if val else ""
                        )
                    elif isinstance(val, bool):
                        display_data[label] = "Yes" if val else "No"
                    elif val is None:
                        display_data[label] = ""
                    else:
                        display_data[label] = val

                valid_users_display.append(display_data)

            else:
                invalid_users.append(
                    {
                        "row": i + 2,  # +2 because 0-indexed and header row
                        "email": user_data.get("email", "Unknown"),
                        "name": user_data.get("name", "Unknown"),
                        "errors": user_form.errors,
                    }
                )
        except Exception as e:
            invalid_users.append(
                {
                    "row": i + 2,
                    "email": user_data.get("email", "Unknown"),
                    "name": user_data.get("name", "Unknown"),
                    "errors": {"exception": [str(e)]},
                }
            )

    return valid_users_raw, valid_users_display, invalid_users


def save_users_from_data(valid_users_data, email_confirmation=False):
    """
    Takes a list of valid user data dictionaries (output from parse_users_from_file),
    creates the forms, and saves the users.
    """
    created_users = []
    for data in valid_users_data:
        # Remove 'group_name' key before creating the form if it exists
        # as it's not a field on the ExtendedUserCreationForm.
        data_copy = data.copy()
        data_copy.pop("group_name", None)
        form = ExtendedUserCreationForm(data_copy)
        if form.is_valid():
            user = form.save(email_confirmation=email_confirmation)
            created_users.append(user)
    return created_users


def fill_user_form_data(user_data, debug=False):
    form = ExtendedUserCreationForm()

    # if dataframe provided, convert to dict
    if type(user_data) is DataFrame:
        user_data_tmp = user_data.T.to_dict()
        user_data = next(iter(user_data_tmp.values()))

    # preprocess raw data before parsing for form
    user_data = prep_form_data(user_data)

    # initialize all form data values to their defaults
    form_data = {
        name: [field.initial] if type(field) is MMCField else field.initial
        for name, field in form.fields.items()
    }

    # figure out common fields between form and csv
    common_fields = set(form.fields.keys()).intersection(set(user_data.keys()))

    # iterate over these fields and add to form data
    for field_name in common_fields:
        # raw value to be parsed
        value = user_data[field_name]
        # pointer to form field object
        form_field = form.fields.get(field_name)
        # initialize form_value to form_field's default value.
        # If match found below, will be overridden
        form_value = form_field.initial
        # multiple choice field
        if hasattr(form_field, "choices"):
            if hasattr(form_field.choices, "queryset"):
                # query the options for submitted value
                # NOTE: this assumes queryset objects have 'name' field and are
                # indexed as such. If not the case, need to use new field in query below
                qs = form_field.choices.queryset
                # first, try (case insensitive) exact matching. If no match,
                # then try conservative fuzzy matching
                try:
                    match = qs.filter(name__iexact=value)
                    if not match.exists():
                        options = list(qs.values_list("name", flat=True))
                        if value and options:
                            match_name = fuzzy_match(
                                str(value),
                                None,
                                options=options,
                                threshold=95,
                            )
                            if match_name:
                                match = qs.filter(name__iexact=match_name)
                except Exception as err:
                    print(
                        f"WARN: Unable to match due to error: {err}\n \
                        Assigning none queryset."
                    )
                    match = qs.none()

                if debug:
                    print(
                        f"{field_name}: searching for {value} against \
                        {qs.values_list('name', flat=True)}"
                    )
                    print(f"match: {match}")

                # if matching option, set form value to its primary key.
                # Otherwise, use form field's default value
                if match.exists():
                    form_value = match
                # if group field, try adding s if any groups are plural and rerun query
                elif field_name == "group":
                    plural_names = any(
                        x.endswith("s") for x in qs.values_list("name", flat=True)
                    )
                    if plural_names and not value.endswith("s"):
                        value += "s"
                        match = qs.filter(name__iexact=value)
                        if debug:
                            print("trying new group logic...")
                            print(
                                f"searched for {value} against \
                                {qs.values_list('name', flat=True)}"
                            )
                            print(f"match: {match}")

                        if match.exists():
                            form_value = match
            else:
                # find closest match against list of choices manually
                for _, choice in form_field.choices:
                    # First, assume field is string. If not, fall back to
                    # non-string matching
                    try:
                        value, choice = value.lower(), choice.lower()
                    except Exception as err:
                        print(f"Error: {err}")
                    if choice == value:
                        form_value = choice
                        break
        # boolean field
        elif type(form_field) is BooleanField:
            form_value = (
                bool(value)
                if type(value) in [int, bool, float]
                else str(value).lower() in {"true", "1", "1.0", "yes", "y"}
            )

        # catch-all for other field types - likely freeform/text entry
        else:
            form_value = value

        # add parsed value to form's data
        form_data[field_name] = form_value
    # final preprocessing for form (have to call function again to convert
    # values to objects)
    final_data = prep_form_data(form_data)

    return ExtendedUserCreationForm(final_data)


# assign geographic access based on group and location restrictions. Also some
# logic to handle facility restrictions for field workers. If new groups are
# added to the system, need to update this logic.
def prep_form_data(user_data, debug=False, default_group="data viewer"):
    # if geographic_access missing, assign based on group and/or location_restrictions
    if "group" not in user_data:
        raise (ValueError("Must provide group to determine geographic access."))
    # if location_restriction key exists but value is None, convert to empty string
    elif not user_data.get("location_restrictions", None):
        user_data["location_restrictions"] = ""

    # Parse and clean group name. Default to default_group if no match found
    group_name = default_group
    if isinstance(user_data["group"], QuerySet):
        # queryset provided (i.e. match found). If non-empty, parse group's name field.
        if len(user_data["group"]) > 0:
            group_name = user_data["group"].first().name
    else:
        # group field is a string. Try matching against known groups
        try:
            # strip plural from group name
            group_name = re.sub("s$", "", user_data["group"].lower())
        except Exception as err:
            print(f"Error: {err}")

        # check group name against known groups. Update group_name to match if found,
        # otherwise default to data viewer
        group_match = fuzzy_match(
            group_name, None, options=list(GROUPS_PERMISSIONS.keys())
        )
        if debug:
            print(f"group key for fuzzy match: {group_name}")
            print(f"group match: {group_match}")
        group_name = group_match if group_match else default_group
        if debug:
            print(f"final group name: {group_name}")
        # override raw group name with clean one
        user_data["group"] = group_name

    # field worker logic
    # Note: the Field Worker role may not be used in VACMS; for now we
    # just ensure that the Field Worker role is treated the same as
    # other roles when considering location restrictions by disabling
    # the Field Workers conditional block
    if False:
        geo_access = "location-specific"
        # if no facility restriction, check for group restriction. Otherwise,
        # drop location restriction
        if user_data.get("facility_restrictions", None) in [None, [], "", [""]]:
            user_data["facility_restrictions"] = user_data["location_restrictions"]
        _ = user_data.pop("location_restrictions")
    # catch-all logic for groups in ["data viewer", "data manager", "dashboard viewer"]
    else:
        geo_access = "national"
        if (
            not pd.isna(user_data["location_restrictions"])
            and len(user_data["location_restrictions"]) > 0
            and user_data["location_restrictions"][0] is not None
        ):
            geo_access = "location-specific"
        if geo_access == "national":
            _ = user_data.pop("location_restrictions")
        # remove facility restriction data if it exists (only field workers should
        # have this key)
        if "facility_restrictions" in user_data:
            _ = user_data.pop("facility_restrictions")

    user_data["geographic_access"] = geo_access

    # convert all queryset objects to primary keys of first match
    for field, data in user_data.items():
        if isinstance(data, QuerySet) and len(data) > 0:
            user_data[field] = (
                data.first().pk if field == "group" else [data.first().pk]
            )

    return user_data


# get schema information of a django form including field types, descriptions,
# whether they're required, and default values
def get_form_fields(form_type=ExtendedUserCreationForm, orient="v"):
    form = form_type()
    field_dict = {
        name: {
            "type": str(type(field)).split(".")[-1].replace("'>", ""),
            "description": field.help_text,
            "required": field.required,
            "default_value": field.initial,
        }
        for name, field in form.fields.items()
    }
    field_df = pd.DataFrame(field_dict)
    return field_df if orient.startswith("v") else field_df.T
