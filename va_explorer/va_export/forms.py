from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms import (
    DateField,
    ModelMultipleChoiceField,
    RadioSelect,
    Select,
    SelectMultiple,
)

from va_explorer.users.forms import LocationRestrictionsSelectMultiple
from va_explorer.va_data_management.models import CauseOfDeath, SRSClusterLocation

EXPORT_LOCATION_LEVELS = ("province", "district", "constituency", "ward")

DATASET_CHOICES = (
    ("verbalautopsy", "Verbal Autopsy"),
    ("household", "Household"),
    ("pregnancy", "Pregnancy"),
    ("pregnancy_outcome", "Pregnancy Outcome"),
    ("death", "Death"),
    ("rumor_surveillance", "Rumor Surveillance"),
)

VA_RECORD_TYPE_CHOICES = (
    ("abridged", "Abridged VA (Location, Demographics, CoD, PII)"),
    ("full", "Full VA Record"),
)

EXPORT_FORMAT_CHOICES = (("csv", "csv"), ("json", "json"))


# load COD options for export form
def get_cod_options():
    options = list(
        CauseOfDeath.objects.distinct("cause").values_list("cause", flat=True)
    )
    options.insert(0, "All")
    return [(o, o) for o in options]


class DateInput(forms.DateInput):
    input_type = "date"


class _TokenListField(forms.Field):
    def to_python(self, value):
        if value in self.empty_values:
            return []

        values = value if isinstance(value, (list, tuple)) else [value]
        tokens = []
        for raw_value in values:
            tokens.extend(
                token.strip() for token in str(raw_value).split(",") if token.strip()
            )
        return tokens


class _IntegerTokenListField(_TokenListField):
    default_error_messages = {"invalid": "Enter a valid list of IDs."}

    def validate(self, value):
        super().validate(value)
        if any(not token.isdigit() or int(token) <= 0 for token in value):
            raise ValidationError(self.error_messages["invalid"], code="invalid")

    def clean(self, value):
        return [int(token) for token in super().clean(value)]


class ExportRequestForm(forms.Form):
    """Strict validation for the download endpoint's public request contract."""

    dataset = forms.ChoiceField(choices=DATASET_CHOICES)
    va_record_type = forms.ChoiceField(choices=VA_RECORD_TYPE_CHOICES, required=False)
    format = forms.ChoiceField(choices=EXPORT_FORMAT_CHOICES, required=False)
    start_date = DateField(required=False)
    end_date = DateField(required=False)
    locations = _IntegerTokenListField(required=False)
    causes = _TokenListField(required=False)
    ids = _IntegerTokenListField(required=False)

    def clean_locations(self):
        location_ids = self.cleaned_data["locations"]
        if not location_ids:
            return []
        found_ids = set(
            SRSClusterLocation.objects.filter(pk__in=location_ids).values_list(
                "pk", flat=True
            )
        )
        if found_ids != set(location_ids):
            raise ValidationError("One or more selected locations do not exist.")
        return list(dict.fromkeys(location_ids))

    def clean_causes(self):
        tokens = self.cleaned_data["causes"]
        if not tokens:
            return []

        cause_ids = [int(token) for token in tokens if token.isdigit()]
        cause_names = [token for token in tokens if not token.isdigit()]
        matches = CauseOfDeath.objects.filter(
            Q(pk__in=cause_ids) | Q(cause__in=cause_names)
        ).values("pk", "cause")
        matched_ids = set()
        matched_names = set()
        resolved_names = []
        for match in matches:
            if match["pk"] in cause_ids:
                matched_ids.add(match["pk"])
            matched_names.add(match["cause"])
            if match["cause"] not in resolved_names:
                resolved_names.append(match["cause"])

        if matched_ids != set(cause_ids) or not set(cause_names).issubset(
            matched_names
        ):
            raise ValidationError("One or more selected causes do not exist.")
        return resolved_names

    def clean(self):
        cleaned_data = super().clean()
        dataset = cleaned_data.get("dataset")
        ids = cleaned_data.get("ids") or []
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")

        if bool(start_date) != bool(end_date):
            missing_field = "end_date" if start_date else "start_date"
            self.add_error(
                missing_field,
                "Both Earliest Date and Latest Date are required.",
            )
        elif not ids and not start_date and not end_date:
            self.add_error("start_date", "Earliest Date is required.")
            self.add_error("end_date", "Latest Date is required.")
        elif start_date and end_date and start_date > end_date:
            self.add_error("end_date", "Latest Date must be on or after Earliest Date.")

        if ids and dataset != "verbalautopsy":
            self.add_error("ids", "Record IDs are only supported for VA exports.")

        if dataset != "verbalautopsy":
            cleaned_data["va_record_type"] = None
            cleaned_data["causes"] = []
            cleaned_data["format"] = "csv"
        else:
            cleaned_data["va_record_type"] = (
                cleaned_data.get("va_record_type") or "full"
            )
            cleaned_data["format"] = cleaned_data.get("format") or "csv"

        return cleaned_data


class VADownloadForm(forms.Form):
    dataset = forms.ChoiceField(
        label="Dataset",
        choices=DATASET_CHOICES,
        initial="verbalautopsy",
        widget=Select(),
        required=True,
        help_text="Choose which dataset to export.",
    )

    va_record_type = forms.ChoiceField(
        # Label is intentionally blank: the field is rendered inside a fieldset
        # "group box" whose <legend> ("VA Record Type") provides the title.
        label="",
        choices=VA_RECORD_TYPE_CHOICES,
        initial="abridged",
        widget=RadioSelect(),
        required=False,
        help_text="Choose how much Verbal Autopsy detail to include.",
    )

    action = forms.ChoiceField(
        label="Action",
        choices=(("download", "Download Data"),),
        initial="download",
        widget=Select(),
        required=True,
        help_text="Download data locally as a file.",
    )

    start_date = DateField(
        label="Earliest Date",
        required=True,
        widget=DateInput(attrs={"class": "datepicker"}),
        help_text="Earliest record date to download",
    )

    end_date = DateField(
        label="Latest Date",
        required=True,
        widget=DateInput(attrs={"class": "datepicker"}),
        help_text="Latest record date to download",
    )

    locations = ModelMultipleChoiceField(
        queryset=SRSClusterLocation.objects.filter(
            location_type__in=EXPORT_LOCATION_LEVELS
        )
        .exclude(name="Unknown")
        .order_by("path"),
        widget=LocationRestrictionsSelectMultiple(
            attrs={"class": "location-restrictions-select"}
        ),
        required=False,
        help_text="Choose location(s) from which to download data",
    )

    causes = ModelMultipleChoiceField(
        queryset=CauseOfDeath.objects.distinct("cause").order_by("cause"),
        widget=SelectMultiple(attrs={"class": "cod-select"}),
        required=False,
        help_text="Filter data by Cause of Death (CoD)",
    )

    # which endpoint system you're exporting to. Only for exports (not downloads)
    export_config = forms.ChoiceField(
        disabled=True,
        label="<b>#TODO:</b> Export Endpoint",
        choices=(
            ("dhis2", "DHIS2"),
            ("postgres", "database2"),
            ("oracle", "database3..."),
        ),
        initial="dhis2",
        widget=Select(),
        required=False,
        help_text="Type of endpoint to export to",
    )

    format = forms.ChoiceField(
        label="Data Format",
        choices=EXPORT_FORMAT_CHOICES,
        initial="csv",
        widget=Select(),
        required=False,
    )

    # clean up params before sending request
    def clean(self, *args, **kwargs):
        """
        Normal cleanup
        """
        cleaned_data = super().clean(*args, **kwargs)

        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date and end_date and start_date > end_date:
            self.add_error("end_date", "Latest Date must be on or after Earliest Date.")

        # Convert location objects back to ids for API
        locs = cleaned_data.get("locations", [])
        if len(locs) > 0:
            cleaned_data["locations"] = ",".join([str(loc.pk) for loc in list(locs)])
        else:
            cleaned_data["locations"] = ""

        # make sure action is specified
        if "action" not in cleaned_data:
            cleaned_data["action"] = "download"

        # Convert any COD objects back to ids for API
        cods = cleaned_data.get("causes", [])
        if cleaned_data.get("dataset") == "verbalautopsy" and len(cods) > 0:
            cleaned_data["causes"] = ",".join([str(cod.cause) for cod in cods])
        else:
            cleaned_data["causes"] = None

        # Keep the VA-only option out of non-VA requests and use the UI default
        # when the field is disabled or missing.
        if cleaned_data.get("dataset") == "verbalautopsy":
            cleaned_data["va_record_type"] = (
                cleaned_data.get("va_record_type") or "abridged"
            )
        else:
            cleaned_data["va_record_type"] = None

        # Non-VA datasets are exported as CSV.
        if cleaned_data.get("dataset") != "verbalautopsy":
            cleaned_data["format"] = "csv"

        # convert format to lowercase
        cleaned_data["format"] = cleaned_data.get("format", "csv").lower()
        return cleaned_data
