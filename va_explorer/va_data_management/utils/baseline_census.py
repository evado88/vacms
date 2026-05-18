"""Helpers for the *current baseline census* province configuration.

A province is part of the current baseline census unless an admin explicitly
marks it out via the admin panel's "Baseline Census Config" section (which is
persisted as :class:`BaselineCensusProvince` rows). The Baseline Census
Dashboard's "Current Baseline Census" filter uses these helpers to decide which
provinces' data to show in the table, charts and KPI cards.
"""

from va_explorer.va_data_management.models import (
    BaselineCensusProvince,
    SRSEAPopulationTarget,
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


def get_baseline_census_province_config():
    """Every province in the EA target table with its current-census flag.

    Returns a list of ``{"province_name", "is_current"}`` dicts ordered by
    name. A province with no explicit config row defaults to included.
    """
    provinces = (
        SRSEAPopulationTarget.objects.exclude(province_name="")
        .values_list("province_name", flat=True)
        .distinct()
    )
    excluded = set(get_excluded_census_provinces())
    return [
        {"province_name": name, "is_current": name not in excluded}
        for name in sorted(provinces)
    ]


def save_baseline_census_province_config(checked_province_names):
    """Persist the current-census flag for every known province.

    ``checked_province_names`` is the set of provinces the admin left checked
    (part of the current census). Every distinct province in the EA target
    table is upserted so the configuration is always complete.
    """
    checked = {str(name).strip() for name in checked_province_names}
    all_provinces = (
        SRSEAPopulationTarget.objects.exclude(province_name="")
        .values_list("province_name", flat=True)
        .distinct()
    )
    for name in all_provinces:
        BaselineCensusProvince.objects.update_or_create(
            province_name=name,
            defaults={"is_current": name in checked},
        )
