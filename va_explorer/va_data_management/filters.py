from crispy_forms.helper import FormHelper
from django import forms
from django.core.exceptions import ValidationError
from django.forms import TextInput
from django_filters import CharFilter, DateFilter, FilterSet
from fuzzywuzzy import fuzz

from .models import VASA, Death, Household, Pregnancy, PregnancyOutcome, VerbalAutopsy


def validate_integer(value: str):
    if not str(value).isdigit():
        raise ValidationError(
            ("%(value)s is invalid or negative. Please enter only numbers"),
            params={"value": value},
        )


class DateInput(forms.DateInput):
    input_type = "date"


def date_filter_widget(placeholder: str):
    return DateInput(
        attrs={
            "class": "form-date datepicker",
            "placeholder": placeholder,
            "aria-label": placeholder,
        }
    )


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

    field_order = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.form.helper = FormHelper()
        self.form.helper.form_tag = False
        self.form.helper.disable_csrf = True
        if self.field_order:
            self.form.order_fields(self.field_order)


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
        label="Earliest",
        widget=date_filter_widget("Earliest Date"),
    )
    end_date = DateFilter(
        field_name="Id10023",
        lookup_expr="lte",
        label="Latest",
        widget=date_filter_widget("Latest Date"),
    )
    cause = CharFilter(
        field_name="causes__cause",
        lookup_expr="icontains",
        label="",
        widget=TextInput(
            attrs={"class": "form-text", "placeholder": "Cause", "aria-label": "Cause"}
        ),
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


class VASAFilter(VAFilter):
    """VASA line-list filter.

    Reuses every VAFilter field (interviewer/deceased/district/date/cause) — VASA
    shares those WHO columns via BaseVerbalAutopsy — so the same GET params filter
    the VASA leg of the merged verbal-autopsy line list identically.
    """

    class Meta:
        model = VASA
        fields = []


# =========================
# Pregnancy list
# =========================
class PregnancyFilter(GeoEnumeratorFilterSet):
    field_order = ("id", "enumerator", "district", "ward", "start_date", "end_date")

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
        label="Earliest",
        widget=date_filter_widget("Earliest Date"),
    )
    end_date = DateFilter(
        field_name="created",
        lookup_expr="lte",
        label="Latest",
        widget=date_filter_widget("Latest Date"),
    )

    class Meta:
        model = Pregnancy
        fields = []


# =========================
# Household list
# =========================
class HouseholdFilter(GeoEnumeratorFilterSet):
    field_order = ("id", "enumerator", "district", "ward")

    id = CharFilter(
        field_name="id",
        lookup_expr="icontains",
        label="",
        widget=TextInput(
            attrs={"class": "form-text", "placeholder": "ID", "aria-label": "ID"}
        ),
    )

    class Meta:
        model = Household
        fields = []


# =========================
# Pregnancy Outcome list
# =========================
class PregnancyOutcomeFilter(GeoEnumeratorFilterSet):
    field_order = (
        "enumerator",
        "district",
        "ward",
        "outcome_type",
        "start_date",
        "end_date",
    )

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
        label="Earliest",
        widget=date_filter_widget("Earliest Date"),
    )
    end_date = DateFilter(
        field_name="PO_41",
        lookup_expr="lte",
        label="Latest",
        widget=date_filter_widget("Latest Date"),
    )

    class Meta:
        model = PregnancyOutcome
        fields = []


# =========================
# Death list
# =========================
class DeathFilter(GeoEnumeratorFilterSet):
    field_order = (
        "enumerator",
        "district",
        "ward",
        "deceased",
        "start_date",
        "end_date",
    )

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
        label="Earliest",
        widget=date_filter_widget("Earliest Date"),
    )
    end_date = DateFilter(
        field_name="DE_06",
        lookup_expr="lte",
        label="Latest",
        widget=date_filter_widget("Latest Date"),
    )

    class Meta:
        model = Death
        fields = []
