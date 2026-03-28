from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse

from va_explorer.va_analytics.views import (
    _build_pregnancy_ga_anc_points,
    _build_pregnancy_ga_detection_distribution,
    _build_pregnancy_trend_series,
    build_pregnancy_qs,
)
from va_explorer.tests.factories import LocationFactory
from va_explorer.va_data_management.models import Pregnancy


class PregnancyDashboardDataTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _create_pregnancy(self, key, **kwargs):
        defaults = {
            "key": key,
            "submissiondate": "2026-02-15",
            "today": "2026-02-15",
            "start": "2026-02-15",
            "end": "2026-02-15",
            "PE_09A": "2026-02-01",  # LMP
            "PE_07A": 24,
            "PE_22": 3,
            "province": "Lusaka",
            "district": "Lusaka",
        }
        defaults.update(kwargs)
        return Pregnancy.objects.create(**defaults)

    def test_lmp_custom_range_filter(self):
        in_range = self._create_pregnancy("preg-in", PE_09A="2026-02-10")
        self._create_pregnancy("preg-out", PE_09A="2025-12-01")

        request = self.factory.get(
            "/va_analytics/pregnancy-dashboard/",
            {
                "time_preset": "custom",
                "start_datetime": "2026-02-01T00:00",
                "end_datetime": "2026-02-28T23:59",
            },
        )
        qs = build_pregnancy_qs(request)

        self.assertEqual(list(qs.values_list("key", flat=True)), [in_range.key])

    def test_applies_user_location_restrictions(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="preg_loc_test_user",
            email="preg-loc@example.com",
            password="test-password-123",
        )
        province = LocationFactory.create(name="Southern", location_type="province")
        user.location_restrictions.set([province])
        self._create_pregnancy("preg-south", province="Southern", district="Choma")
        self._create_pregnancy("preg-other", province="Lusaka", district="Lusaka")

        request = self.factory.get("/va_analytics/pregnancy-dashboard/")
        request.user = user
        qs = build_pregnancy_qs(request)

        self.assertEqual(set(qs.values_list("key", flat=True)), {"preg-south"})

    def test_ga_detection_distribution_computation(self):
        self._create_pregnancy(
            "preg-ga",
            PE_09A="2026-01-01",
            submissiondate="2026-01-15",  # 14 days => 2 weeks
        )

        labels, counts = _build_pregnancy_ga_detection_distribution(Pregnancy.objects.all())
        self.assertEqual(labels[1], "2")
        self.assertEqual(counts[1], 1)

    def test_ga_anc_points_computation(self):
        self._create_pregnancy(
            "preg-ga-anc",
            PE_09A="2026-01-01",
            submissiondate="2026-01-29",  # 28 days => 4 weeks
            PE_22=5,
        )

        payload = _build_pregnancy_ga_anc_points(Pregnancy.objects.all())
        self.assertIn({"x": 4, "y": 5}, payload["points"])
        self.assertEqual(payload["x_min"], 1)
        self.assertEqual(payload["x_max"], 40)

    def test_ga_anc_points_caps_anc_visits_at_fifteen(self):
        self._create_pregnancy(
            "preg-ga-anc-cap",
            PE_09A="2026-01-01",
            submissiondate="2026-01-29",  # 28 days => 4 weeks
            PE_22=24,
        )

        payload = _build_pregnancy_ga_anc_points(Pregnancy.objects.all())
        self.assertIn({"x": 4, "y": 15}, payload["points"])

    def test_pregnancy_trend_hard_starts_at_september_2024(self):
        self._create_pregnancy("preg-legacy", PE_09A="2024-08-20")
        self._create_pregnancy("preg-start", PE_09A="2024-09-05")

        labels, counts = _build_pregnancy_trend_series(Pregnancy.objects.all())

        self.assertIn("Sep 2024", labels)
        self.assertNotIn("Aug 2024", labels)
        self.assertEqual(sum(counts), 1)


class PregnancyDashboardEndpointSchemaTests(TestCase):
    def setUp(self):
        permission = Permission.objects.filter(codename="view_dashboard").first()
        group = Group.objects.create(name="pregnancy-dashboard-test-group")
        if permission:
            group.permissions.add(permission)
        User = get_user_model()
        user = User.objects.create_user(
            username="pregnancy_dashboard_test_user",
            password="test-password-123",
        )
        user.groups.add(group)
        self.client.force_login(user)

        Pregnancy.objects.create(
            key="preg-schema-1",
            submissiondate="2026-02-15",
            today="2026-02-15",
            start="2026-02-15",
            end="2026-02-15",
            PE_09A="2026-02-01",
            PE_07A=26,
            PE_22=2,
            province="Lusaka",
            district="Lusaka",
        )

    def test_summary_schema(self):
        data = self.client.get(reverse("va_analytics:pregnancy-summary-api")).json()
        self.assertEqual(
            set(data.keys()),
            {
                "card_last_data_update",
                "card_last_event_date",
                "card_number_of_events",
                "card_mean_age",
            },
        )

    def test_trend_schema(self):
        data = self.client.get(reverse("va_analytics:pregnancy-trend-api")).json()
        self.assertEqual(set(data.keys()), {"labels", "data"})

    def test_ga_detection_schema(self):
        data = self.client.get(reverse("va_analytics:pregnancy-ga-detection-api")).json()
        self.assertEqual(set(data.keys()), {"labels", "data"})

    def test_ga_anc_schema(self):
        data = self.client.get(reverse("va_analytics:pregnancy-ga-anc-api")).json()
        self.assertEqual(set(data.keys()), {"points", "x_min", "x_max"})

    def test_map_schema(self):
        data = self.client.get(reverse("va_analytics:pregnancy-map-api")).json()
        self.assertTrue(
            {
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
            }.issubset(set(data.keys()))
        )
