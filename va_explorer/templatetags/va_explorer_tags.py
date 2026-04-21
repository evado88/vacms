import numbers
import os
import re
from datetime import date, datetime

from django import template
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from va_explorer.va_data_management.constants import PII_FIELDS, REDACTED_STRING
from va_explorer.va_data_management.utils.date_parsing import parse_date

register = template.Library()


@register.filter
def format_enumerator(value):
    if not value:
        return value
    # Remove trailing underscore(s) followed by digits (e.g. _123 or _1_2)
    cleaned = re.sub(r"(_\d+)+$", "", str(value))
    # Replace remaining underscores with spaces
    return cleaned.replace("_", " ")


ISO_DATE_FORMAT = "%Y-%m-%d"
ISO_DATETIME_FORMAT = "%Y-%m-%d %H:%M"
STANDARDIZED_DATE_FIELDS = {
    "submissiondate",
    "submission_date",
    "today",
    "start",
    "end",
    "next_visit_date",
    "submit_time",
    "date_of_death",
    "pe_07",
    "pe_09a",
    "pe_10a",
    "pe_21",
    "po_05",
    "po_18",
    "po_19",
    "po_24",
    "po_41",
    "de_04",
    "de_06",
    "de_20",
    "de_27",
    "deathdate",
    "interviewed",
    "id10011",
    "id10012",
    "id10021",
    "id10023",
    "id10023_a",
    "id10023_b",
}
STANDARDIZED_DATE_FIELDS = {name.lower() for name in STANDARDIZED_DATE_FIELDS}
HIDDEN_LIST_FIELDS = {
    "household_descriptors",
    "consented",
    "pe_04",
    "po_group",
    "informant",
    "antenatal_card_details",
}


def _format_standard_date(value, include_time=False):
    if value is None:
        return ""
    if isinstance(value, datetime):
        localized = timezone.localtime(value) if timezone.is_aware(value) else value
        if include_time:
            return localized.strftime(ISO_DATETIME_FORMAT)
        return localized.date().strftime(ISO_DATE_FORMAT)
    if isinstance(value, date):
        return value.strftime(ISO_DATE_FORMAT)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ""
        lowered = stripped.lower()
        if lowered in {"dk", "nan"}:
            return stripped
        parsed = parse_date(stripped, return_format=ISO_DATE_FORMAT)
        return parsed
    return value


@register.filter
def standard_date(value):
    """
    Render a value as a YYYY-MM-DD string when possible, otherwise return the original value.
    """
    return _format_standard_date(value, include_time=False)


@register.filter
def standard_datetime(value):
    """
    Render a datetime value as YYYY-MM-DD HH:MM when possible.
    """
    return _format_standard_date(value, include_time=True)


@register.filter
def format_date_field(value, field_name):
    """
    Standardize display of date-like survey fields that are stored as text.
    """
    if field_name and str(field_name).lower() in STANDARDIZED_DATE_FIELDS:
        return standard_date(value)
    return value


@register.filter
def hide_list_field(field_name):
    """
    Returns True when a field should be hidden from record list tables.
    """
    return str(field_name).lower() in HIDDEN_LIST_FIELDS


@register.filter
def is_production(settings):
    return os.environ.get("DJANGO_SETTINGS_MODULE") == "config.settings.production"


@register.filter
def replace(value):
    if isinstance(value, str):
        value = value.strip()
        value_lowercase = value.lower()
        return {
            "dk": "Don't Know",
            "nan": "N/A",
            "veryl": "Very Low",
            "ref": "Refuse to Answer",
        }.get(value_lowercase, value)
    else:
        return value


@register.simple_tag(takes_context=True)
def active(context, pattern_or_url):
    try:
        pattern = reverse(pattern_or_url)
    except NoReverseMatch:
        pattern = pattern_or_url

    path = context["request"].path

    if re.search(pattern, path):
        return "active"
    return ""


@register.filter
def is_numeric(value):
    return isinstance(value, numbers.Number)


@register.simple_tag(takes_context=True)
def pii_filter(context, field, value):
    if field in PII_FIELDS and not context["user"].can_view_pii:
        return REDACTED_STRING
    return value


@register.simple_tag(takes_context=True)
def param_replace(context, **kwargs):
    """
    Return encoded URL parameters that are the same as the current
    request's parameters, only with the specified GET parameters added or changed.
    It also removes any empty parameters to keep things neat,
    so you can remove a parm by setting it to ``""``.
    For example, if you're on the page ``/things/?with_frosting=true&page=5``,
    then
    <a href="/things/?{% param_replace page=3 %}">Page 3</a>
    would expand to
    <a href="/things/?with_frosting=true&page=3">Page 3</a>
    Based on
    https://stackoverflow.com/questions/22734695/next-and-before-links-for-a-django-paginated-query/22735278#22735278
    """
    d = context["request"].GET.copy()
    for k, v in kwargs.items():
        # check for order_by key for sorting and flip direction
        if k == "order_by":
            existing_value = d.get(k, "")
            if existing_value.startswith("-"):
                v = v.lstrip("-")
            else:
                v = "-" + v if not v.startswith("-") else v
            d[k] = v

        # for all other fields, just override previous value
        else:
            d[k] = v
    keys = [k for k, v in d.items() if not v]
    for k in keys:
        del d[k]
    return d.urlencode()


@register.simple_tag(takes_context=True)
def sort_url(context, value, direction=""):
    sort_value = direction + value
    return param_replace(context, order_by=sort_value)


@register.filter
def has_group(user, group_name):
    if not getattr(user, "is_authenticated", False):
        return False
    return user.groups.filter(name=group_name).exists()
