"""
pytest-benchmark baselines for the three heaviest views.

Run with:
    pytest va_explorer/tests/test_benchmarks.py --benchmark-only
    pytest va_explorer/tests/test_benchmarks.py --benchmark-save=baseline

Compare against a saved baseline:
    pytest va_explorer/tests/test_benchmarks.py --benchmark-compare=baseline

These tests require an authenticated user and the standard permissions group
fixture, but do NOT require real data — the baseline just measures framework
overhead so later Tier 3/4 changes can demonstrate measurable wins.
"""

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from va_explorer.tests.factories import UserFactory

pytestmark = pytest.mark.django_db(transaction=True)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def django_db_setup_module(django_db_setup, django_db_blocker):
    """Module-level DB setup so fixture data is created once per benchmark run."""


@pytest.fixture
def staff_client():
    """Authenticated client with broad view permissions."""
    user = UserFactory.create(is_superuser=True, is_staff=True)
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def client_with_va_perms():
    """Client with permissions needed for the VA data-management views."""

    view_va = Permission.objects.filter(codename="view_verbalautopsy").first()
    view_pii = Permission.objects.filter(codename="view_pii").first()
    user = UserFactory.create()
    if view_va:
        user.user_permissions.add(view_va)
    if view_pii:
        user.user_permissions.add(view_pii)
    client = Client()
    client.force_login(user)
    return client


# ---------------------------------------------------------------------------
# Benchmark: home dashboard index
# ---------------------------------------------------------------------------


def test_bench_home_index(benchmark, staff_client):
    """Baseline response time for the home dashboard (/)."""
    url = reverse("home:index")

    def _request():
        return staff_client.get(url)

    response = benchmark(_request)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Benchmark: analytics dashboard
# ---------------------------------------------------------------------------


def test_bench_analytics_dashboard(benchmark, staff_client):
    """Baseline response time for the analytics dashboard (/va_analytics/dashboard/)."""
    url = reverse("va_analytics:dashboard")

    def _request():
        return staff_client.get(url)

    response = benchmark(_request)
    assert response.status_code in (200, 302)


# ---------------------------------------------------------------------------
# Benchmark: data cleanup index
# ---------------------------------------------------------------------------


def test_bench_data_cleanup_index(benchmark, staff_client):
    """Baseline response time for the data cleanup list (/va_data_cleanup/)."""
    url = reverse("data_cleanup:index")

    def _request():
        return staff_client.get(url)

    response = benchmark(_request)
    assert response.status_code in (200, 302)
