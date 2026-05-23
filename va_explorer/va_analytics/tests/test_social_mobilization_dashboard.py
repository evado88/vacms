from datetime import timedelta
from datetime import timezone as dt_timezone

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from va_explorer.tests.factories import GroupFactory, LocationFactory, UserFactory
from va_explorer.va_data_management.models import SocialMobilization

pytestmark = pytest.mark.django_db

DASHBOARD_URL = "va_analytics:baseline-census-dashboard"
COMPONENT_URL = "va_analytics:baseline-census-social-mobilization-component"
KPIS_URL = "va_analytics:baseline-census-social-mobilization-kpis-component"
CHART_URL = "va_analytics:baseline-census-social-mobilization-chart-data"


def _dashboard_user(**kwargs):
    permission = Permission.objects.filter(codename="view_dashboard").first()
    group = GroupFactory.create(permissions=[permission] if permission else [])
    return UserFactory.create(groups=[group], **kwargs)


def _unprivileged_user():
    return UserFactory.create(groups=[GroupFactory.create(permissions=[])])


def _client(user=None):
    client = Client()
    client.force_login(user=user or _dashboard_user())
    return client


def _iso(dt):
    return dt.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _row(province, district, facility, village, submissiondate, **metrics):
    data = {
        "province": province,
        "district": district,
        "health_facility": facility,
        "village_neighbourhood": village,
        "submissiondate": submissiondate,
    }
    # Store counts as text, exactly as the ODK CSV export delivers them.
    data.update({key: str(value) for key, value in metrics.items()})
    return SocialMobilization(key=f"{province}-{village}-{submissiondate}", **data)


def _build_fixture():
    """Two provinces. Copperbelt has Mpongwe (one village, two same-day rows) and
    Kitwe; Central has Kabwe. One old Copperbelt row exercises the time filter."""
    now = timezone.now()
    recent = _iso(now - timedelta(days=5))
    old = _iso(now - timedelta(days=200))

    SocialMobilization.objects.bulk_create(
        [
            _row(
                "copperbelt",
                "mpongwe",
                "HP1",
                "V1",
                recent,
                households_visited=2,
                people_over_15=10,
                children_5_14=7,
                children_12_59months=3,
                refusal_identified=2,
                refusal_managed=1,
                schools_reached=5,
                religious_leaders_reached=4,
                children_affected=0,
                refusals_from_rumor=0,
                rumor_description="",
            ),
            _row(
                "copperbelt",
                "mpongwe",
                "HP1",
                "V1",
                recent + "-b",
                households_visited=3,
                people_over_15=5,
                children_5_14=2,
                children_12_59months=1,
                refusal_identified=4,
                refusal_managed=2,
                schools_reached=1,
                religious_leaders_reached=0,
                children_affected=6,
                refusals_from_rumor=1,
                rumor_description="Salt prevents disease",
                rumor_source="community",
            ),
            _row(
                "copperbelt",
                "kitwe",
                "HP2",
                "V2",
                recent,
                households_visited=1,
                people_over_15=4,
                children_5_14=0,
                children_12_59months=0,
                refusal_identified=0,
                refusal_managed=0,
                schools_reached=2,
                religious_leaders_reached=1,
                children_affected=0,
                refusals_from_rumor=0,
                rumor_description="",
            ),
            _row(
                "central",
                "kabwe",
                "HP3",
                "V3",
                recent,
                households_visited=4,
                people_over_15=8,
                children_5_14=3,
                children_12_59months=2,
                refusal_identified=1,
                refusal_managed=1,
                schools_reached=0,
                religious_leaders_reached=0,
                children_affected=2,
                refusals_from_rumor=0,
                rumor_description="Water is unsafe",
                rumor_source="market",
            ),
            _row(
                "copperbelt",
                "mpongwe",
                "HP1",
                "V1",
                old,
                households_visited=9,
                people_over_15=9,
                children_5_14=0,
                children_12_59months=0,
                refusal_identified=0,
                refusal_managed=0,
                schools_reached=0,
                religious_leaders_reached=0,
                children_affected=0,
                refusals_from_rumor=0,
                rumor_description="",
            ),
        ]
    )


# --- Tab rendering ------------------------------------------------------------


class TestSocialMobilizationTab:
    def test_dashboard_renders_social_mobilization_tab(self):
        body = _client().get(reverse(DASHBOARD_URL)).content.decode()

        assert 'data-tab="social_mob"' in body
        assert 'id="tab-social-mob"' in body
        assert "Social Mobilization" in body

    def test_tab_renders_chart_subtabs_and_canvases(self):
        body = _client().get(reverse(DASHBOARD_URL)).content.decode()

        assert 'id="smChartTabs"' in body
        for canvas_id in (
            "smReachChart",
            "smRefusalsChart",
            "smRumoursChart",
            "smTrendChart",
        ):
            assert f'id="{canvas_id}"' in body
        assert 'data-sm-subtab="reach" role="tab" aria-selected="true"' in body

    def test_query_param_marks_tab_active(self):
        response = _client().get(reverse(DASHBOARD_URL) + "?tab=social_mob")

        assert response.context["baseline_active_tab"] == "social_mob"

    def test_tab_has_tables_and_charts_subtabs(self):
        body = _client().get(reverse(DASHBOARD_URL)).content.decode()

        assert 'data-sm-view-tab="tables"' in body
        assert 'data-sm-view-tab="charts"' in body
        # The table component sits under the Tables panel; the chart under Charts.
        tables_panel = body.index('data-sm-view-panel="tables"')
        charts_panel = body.index('data-sm-view-panel="charts"')
        slot = body.index('id="baselineSocialMobSlot"')
        chart = body.index('id="sm-chart-view"')
        assert tables_panel < slot < charts_panel < chart

    def test_layout_has_side_rail_kpis_and_charts_default(self):
        body = _client().get(reverse(DASHBOARD_URL)).content.decode()

        # Two-column layout: left rail with the sub-tabs, main column with KPIs.
        assert 'class="sm-side"' in body
        assert 'class="sm-main"' in body
        assert 'id="baselineSocialMobKpisSlot"' in body
        # Charts is the first sub-tab and active by default.
        charts_btn = body.index('data-sm-view-tab="charts"')
        tables_btn = body.index('data-sm-view-tab="tables"')
        assert charts_btn < tables_btn
        assert 'data-sm-view-tab="charts" role="tab" aria-selected="true"' in body
        assert (
            'sm-view-panel sm-view-panel--active" data-sm-view-panel="charts"' in body
        )
        # Tables sub-tab carries its own styling hook.
        assert "sm-subtab--tables" in body

    def test_tables_panel_has_activity_and_rumour_pills(self):
        body = _client().get(reverse(DASHBOARD_URL)).content.decode()

        assert 'id="smTableTabs"' in body
        assert 'data-sm-table-tab="activity" role="tab" aria-selected="true"' in body
        assert 'data-sm-table-tab="rumour" role="tab" aria-selected="false"' in body
        assert "Activity by location" in body
        assert "Rumour Log" in body

    def test_chart_heading_is_inline_with_breadcrumb(self):
        body = _client().get(reverse(DASHBOARD_URL)).content.decode()

        # "Trends & breakdowns:" sits in the breadcrumb row, inline with the
        # JS-rendered "All Provinces" crumb.
        title = body.index("Trends &amp; breakdowns:")
        breadcrumb = body.index('id="smChartBreadcrumb"')
        row_start = body.rindex("baseline-chart-breadcrumb-row", 0, title)
        assert row_start < title < breadcrumb


# --- Component endpoint (KPIs + tables) ---------------------------------------


class TestSocialMobilizationComponent:
    def test_requires_view_dashboard_permission(self):
        client = Client()
        client.force_login(user=_unprivileged_user())

        response = client.get(reverse(COMPONENT_URL))

        assert response.status_code in (302, 403)

    def test_kpis_component_endpoint_renders_cards(self):
        _build_fixture()
        response = _client().get(reverse(KPIS_URL))

        assert response.status_code == 200
        body = response.content.decode()
        assert "Households &amp; Outreach" in body
        assert "People Reached" in body
        # Totals are computed the same way as the table component.
        assert response.context["sm_totals"]["households_visited"] == 19

    def test_kpis_component_requires_permission(self):
        client = Client()
        client.force_login(user=_unprivileged_user())

        response = client.get(reverse(KPIS_URL))

        assert response.status_code in (302, 403)

    def test_kpi_totals_sum_all_records(self):
        _build_fixture()
        response = _client().get(reverse(COMPONENT_URL))

        assert response.status_code == 200
        totals = response.context["sm_totals"]
        # Households: 2 + 3 + 1 + 4 + 9 (old included by default all-time view).
        assert totals["households_visited"] == 19
        # People reached = 15+ plus both child bands across every row
        # (20 + 8 + 4 + 13 + 9).
        assert totals["people_reached"] == 54
        assert totals["schools_reached"] == 8
        assert totals["rumours_reported"] == 2
        # Refusals identified 7, managed 4 -> 57.1%.
        assert totals["refusal_identified"] == 7
        assert totals["refusal_managed"] == 4
        assert totals["refusal_managed_rate"] == 57.1

    def test_table_has_one_row_per_location_tuple(self):
        _build_fixture()
        response = _client().get(reverse(COMPONENT_URL))
        rows = response.context["sm_stats"]

        keyed = {
            (r["province"], r["district"], r["village_neighbourhood"]): r for r in rows
        }
        assert set(keyed) == {
            ("copperbelt", "mpongwe", "V1"),
            ("copperbelt", "kitwe", "V2"),
            ("central", "kabwe", "V3"),
        }
        # Mpongwe V1 merges its two same-day rows and the old row: 2 + 3 + 9.
        assert keyed[("copperbelt", "mpongwe", "V1")]["households_visited"] == 14
        # Activity table renders location names title-cased.
        assert "Copperbelt" in response.content.decode()
        assert "Mpongwe" in response.content.decode()

    def test_table_component_marks_activity_and_rumour_panels(self):
        _build_fixture()
        body = _client().get(reverse(COMPONENT_URL)).content.decode()

        assert 'data-sm-table-panel="activity"' in body
        assert 'data-sm-table-panel="rumour"' in body
        assert 'id="sm-table-panel-activity"' in body
        assert 'id="sm-table-panel-rumour"' in body

    def test_rumour_log_lists_only_rumour_rows(self):
        _build_fixture()
        rumours = _client().get(reverse(COMPONENT_URL)).context["sm_rumours"]

        descriptions = {row["description"] for row in rumours}
        assert descriptions == {"Salt prevents disease", "Water is unsafe"}

    def test_search_narrows_table(self):
        _build_fixture()
        rows = (
            _client().get(reverse(COMPONENT_URL), {"sm_q": "kitwe"}).context["sm_stats"]
        )

        assert {r["district"] for r in rows} == {"kitwe"}

    def test_explicit_preset_applies_time_window(self):
        _build_fixture()
        # last-24h preset: every record (>= 5 days old) drops out.
        totals = (
            _client().get(reverse(COMPONENT_URL), {"preset": "24"}).context["sm_totals"]
        )

        assert totals["households_visited"] == 0


# --- Chart-data endpoint ------------------------------------------------------


def _chart(client, **params):
    response = client.get(reverse(CHART_URL), params)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")
    return response.json()


def _chart_row(payload, value):
    return next(row for row in payload["results"] if row["value"] == value)


class TestSocialMobilizationChartData:
    def test_requires_view_dashboard_permission(self):
        client = Client()
        client.force_login(user=_unprivileged_user())

        response = client.get(reverse(CHART_URL))

        assert response.status_code in (302, 403)

    def test_province_level_by_default(self):
        _build_fixture()
        payload = _chart(_client())

        assert payload["level"] == "province"
        assert payload["child_level"] == "district"
        assert {row["value"] for row in payload["results"]} == {
            "copperbelt",
            "central",
        }
        copperbelt = _chart_row(payload, "copperbelt")
        assert copperbelt["households_visited"] == 15  # 2 + 3 + 1 + 9
        assert copperbelt["has_children"] is True

    def test_refusal_rate_and_rumours_rolled_up(self):
        _build_fixture()
        copperbelt = _chart_row(_chart(_client()), "copperbelt")

        # Identified 6, managed 3 across Copperbelt -> 50%.
        assert copperbelt["refusal_identified"] == 6
        assert copperbelt["refusal_managed"] == 3
        assert copperbelt["refusal_managed_rate"] == 50.0
        assert copperbelt["rumours_reported"] == 1

    def test_drilldown_into_districts(self):
        _build_fixture()
        payload = _chart(_client(), level="district", province="copperbelt")

        assert payload["level"] == "district"
        assert payload["child_level"] == "health_facility"
        assert {row["value"] for row in payload["results"]} == {"mpongwe", "kitwe"}

    def test_breadcrumbs_track_drill_path(self):
        _build_fixture()
        payload = _chart(
            _client(),
            level="health_facility",
            province="copperbelt",
            district="mpongwe",
        )

        labels = [crumb["label"] for crumb in payload["breadcrumbs"]]
        assert labels == ["All Provinces", "copperbelt", "mpongwe"]

    def test_village_is_final_level(self):
        _build_fixture()
        payload = _chart(
            _client(),
            level="village_neighbourhood",
            province="copperbelt",
            district="mpongwe",
            health_facility="HP1",
        )

        assert payload["child_level"] is None
        assert _chart_row(payload, "V1")["has_children"] is False

    def test_trend_buckets_by_day(self):
        _build_fixture()
        payload = _chart(_client())

        # Recent rows share one day; the old row adds a second bucket.
        assert len(payload["trend"]) == 2
        assert all("households_visited" in row for row in payload["trend"])

    def test_location_filter_narrows_results(self):
        _build_fixture()
        payload = _chart(_client(), location_level="province", location_value="central")

        assert {row["value"] for row in payload["results"]} == {"central"}


# --- Location scoping ---------------------------------------------------------


class TestSocialMobilizationLocationScoping:
    def test_restricted_user_sees_only_their_province(self):
        _build_fixture()
        province = LocationFactory.create(
            name="copperbelt", location_type="province", path="0009"
        )
        user = _dashboard_user(location_restrictions=[province])

        payload = _chart(_client(user))

        assert {row["value"] for row in payload["results"]} == {"copperbelt"}
