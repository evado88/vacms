"""Integration tests for GPS map endpoints with PII gating."""

import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from va_explorer.tests.factories import GroupFactory, UserFactory
from va_explorer.va_data_management.models import (
    Death,
    Location,
    Pregnancy,
    PregnancyOutcome,
    VerbalAutopsy,
)


def _make_user_with_perms(client, codenames, *, location_restrictions=None):
    perms = list(Permission.objects.filter(codename__in=codenames))
    group = GroupFactory.create(permissions=perms)
    user = UserFactory.create(
        groups=[group], location_restrictions=location_restrictions or []
    )
    client.force_login(user)
    return user


def _make_scoped_province(name="Scoped Province"):
    return Location.add_root(name=name, location_type="province", is_active=True)


@pytest.mark.django_db
class TestPregnancyOutcomeMapGPS:
    @pytest.fixture(autouse=True)
    def _seed(self):
        PregnancyOutcome.objects.create(
            key="po-gps-1",
            province="Lusaka",
            district="Lusaka",
            ward="Kabulonga",
            ea="EA-1",
            gps="-14.5 28.3 1242 5",
            submissiondate="2026-02-01",
            today="2026-02-01",
        )
        PregnancyOutcome.objects.create(
            key="po-no-gps",
            province="Lusaka",
            district="Lusaka",
            gps="",
            submissiondate="2026-02-01",
            today="2026-02-01",
        )

    def test_gps_omitted_by_default(self, client):
        _make_user_with_perms(client, ["view_dashboard", "view_pii"])
        data = client.get(reverse("va_analytics:po-map-api")).json()
        assert "gps_points" not in data

    def test_pii_user_gets_gps_points(self, client):
        _make_user_with_perms(client, ["view_dashboard", "view_pii"])
        data = client.get(
            reverse("va_analytics:po-map-api"), {"include_gps_points": "true"}
        ).json()
        assert data.get("gps_pii_denied") is False
        assert isinstance(data.get("gps_points"), list)
        assert len(data["gps_points"]) == 1
        point = data["gps_points"][0]
        assert point["event_type"] == "pregnancy_outcome"
        assert point["latitude"] == -14.5
        assert point["longitude"] == 28.3
        assert point["province"] == "Lusaka"

    def test_non_pii_user_gets_denied(self, client):
        _make_user_with_perms(client, ["view_dashboard"])
        data = client.get(
            reverse("va_analytics:po-map-api"), {"include_gps_points": "true"}
        ).json()
        assert data.get("gps_pii_denied") is True
        assert data.get("gps_points") == []

    def test_pagination_limit_respected(self, client):
        _make_user_with_perms(client, ["view_dashboard", "view_pii"])
        # Add a second valid GPS record
        PregnancyOutcome.objects.create(
            key="po-gps-2",
            province="Lusaka",
            district="Lusaka",
            gps="-15.0 28.5",
            submissiondate="2026-02-01",
            today="2026-02-01",
        )
        data = client.get(
            reverse("va_analytics:po-map-api"),
            {"include_gps_points": "true", "gps_limit": "1"},
        ).json()
        assert len(data["gps_points"]) == 1
        assert data["gps_total"] == 2
        assert data["gps_truncated"] is True

    def test_pregnancy_events_map_alias_returns_gps_points(self, client):
        _make_user_with_perms(client, ["view_dashboard", "view_pii"])
        data = client.get(
            reverse("va_analytics:preg-events-map-api"),
            {"include_gps_points": "true"},
        ).json()
        assert data.get("gps_pii_denied") is False
        assert len(data["gps_points"]) == 1
        point = data["gps_points"][0]
        assert point["event_type"] == "pregnancy_outcome"
        assert point["latitude"] == -14.5
        assert point["longitude"] == 28.3

    def test_pregnancy_events_page_loads_gps_overlay_assets(self, client):
        _make_user_with_perms(client, ["view_dashboard", "view_pii"])
        response = client.get(reverse("va_analytics:pregnancy-events-dashboard"))
        assert response.status_code == 200
        html = response.content.decode()
        assert 'data-user-pii="true"' in html
        assert "dashboard_gps_layer.js" in html
        assert "hierarchical_dashboard_map.js" in html
        assert "pregnancy_events_dashboard.js" in html


@pytest.mark.django_db
class TestPregnancyMapGPS:
    def test_pe25_field_used(self, client):
        _make_user_with_perms(client, ["view_dashboard", "view_pii"])
        Pregnancy.objects.create(
            key="preg-1",
            province="Lusaka",
            district="Lusaka",
            PE_25="-14.5 28.3",
            submissiondate="2026-02-01",
            today="2026-02-01",
        )
        data = client.get(
            reverse("va_analytics:pregnancy-map-api"), {"include_gps_points": "true"}
        ).json()
        assert len(data["gps_points"]) == 1
        assert data["gps_points"][0]["event_type"] == "pregnancy"


@pytest.mark.django_db
class TestDeathsMapGPS:
    def test_de34_field_used(self, client):
        _make_user_with_perms(client, ["view_dashboard", "view_pii"])
        # bulk_create bypasses post_save signals so the test doesn't depend on
        # a running Celery broker (some Death post_save handlers publish tasks).
        Death.objects.bulk_create(
            [
                Death(
                    key="death-1",
                    province="Lusaka",
                    district="Lusaka",
                    DE_34="-15.0 28.5",
                    submissiondate="2026-02-01",
                    today="2026-02-01",
                )
            ]
        )
        data = client.get(
            reverse("va_analytics:outcomes-deaths-map-api"),
            {"include_gps_points": "true"},
        ).json()
        assert len(data["gps_points"]) == 1
        point = data["gps_points"][0]
        assert point["event_type"] == "death"
        assert point["latitude"] == -15.0


@pytest.mark.django_db
@pytest.mark.parametrize(
    (
        "endpoint_name",
        "model",
        "event_type",
        "match_kwargs",
        "other_kwargs",
        "use_bulk_create",
    ),
    [
        (
            "va_analytics:pregnancy-map-api",
            Pregnancy,
            "pregnancy",
            {
                "key": "scoped-pregnancy-gps",
                "PE_25": "-14.5 28.3",
                "submissiondate": "2026-02-01",
                "today": "2026-02-01",
            },
            {
                "key": "outside-pregnancy-gps",
                "PE_25": "-15.0 28.5",
                "submissiondate": "2026-02-01",
                "today": "2026-02-01",
            },
            False,
        ),
        (
            "va_analytics:po-map-api",
            PregnancyOutcome,
            "pregnancy_outcome",
            {
                "key": "scoped-po-gps",
                "gps": "-14.5 28.3",
                "submissiondate": "2026-02-01",
                "today": "2026-02-01",
            },
            {
                "key": "outside-po-gps",
                "gps": "-15.0 28.5",
                "submissiondate": "2026-02-01",
                "today": "2026-02-01",
            },
            False,
        ),
        (
            "va_analytics:outcomes-deaths-map-api",
            Death,
            "death",
            {
                "key": "scoped-death-gps",
                "DE_34": "-14.5 28.3",
                "submissiondate": "2026-02-01",
                "today": "2026-02-01",
            },
            {
                "key": "outside-death-gps",
                "DE_34": "-15.0 28.5",
                "submissiondate": "2026-02-01",
                "today": "2026-02-01",
            },
            True,
        ),
        (
            "va_analytics:dashboard-api",
            VerbalAutopsy,
            "verbal_autopsy",
            {
                "instanceid": "scoped-va-gps",
                "geopoint": "-14.5 28.3",
                "Id10023": "2026-02-01",
            },
            {
                "instanceid": "outside-va-gps",
                "geopoint": "-15.0 28.5",
                "Id10023": "2026-02-01",
            },
            False,
        ),
    ],
)
def test_location_scoped_pii_user_gets_only_scoped_gps_points(
    client,
    endpoint_name,
    model,
    event_type,
    match_kwargs,
    other_kwargs,
    use_bulk_create,
):
    scoped_location = _make_scoped_province("Scoped Province")
    match = {"province": "Scoped Province", **match_kwargs}
    other = {"province": "Other Province", **other_kwargs}

    if use_bulk_create:
        model.objects.bulk_create([model(**match), model(**other)])
    else:
        model.objects.create(**match)
        model.objects.create(**other)

    _make_user_with_perms(
        client,
        ["view_dashboard", "view_pii"],
        location_restrictions=[scoped_location],
    )

    data = client.get(reverse(endpoint_name), {"include_gps_points": "true"}).json()

    assert data.get("gps_pii_denied") is False
    assert data.get("gps_total") == 1
    assert len(data["gps_points"]) == 1
    point = data["gps_points"][0]
    assert point["event_type"] == event_type
    assert point["province"] == "Scoped Province"
    assert point["latitude"] == -14.5
    assert point["longitude"] == 28.3


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("endpoint_name", "model", "create_kwargs", "use_bulk_create"),
    [
        (
            "va_analytics:pregnancy-map-api",
            Pregnancy,
            {
                "key": "preg-ea-tooltip",
                "province": "LUSAKA",
                "district": "LUSAKA",
                "constituency": "MANDEVU",
                "ward": "CHAISA",
                "ea": "EA_05055012002",
                "submissiondate": "2026-02-01",
                "today": "2026-02-01",
            },
            False,
        ),
        (
            "va_analytics:po-map-api",
            PregnancyOutcome,
            {
                "key": "po-ea-tooltip",
                "province": "LUSAKA",
                "district": "LUSAKA",
                "constituency": "MANDEVU",
                "ward": "CHAISA",
                "ea": "EA_05055012002",
                "submissiondate": "2026-02-01",
                "today": "2026-02-01",
            },
            False,
        ),
        (
            "va_analytics:outcomes-deaths-map-api",
            Death,
            {
                "key": "death-ea-tooltip",
                "province": "LUSAKA",
                "district": "LUSAKA",
                "constituency": "MANDEVU",
                "ward": "CHAISA",
                "ea": "EA_05055012002",
                "submissiondate": "2026-02-01",
                "today": "2026-02-01",
            },
            True,
        ),
    ],
)
def test_map_ea_sums_match_geojson_ea_names(
    client, endpoint_name, model, create_kwargs, use_bulk_create
):
    _make_user_with_perms(client, ["view_dashboard"])
    if use_bulk_create:
        model.objects.bulk_create([model(**create_kwargs)])
    else:
        model.objects.create(**create_kwargs)

    data = client.get(
        reverse(endpoint_name),
        {
            "geography_level": "ward",
            "geography_value": "CHAISA",
            "map_view": "Ward",
        },
    ).json()

    assert {"ea_name": "05055012002", "count": 1} in data["map_ea_sums"]
    assert "EA_05055012002" not in {row["ea_name"] for row in data["map_ea_sums"]}
