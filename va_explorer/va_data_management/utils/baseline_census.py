"""Helpers for the baseline-census province configuration."""

from django.db.models import Sum

from va_explorer.va_data_management.models import (
    BaselineCensusProvince,
    SRSEAPopulationTarget,
)

# location_level -> SRSEAPopulationTarget field, mirroring
# BaselineCensusProvinceTableComponent._LOCATION_FIELD_MAP so the coverage
# summary chart's estimate bars agree with the Census Table totals.
_TARGET_LOCATION_FIELDS = {
    "province": "province_name",
    "district": "district_name",
    "ward": "ward_name",
    "ea": "geoid",
}


def _distinct_province_names():
    """Distinct, sorted province names from the EA target table.

    ``.order_by("province_name")`` is required: ``SRSEAPopulationTarget`` has a
    default ``geoid`` ordering, and without overriding it Django adds ``geoid``
    to the ``SELECT DISTINCT`` and the de-duplication silently breaks.
    """
    return list(
        SRSEAPopulationTarget.objects.exclude(province_name="")
        .order_by("province_name")
        .values_list("province_name", flat=True)
        .distinct()
    )


def _province_flag_lookup():
    return {
        row.province_name: {
            "is_current": row.is_current,
            "is_active": row.is_active,
        }
        for row in BaselineCensusProvince.objects.all()
    }


def get_current_census_provinces():
    """Province names explicitly marked as part of the current census."""
    return list(
        BaselineCensusProvince.objects.filter(is_current=True).values_list(
            "province_name", flat=True
        )
    )


def get_excluded_census_provinces():
    """Province names explicitly excluded from the current baseline census.

    Provinces without a config row default to included, so only the rows
    flagged ``is_current=False`` are returned.
    """
    return list(
        BaselineCensusProvince.objects.filter(is_current=False).values_list(
            "province_name", flat=True
        )
    )


def get_active_census_provinces():
    """Province names explicitly marked active for the baseline dashboard."""
    return list(
        BaselineCensusProvince.objects.filter(is_active=True).values_list(
            "province_name", flat=True
        )
    )


def get_inactive_census_provinces():
    """Province names explicitly deactivated for the baseline dashboard."""
    return list(
        BaselineCensusProvince.objects.filter(is_active=False).values_list(
            "province_name", flat=True
        )
    )


def get_dashboard_excluded_census_provinces(include_current=False):
    """Province names hidden by the baseline dashboard's province rules."""
    excluded = set(get_inactive_census_provinces())
    if include_current:
        excluded.update(get_excluded_census_provinces())
    return sorted(excluded)


def get_baseline_census_province_config():
    """Every province in the EA target table with its current/active flags.

    Returns a list of ``{"province_name", "is_current", "is_active"}`` dicts
    ordered by name. A province with no explicit config row defaults to both
    ``True`` flags.
    """
    configured = _province_flag_lookup()
    return [
        {
            "province_name": name,
            "is_current": configured.get(name, {}).get("is_current", True),
            "is_active": configured.get(name, {}).get("is_active", True),
        }
        for name in _distinct_province_names()
    ]


def get_baseline_census_target_totals(
    *, excluded_provinces=None, location_level=None, location_value=None
):
    """Summed population/household targets for the coverage summary chart.

    Applies the same province exclusion and location filter the Census Table
    uses on ``SRSEAPopulationTarget`` so the chart's estimate bars agree with
    the table totals. Returns
    ``{"population_target": int, "household_target": int}``.
    """
    qs = SRSEAPopulationTarget.objects.all()
    if excluded_provinces:
        qs = qs.exclude(province_name__in=excluded_provinces)
    field = _TARGET_LOCATION_FIELDS.get((location_level or "").strip().lower())
    if field and location_value:
        qs = qs.filter(**{f"{field}__icontains": location_value})
    totals = qs.aggregate(
        population_target=Sum("population_target"),
        household_target=Sum("household_target"),
    )
    return {
        "population_target": totals["population_target"] or 0,
        "household_target": totals["household_target"] or 0,
    }


def save_baseline_census_province_config(
    checked_current_province_names, checked_active_province_names
):
    """Persist the current/active flags for every known province.

    Every distinct province in the EA target table is upserted so the
    configuration is always complete.
    """
    checked_current = {str(name).strip() for name in checked_current_province_names}
    checked_active = {str(name).strip() for name in checked_active_province_names}
    for name in _distinct_province_names():
        BaselineCensusProvince.objects.update_or_create(
            province_name=name,
            defaults={
                "is_current": name in checked_current,
                "is_active": name in checked_active,
            },
        )
