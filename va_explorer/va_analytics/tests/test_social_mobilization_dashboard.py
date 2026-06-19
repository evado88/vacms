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
    Kitwe; Central has Kabwe. One old Copperbelt row exercises the time filter.

    Fixture reflects the updated SRS form: refusal fields are yes/no strings,
    people counts are numeric text, households_visited / schools are not used.

    Totals:
      event_count=5, people_reached=54, rumours_reported=2,
      refusal_reluctance_identified=3 (yes), refusal_reluctance_managed=2 (yes),
      refusal_managed_rate=66.7%

    Copperbelt (rows 1-3, 5):
      event_count=4, people_reached=41,
      refusal_reluctance_identified=2, refusal_reluctance_managed=1, rate=50.0%,
      rumours_reported=1

    Mpongwe V1 (rows 1, 2, 5):
      event_count=3, people_reached=37
    """
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
                people_over_15=10,
                children_5_14=7,
                children_12_59months=3,
                refusal_reluctance_identified="yes",
                refusal_reluctance_managed="yes",
                rumor_description="",
            ),
            _row(
                "copperbelt",
                "mpongwe",
                "HP1",
                "V1",
                recent + "-b",
                people_over_15=5,
                children_5_14=2,
                children_12_59months=1,
                refusal_reluctance_identified="yes",
                refusal_reluctance_managed="no",
                rumor_description="Salt prevents disease",
                rumor_source="community",
            ),
            _row(
                "copperbelt",
                "kitwe",
                "HP2",
                "V2",
                recent,
                people_over_15=4,
                children_5_14=0,
                children_12_59months=0,
                refusal_reluctance_identified="no",
                refusal_reluctance_managed="no",
                rumor_description="",
            ),
            _row(
                "central",
                "kabwe",
                "HP3",
                "V3",
                recent,
                people_over_15=8,
                children_5_14=3,
                children_12_59months=2,
                refusal_reluctance_identified="yes",
                refusal_reluctance_managed="yes",
                rumor_description="Water is unsafe",
                rumor_source="market",
            ),
            _row(
                "copperbelt",
                "mpongwe",
                "HP1",
                "V1",
                old,
                people_over_15=9,
                children_5_14=0,
                children_12_59months=0,
                refusal_reluctance_identified="no",
                refusal_reluctance_managed="no",
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
        assert "Activity by Location" in body
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
        assert "Events" in body
        assert "People Reached" in body
        # Totals are computed the same way as the table component.
        assert response.context["sm_totals"]["event_count"] == 5

    def test_kpis_component_requires_permission(self):
        client = Client()
        client.force_login(user=_unprivileged_user())

        response = client.get(reverse(KPIS_URL))

        assert response.status_code in (302, 403)

    def test_activity_table_wraps_long_metric_headers(self):
        response = _client().get(reverse(COMPONENT_URL))

        assert response.status_code == 200
        assert response.context["sm_wrapping_metric_columns"] == (
            "event_count",
            "people_reached",
            "refusal_reluctance_identified",
            "refusal_reluctance_managed",
            "refusal_managed_rate",
        )
        assert response.content.decode().count('class="numeric sm-wrap-header"') == 5

    def test_kpi_totals_sum_all_records(self):
        _build_fixture()
        response = _client().get(reverse(COMPONENT_URL))

        assert response.status_code == 200
        totals = response.context["sm_totals"]
        # 5 rows total (all-time view).
        assert totals["event_count"] == 5
        # People reached = 15+ plus both child bands across every row
        # (20 + 8 + 4 + 13 + 9) = 54.
        assert totals["people_reached"] == 54
        assert totals["rumours_reported"] == 2
        # yes counts: rows 1,2,4 identified; rows 1,4 managed -> 66.7%.
        assert totals["refusal_reluctance_identified"] == 3
        assert totals["refusal_reluctance_managed"] == 2
        assert totals["refusal_managed_rate"] == 66.7

    def test_table_has_one_row_per_location_tuple(self):
        _build_fixture()
        response = _client().get(reverse(COMPONENT_URL), {"sm_page": 2})
        rows = response.context["sm_stats"]

        keyed = {
            (r["province"], r["district"], r["village_neighbourhood"]): r for r in rows
        }
        assert set(keyed) == {("copperbelt", "mpongwe", "V1")}
        # Mpongwe V1 merges its two same-day rows and the old row: 3 events.
        assert keyed[("copperbelt", "mpongwe", "V1")]["event_count"] == 3
        # Activity table renders location names title-cased.
        assert "Copperbelt" in response.content.decode()
        assert "Mpongwe" in response.content.decode()

    def test_table_paginates_one_district_per_page_with_scoped_and_grand_totals(self):
        _build_fixture()
        response = _client().get(reverse(COMPONENT_URL), {"sm_page": 2})

        rows = response.context["sm_stats"]
        assert {row["district"] for row in rows} == {"mpongwe"}
        assert response.context["sm_total_pages"] == 3
        assert response.context["sm_visible_count"] == 1
        assert response.context["sm_page_totals"]["event_count"] == 3
        # Mpongwe V1 people reached: (10+5+9)+(7+2+0)+(3+1+0) = 37.
        assert response.context["sm_page_totals"]["people_reached"] == 37
        assert response.context["sm_grand_totals"]["event_count"] == 5
        assert response.context["sm_grand_totals"]["people_reached"] == 54

        body = response.content.decode()
        assert "Total: Mpongwe" in body
        assert "Grand Total" in body

    def test_table_normalizes_health_facility_names(self):
        _row(
            "copperbelt",
            "Ndola",
            "Ndola_Kanfinsa_Health_Post",
            "V1",
            _iso(timezone.now()),
            people_over_15=1,
        ).save()

        response = _client().get(reverse(COMPONENT_URL))

        assert response.context["sm_stats"][0]["health_facility"] == (
            "Kanfinsa Health Post"
        )
        assert "Kanfinsa Health Post" in response.content.decode()
        assert "Ndola Kanfinsa Health Post" not in response.content.decode()

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

        assert totals["event_count"] == 0


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
        assert copperbelt["event_count"] == 4  # rows 1, 2, 3, 5
        assert copperbelt["has_children"] is True

    def test_refusal_rate_and_rumours_rolled_up(self):
        _build_fixture()
        copperbelt = _chart_row(_chart(_client()), "copperbelt")

        # Yes-counts: rows 1,2 identified (2); row 1 managed (1) -> 50%.
        assert copperbelt["refusal_reluctance_identified"] == 2
        assert copperbelt["refusal_reluctance_managed"] == 1
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
        assert all("people_reached" in row for row in payload["trend"])

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
