import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from va_explorer.tests.factories import GroupFactory, UserFactory

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
        assert "Copperbelt and Central Province Baseline Census" in body
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
