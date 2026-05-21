from crispy_forms.helper import FormHelper
from django import forms
from django.core.exceptions import ValidationError
from django.forms import Select, TextInput
from django_filters import BooleanFilter, CharFilter, DateFilter, FilterSet
from fuzzywuzzy import fuzz

from .models import Death, Household, Pregnancy, PregnancyOutcome, VerbalAutopsy

TRUE_FALSE_CHOICES = (
    (False, "No"),
    (True, "Yes"),
)


def validate_integer(value: str):
    if not str(value).isdigit():
        raise ValidationError(
            ("%(value)s is invalid or negative. Please enter only numbers"),
            params={"value": value},
        )


class DateInput(forms.DateInput):
    input_type = "date"


# =============================================================
# Shared base filter — district / ward / enumerator
# are declared on every list FilterSet.  Subclasses inherit
# these fields and only need to declare model-specific extras.
# =============================================================
class GeoEnumeratorFilterSet(FilterSet):
    """Abstract base providing shared geography + enumerator filters.

    Concrete subclasses must declare their own ``class Meta`` with ``model``
    and ``fields``; they do NOT need to redeclare district/ward/enumerator.
    """

    district = CharFilter(
        field_name="district",
        lookup_expr="icontains",
        label="",
        widget=TextInput(
            attrs={
                "class": "form-text",
                "placeholder": "District",
                "aria-label": "District",
            }
        ),
    )
    ward = CharFilter(
        field_name="ward",
        lookup_expr="icontains",
        label="",
        widget=TextInput(
            attrs={"class": "form-text", "placeholder": "Ward", "aria-label": "Ward"}
        ),
    )
    enumerator = CharFilter(
        field_name="enumerator",
        lookup_expr="icontains",
        label="",
        widget=TextInput(
            attrs={
                "class": "form-text",
                "placeholder": "Enumerator",
                "aria-label": "Enumerator",
            }
        ),
    )

    class Meta:
        abstract = True
        fields = []


# =========================
# Verbal Autopsy (VA) list
# =========================
class VAFilter(FilterSet):
    interviewer = CharFilter(
        field_name="Id10010",
        lookup_expr="icontains",
        label="",
        widget=TextInput(
            attrs={
                "class": "form-text",
                "placeholder": "Interviewer",
                "aria-label": "Interviewer",
            }
        ),
    )
    deceased = CharFilter(
        method="filter_deceased",
        label="",
        widget=TextInput(
            attrs={
                "class": "form-text",
                "placeholder": "Deceased",
                "aria-label": "Deceased",
            }
        ),
    )
    district = CharFilter(
        field_name="district",
        lookup_expr="icontains",
        label="",
        widget=TextInput(
            attrs={
                "class": "form-text",
                "placeholder": "District",
                "aria-label": "District",
            }
        ),
    )
    start_date = DateFilter(
        field_name="Id10023",
        lookup_expr="gte",
        label="Earliest Date",
        widget=DateInput(attrs={"class": "form-date datepicker"}),
    )
    end_date = DateFilter(
        field_name="Id10023",
        lookup_expr="lte",
        label="Latest Date",
        widget=DateInput(attrs={"class": "form-date datepicker"}),
    )
    cause = CharFilter(
        field_name="causes__cause",
        lookup_expr="icontains",
        label="",
        widget=TextInput(
            attrs={"class": "form-text", "placeholder": "Cause", "aria-label": "Cause"}
        ),
    )
    only_errors = BooleanFilter(
        method="filter_errors",
        label="Only Errors",
        widget=Select(choices=TRUE_FALSE_CHOICES, attrs={"class": "custom-select"}),
    )

    class Meta:
        model = VerbalAutopsy
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.form.helper = FormHelper()
        self.form.helper.form_tag = False
        self.form.helper.disable_csrf = True

    def filter_deceased(self, queryset, name, value):
        if value:
            threshold = 75
            vas = queryset.values("id", "Id10017", "Id10018")
            matches = [
                va.get("id")
                for va in vas
                if fuzz.token_set_ratio(
                    value,
                    " ".join(
                        part for part in [va.get("Id10017"), va.get("Id10018")] if part
                    ),
                )
                > threshold
            ]
            return queryset.filter(id__in=matches)
        return queryset

    def filter_errors(self, queryset, name, value):
        # Keep as-is for VA: project-specific relation "coding_issues"
        if value:
            return queryset.filter(coding_issues__severity="error").distinct()
        return queryset


# =========================
# Pregnancy list
# =========================
class PregnancyFilter(GeoEnumeratorFilterSet):
    id = CharFilter(
        field_name="id",
        lookup_expr="icontains",
        label="",
        widget=TextInput(
            attrs={"class": "form-text", "placeholder": "ID", "aria-label": "ID"}
        ),
    )
    start_date = DateFilter(
        field_name="created",
        lookup_expr="gte",
        label="Earliest Record Date",
        widget=DateInput(attrs={"class": "form-date datepicker"}),
    )
    end_date = DateFilter(
        field_name="created",
        lookup_expr="lte",
        label="Latest Record Date",
        widget=DateInput(attrs={"class": "form-date datepicker"}),
    )

    class Meta:
        model = Pregnancy
        fields = []


# =========================
# Household list
# =========================
class HouseholdFilter(GeoEnumeratorFilterSet):
    id = CharFilter(
        field_name="id",
        lookup_expr="icontains",
        label="",
        widget=TextInput(
            attrs={"class": "form-text", "placeholder": "ID", "aria-label": "ID"}
        ),
    )
    supervisor = CharFilter(
        field_name="supervisor",
        lookup_expr="icontains",
        label="",
        widget=TextInput(
            attrs={
                "class": "form-text",
                "placeholder": "Supervisor",
                "aria-label": "Supervisor",
            }
        ),
    )

    class Meta:
        model = Household
        fields = []


# =========================
# Pregnancy Outcome list
# =========================
class PregnancyOutcomeFilter(GeoEnumeratorFilterSet):
    outcome_type = CharFilter(
        field_name="po_group",
        lookup_expr="icontains",
        label="",
        widget=TextInput(
            attrs={
                "class": "form-text",
                "placeholder": "Pregnancy Outcome",
                "aria-label": "Pregnancy Outcome",
            }
        ),
    )
    start_date = DateFilter(
        field_name="PO_41",
        lookup_expr="gte",
        label="Earliest Delivery",
        widget=DateInput(attrs={"class": "form-date datepicker"}),
    )
    end_date = DateFilter(
        field_name="PO_41",
        lookup_expr="lte",
        label="Latest Delivery",
        widget=DateInput(attrs={"class": "form-date datepicker"}),
    )

    class Meta:
        model = PregnancyOutcome
        fields = []


# =========================
# Death list
# =========================
class DeathFilter(GeoEnumeratorFilterSet):
    deceased = CharFilter(
        field_name="DE_03",
        lookup_expr="icontains",
        label="",
        widget=TextInput(
            attrs={
                "class": "form-text",
                "placeholder": "Deceased",
                "aria-label": "Deceased",
            }
        ),
    )
    start_date = DateFilter(
        field_name="DE_06",
        lookup_expr="gte",
        label="Earliest DOD",
        widget=DateInput(attrs={"class": "form-date datepicker"}),
    )
    end_date = DateFilter(
        field_name="DE_06",
        lookup_expr="lte",
        label="Latest DOD",
        widget=DateInput(attrs={"class": "form-date datepicker"}),
    )

    class Meta:
        model = Death
        fields = []
