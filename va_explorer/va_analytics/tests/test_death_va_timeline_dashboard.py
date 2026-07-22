from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse

from va_explorer.tests.factories import (
    LocationFactory,
    VASAFactory,
    VerbalAutopsyFactory,
)
from va_explorer.va_analytics.views import (
    _build_death_va_timeline,
    build_deaths_qs,
)
from va_explorer.va_data_management.models import (
    Death,
    MatchStatus,
    VADeathMatch,
)


def _make_death(**kwargs):
    # bulk_create avoids Death's post_save signal (queues a Celery task to an
    # unreachable broker in tests); Postgres populates pk on the instance.
    death = Death(**kwargs)
    Death.objects.bulk_create([death])
    return death


def _link(death, *, va=None, vasa=None, status=MatchStatus.MATCHED_AUTO):
    return VADeathMatch.objects.create(
        verbal_autopsy=va,
        vasa=vasa,
        death=death,
        status=status,
    )


class DeathVaTimelineBuilderTests(TestCase):
    def test_payload_shape(self):
        payload = _build_death_va_timeline(Death.objects.all())

        assert set(payload.keys()) == {
            "cards",
            "delay_histogram",
            "cumulative",
            "monthly_medians",
            "excluded",
        }
        assert set(payload["cards"].keys()) == {
            "total_deaths",
            "linked_deaths",
            "linked_coverage_pct",
            "median_death_to_capture_days",
            "median_death_to_va_days",
            "median_capture_to_va_days",
            "review_queue_pending",
            "review_queue_ambiguous",
            "matches_with_contradictions",
        }
        assert set(payload["delay_histogram"].keys()) == {
            "labels",
            "death_to_capture",
            "death_to_va",
            "capture_to_va",
        }
        assert set(payload["cumulative"].keys()) == {
            "thresholds",
            "captured_pct",
            "va_submitted_pct",
            "denominator",
        }
        assert set(payload["monthly_medians"].keys()) == {
            "labels",
            "capture_delay",
            "va_delay",
            "capture_to_va_delay",
        }
        assert set(payload["excluded"].keys()) == {
            "unparseable_death_date",
            "negative_delta",
            "implausible_delta",
        }

    def test_bucketing_and_medians_with_known_dates(self):
        death = _make_death(
            key="timeline-known",
            DE_06="2026-01-01",
            submissiondate="2026-01-02",
        )
        va = VerbalAutopsyFactory.create(submissiondate="2026-01-15")
        _link(death, va=va)

        payload = _build_death_va_timeline(Death.objects.all())

        labels = payload["delay_histogram"]["labels"]
        assert labels == ["<0", "0-1", "2-3", "4-7", "8-14", "15-30", "31-60", ">60"]
        # capture delay = 1 day -> "0-1"; VA delay = 14 days -> "8-14"
        assert payload["delay_histogram"]["death_to_capture"] == [
            0,
            1,
            0,
            0,
            0,
            0,
            0,
            0,
        ]
        assert payload["delay_histogram"]["death_to_va"] == [
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
        ]
        assert payload["delay_histogram"]["capture_to_va"] == [
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
        ]

        cards = payload["cards"]
        assert cards["total_deaths"] == 1
        assert cards["linked_deaths"] == 1
        assert cards["linked_coverage_pct"] == 100.0
        assert cards["median_death_to_capture_days"] == 1.0
        assert cards["median_death_to_va_days"] == 14.0
        assert cards["median_capture_to_va_days"] == 13.0

        cumulative = payload["cumulative"]
        assert cumulative["thresholds"] == [0, 1, 3, 7, 14, 30, 60, 90]
        assert cumulative["denominator"] == 1
        assert cumulative["captured_pct"] == [
            0.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
        ]
        assert cumulative["va_submitted_pct"] == [
            0.0,
            0.0,
            0.0,
            0.0,
            100.0,
            100.0,
            100.0,
            100.0,
        ]

        monthly = payload["monthly_medians"]
        assert monthly["labels"] == ["2026-01"]
        assert monthly["capture_delay"] == [1.0]
        assert monthly["va_delay"] == [14.0]
        assert monthly["capture_to_va_delay"] == [13.0]

    def test_coverage_denominator_counts_unlinked_deaths(self):
        linked = _make_death(
            key="timeline-cov-linked",
            DE_06="2026-01-01",
            submissiondate="2026-01-02",
        )
        _make_death(
            key="timeline-cov-unlinked-1",
            DE_06="2026-01-01",
            submissiondate="2026-01-03",
        )
        _make_death(
            key="timeline-cov-unlinked-2",
            DE_06="2026-01-01",
            submissiondate="2026-01-04",
        )
        va = VerbalAutopsyFactory.create(submissiondate="2026-01-10")
        _link(linked, va=va)

        payload = _build_death_va_timeline(Death.objects.all())

        assert payload["cards"]["total_deaths"] == 3
        assert payload["cards"]["linked_deaths"] == 1
        assert abs(payload["cards"]["linked_coverage_pct"] - 33.3) < 0.05
        # All three deaths contribute capture delays; only the linked one
        # contributes a VA delay.
        assert sum(payload["delay_histogram"]["death_to_capture"]) == 3
        assert sum(payload["delay_histogram"]["death_to_va"]) == 1
        assert payload["cumulative"]["denominator"] == 3

    def test_unparseable_death_date_is_counted_and_excluded(self):
        _make_death(
            key="timeline-baddate",
            DE_06="not-a-date",
            submissiondate="2026-01-02",
        )

        payload = _build_death_va_timeline(Death.objects.all())

        assert payload["excluded"]["unparseable_death_date"] == 1
        assert sum(payload["delay_histogram"]["death_to_capture"]) == 0
        assert payload["cumulative"]["denominator"] == 0

    def test_va_dod_fallback_when_death_date_missing(self):
        death = _make_death(key="timeline-fallback", DE_06="")
        va = VerbalAutopsyFactory.create(
            Id10023="2026-01-01", submissiondate="2026-01-05"
        )
        _link(death, va=va)

        payload = _build_death_va_timeline(Death.objects.all())

        assert payload["excluded"]["unparseable_death_date"] == 0
        # VA delay = 4 days -> "4-7"
        assert payload["delay_histogram"]["death_to_va"] == [0, 0, 0, 1, 0, 0, 0, 0]
        assert payload["cumulative"]["denominator"] == 1

    def test_negative_delta_is_counted_and_excluded(self):
        death = _make_death(key="timeline-negative", DE_06="2026-02-01")
        va = VerbalAutopsyFactory.create(submissiondate="2026-01-01")
        _link(death, va=va)

        payload = _build_death_va_timeline(Death.objects.all())

        assert payload["excluded"]["negative_delta"] == 1
        assert sum(payload["delay_histogram"]["death_to_va"]) == 0
        assert payload["cards"]["median_death_to_va_days"] is None

    def test_va_before_e_death_capture_is_bucketed_as_negative_capture_to_va_gap(self):
        death = _make_death(
            key="timeline-va-before-edeath",
            DE_06="2026-01-01",
            submissiondate="2026-01-20",
        )
        va = VerbalAutopsyFactory.create(submissiondate="2026-01-15")
        _link(death, va=va)

        payload = _build_death_va_timeline(Death.objects.all())

        assert payload["delay_histogram"]["labels"][0] == "<0"
        assert payload["delay_histogram"]["capture_to_va"][0] == 1
        assert payload["cards"]["median_capture_to_va_days"] == -5.0

    def test_implausible_delta_is_counted_and_excluded(self):
        death = _make_death(key="timeline-implausible", DE_06="2025-01-01")
        va = VerbalAutopsyFactory.create(submissiondate="2031-01-01")
        _link(death, va=va)

        payload = _build_death_va_timeline(Death.objects.all())

        assert payload["excluded"]["implausible_delta"] == 1
        assert sum(payload["delay_histogram"]["death_to_va"]) == 0
        # A month with no valid deltas is pruned from the monthly series.
        assert payload["monthly_medians"]["labels"] == []

    def test_earliest_submission_wins_when_va_and_vasa_both_matched(self):
        death = _make_death(key="timeline-precedence", DE_06="2026-01-01")
        va = VerbalAutopsyFactory.create(submissiondate="2026-01-20")
        vasa = VASAFactory.create(submissiondate="2026-01-10")
        _link(death, va=va)
        _link(death, vasa=vasa)

        payload = _build_death_va_timeline(Death.objects.all())

        assert payload["cards"]["linked_deaths"] == 1
        # Earliest submission (the VASA, 9 days) wins -> "8-14"; exactly one
        # observation despite two match rows.
        assert payload["delay_histogram"]["death_to_va"] == [0, 0, 0, 0, 1, 0, 0, 0]
        assert payload["cards"]["median_death_to_va_days"] == 9.0

    def test_non_authoritative_statuses_do_not_link(self):
        death = _make_death(
            key="timeline-status",
            DE_06="2026-01-01",
            submissiondate="2026-01-02",
        )
        for status, va in [
            (
                MatchStatus.PENDING_REVIEW,
                VerbalAutopsyFactory.create(submissiondate="2026-01-10"),
            ),
            (
                MatchStatus.UNMATCHED,
                VerbalAutopsyFactory.create(submissiondate="2026-01-11"),
            ),
            (
                MatchStatus.REJECTED,
                VerbalAutopsyFactory.create(submissiondate="2026-01-12"),
            ),
        ]:
            _link(death, va=va, status=status)

        payload = _build_death_va_timeline(Death.objects.all())

        assert payload["cards"]["linked_deaths"] == 0
        assert sum(payload["delay_histogram"]["death_to_va"]) == 0

    def test_location_scoping_restricts_all_sections(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="va_timeline_loc_user",
            email="va-timeline-loc@example.com",
            password="test-password-123",
            has_valid_password=True,
        )
        province = LocationFactory.create(name="Southern", location_type="province")
        user.location_restrictions.set([province])

        in_scope = _make_death(
            key="timeline-south",
            province="Southern",
            district="Choma",
            DE_06="2026-01-01",
            submissiondate="2026-01-02",
        )
        out_of_scope = _make_death(
            key="timeline-lusaka",
            province="Lusaka",
            district="Lusaka",
            DE_06="2026-01-01",
            submissiondate="2026-01-03",
        )
        _link(in_scope, va=VerbalAutopsyFactory.create(submissiondate="2026-01-10"))
        _link(out_of_scope, va=VerbalAutopsyFactory.create(submissiondate="2026-01-11"))

        request = RequestFactory().get("/va_analytics/outcomes-dashboard/")
        request.user = user
        payload = _build_death_va_timeline(build_deaths_qs(request))

        assert payload["cards"]["total_deaths"] == 1
        assert payload["cards"]["linked_deaths"] == 1
        assert sum(payload["delay_histogram"]["death_to_capture"]) == 1
        assert sum(payload["delay_histogram"]["death_to_va"]) == 1


class DeathVaTimelineApiTests(TestCase):
    def setUp(self):
        permission = Permission.objects.filter(codename="view_dashboard").first()
        group = Group.objects.create(name="va-timeline-test-group")
        if permission:
            group.permissions.add(permission)

        User = get_user_model()
        user = User.objects.create_user(
            username="va_timeline_api_user",
            email="va-timeline-api@example.com",
            password="test-password-123",
            has_valid_password=True,
        )
        user.groups.add(group)
        self.client.force_login(user)

    def test_endpoint_returns_payload(self):
        _make_death(
            key="timeline-api-1",
            DE_06="2026-02-01",
            submissiondate="2026-02-02",
        )

        response = self.client.get(
            reverse("va_analytics:outcomes-deaths-va-timeline-api")
        )

        assert response.status_code == 200
        payload = response.json()
        assert set(payload.keys()) == {
            "cards",
            "delay_histogram",
            "cumulative",
            "monthly_medians",
            "excluded",
        }
        assert payload["cards"]["total_deaths"] == 1

    def test_tab_guard(self):
        url = reverse("va_analytics:outcomes-deaths-va-timeline-api")

        assert self.client.get(url, {"tab": "pregnancies"}).status_code == 400
        assert self.client.get(url).status_code == 200
        assert self.client.get(url, {"tab": "deaths"}).status_code == 200
        assert self.client.get(url, {"tab": "va_timeline"}).status_code == 200

    def test_custom_date_range_filters_by_date_of_death(self):
        _make_death(
            key="timeline-range-in",
            DE_06="2026-03-05",
            submissiondate="2026-03-06",
        )
        _make_death(
            key="timeline-range-out",
            DE_06="2026-01-20",
            submissiondate="2026-01-21",
        )

        response = self.client.get(
            reverse("va_analytics:outcomes-deaths-va-timeline-api"),
            {
                "time_preset": "custom",
                "start_datetime": "2026-03-01T00:00",
                "end_datetime": "2026-03-31T23:59",
            },
        )

        assert response.status_code == 200
        assert response.json()["cards"]["total_deaths"] == 1

    def test_operations_dashboard_renders_va_timeline_tab(self):
        response = self.client.get(reverse("home:index"), {"tab": "va_timeline"})

        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert 'id="deathVaTimelineApp"' in content
        assert 'data-tab-panel="va_timeline"' in content
        assert 'data-tab="va_timeline"' in content
        assert 'id="va-timeline-tab"' in content
        assert 'class="nav-link active" id="va-timeline-tab"' in content
        assert "E_DEATH Capture &rarr; VA Capture" in content
        assert "Date of death &rarr; E_DEATH capture" in content
        assert "css/death_va_timeline_dashboard.css" in content
        assert "js/death_va_timeline_dashboard.js" in content

    def test_outcomes_dashboard_no_longer_renders_va_timeline_tab(self):
        response = self.client.get(
            reverse("va_analytics:outcomes-dashboard"), {"tab": "va_timeline"}
        )

        assert response.status_code == 200
        assert response.context["active_tab"] == "pregnancies"
        content = response.content.decode("utf-8")
        assert 'id="deathVaTimelineApp"' not in content
        assert 'data-tab-panel="va_timeline"' not in content
        assert "js/death_va_timeline_dashboard.js" not in content
