from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse

from va_explorer.tests.factories import LocationFactory
from va_explorer.va_analytics.views import (
    _build_deaths_age_sex_profile,
    _build_deaths_summary_cards,
    _build_deaths_trend_series,
    build_deaths_qs,
    get_deaths_filter_state,
)
from va_explorer.va_data_management.models import Death


class DeathsFilterParsingTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_defaults(self):
        request = self.factory.get("/va_analytics/outcomes-dashboard/")
        state = get_deaths_filter_state(request)

        assert state["time_preset"] == "all_time"
        assert state["start_datetime"] == ""
        assert state["end_datetime"] == ""
        assert state["sex"] == ""
        assert state["age_group"] == ""
        assert state["place_of_death"] == ""
        assert state["map_view"] == "Province"

    def test_invalid_values_are_sanitized(self):
        request = self.factory.get(
            "/va_analytics/outcomes-dashboard/",
            {"time_preset": "bad", "age_group": "retired", "map_view": "ward"},
        )
        state = get_deaths_filter_state(request)

        assert state["time_preset"] == "all_time"
        assert state["age_group"] == ""
        assert state["map_view"] == "Ward"

    def test_applies_user_location_restrictions(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="death_loc_test_user",
            email="death-loc@example.com",
            password="test-password-123",
            has_valid_password=True,
        )
        province = LocationFactory.create(name="Southern", location_type="province")
        user.location_restrictions.set([province])
        Death.objects.bulk_create(
            [
                Death(key="death-south", province="Southern", district="Choma"),
                Death(key="death-other", province="Lusaka", district="Lusaka"),
            ]
        )

        request = self.factory.get("/va_analytics/outcomes-dashboard/")
        request.user = user
        qs = build_deaths_qs(request)

        assert set(qs.values_list("key", flat=True)) == {"death-south"}

    def test_custom_date_range_filter_uses_date_of_death(self):
        Death.objects.bulk_create(
            [
                Death(
                    key="death-filter-in",
                    DE_06="2026-03-05",
                    submissiondate="2026-02-01",
                ),
                Death(
                    key="death-filter-out",
                    DE_06="2026-01-20",
                    submissiondate="2026-03-10",
                ),
            ]
        )

        request = self.factory.get(
            "/va_analytics/outcomes-dashboard/",
            {
                "time_preset": "custom",
                "start_datetime": "2026-03-01T00:00",
                "end_datetime": "2026-03-31T23:59",
            },
        )
        qs = build_deaths_qs(request)

        assert list(qs.values_list("key", flat=True)) == ["death-filter-in"]


class DeathsUnder5CalculationTests(TestCase):
    def test_under_5_percentage_handles_missing_age_safely(self):
        Death.objects.bulk_create(
            [
                Death(
                    key="death-u5-1",
                    DE_04="2024-01-01",
                    DE_06="2026-01-01",
                    DE_05="Female",
                    DE_07="home",
                    submissiondate="2026-01-02",
                ),
                Death(
                    key="death-u5-2",
                    DE_04="1980-01-01",
                    DE_06="2026-01-01",
                    DE_05="Male",
                    DE_07="hospital",
                    submissiondate="2026-01-02",
                ),
                Death(
                    key="death-u5-3",
                    DE_04="",
                    DE_06="2026-01-01",
                    DE_05="",
                    DE_07="",
                    submissiondate="2026-01-02",
                ),
            ]
        )

        payload = _build_deaths_summary_cards(Death.objects.all())

        assert payload["death_card_total_events"] == 3
        assert abs(payload["death_card_under_5_pct"] - 33.3) < 0.05


class DeathsTrendDataTests(TestCase):
    def test_trend_uses_date_of_death(self):
        Death.objects.bulk_create(
            [
                Death(
                    key="death-trend-dod",
                    DE_06="2026-03-05",
                    submissiondate="2026-02-01",
                ),
                Death(
                    key="death-trend-submission-only",
                    DE_06="",
                    submissiondate="2026-02-15",
                ),
            ]
        )

        labels, counts = _build_deaths_trend_series(Death.objects.all())

        assert labels == ["Mar 2026"]
        assert counts == [1]

    def test_dob_before_cutoff_still_contributes_to_age_calculations(self):
        Death.objects.bulk_create(
            [
                Death(
                    key="death-age-cutoff",
                    DE_04="2024-02-01",
                    DE_05="Male",
                    DE_06="2025-02-01",
                    submissiondate="2025-02-02",
                )
            ]
        )

        payload = _build_deaths_age_sex_profile(Death.objects.all())

        assert payload["male"][1] == 1


class DeathsApiSchemaTests(TestCase):
    def setUp(self):
        permission = Permission.objects.filter(codename="view_dashboard").first()
        group = Group.objects.create(name="deaths-dashboard-test-group")
        if permission:
            group.permissions.add(permission)

        User = get_user_model()
        user = User.objects.create_user(
            username="deaths_dashboard_test_user",
            email="deaths-dashboard-test@example.com",
            password="test-password-123",
            has_valid_password=True,
        )
        user.groups.add(group)
        self.client.force_login(user)

        Death.objects.bulk_create(
            [
                Death(
                    key="death-schema-1",
                    DE_04="2024-01-01",
                    DE_05="Unknown",
                    DE_06="2026-02-01",
                    DE_07="",
                    DE_15="",
                    province="Lusaka",
                    district="Lusaka",
                    submissiondate="2026-02-02",
                    today="2026-02-02",
                    start="2026-02-02",
                )
            ]
        )

    def test_death_endpoints_json_schema(self):
        summary = self.client.get(
            reverse("va_analytics:outcomes-deaths-summary-api")
        ).json()
        assert set(summary.keys()) == {
            "death_card_last_data_update",
            "death_card_last_death_date",
            "death_card_total_events",
            "death_card_mean_age",
            "death_card_under_5_pct",
            "death_card_median_delay_days",
        }

        trend = self.client.get(
            reverse("va_analytics:outcomes-deaths-trend-api")
        ).json()
        assert set(trend.keys()) == {"labels", "data"}

        map_payload = self.client.get(
            reverse("va_analytics:outcomes-deaths-map-api")
        ).json()
        assert {
            "map_view",
            "counts",
            "province_counts",
            "district_counts",
            "constituency_counts",
            "ward_counts",
            "ea_counts",
            "map_total_events",
            "map_province_sums",
            "map_district_sums",
            "map_constituency_sums",
            "map_ward_sums",
            "map_ea_sums",
        }.issubset(set(map_payload.keys()))

        age_sex = self.client.get(
            reverse("va_analytics:outcomes-deaths-age-sex-api")
        ).json()
        assert set(age_sex.keys()) == {"labels", "male", "female"}

        place = self.client.get(
            reverse("va_analytics:outcomes-deaths-place-api")
        ).json()
        assert set(place.keys()) == {"labels", "count_data", "percentage_data"}

        timeliness = self.client.get(
            reverse("va_analytics:outcomes-deaths-timeliness-api")
        ).json()
        assert set(timeliness.keys()) == {"labels", "data", "median_delay_days"}

        top_causes = self.client.get(
            reverse("va_analytics:outcomes-deaths-top-causes-api")
        ).json()
        assert set(top_causes.keys()) == {
            "has_coded",
            "labels",
            "count_data",
            "percentage_data",
        }

        cause_trend = self.client.get(
            reverse("va_analytics:outcomes-deaths-cause-trend-api")
        ).json()
        assert set(cause_trend.keys()) == {"has_coded", "labels", "datasets"}

        signals = self.client.get(
            reverse("va_analytics:outcomes-deaths-signals-api")
        ).json()
        assert set(signals.keys()) == {
            "threshold_pct",
            "baseline_min",
            "all_deaths_7d",
            "all_deaths_30d",
            "under5_deaths_7d",
            "under5_deaths_30d",
        }

    def test_non_death_tab_guard_on_death_endpoint(self):
        response = self.client.get(
            reverse("va_analytics:outcomes-deaths-summary-api"),
            {"tab": "pregnancy_outcomes"},
        )
        assert response.status_code == 400

    def test_map_applies_demographic_and_geography_filters(self):
        Death.objects.bulk_create(
            [
                Death(
                    key="death-filtered-map",
                    DE_04="1990-01-01",
                    DE_05="Female",
                    DE_06="2026-03-01",
                    province="Southern",
                    district="Choma",
                    submissiondate="2026-03-02",
                )
            ]
        )

        data = self.client.get(
            reverse("va_analytics:outcomes-deaths-map-api"),
            {
                "sex": "Female",
                "age_group": "adult",
                "geography_level": "province",
                "geography_value": "Southern",
            },
        ).json()

        assert data["map_total_events"] == 1
        assert data["province_counts"] == [{"name": "Southern", "count": 1}]

    def test_mortality_trend_hard_starts_at_january_2025(self):
        Death.objects.bulk_create(
            [
                Death(
                    key="death-trend-legacy",
                    DE_04="2020-01-01",
                    DE_05="Female",
                    DE_06="2024-12-15",
                    DE_07="home",
                    province="Lusaka",
                    district="Lusaka",
                    submissiondate="2024-12-16",
                    today="2024-12-16",
                    start="2024-12-16",
                ),
                Death(
                    key="death-trend-2025",
                    DE_04="2020-01-01",
                    DE_05="Male",
                    DE_06="2025-01-10",
                    DE_07="home",
                    province="Lusaka",
                    district="Lusaka",
                    submissiondate="2025-01-11",
                    today="2025-01-11",
                    start="2025-01-11",
                ),
            ]
        )

        trend = self.client.get(
            reverse("va_analytics:outcomes-deaths-trend-api")
        ).json()

        assert "Jan 2025" in trend["labels"]
        assert "Dec 2024" not in trend["labels"]

    def test_custom_range_ending_before_chart_cutoff_returns_empty_trend(self):
        trend = self.client.get(
            reverse("va_analytics:outcomes-deaths-trend-api"),
            {
                "time_preset": "custom",
                "start_datetime": "2024-01-01T00:00",
                "end_datetime": "2024-12-31T23:59",
            },
        ).json()

        assert trend == {"labels": [], "data": []}

    def test_custom_range_spanning_cutoff_returns_only_january_2025_onward(self):
        Death.objects.bulk_create(
            [
                Death(
                    key="death-span-2024",
                    DE_06="2024-12-31",
                    province="Lusaka",
                    district="Lusaka",
                    submissiondate="2024-12-31",
                ),
                Death(
                    key="death-span-2025",
                    DE_06="2025-01-01",
                    province="Lusaka",
                    district="Lusaka",
                    submissiondate="2025-01-01",
                ),
            ]
        )

        trend = self.client.get(
            reverse("va_analytics:outcomes-deaths-trend-api"),
            {
                "time_preset": "custom",
                "start_datetime": "2024-12-01T00:00",
                "end_datetime": "2025-01-31T23:59",
            },
        ).json()

        assert trend["labels"] == ["Jan 2025"]
        assert trend["data"] == [1]


class OutcomesTabsIsolationTests(TestCase):
    def setUp(self):
        permission = Permission.objects.filter(codename="view_dashboard").first()
        group = Group.objects.create(name="outcomes-tabs-isolation-group")
        if permission:
            group.permissions.add(permission)

        User = get_user_model()
        user = User.objects.create_user(
            username="outcomes_tabs_test_user",
            email="outcomes-tabs-test@example.com",
            password="test-password-123",
            has_valid_password=True,
        )
        user.groups.add(group)
        self.client.force_login(user)

    def test_pregnancy_tabs_render_without_deaths_container(self):
        response = self.client.get(
            reverse("va_analytics:outcomes-dashboard"), {"tab": "pregnancies"}
        )
        content = response.content.decode("utf-8")

        assert response.status_code == 200
        assert 'id="pregnancyDashboardApp"' in content
