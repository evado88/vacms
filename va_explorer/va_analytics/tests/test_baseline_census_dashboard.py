from datetime import timedelta
from datetime import timezone as dt_timezone

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from va_explorer.tests.factories import GroupFactory, UserFactory
from va_explorer.va_data_management.models import (
    Death,
    Household,
    HouseholdMember,
    Pregnancy,
    PregnancyOutcome,
    SRSClusterLocation,
    SRSEAPopulationTarget,
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
        assert 'id="tableLocationLevel"' in body
        assert 'id="tableLocationValue"' in body
        assert "baseline-table-filters-row" in body

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


def _table_rows(client, **params):
    response = client.get(
        reverse("va_analytics:baseline-census-province-table-component"), params
    )
    assert response.status_code == 200
    return {row["ea"]: row for row in response.context["bc_stats"]}


class TestBaselineCensusTableData:
    def test_rows_sourced_from_target_table(self):
        _build_baseline_census_fixture()
        client = Client()
        client.force_login(user=_dashboard_user())

        rows = _table_rows(client)

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

        row_b = _table_rows(client)[GEOID_B]

        assert row_b["hh_enumerated"] == 0
        assert row_b["pop_enumerated"] == 0
        assert row_b["total_deaths"] == 0
        assert row_b["pop_estimate"] == 50

    def test_current_census_window_aggregates_recent_records(self):
        _build_baseline_census_fixture()
        client = Client()
        client.force_login(user=_dashboard_user())

        # bc_current defaults to on -> rolling 3-month window.
        row_a = _table_rows(client)[GEOID_A]

        assert row_a["hh_enumerated"] == 3  # the old household is excluded
        assert row_a["pop_enumerated"] == 3
        assert row_a["refusals"] == 1  # consent="No" only; the old "Refused" is out
        assert row_a["non_contact"] == 1
        assert row_a["total_pregnancies"] == 1
        assert row_a["total_deaths"] == 1
        assert row_a["total_preg_outcomes"] == 1

    def test_unchecked_includes_all_records(self):
        _build_baseline_census_fixture()
        client = Client()
        client.force_login(user=_dashboard_user())

        row_a = _table_rows(client, bc_current="0")[GEOID_A]

        assert row_a["hh_enumerated"] == 4
        assert row_a["pop_enumerated"] == 4
        assert row_a["refusals"] == 2  # consent="No" + the old result="Refused"
        assert row_a["total_pregnancies"] == 2

    def test_explicit_preset_overrides_checkbox(self):
        _build_baseline_census_fixture()
        client = Client()
        client.force_login(user=_dashboard_user())

        # Last 24h preset with the checkbox still on: records 5 days old drop out.
        row_a = _table_rows(client, preset="24", bc_current="1")[GEOID_A]

        assert row_a["hh_enumerated"] == 0
        assert row_a["total_pregnancies"] == 0

    def test_coverage_calculation_and_bands(self):
        _build_baseline_census_fixture()
        client = Client()
        client.force_login(user=_dashboard_user())

        rows = _table_rows(client)
        row_a = rows[GEOID_A]
        row_b = rows[GEOID_B]

        # EA A: 3 / 4 -> 75.0% -> "yellow" band.
        assert row_a["hh_coverage"] == 75.0
        assert row_a["hh_coverage_band"] == "yellow"
        assert row_a["pop_coverage"] == 75.0
        assert row_a["pop_coverage_band"] == "yellow"

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

        assert "coverage-cell coverage-cell--yellow" in body

    def test_location_filter_narrows_to_province(self):
        _build_baseline_census_fixture()
        client = Client()
        client.force_login(user=_dashboard_user())

        rows = _table_rows(
            client, location_level="province", location_value="COPPERBELT"
        )

        assert set(rows) == {GEOID_B}


class TestBaselineCensusCheckboxRendering:
    def test_table_tab_renders_current_census_checkbox_first(self):
        client = Client()
        client.force_login(user=_dashboard_user())

        response = client.get(reverse("va_analytics:baseline-census-dashboard"))
        body = response.content.decode()

        assert 'id="tableCurrentCensus"' in body
        # The checkbox precedes the location-level select in the filter row.
        assert body.index('id="tableCurrentCensus"') < body.index(
            'id="tableLocationLevel"'
        )
