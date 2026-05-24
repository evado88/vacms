from django import forms
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


# load COD options for export form
def get_cod_options():
    options = list(
        CauseOfDeath.objects.distinct("cause").values_list("cause", flat=True)
    )
    options.insert(0, "All")
    return [(o, o) for o in options]


class DateInput(forms.DateInput):
    input_type = "date"


class VADownloadForm(forms.Form):
    dataset = forms.ChoiceField(
        label="Dataset",
        choices=(
            ("verbalautopsy", "Verbal Autopsy"),
            ("household", "Household"),
            ("pregnancy", "Pregnancy"),
            ("pregnancy_outcome", "Pregnancy Outcome"),
            ("death", "Death"),
            ("rumor_surveillance", "Rumor Surveillance"),
        ),
        initial="verbalautopsy",
        widget=Select(),
        required=True,
        help_text="Choose which dataset to export.",
    )

    va_record_type = forms.ChoiceField(
        label="VA Record Type",
        choices=(
            (
                "abridged",
                "Abridged VA (Location, Demographics, CoD, and necessary "
                "identifier values for analysis and tracking)",
            ),
            ("full", "Full VA Record"),
        ),
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
        required=False,
        widget=DateInput(attrs={"class": "datepicker"}),
        help_text="Earliest VA date to download",
    )

    end_date = DateField(
        label="Latest Date",
        required=False,
        widget=DateInput(attrs={"class": "datepicker"}),
        help_text="Latest VA date to download",
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
        choices=(("csv", "csv"), ("json", "json")),
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

        # The VA record-type option is currently a UI choice only. Keep it out
        # of non-VA requests and default it for VA requests when disabled/missing.
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
