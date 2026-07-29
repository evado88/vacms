import re

from crispy_forms.helper import FormHelper
from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms import TextInput
from django_filters import CharFilter, DateFilter, FilterSet
from fuzzywuzzy import fuzz

from .models import (
    VASA,
    Death,
    Household,
    ODKFormChoice,
    Pregnancy,
    PregnancyOutcome,
    VerbalAutopsy,
)


def _normalize_odk_code(value):
    text = str(value or "").strip()
    if re.fullmatch(r"\d+", text):
        return str(int(text))
    if re.fullmatch(r"\d+\.0+", text):
        return str(int(text.split(".", 1)[0]))
    return text


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
        method="filter_district",
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
        method="filter_ward",
        label="",
        widget=TextInput(
            attrs={
                "class": "form-text",
                "placeholder": "Ward",
                "aria-label": "Ward",
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
        value = str(value or "").strip()
        if value:
            threshold = 75
            needle = " ".join(str(value).split()).casefold()
            compact_needle = needle.replace(" ", "")
            vas = queryset.values("id", "Id10017", "Id10018")
            matches = [
                va.get("id")
                for va in vas
                if self._deceased_name_matches(
                    va,
                    value,
                    needle,
                    compact_needle,
                    threshold,
                )
            ]
            return queryset.filter(id__in=matches)
        return queryset

    @staticmethod
    def _deceased_name_matches(va, value, needle, compact_needle, threshold):
        candidate = " ".join(
            part for part in [va.get("Id10017"), va.get("Id10018")] if part
        )
        normalized_candidate = " ".join(candidate.split()).casefold()
        return (
            needle in normalized_candidate
            or compact_needle in normalized_candidate.replace(" ", "")
            or fuzz.token_set_ratio(value, candidate) > threshold
        )

    def filter_district(self, queryset, name, value):
        return self._filter_decoded_geography(
            queryset, "district", value, fallback_fields=("area",)
        )

    def filter_ward(self, queryset, name, value):
        return self._filter_decoded_geography(queryset, "ward", value)

    @staticmethod
    def _filter_decoded_geography(queryset, field_name, value, fallback_fields=()):
        value = str(value or "").strip()
        if not value:
            return queryset

        matching_codes = {
            _normalize_odk_code(raw_code)
            for raw_code in ODKFormChoice.objects.filter(
                form_name="pregnancy",
                field_name=field_name,
                label__icontains=value,
            ).values_list("value", flat=True)
        }
        stored_code_matches = [
            raw_value
            for raw_value in queryset.values_list(field_name, flat=True).distinct()
            if _normalize_odk_code(raw_value) in matching_codes
        ]
        geography_filter = Q(**{f"{field_name}__icontains": value}) | Q(
            **{f"{field_name}__in": stored_code_matches}
        )
        for fallback_field in fallback_fields:
            geography_filter |= Q(**{f"{fallback_field}__icontains": value})
        return queryset.filter(geography_filter)


class VASAFilter(VAFilter):
    """VASA line-list filter.

    Reuses every VAFilter field (interviewer/deceased/district/ward/date/cause) —
    VASA shares those WHO columns via BaseVerbalAutopsy — so the same GET params
    filter the VASA leg of the merged verbal-autopsy line list identically.
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
        "ea",
        "deceased",
        "start_date",
        "end_date",
    )

    ea = CharFilter(
        field_name="ea",
        lookup_expr="icontains",
        label="",
        widget=TextInput(
            attrs={"class": "form-text", "placeholder": "EA", "aria-label": "EA"}
        ),
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
