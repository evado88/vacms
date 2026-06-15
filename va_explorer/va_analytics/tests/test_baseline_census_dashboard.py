import csv
import io
from datetime import timedelta
from datetime import timezone as dt_timezone

import pytest
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from va_explorer.tests.factories import AdminFactory, GroupFactory, UserFactory
from va_explorer.va_data_management.models import (
    BaselineCensusProvince,
    Death,
    Household,
    HouseholdMember,
    ODKFormChoice,
    Pregnancy,
    PregnancyOutcome,
    SRSClusterLocation,
    SRSEAPopulationTarget,
)
from va_explorer.va_data_management.utils.baseline_census import (
    get_active_census_provinces,
    get_baseline_census_province_config,
    get_baseline_census_target_totals,
    get_current_census_provinces,
    get_excluded_census_provinces,
    get_inactive_census_provinces,
    save_baseline_census_province_config,
)

pytestmark = pytest.mark.django_db


def _dashboard_user():
    permission = Permission.objects.filter(codename="view_dashboard").first()
    group = GroupFactory.create(permissions=[permission] if permission else [])
    return UserFactory.create(groups=[group])


def _unprivileged_user():
    group = GroupFactory.create(permissions=[])
    return UserFactory.create(groups=[group])


class TestBaselineCensusDashboardRoute:
    def test_returns_200_with_view_permission(self):
        client = Client()
        client.force_login(user=_dashboard_user())

        response = client.get(reverse("va_analytics:baseline-census-dashboard"))

        assert response.status_code == 200

    def test_renders_only_target_tabs(self):
        client = Client()
        client.force_login(user=_dashboard_user())

        response = client.get(reverse("va_analytics:baseline-census-dashboard"))
        body = response.content.decode()

        assert 'data-tab="overview"' in body
        assert 'data-tab="table"' in body
        assert 'data-tab="trends"' not in body
        assert 'data-tab="csa_supervision"' not in body
        assert 'data-tab="va_statistics"' not in body
        assert 'data-tab="mso_supervision"' not in body
        assert 'data-tab="rumor_overview"' not in body
        assert 'data-tab="rumor_details"' not in body
        assert 'id="baselineLocationLevel"' in body
        assert 'id="baselineLocationValue"' in body
        assert "baseline-shared-filters-row" in body

    def test_shell_uses_baseline_census_data_shell(self):
        client = Client()
        client.force_login(user=_dashboard_user())

        response = client.get(reverse("va_analytics:baseline-census-dashboard"))
        body = response.content.decode()

        assert 'data-shell="baseline-census"' in body


class TestBaselineCensusSidebarWiring:
    def test_link_targets_new_route_and_marks_active(self):
        client = Client()
        client.force_login(user=_dashboard_user())

        response = client.get(reverse("va_analytics:baseline-census-dashboard"))
        body = response.content.decode()
        target_url = reverse("va_analytics:baseline-census-dashboard")

        assert f'href="{target_url}"' in body
        # The sidebar link wraps the Baseline Census label; the active class
        # is applied when visiting the new route.
        assert "Baseline Census Dashboard" in body
        active_link_marker = f'class="nav-link active"\n            href="{target_url}"'
        assert active_link_marker in body


class TestBaselineCensusComponentEndpoints:
    def test_overview_kpis_component_returns_left_side_cards(self):
        client = Client()
        client.force_login(user=_dashboard_user())

        response = client.get(
            reverse("va_analytics:baseline-census-overview-kpis-component")
        )

        assert response.status_code == 200
        body = response.content.decode()
        assert 'id="nov-kpi-total-eas"' in body
        assert 'id="nov-kpi-total-households"' in body
        assert 'id="nov-kpi-total-people"' in body
        # Pregnancy, pregnancy outcome, and death KPI cards are now included.
        assert 'id="nov-kpi-total-pregnancies"' in body
        assert 'id="nov-kpi-total-preg-outcomes"' in body
        assert 'id="nov-kpi-total-deaths"' in body
        # Verbal autopsy cards are not shown on the baseline census overview.
        assert 'id="nov-kpi-total-vas"' not in body

    def test_province_table_component_renders_search_inputs_and_columns(self):
        client = Client()
        client.force_login(user=_dashboard_user())

        response = client.get(
            reverse("va_analytics:baseline-census-province-table-component")
        )

        assert response.status_code == 200
        body = response.content.decode()
        assert 'id="bcProvinceSearch"' in body
        assert 'id="bcDistrictSearch"' in body
        assert 'id="bcWardSearch"' in body
        assert 'id="bcEaSearch"' in body
        assert "baseline-census-search-group" in body
        assert "baseline-census-table" in body
        assert "baseline-census-pagination" in body
        assert "HH Enumerated" in body
        assert "Total Deaths" in body
        assert "Total Pregnancies" in body
        assert "Total Preg. Outcomes" in body
        assert "Download CSV" in body
        assert reverse("va_analytics:baseline-census-province-table-csv-export") in body


class TestBaselineCensusDataEndpoints:
    def test_overview_filter_data_endpoint_returns_kpis(self):
        client = Client()
        client.force_login(user=_dashboard_user())

        response = client.get(
            reverse("va_analytics:baseline-census-overview-filter-data")
            + "?kpis_only=1"
        )

        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        payload = response.json()
        assert "kpis" in payload

    def test_overview_map_data_endpoint_returns_json(self):
        client = Client()
        client.force_login(user=_dashboard_user())

        response = client.get(reverse("va_analytics:baseline-census-overview-map-data"))

        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")


class TestBackwardsCompat:
    def test_legacy_dashboard_route_still_redirects_to_outcomes(self):
        client = Client()
        client.force_login(user=_dashboard_user())

        response = client.get(reverse("va_analytics:dashboard"))

        assert response.status_code == 302
        assert reverse("va_analytics:outcomes-dashboard") in response["Location"]
        assert "tab=pregnancies" in response["Location"]


# --- Baseline Census Table tab: target-driven rows + enumeration enrichment ---

GEOID_A = "01021011002"  # EA with census records
GEOID_B = "02030011004"  # EA in the target table with no enumeration yet
GEOID_CODED = "01021011055"  # EA whose vital events use ODK coded geography
GEOID_FALLBACK = "01021011077"  # EA populated on Household.ea, not cluster
GEOID_UNMATCHED = "09021022004"  # EA absent from the target table entirely


def _iso(dt):
    """ISO-8601 UTC text, mirroring how ODK stores ``submissiondate``."""
    return dt.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _build_baseline_census_fixture():
    """Two target EAs; EA A has 3 recent + 1 old household, EA B has none."""
    now = timezone.now()
    recent = _iso(now - timedelta(days=5))
    old = _iso(now - timedelta(days=200))

    # EA A — targets chosen so the rolling-window coverage lands at 75%.
    SRSEAPopulationTarget.objects.create(
        geoid=GEOID_A,
        province_name="CENTRAL",
        district_name="CHISAMBA",
        constituency_name="CHISAMBA CENTRAL",
        ward_name="CHAMUKA",
        household_target=4,
        population_target=4,
    )
    cluster_a = SRSClusterLocation.add_root(
        name=GEOID_A, location_type="ea", code="A1", status="Active"
    )
    # EA B — present in the target table but with zero enumeration records.
    SRSEAPopulationTarget.objects.create(
        geoid=GEOID_B,
        province_name="COPPERBELT",
        district_name="KITWE",
        constituency_name="KITWE CENTRAL",
        ward_name="CHAMBISHI",
        household_target=0,
        population_target=50,
    )

    hh1 = Household.objects.create(
        key="hh-a-1",
        cluster=cluster_a,
        submissiondate=recent,
        consent="Yes",
        result_list="Completed",
    )
    Household.objects.create(
        key="hh-a-2",
        cluster=cluster_a,
        submissiondate=recent,
        consent="No",
        result_list="Completed",  # refusal via consent=No
    )
    Household.objects.create(
        key="hh-a-3",
        cluster=cluster_a,
        submissiondate=recent,
        consent="Yes",
        result_list="Non-contact",  # non-contact
    )
    hh_old = Household.objects.create(
        key="hh-a-old",
        cluster=cluster_a,
        submissiondate=old,
        consent="Yes",
        result_list="Refused",  # outside the rolling window
    )

    for idx in range(3):  # 3 members in the rolling window
        HouseholdMember.objects.create(household=hh1, first_name=f"M{idx}")
    HouseholdMember.objects.create(household=hh_old, first_name="Old")

    Pregnancy.objects.create(key="preg-a-1", ea="EA_" + GEOID_A, submissiondate=recent)
    Pregnancy.objects.create(key="preg-a-old", ea="EA_" + GEOID_A, submissiondate=old)
    # bulk_create skips the Death post_save signal, which would otherwise queue a
    # Celery task against an unreachable broker in the test environment.
    Death.objects.bulk_create(
        [Death(key="death-a-1", ea="EA_" + GEOID_A, submissiondate=recent)]
    )
    PregnancyOutcome.objects.create(
        key="po-a-1", cluster=cluster_a, submissiondate=recent
    )


def _seed_odk_geo_choices():
    cache.delete("baseline_census:odk_location_choice_lookup:v1")
    choices = {
        "province": ("1", "CENTRAL"),
        "district": ("2", "CHISAMBA"),
        "constituency": ("3", "CHISAMBA CENTRAL"),
        "ward": ("4", "CHAMUKA"),
        "ea": ("5", "EA_" + GEOID_CODED),
    }
    for form_name in ("pregnancy", "pregnancy_outcome", "death"):
        for field_name, (value, label) in choices.items():
            ODKFormChoice.objects.create(
                form_name=form_name,
                field_name=field_name,
                value=value,
                label=label,
            )


def _build_coded_vital_events_fixture():
    now = timezone.now()
    recent = _iso(now - timedelta(days=5))
    _seed_odk_geo_choices()
    SRSEAPopulationTarget.objects.create(
        geoid=GEOID_CODED,
        province_name="CENTRAL",
        district_name="CHISAMBA",
        constituency_name="CHISAMBA CENTRAL",
        ward_name="CHAMUKA",
        household_target=0,
        population_target=0,
    )
    coded_geo = {
        "province": "01",
        "district": "2.0",
        "constituency": "003",
        "ward": "4",
        "ea": "5",
    }
    Pregnancy.objects.create(key="preg-coded", submissiondate=recent, **coded_geo)
    Death.objects.bulk_create(
        [Death(key="death-coded", submissiondate=recent, **coded_geo)]
    )
    PregnancyOutcome.objects.create(key="po-coded", submissiondate=recent, **coded_geo)


def _build_household_ea_fallback_fixture():
    now = timezone.now()
    recent = _iso(now - timedelta(days=5))
    SRSEAPopulationTarget.objects.create(
        geoid=GEOID_FALLBACK,
        province_name="CENTRAL",
        district_name="CHISAMBA",
        constituency_name="CHISAMBA CENTRAL",
        ward_name="CHAMUKA",
        household_target=1,
        population_target=3,
    )
    household = Household.objects.create(
        key="hh-ea-fallback",
        cluster=None,
        ea="EA_" + GEOID_FALLBACK,
        submissiondate=recent,
        consent="No",
        result_list="Non-contact",
    )
    for idx in range(3):
        HouseholdMember.objects.create(
            household=household,
            first_name=f"Fallback member {idx}",
        )


def _build_unmatched_ea_fixture():
    """One CENTRAL target EA with an enumerated household inside it, plus a
    second household whose EA is absent from the target table. The unmatched
    household still names CENTRAL/CHISAMBA (in mixed case) so the chart can
    attribute it by administrative text rather than dropping it."""
    now = timezone.now()
    recent = _iso(now - timedelta(days=5))

    SRSEAPopulationTarget.objects.create(
        geoid=GEOID_A,
        province_name="CENTRAL",
        district_name="CHISAMBA",
        constituency_name="CHISAMBA CENTRAL",
        ward_name="CHAMUKA",
        household_target=10,
        population_target=20,
    )
    cluster_a = SRSClusterLocation.add_root(
        name=GEOID_A, location_type="ea", code="A1", status="Active"
    )
    resolved_hh = Household.objects.create(
        key="hh-resolved",
        cluster=cluster_a,
        submissiondate=recent,
    )
    for idx in range(2):
        HouseholdMember.objects.create(
            household=resolved_hh, first_name=f"Resolved {idx}"
        )

    unmatched_hh = Household.objects.create(
        key="hh-unmatched",
        cluster=None,
        ea=GEOID_UNMATCHED,
        province="Central",
        district="Chisamba",
        constituency="Chisamba Central",
        ward="Chamuka",
        submissiondate=recent,
    )
    for idx in range(3):
        HouseholdMember.objects.create(
            household=unmatched_hh, first_name=f"Unmatched {idx}"
        )


def _client():
    client = Client()
    client.force_login(user=_dashboard_user())
    return client


def _table_response(client, **params):
    response = client.get(
        reverse("va_analytics:baseline-census-province-table-component"), params
    )
    assert response.status_code == 200
    return response


def _table_rows(client, **params):
    """EA rows on the active ward page only, keyed by geoid."""
    response = _table_response(client, **params)
    return {row["ea"]: row for row in response.context["bc_stats"]}


def _all_table_rows(client, **params):
    """Merge EA rows across every ward page into one dict keyed by geoid."""
    merged = {}
    page = 1
    while True:
        response = _table_response(client, **{**params, "bc_page": page})
        merged.update({row["ea"]: row for row in response.context["bc_stats"]})
        if not response.context["bc_has_next"]:
            break
        page += 1
    return merged


def _csv_response_rows(response):
    return list(csv.DictReader(io.StringIO(response.content.decode())))


class TestBaselineCensusTableData:
    def test_rows_sourced_from_target_table(self):
        _build_baseline_census_fixture()
        client = Client()
        client.force_login(user=_dashboard_user())

        rows = _all_table_rows(client)

        assert set(rows) == {GEOID_A, GEOID_B}
        row_a = rows[GEOID_A]
        assert row_a["province"] == "CENTRAL"
        assert row_a["district"] == "CHISAMBA"
        assert row_a["ward"] == "CHAMUKA"
        assert row_a["hh_estimate"] == 4
        assert row_a["pop_estimate"] == 4

    def test_ea_without_enumeration_still_appears(self):
        _build_baseline_census_fixture()
        client = Client()
        client.force_login(user=_dashboard_user())

        row_b = _all_table_rows(client)[GEOID_B]

        assert row_b["hh_enumerated"] == 0
        assert row_b["pop_enumerated"] == 0
        assert row_b["total_deaths"] == 0
        assert row_b["pop_estimate"] == 50

    def test_default_aggregation_includes_every_record(self):
        _build_baseline_census_fixture()
        client = Client()
        client.force_login(user=_dashboard_user())

        # No rolling window: the "Current Baseline Census" filter no longer
        # bounds by date, so every record (recent and old) is aggregated.
        row_a = _table_rows(client)[GEOID_A]

        assert row_a["hh_enumerated"] == 4
        assert row_a["pop_enumerated"] == 4
        assert row_a["refusals"] == 2  # consent="No" + the old result="Refused"
        assert row_a["non_contact"] == 1
        assert row_a["total_pregnancies"] == 2
        assert row_a["total_deaths"] == 1
        assert row_a["total_preg_outcomes"] == 1

    def test_population_and_households_fallback_to_household_ea_when_cluster_missing(
        self,
    ):
        _build_household_ea_fallback_fixture()

        row = _table_rows(_client())[GEOID_FALLBACK]

        assert row["hh_enumerated"] == 1
        assert row["pop_enumerated"] == 3
        assert row["hh_coverage"] == 100.0
        assert row["pop_coverage"] == 100.0
        assert row["refusals"] == 1
        assert row["non_contact"] == 1

    def test_current_census_excludes_unconfigured_province(self):
        _build_baseline_census_fixture()
        # COPPERBELT (EA B) is configured out of the current baseline census.
        BaselineCensusProvince.objects.create(
            province_name="COPPERBELT", is_current=False
        )
        client = Client()
        client.force_login(user=_dashboard_user())

        # bc_current off by default -> both activated provinces are shown.
        all_rows = _all_table_rows(client)
        assert set(all_rows) == {GEOID_A, GEOID_B}

        # bc_current on -> COPPERBELT is dropped from the table.
        current_rows = _all_table_rows(client, bc_current="1")
        assert set(current_rows) == {GEOID_A}

    def test_inactive_province_is_always_excluded(self):
        _build_baseline_census_fixture()
        BaselineCensusProvince.objects.create(
            province_name="COPPERBELT", is_current=True, is_active=False
        )
        client = Client()
        client.force_login(user=_dashboard_user())

        current_rows = _all_table_rows(client)
        assert set(current_rows) == {GEOID_A}

        all_rows = _all_table_rows(client, bc_current="0")
        assert set(all_rows) == {GEOID_A}

    def test_explicit_preset_applies_time_window(self):
        _build_baseline_census_fixture()
        client = Client()
        client.force_login(user=_dashboard_user())

        # An explicit last-24h preset bounds by date: records 5 days old drop out.
        row_a = _table_rows(client, preset="24", bc_current="1")[GEOID_A]

        assert row_a["hh_enumerated"] == 0
        assert row_a["total_pregnancies"] == 0

    def test_coverage_calculation_and_bands(self):
        _build_baseline_census_fixture()
        client = Client()
        client.force_login(user=_dashboard_user())

        rows = _all_table_rows(client)
        row_a = rows[GEOID_A]
        row_b = rows[GEOID_B]

        # EA A: 4 / 4 -> 100.0% -> "light-green" band.
        assert row_a["hh_coverage"] == 100.0
        assert row_a["hh_coverage_band"] == "light-green"
        assert row_a["pop_coverage"] == 100.0
        assert row_a["pop_coverage_band"] == "light-green"

        # EA B: zero household target -> no division, coverage is None / "empty".
        assert row_b["hh_coverage"] is None
        assert row_b["hh_coverage_band"] == "empty"
        # Zero enumeration against a real population target -> 0.0% -> "red".
        assert row_b["pop_coverage"] == 0.0
        assert row_b["pop_coverage_band"] == "red"

    def test_coverage_cells_rendered_with_band_classes(self):
        _build_baseline_census_fixture()
        client = Client()
        client.force_login(user=_dashboard_user())

        response = client.get(
            reverse("va_analytics:baseline-census-province-table-component")
        )
        body = response.content.decode()

        assert "coverage-cell coverage-cell--light-green" in body

    def test_location_filter_narrows_to_province(self):
        _build_baseline_census_fixture()
        client = Client()
        client.force_login(user=_dashboard_user())

        rows = _table_rows(
            client, location_level="province", location_value="COPPERBELT"
        )

        assert set(rows) == {GEOID_B}

    def test_vital_event_counts_resolve_odk_coded_geography(self):
        _build_coded_vital_events_fixture()

        row = _table_rows(_client())[GEOID_CODED]

        assert row["total_deaths"] == 1
        assert row["total_pregnancies"] == 1
        assert row["total_preg_outcomes"] == 1

    def test_csv_export_returns_full_dataset_and_ignores_normal_filters(self):
        _build_baseline_census_fixture()
        client = _client()

        response = client.get(
            reverse("va_analytics:baseline-census-province-table-csv-export"),
            {
                "location_level": "province",
                "location_value": "COPPERBELT",
                "bc_province_search": "COPPERBELT",
                "bc_page": "99",
            },
        )

        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
        assert "attachment;" in response["Content-Disposition"]
        rows = _csv_response_rows(response)
        by_ea = {row["EA"]: row for row in rows if row["EA"]}
        assert {GEOID_A, GEOID_B}.issubset(by_ea)
        assert by_ea[GEOID_A]["Province"] == "CENTRAL"
        assert by_ea[GEOID_A]["HH Enumerated"] == "4"
        assert by_ea[GEOID_A]["Pop. Coverage %"] == "100.0"

    def test_csv_export_applies_current_census_only_when_requested(self):
        _build_baseline_census_fixture()
        BaselineCensusProvince.objects.create(
            province_name="COPPERBELT", is_current=False
        )
        client = _client()

        all_response = client.get(
            reverse("va_analytics:baseline-census-province-table-csv-export"),
            {"current_census": "false"},
        )
        current_response = client.get(
            reverse("va_analytics:baseline-census-province-table-csv-export"),
            {"current_census": "true"},
        )

        all_eas = {row["EA"] for row in _csv_response_rows(all_response) if row["EA"]}
        current_eas = {
            row["EA"] for row in _csv_response_rows(current_response) if row["EA"]
        }
        assert {GEOID_A, GEOID_B}.issubset(all_eas)
        assert current_eas == {GEOID_A}

    def test_csv_export_empty_dataset_has_headers(self):
        response = _client().get(
            reverse("va_analytics:baseline-census-province-table-csv-export")
        )

        assert response.status_code == 200
        assert (
            response.content.decode()
            .splitlines()[0]
            .startswith("Province,District,Ward,EA")
        )
        assert _csv_response_rows(response) == []


class TestBaselineCensusSharedFilterRendering:
    def test_dashboard_renders_one_shared_filter_row_above_tabs(self):
        client = Client()
        client.force_login(user=_dashboard_user())

        response = client.get(reverse("va_analytics:baseline-census-dashboard"))
        body = response.content.decode()

        assert body.count("baseline-shared-filters-row") == 1
        assert "baseline-table-filters-row" not in body
        assert "baseline-chart-filters-row" not in body
        assert "nov-filters-row" not in body
        assert body.index('id="baselineSharedFilters"') < body.index(
            'id="baselineCensusTabs"'
        )

    def test_shared_filter_controls_include_required_union(self):
        client = Client()
        client.force_login(user=_dashboard_user())

        response = client.get(reverse("va_analytics:baseline-census-dashboard"))
        body = response.content.decode()

        assert 'id="baselineCurrentCensus"' in body
        assert 'id="baselineCurrentCensus" checked' not in body
        assert 'id="baselineStartDatetime"' in body
        assert 'id="baselineEndDatetime"' in body
        for level in ("national", "province", "district", "constituency", "ward", "ea"):
            assert f'<option value="{level}"' in body
        assert 'id="tableCurrentCensus"' not in body
        assert 'id="chartCurrentCensus"' not in body
        assert 'id="novCurrentCensus"' not in body


# --- Ward-grouped pagination + ward/grand total summary rows -------------------

WARD_ALPHA_EA_1 = "11000000001"
WARD_ALPHA_EA_2 = "11000000002"
WARD_BETA_EA = "22000000001"


def _build_multi_ea_ward_fixture():
    """Ward ALPHA holds two EAs, ward BETA holds one.

    Recent household records give ALPHA 8 enumerated households (5 + 3) and
    BETA 4, so the ward- and grand-total maths have distinct, checkable values.
    """
    recent = _iso(timezone.now() - timedelta(days=5))
    specs = (
        (WARD_ALPHA_EA_1, "PROV1", "DIST1", "ALPHA", 10, 20, 5),
        (WARD_ALPHA_EA_2, "PROV1", "DIST1", "ALPHA", 10, 20, 3),
        (WARD_BETA_EA, "PROV2", "DIST2", "BETA", 8, 16, 4),
    )
    for geoid, province, district, ward, hh_target, pop_target, enumerated in specs:
        SRSEAPopulationTarget.objects.create(
            geoid=geoid,
            province_name=province,
            district_name=district,
            ward_name=ward,
            household_target=hh_target,
            population_target=pop_target,
        )
        cluster = SRSClusterLocation.add_root(
            name=geoid, location_type="ea", code=geoid, status="Active"
        )
        for idx in range(enumerated):
            Household.objects.create(
                key=f"hh-{geoid}-{idx}",
                cluster=cluster,
                submissiondate=recent,
                consent="Yes",
                result_list="Completed",
            )


class TestBaselineCensusWardPagination:
    def test_one_ward_per_page_keeps_eas_together(self):
        _build_multi_ea_ward_fixture()
        response = _table_response(_client())

        # Two wards -> two pages; page 1 holds both ALPHA EAs, never split.
        assert response.context["bc_total_pages"] == 2
        page_one = response.context["bc_stats"]
        assert {row["ward"] for row in page_one} == {"ALPHA"}
        assert {row["ea"] for row in page_one} == {WARD_ALPHA_EA_1, WARD_ALPHA_EA_2}

    def test_second_page_is_the_next_ward(self):
        _build_multi_ea_ward_fixture()
        response = _table_response(_client(), bc_page=2)

        assert {row["ward"] for row in response.context["bc_stats"]} == {"BETA"}

    def test_out_of_range_page_clamps_to_last_ward(self):
        _build_multi_ea_ward_fixture()
        response = _table_response(_client(), bc_page=99)

        assert response.context["bc_page"] == 2
        assert {row["ward"] for row in response.context["bc_stats"]} == {"BETA"}

    def test_ward_pages_ordered_by_the_sorted_column(self):
        _build_multi_ea_ward_fixture()
        # Sorted by enumerated households ascending: BETA (4) outranks ALPHA (8),
        # so it leads even though location order would place ALPHA first.
        response = _table_response(_client(), bc_sort="hh_enumerated", bc_dir="asc")

        assert {row["ward"] for row in response.context["bc_stats"]} == {"BETA"}

    def test_filter_rebuilds_pagination_to_matching_wards(self):
        _build_multi_ea_ward_fixture()
        response = _table_response(
            _client(), location_level="province", location_value="PROV2"
        )

        assert response.context["bc_total_pages"] == 1
        assert {row["ward"] for row in response.context["bc_stats"]} == {"BETA"}


class TestBaselineCensusSummaryRows:
    def test_ward_total_sums_the_active_ward(self):
        _build_multi_ea_ward_fixture()
        ward_total = _table_response(_client()).context["bc_ward_total"]

        # ALPHA: 5 + 3 enumerated against a 10 + 10 household target.
        assert ward_total["hh_estimate"] == 20
        assert ward_total["hh_enumerated"] == 8
        assert ward_total["hh_coverage"] == 40.0
        assert ward_total["hh_coverage_band"] == "red"

    def test_ward_total_changes_with_the_active_page(self):
        _build_multi_ea_ward_fixture()
        ward_total = _table_response(_client(), bc_page=2).context["bc_ward_total"]

        # BETA: 4 enumerated against an 8 household target -> 50%.
        assert ward_total["hh_enumerated"] == 4
        assert ward_total["hh_coverage"] == 50.0
        assert ward_total["hh_coverage_band"] == "amber"

    def test_grand_total_covers_every_filtered_ward(self):
        _build_multi_ea_ward_fixture()
        grand_total = _table_response(_client()).context["bc_grand_total"]

        # 5 + 3 + 4 enumerated against a 10 + 10 + 8 household target -> recomputed
        # coverage, not an average of the per-ward percentages.
        assert grand_total["hh_estimate"] == 28
        assert grand_total["hh_enumerated"] == 12
        assert grand_total["hh_coverage"] == 42.9
        assert grand_total["hh_coverage_band"] == "red"

    def test_grand_total_is_independent_of_the_active_page(self):
        _build_multi_ea_ward_fixture()
        page_two = _table_response(_client(), bc_page=2)

        assert page_two.context["bc_grand_total"]["hh_enumerated"] == 12

    def test_summary_rows_render_in_html(self):
        _build_multi_ea_ward_fixture()
        body = _table_response(_client()).content.decode()

        assert "bc-ward-total-row" in body
        assert "Ward Total / Average" in body
        assert "bc-grand-total-row" in body
        assert "Grand Total / Average" in body

    def test_summary_rows_absent_without_data(self):
        response = _table_response(_client())

        assert response.context["bc_ward_total"] is None
        assert response.context["bc_grand_total"] is None
        body = response.content.decode()
        assert "bc-ward-total-row" not in body
        assert "bc-grand-total-row" not in body


# --- Census Chart tab: drill-down JSON endpoint -------------------------------

CHART_URL = "va_analytics:baseline-census-chart-data"


def _chart_payload(client, **params):
    response = client.get(reverse(CHART_URL), params)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")
    return response.json()


def _chart_row(payload, value):
    """The single result row whose geography ``value`` matches."""
    return next(row for row in payload["results"] if row["value"] == value)


class TestBaselineCensusChartTab:
    def test_dashboard_renders_census_chart_tab(self):
        response = _client().get(reverse("va_analytics:baseline-census-dashboard"))
        body = response.content.decode()

        assert 'data-tab="chart"' in body
        assert 'id="tab-chart"' in body

    def test_census_chart_tab_renders_three_indicator_subtabs(self):
        response = _client().get(reverse("va_analytics:baseline-census-dashboard"))
        body = response.content.decode()

        assert 'id="censusChartTabs"' in body
        # Deaths, pregnancies and pregnancy outcomes are merged into one
        # "Vital Events" sub-tab and chart.
        for canvas_id in (
            "censusPopulationChart",
            "censusHouseholdsChart",
            "censusVitalEventsChart",
        ):
            assert f'id="{canvas_id}"' in body
        for stale_canvas_id in (
            "censusDeathsChart",
            "censusPregnanciesChart",
            "censusPregnancyOutcomesChart",
        ):
            assert f'id="{stale_canvas_id}"' not in body
        assert 'data-census-subtab="vital_events"' in body
        assert "Vital Events" in body
        # Population is the default active sub-tab.
        assert 'data-census-subtab="population" role="tab" aria-selected="true"' in body

    def test_chart_tab_query_param_marks_active(self):
        response = _client().get(
            reverse("va_analytics:baseline-census-dashboard") + "?tab=chart"
        )

        assert response.context["baseline_active_tab"] == "chart"


class TestBaselineCensusChartData:
    def test_requires_view_dashboard_permission(self):
        client = Client()
        client.force_login(user=_unprivileged_user())

        response = client.get(reverse(CHART_URL))

        assert response.status_code in (302, 403)

    def test_province_level_returned_by_default(self):
        _build_baseline_census_fixture()

        payload = _chart_payload(_client())

        assert payload["level"] == "province"
        assert payload["child_level"] == "district"
        assert {row["value"] for row in payload["results"]} == {
            "CENTRAL",
            "COPPERBELT",
        }

    def test_population_and_household_coverage_rolled_up(self):
        _build_baseline_census_fixture()

        central = _chart_row(_chart_payload(_client()), "CENTRAL")

        # EA A rolls up into CENTRAL: 4 enumerated against a target of 4 -> 100%.
        assert central["population_estimate"] == 4
        assert central["population_enumerated"] == 4
        assert central["population_coverage"] == 100.0
        assert central["household_estimate"] == 4
        assert central["household_enumerated"] == 4
        assert central["household_coverage"] == 100.0

    def test_population_and_households_fallback_to_household_ea_when_cluster_missing(
        self,
    ):
        _build_household_ea_fallback_fixture()

        central = _chart_row(_chart_payload(_client()), "CENTRAL")

        assert central["population_estimate"] == 3
        assert central["population_enumerated"] == 3
        assert central["population_coverage"] == 100.0
        assert central["household_estimate"] == 1
        assert central["household_enumerated"] == 1
        assert central["household_coverage"] == 100.0

    def test_unmatched_ea_members_fold_into_province_group(self):
        _build_unmatched_ea_fixture()

        payload = _chart_payload(_client())
        central = _chart_row(payload, "CENTRAL")

        # 2 resolved + 3 unmatched members; 1 resolved + 1 unmatched household.
        assert central["population_enumerated"] == 5
        assert central["household_enumerated"] == 2
        # The unmatched household names CENTRAL, so no separate bucket appears.
        assert all(row["value"] != "Unmatched" for row in payload["results"])

    def test_unmatched_ea_members_fold_into_district_group(self):
        _build_unmatched_ea_fixture()

        payload = _chart_payload(_client(), level="district", province="CENTRAL")
        chisamba = _chart_row(payload, "CHISAMBA")

        assert chisamba["population_enumerated"] == 5
        assert chisamba["household_enumerated"] == 2

    def test_unmatched_ea_surfaces_as_bucket_at_ea_level(self):
        _build_unmatched_ea_fixture()

        payload = _chart_payload(
            _client(),
            level="ea",
            province="CENTRAL",
            district="CHISAMBA",
            constituency="CHISAMBA CENTRAL",
            ward="CHAMUKA",
        )

        # The resolved EA still carries its 2 members.
        assert _chart_row(payload, GEOID_A)["population_enumerated"] == 2

        # The EA missing from the target table surfaces explicitly instead of
        # being silently dropped.
        unmatched = _chart_row(payload, "Unmatched")
        assert unmatched["population_enumerated"] == 3
        assert unmatched["population_estimate"] == 0
        assert unmatched["population_coverage"] is None
        assert unmatched["has_children"] is False

    def test_chart_population_total_reconciles_with_member_count(self):
        _build_unmatched_ea_fixture()

        payload = _chart_payload(_client())
        total = sum(row["population_enumerated"] for row in payload["results"])

        # No enumerated member is silently dropped, mirroring the Enumeration
        # Coverage summary. Content-duplicate rows are excluded so each person
        # counts once.
        assert total == HouseholdMember.objects.canonical().count()

    def test_unmatched_ea_respects_drilldown_filter(self):
        _build_unmatched_ea_fixture()
        # An unmatched household in another province must not leak into a
        # drill-down scoped to CENTRAL.
        other_hh = Household.objects.create(
            key="hh-unmatched-other",
            cluster=None,
            ea=GEOID_UNMATCHED,
            province="Copperbelt",
            district="Kitwe",
            submissiondate=_iso(timezone.now() - timedelta(days=5)),
        )
        for idx in range(7):
            HouseholdMember.objects.create(
                household=other_hh, first_name=f"Other {idx}"
            )

        payload = _chart_payload(_client(), level="district", province="CENTRAL")

        assert _chart_row(payload, "CHISAMBA")["population_enumerated"] == 5
        assert all(row["value"] != "Kitwe" for row in payload["results"])

    def test_death_estimate_and_coverage_are_null(self):
        _build_baseline_census_fixture()

        central = _chart_row(_chart_payload(_client()), "CENTRAL")

        # SRSEAPopulationTarget has no death target, so estimate/coverage are
        # null while the enumerated death count is still reported.
        assert central["death_estimate"] is None
        assert central["death_coverage"] is None
        assert central["death_enumerated"] == 1

    def test_zero_target_keeps_coverage_null(self):
        _build_baseline_census_fixture()

        # EA B has a zero household target -> division-safe null coverage.
        copperbelt = _chart_row(_chart_payload(_client()), "COPPERBELT")

        assert copperbelt["household_estimate"] == 0
        assert copperbelt["household_coverage"] is None
        assert copperbelt["population_coverage"] == 0.0

    def test_pregnancy_and_outcome_enumerated_rolled_up(self):
        _build_baseline_census_fixture()

        central = _chart_row(_chart_payload(_client()), "CENTRAL")

        # Fixture: two pregnancies and one pregnancy outcome under CENTRAL.
        assert central["pregnancy_enumerated"] == 2
        assert central["pregnancy_outcome_enumerated"] == 1
        # No targets exist for these indicators -> estimate/coverage are null.
        assert central["pregnancy_estimate"] is None
        assert central["pregnancy_coverage"] is None
        assert central["pregnancy_outcome_estimate"] is None
        assert central["pregnancy_outcome_coverage"] is None

    def test_vital_events_resolve_odk_coded_geography_at_each_level(self):
        _build_coded_vital_events_fixture()
        client = _client()

        cases = (
            ({}, "CENTRAL"),
            ({"level": "district", "province": "CENTRAL"}, "CHISAMBA"),
            (
                {
                    "level": "constituency",
                    "province": "CENTRAL",
                    "district": "CHISAMBA",
                },
                "CHISAMBA CENTRAL",
            ),
            (
                {
                    "level": "ward",
                    "province": "CENTRAL",
                    "district": "CHISAMBA",
                    "constituency": "CHISAMBA CENTRAL",
                },
                "CHAMUKA",
            ),
            (
                {
                    "level": "ea",
                    "province": "CENTRAL",
                    "district": "CHISAMBA",
                    "constituency": "CHISAMBA CENTRAL",
                    "ward": "CHAMUKA",
                },
                GEOID_CODED,
            ),
        )

        for params, value in cases:
            row = _chart_row(_chart_payload(client, **params), value)
            assert row["death_enumerated"] == 1
            assert row["pregnancy_enumerated"] == 1
            assert row["pregnancy_outcome_enumerated"] == 1

    def test_vital_events_already_label_values_still_aggregate(self):
        _build_coded_vital_events_fixture()
        recent = _iso(timezone.now() - timedelta(days=5))
        label_geo = {
            "province": "central",
            "district": "CHISAMBA",
            "constituency": "Chisamba Central",
            "ward": "CHAMUKA",
            "ea": "EA_" + GEOID_CODED,
        }
        Pregnancy.objects.create(key="preg-label", submissiondate=recent, **label_geo)

        central = _chart_row(_chart_payload(_client()), "CENTRAL")

        assert central["pregnancy_enumerated"] == 2

    def test_unmapped_vital_event_geography_is_skipped_without_bogus_rows(self):
        _build_coded_vital_events_fixture()
        recent = _iso(timezone.now() - timedelta(days=5))
        Pregnancy.objects.create(
            key="preg-unmapped",
            submissiondate=recent,
            province="999",
            district="999",
            constituency="999",
            ward="999",
            ea="999",
        )

        payload = _chart_payload(_client())
        central = _chart_row(payload, "CENTRAL")

        assert {row["value"] for row in payload["results"]} == {"CENTRAL"}
        assert central["pregnancy_enumerated"] == 1

    def test_current_census_filter_applies_after_odk_location_resolution(self):
        _build_coded_vital_events_fixture()
        BaselineCensusProvince.objects.create(province_name="CENTRAL", is_current=False)
        client = _client()

        all_rows = _chart_payload(client)
        current_rows = _chart_payload(client, bc_current="1")

        assert _chart_row(all_rows, "CENTRAL")["pregnancy_enumerated"] == 1
        assert current_rows["results"] == []

    def test_inactive_province_filter_applies_after_odk_location_resolution(self):
        _build_coded_vital_events_fixture()
        BaselineCensusProvince.objects.create(
            province_name="CENTRAL", is_current=True, is_active=False
        )

        payload = _chart_payload(_client())

        assert payload["results"] == []

    def test_indicator_param_accepts_supported_values(self):
        _build_baseline_census_fixture()

        payload = _chart_payload(_client(), indicator="pregnancies")

        assert payload["indicator"] == "pregnancies"
        assert len(payload["results"]) > 0

    def test_indicator_param_rejects_unsupported_value(self):
        _build_baseline_census_fixture()

        response = _client().get(reverse(CHART_URL), {"indicator": "bogus"})

        assert response.status_code == 400
        assert "error" in response.json()

    def test_district_level_filters_to_parent_province(self):
        _build_baseline_census_fixture()

        payload = _chart_payload(_client(), level="district", province="CENTRAL")

        assert payload["level"] == "district"
        assert payload["child_level"] == "constituency"
        assert {row["value"] for row in payload["results"]} == {"CHISAMBA"}
        assert payload["results"][0]["has_children"] is True

    def test_constituency_level_filters_to_parent_district(self):
        _build_baseline_census_fixture()

        payload = _chart_payload(
            _client(),
            level="constituency",
            province="CENTRAL",
            district="CHISAMBA",
        )

        assert payload["level"] == "constituency"
        assert payload["child_level"] == "ward"
        assert {row["value"] for row in payload["results"]} == {"CHISAMBA CENTRAL"}

    def test_ward_level_filters_to_parent_constituency(self):
        _build_baseline_census_fixture()

        payload = _chart_payload(
            _client(),
            level="ward",
            province="CENTRAL",
            district="CHISAMBA",
            constituency="CHISAMBA CENTRAL",
        )

        assert payload["level"] == "ward"
        assert payload["child_level"] == "ea"
        assert {row["value"] for row in payload["results"]} == {"CHAMUKA"}

    def test_ea_level_is_the_final_level(self):
        _build_baseline_census_fixture()

        payload = _chart_payload(
            _client(),
            level="ea",
            province="CENTRAL",
            district="CHISAMBA",
            ward="CHAMUKA",
        )

        assert payload["level"] == "ea"
        assert payload["child_level"] is None
        assert {row["value"] for row in payload["results"]} == {GEOID_A}
        assert payload["results"][0]["has_children"] is False

    def test_current_census_excludes_unconfigured_province(self):
        _build_baseline_census_fixture()
        # COPPERBELT is configured out of the current baseline census.
        BaselineCensusProvince.objects.create(
            province_name="COPPERBELT", is_current=False
        )
        client = _client()

        # bc_current off by default -> both activated provinces are charted.
        payload_all = _chart_payload(client)
        assert {row["value"] for row in payload_all["results"]} == {
            "CENTRAL",
            "COPPERBELT",
        }

        # bc_current on -> only CENTRAL is charted.
        payload = _chart_payload(client, bc_current="1")
        assert {row["value"] for row in payload["results"]} == {"CENTRAL"}

    def test_inactive_province_is_always_excluded(self):
        _build_baseline_census_fixture()
        BaselineCensusProvince.objects.create(
            province_name="COPPERBELT", is_current=True, is_active=False
        )
        client = _client()

        payload = _chart_payload(client)
        assert {row["value"] for row in payload["results"]} == {"CENTRAL"}

        payload_all = _chart_payload(client, bc_current="0")
        assert {row["value"] for row in payload_all["results"]} == {"CENTRAL"}

    def test_explicit_preset_applies_time_window(self):
        _build_baseline_census_fixture()

        # An explicit last-24h preset bounds by date: 5-day-old records drop out.
        central = _chart_row(
            _chart_payload(_client(), preset="24", bc_current="1"), "CENTRAL"
        )

        assert central["household_enumerated"] == 0
        assert central["death_enumerated"] == 0

    def test_location_filter_narrows_results(self):
        _build_baseline_census_fixture()

        payload = _chart_payload(
            _client(), location_level="province", location_value="CENTRAL"
        )

        assert {row["value"] for row in payload["results"]} == {"CENTRAL"}

    def test_non_matching_filter_returns_empty_results(self):
        _build_baseline_census_fixture()

        payload = _chart_payload(_client(), level="district", province="NOWHERE")

        assert payload["results"] == []

    def test_breadcrumbs_track_the_drill_path(self):
        _build_baseline_census_fixture()

        payload = _chart_payload(
            _client(),
            level="constituency",
            province="CENTRAL",
            district="CHISAMBA",
        )

        labels = [crumb["label"] for crumb in payload["breadcrumbs"]]
        assert labels == ["All Provinces", "CENTRAL", "CHISAMBA"]


# --- Baseline Census Config: per-province current-census flags ----------------


def _admin_client():
    client = Client()
    client.force_login(user=AdminFactory.create())
    return client


def _seed_two_province_targets():
    # Two CENTRAL EAs (distinct geoids) plus one COPPERBELT EA — a province
    # must collapse to a single config entry regardless of EA count.
    SRSEAPopulationTarget.objects.create(
        geoid=GEOID_A,
        province_name="CENTRAL",
        household_target=1,
        population_target=1,
    )
    SRSEAPopulationTarget.objects.create(
        geoid="01021011099",
        province_name="CENTRAL",
        household_target=1,
        population_target=1,
    )
    SRSEAPopulationTarget.objects.create(
        geoid=GEOID_B,
        province_name="COPPERBELT",
        household_target=1,
        population_target=1,
    )


class TestBaselineCensusProvinceConfigHelpers:
    def test_config_lists_every_province_defaulting_to_current(self):
        _seed_two_province_targets()

        config = get_baseline_census_province_config()

        assert config == [
            {"province_name": "CENTRAL", "is_current": True, "is_active": True},
            {"province_name": "COPPERBELT", "is_current": True, "is_active": True},
        ]
        assert get_current_census_provinces() == []
        assert get_active_census_provinces() == []
        assert get_excluded_census_provinces() == []
        assert get_inactive_census_provinces() == []

    def test_save_marks_unchecked_current_provinces_excluded(self):
        _seed_two_province_targets()

        # Only CENTRAL stays checked.
        save_baseline_census_province_config(["CENTRAL"], ["CENTRAL", "COPPERBELT"])

        assert get_excluded_census_provinces() == ["COPPERBELT"]
        assert get_inactive_census_provinces() == []
        config = {
            row["province_name"]: (row["is_current"], row["is_active"])
            for row in get_baseline_census_province_config()
        }
        assert config == {"CENTRAL": (True, True), "COPPERBELT": (False, True)}

    def test_save_marks_unchecked_active_provinces_inactive(self):
        _seed_two_province_targets()
        save_baseline_census_province_config(["CENTRAL", "COPPERBELT"], ["CENTRAL"])

        assert get_excluded_census_provinces() == []
        assert get_inactive_census_provinces() == ["COPPERBELT"]
        config = {
            row["province_name"]: (row["is_current"], row["is_active"])
            for row in get_baseline_census_province_config()
        }
        assert config == {"CENTRAL": (True, True), "COPPERBELT": (True, False)}

    def test_save_is_idempotent_and_can_re_include(self):
        _seed_two_province_targets()
        save_baseline_census_province_config(["CENTRAL"], ["CENTRAL"])

        # Re-checking COPPERBELT flips it back to current and active.
        save_baseline_census_province_config(
            ["CENTRAL", "COPPERBELT"], ["CENTRAL", "COPPERBELT"]
        )

        assert get_excluded_census_provinces() == []
        assert get_inactive_census_provinces() == []
        assert BaselineCensusProvince.objects.count() == 2


class TestBaselineCensusConfigAdminPanel:
    def test_admin_panel_renders_config_section(self):
        _seed_two_province_targets()

        response = _admin_client().get(reverse("va_admin:index"))
        body = response.content.decode()

        assert response.status_code == 200
        assert "Baseline Census Config" in body
        assert 'id="baselineCensusConfigSection"' in body
        assert "Current Baseline Census" in body
        assert "Activated Provinces" in body
        assert 'value="CENTRAL"' in body
        assert 'value="COPPERBELT"' in body
        assert 'name="current_provinces"' in body
        assert 'name="active_provinces"' in body

    def test_save_endpoint_persists_unchecked_provinces(self):
        _seed_two_province_targets()

        response = _admin_client().post(
            reverse("va_admin:baseline-census-config"),
            {
                "current_provinces": ["CENTRAL"],
                "active_provinces": ["CENTRAL", "COPPERBELT"],
            },
        )

        assert response.status_code == 302
        assert get_excluded_census_provinces() == ["COPPERBELT"]
        assert get_inactive_census_provinces() == []

    def test_save_endpoint_persists_inactive_provinces(self):
        _seed_two_province_targets()

        response = _admin_client().post(
            reverse("va_admin:baseline-census-config"),
            {
                "current_provinces": ["CENTRAL", "COPPERBELT"],
                "active_provinces": ["CENTRAL"],
            },
        )

        assert response.status_code == 302
        assert get_excluded_census_provinces() == []
        assert get_inactive_census_provinces() == ["COPPERBELT"]

    def test_save_endpoint_rejects_non_superuser(self):
        _seed_two_province_targets()
        client = Client()
        client.force_login(user=_dashboard_user())

        response = client.post(
            reverse("va_admin:baseline-census-config"),
            {
                "current_provinces": ["CENTRAL"],
                "active_provinces": ["CENTRAL", "COPPERBELT"],
            },
        )

        assert response.status_code == 403
        # Nothing was persisted for the unauthorized request.
        assert BaselineCensusProvince.objects.count() == 0


class TestBaselineCensusOverviewKpiGating:
    def test_overview_kpis_gated_by_current_census_provinces(self):
        _seed_two_province_targets()
        recent = _iso(timezone.now() - timedelta(days=2))
        Household.objects.create(
            key="hh-central", province="CENTRAL", submissiondate=recent
        )
        Household.objects.create(
            key="hh-copperbelt", province="COPPERBELT", submissiondate=recent
        )
        BaselineCensusProvince.objects.create(
            province_name="COPPERBELT", is_current=False
        )

        client = _client()
        url = reverse("va_analytics:baseline-census-overview-filter-data")

        # bc_current off by default -> both activated provinces are counted.
        off_payload = client.get(url, {"kpis_only": "1"}).json()
        assert off_payload["kpis"]["households"]["total"] == 2

        # bc_current on -> the COPPERBELT household is excluded.
        on_payload = client.get(url, {"kpis_only": "1", "bc_current": "1"}).json()
        assert on_payload["kpis"]["households"]["total"] == 1

    def test_overview_kpis_always_exclude_inactive_provinces(self):
        _seed_two_province_targets()
        recent = _iso(timezone.now() - timedelta(days=2))
        Household.objects.create(
            key="hh-central", province="CENTRAL", submissiondate=recent
        )
        Household.objects.create(
            key="hh-copperbelt", province="COPPERBELT", submissiondate=recent
        )
        BaselineCensusProvince.objects.create(
            province_name="COPPERBELT", is_current=True, is_active=False
        )

        client = _client()
        url = reverse("va_analytics:baseline-census-overview-filter-data")

        on_payload = client.get(url, {"kpis_only": "1", "bc_current": "1"}).json()
        assert on_payload["kpis"]["households"]["total"] == 1

        off_payload = client.get(url, {"kpis_only": "1", "bc_current": "0"}).json()
        assert off_payload["kpis"]["households"]["total"] == 1


class TestBaselineCensusOverviewTargets:
    """The Enumeration Coverage Summary chart's population/household estimates
    are summed from the SRS EA population target table."""

    def test_overview_filter_data_includes_summed_targets(self):
        # _seed_two_province_targets: 2 CENTRAL EAs + 1 COPPERBELT EA, each with
        # population_target=1 and household_target=1.
        _seed_two_province_targets()

        url = reverse("va_analytics:baseline-census-overview-filter-data")
        payload = _client().get(url, {"kpis_only": "1"}).json()

        assert payload["kpis"]["targets"] == {
            "population_target": 3,
            "household_target": 3,
        }

    def test_targets_drop_non_current_province_when_bc_current_on(self):
        _seed_two_province_targets()
        BaselineCensusProvince.objects.create(
            province_name="COPPERBELT", is_current=False
        )
        client = _client()
        url = reverse("va_analytics:baseline-census-overview-filter-data")

        # bc_current off -> every activated province's target counts.
        off = client.get(url, {"kpis_only": "1"}).json()
        assert off["kpis"]["targets"]["population_target"] == 3

        # bc_current on -> COPPERBELT's EA target is excluded.
        on = client.get(url, {"kpis_only": "1", "bc_current": "1"}).json()
        assert on["kpis"]["targets"] == {
            "population_target": 2,
            "household_target": 2,
        }

    def test_targets_always_drop_inactive_province(self):
        _seed_two_province_targets()
        BaselineCensusProvince.objects.create(
            province_name="COPPERBELT", is_current=True, is_active=False
        )
        client = _client()
        url = reverse("va_analytics:baseline-census-overview-filter-data")

        # An inactive province is excluded regardless of the bc_current flag.
        for params in ({"kpis_only": "1"}, {"kpis_only": "1", "bc_current": "1"}):
            payload = client.get(url, params).json()
            assert payload["kpis"]["targets"]["population_target"] == 2

    def test_helper_respects_location_filter(self):
        _seed_two_province_targets()

        central_only = get_baseline_census_target_totals(
            location_level="province", location_value="CENTRAL"
        )

        assert central_only == {"population_target": 2, "household_target": 2}

    def test_helper_returns_zero_totals_without_targets(self):
        assert get_baseline_census_target_totals() == {
            "population_target": 0,
            "household_target": 0,
        }
