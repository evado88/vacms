from datetime import timedelta

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from va_explorer.tests.factories import GroupFactory, LocationFactory, UserFactory
from va_explorer.va_analytics.views import (
    build_pregnancy_outcomes_qs,
    get_pregnancy_outcomes_filter_state,
)
from va_explorer.va_data_management.models import PregnancyOutcome

pytestmark = pytest.mark.django_db


class TestPregnancyOutcomesFilterParsing:
    def test_defaults(self, rf):
        request = rf.get("/va_analytics/outcomes-dashboard/")
        state = get_pregnancy_outcomes_filter_state(request)

        assert state["pregnancy_outcome"] == ""
        assert state["time_preset"] == "all_time"
        assert state["start_datetime"] == ""
        assert state["end_datetime"] == ""
        assert state["map_view"] == "Province"

    def test_invalid_values_are_sanitized(self, rf):
        request = rf.get(
            "/va_analytics/outcomes-dashboard/",
            {
                "time_preset": "bad",
                "map_view": "ward",
            },
        )
        state = get_pregnancy_outcomes_filter_state(request)

        assert state["time_preset"] == "all_time"
        assert state["map_view"] == "Ward"


class TestPregnancyOutcomesQuerysetBuilder:
    def _create_po(self, **kwargs):
        defaults = {
            "key": f"po-{timezone.now().timestamp()}-{PregnancyOutcome.objects.count()}",
            "po_group": "Live Birth",
            "PO_41": "2026-02-01",
            "submissiondate": "2026-02-01",
            "today": "2026-02-01",
            "start": "2026-02-01",
        }
        defaults.update(kwargs)
        return PregnancyOutcome.objects.create(**defaults)

    def test_filters_by_pregnancy_outcome(self, rf):
        live = self._create_po(key="po-live", po_group="Live Birth")
        still = self._create_po(key="po-still", po_group="Stillbirth")

        request = rf.get(
            "/va_analytics/outcomes-dashboard/", {"pregnancy_outcome": "Stillbirth"}
        )
        qs = build_pregnancy_outcomes_qs(request)

        assert list(qs.values_list("key", flat=True)) == [still.key]
        assert live.key not in list(qs.values_list("key", flat=True))

    def test_filters_by_last_7_days(self, rf):
        now = timezone.localtime(timezone.now())
        recent_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        old_date = (now - timedelta(days=20)).strftime("%Y-%m-%d")

        recent = self._create_po(
            key="po-recent", PO_41=recent_date, submissiondate=recent_date
        )
        old = self._create_po(key="po-old", PO_41=old_date, submissiondate=old_date)

        request = rf.get(
            "/va_analytics/outcomes-dashboard/", {"time_preset": "last_7_days"}
        )
        qs = build_pregnancy_outcomes_qs(request)

        keys = set(qs.values_list("key", flat=True))
        assert recent.key in keys
        assert old.key not in keys

    def test_applies_user_location_restrictions(self, rf):
        province = LocationFactory.create(name="Southern", location_type="province")
        user = UserFactory.create(location_restrictions=[province])
        self._create_po(key="po-southern", province="Southern", district="Choma")
        self._create_po(key="po-lusaka", province="Lusaka", district="Lusaka")

        request = rf.get("/va_analytics/outcomes-dashboard/")
        request.user = user
        qs = build_pregnancy_outcomes_qs(request)

        assert set(qs.values_list("key", flat=True)) == {"po-southern"}


class TestPregnancyOutcomesApiSchemas:
    @pytest.fixture(autouse=True)
    def _setup_user_and_data(self, client):
        permission = Permission.objects.filter(codename="view_dashboard").first()
        group = GroupFactory.create(permissions=[permission])
        user = UserFactory.create(groups=[group])
        client.force_login(user)

        PregnancyOutcome.objects.create(
            key="po-schema-1",
            po_group="Live Birth",
            PO_41="2026-02-01",
            PO_45="yes",
            PO_44="38 weeks",
            PO_44A="38",
            PO_20="3",
            PO_43="home",
            PO_05="2000-02-01",
            PO_21="positive",
            province="Lusaka",
            district="Lusaka",
            submissiondate="2026-02-01",
            today="2026-02-01",
            start="2026-02-01",
            end="2026-02-01T12:00:00",
        )

    def test_summary_schema(self, client):
        data = client.get(reverse("va_analytics:po-summary-api")).json()
        assert set(data.keys()) == {
            "card_last_data_update",
            "card_last_event_date",
            "card_number_of_events",
            "card_multiple_birth_pct",
        }

    def test_trend_schema(self, client):
        data = client.get(reverse("va_analytics:po-trend-api")).json()
        assert set(data.keys()) == {"labels", "data"}
        assert isinstance(data["labels"], list)
        assert isinstance(data["data"], list)

    def test_birth_outcomes_schema(self, client):
        data = client.get(reverse("va_analytics:po-birth-outcomes-api")).json()
        assert set(data.keys()) == {"labels", "count_data", "percentage_data"}

    def test_gestational_age_schema(self, client):
        data = client.get(reverse("va_analytics:po-gestational-age-api")).json()
        assert set(data.keys()) == {"labels", "data"}

    def test_anc_visits_schema(self, client):
        data = client.get(reverse("va_analytics:po-anc-visits-api")).json()
        assert set(data.keys()) == {"labels", "data"}

    def test_kpis_schema(self, client):
        data = client.get(reverse("va_analytics:po-kpis-api")).json()
        assert set(data.keys()) == {"mean_age", "hiv_positive_pct"}

    def test_place_of_birth_schema(self, client):
        data = client.get(reverse("va_analytics:po-place-of-birth-api")).json()
        assert set(data.keys()) == {"labels", "count_data", "percentage_data"}

    def test_map_schema(self, client):
        data = client.get(reverse("va_analytics:po-map-api")).json()
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
        }.issubset(set(data.keys()))
