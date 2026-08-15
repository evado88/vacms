"""Integration tests for GPS map endpoints with PII gating."""

import pytest
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from va_explorer.tests.factories import GroupFactory, UserFactory
from va_explorer.va_data_management.models import (
    VASA,
    Death,
    Location,
    Pregnancy,
    PregnancyOutcome,
    SRSClusterLocation,
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

    def test_invalid_coordinates_are_excluded(self, client):
        _make_user_with_perms(client, ["view_dashboard", "view_pii"])
        PregnancyOutcome.objects.create(
            key="po-invalid-gps",
            gps="-95.0 28.5",
            submissiondate="2026-02-01",
            today="2026-02-01",
        )
        data = client.get(
            reverse("va_analytics:po-map-api"), {"include_gps_points": "true"}
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
        assert "dashboard_gps_layer.css" in html
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
            PE_09A="2026-02-01",
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
                    DE_06="2026-02-01",
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
class TestVerbalAutopsyMapGPS:
    def test_geopoint_field_used(self, client):
        _make_user_with_perms(client, ["view_dashboard", "view_pii"])
        VerbalAutopsy.objects.create(
            instanceid="va-gps-1",
            geopoint="-15.2 28.1",
            Id10023="2026-02-01",
        )
        data = client.get(
            reverse("va_analytics:dashboard-api"),
            {"include_gps_points": "true"},
        ).json()
        assert len(data["gps_points"]) == 1
        point = data["gps_points"][0]
        assert point["event_type"] == "verbal_autopsy"
        assert point["latitude"] == -15.2


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
                "PE_09A": "2026-02-01",
                "submissiondate": "2026-02-01",
                "today": "2026-02-01",
            },
            {
                "key": "outside-pregnancy-gps",
                "PE_25": "-15.0 28.5",
                "PE_09A": "2026-02-01",
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
                "DE_06": "2026-02-01",
                "submissiondate": "2026-02-01",
                "today": "2026-02-01",
            },
            {
                "key": "outside-death-gps",
                "DE_34": "-15.0 28.5",
                "DE_06": "2026-02-01",
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
                "PE_09A": "2026-02-01",
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
                "DE_06": "2026-02-01",
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


def _regular_gps_record(model, index, province):
    common = {
        "key": f"balanced-{model._meta.model_name}-{index}",
        "province": province,
        "district": f"{province} District",
        "constituency": f"{province} Constituency",
        "ward": f"{province} Ward",
        "ea": f"{province} EA",
        "submissiondate": "2026-02-01",
        "today": "2026-02-01",
    }
    coordinate = f"-1{index % 8}.5 2{index % 8}.3"
    if model is Pregnancy:
        return model(PE_25=coordinate, PE_09A="2026-02-01", **common)
    if model is PregnancyOutcome:
        return model(gps=coordinate, **common)
    return model(DE_34=coordinate, DE_06="2026-02-01", **common)


@pytest.mark.django_db
@override_settings(OUTCOMES_DASHBOARD_GPS_MAX_POINTS=4)
@pytest.mark.parametrize(
    ("endpoint_name", "model"),
    [
        ("va_analytics:pregnancy-map-api", Pregnancy),
        ("va_analytics:po-map-api", PregnancyOutcome),
        ("va_analytics:outcomes-deaths-map-api", Death),
    ],
)
def test_default_cap_balances_province_batched_records(client, endpoint_name, model):
    cache.clear()
    provinces = ["Lusaka", "Southern", "Copperbelt", "Central"]
    records = []
    for province in provinces:
        records.extend(
            _regular_gps_record(model, len(records) + offset, province)
            for offset in range(3)
        )
    model.objects.bulk_create(records)
    _make_user_with_perms(client, ["view_dashboard", "view_pii"])

    data = client.get(
        reverse(endpoint_name),
        {"include_gps_points": "true", "map_view": "Province"},
    ).json()

    assert data["gps_total"] == 12
    assert data["gps_truncated"] is True
    assert len(data["gps_points"]) == 4
    assert {point["province"] for point in data["gps_points"]} == set(provinces)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("endpoint_name", "model"),
    [
        ("va_analytics:pregnancy-map-api", Pregnancy),
        ("va_analytics:po-map-api", PregnancyOutcome),
        ("va_analytics:outcomes-deaths-map-api", Death),
    ],
)
@pytest.mark.parametrize(
    ("map_view", "active_field"),
    [
        ("Province", "province"),
        ("District", "district"),
        ("Constituency", "constituency"),
        ("Ward", "ward"),
        ("EA", "ea"),
    ],
)
def test_outcomes_gps_balances_the_active_geographic_level(
    client, endpoint_name, model, map_view, active_field
):
    cache.clear()
    base_geo = {
        "province": "Root",
        "district": "Root District",
        "constituency": "Root Constituency",
        "ward": "Root Ward",
        "ea": "Root EA",
    }
    records = []
    for index, active_value in enumerate(["Alpha", "Alpha", "Alpha", "Beta"]):
        geo = {**base_geo, active_field: active_value}
        record = _regular_gps_record(model, index, geo["province"])
        record.key = f"hierarchy-{model._meta.model_name}-{active_field}-{index}"
        for field_name, value in geo.items():
            setattr(record, field_name, value)
        records.append(record)
    model.objects.bulk_create(records)
    _make_user_with_perms(client, ["view_dashboard", "view_pii"])

    data = client.get(
        reverse(endpoint_name),
        {
            "include_gps_points": "true",
            "gps_limit": "2",
            "map_view": map_view,
        },
    ).json()

    assert len(data["gps_points"]) == 2
    assert {point[active_field] for point in data["gps_points"]} == {
        "Alpha",
        "Beta",
    }


@pytest.mark.django_db
def test_gps_drilldown_filters_to_parent_before_balancing_children(client):
    cache.clear()
    rows = [
        ("Alpha", "District A", "-14.1 28.1"),
        ("Alpha", "District B", "-14.2 28.2"),
        ("Beta", "District C", "-14.3 28.3"),
    ]
    Pregnancy.objects.bulk_create(
        [
            Pregnancy(
                key=f"parent-filter-{index}",
                province=province,
                district=district,
                PE_25=gps,
                PE_09A="2026-02-01",
                submissiondate="2026-02-01",
                today="2026-02-01",
            )
            for index, (province, district, gps) in enumerate(rows)
        ]
    )
    _make_user_with_perms(client, ["view_dashboard", "view_pii"])

    data = client.get(
        reverse("va_analytics:pregnancy-map-api"),
        {
            "include_gps_points": "true",
            "gps_limit": "2",
            "map_view": "District",
            "geography_level": "province",
            "geography_value": "Alpha",
        },
    ).json()

    assert data["gps_total"] == 2
    assert {point["province"] for point in data["gps_points"]} == {"Alpha"}
    assert {point["district"] for point in data["gps_points"]} == {
        "District A",
        "District B",
    }


@pytest.mark.django_db
def test_hierarchy_and_offset_have_independent_deterministic_samples(client):
    cache.clear()
    Pregnancy.objects.bulk_create(
        [
            Pregnancy(
                key=f"cache-offset-{index}",
                province="Alpha",
                district="District A" if index < 3 else "District B",
                PE_25=f"-1{index}.5 2{index}.3",
                PE_09A="2026-02-01",
                submissiondate="2026-02-01",
                today="2026-02-01",
            )
            for index in range(4)
        ]
    )
    _make_user_with_perms(client, ["view_dashboard", "view_pii"])
    endpoint = reverse("va_analytics:pregnancy-map-api")

    province_page = client.get(
        endpoint,
        {"include_gps_points": "true", "gps_limit": "2", "map_view": "Province"},
    ).json()
    district_page = client.get(
        endpoint,
        {"include_gps_points": "true", "gps_limit": "2", "map_view": "District"},
    ).json()
    district_next_page = client.get(
        endpoint,
        {
            "include_gps_points": "true",
            "gps_limit": "2",
            "gps_offset": "2",
            "map_view": "District",
        },
    ).json()

    assert {point["district"] for point in province_page["gps_points"]} == {
        "District A"
    }
    assert {point["district"] for point in district_page["gps_points"]} == {
        "District A",
        "District B",
    }
    assert {point["id"] for point in district_page["gps_points"]}.isdisjoint(
        {point["id"] for point in district_next_page["gps_points"]}
    )
    assert district_next_page["gps_total"] == 4
    assert district_next_page["gps_truncated"] is False


@pytest.mark.django_db
@override_settings(OUTCOMES_DASHBOARD_GPS_MAX_POINTS=4)
def test_va_and_vasa_share_one_geographically_balanced_cap(client):
    cache.clear()
    for index, province in enumerate(["Alpha", "Alpha", "Alpha", "Beta"]):
        VerbalAutopsy.objects.create(
            instanceid=f"balanced-va-{index}",
            geopoint=f"-1{index}.1 2{index}.1",
            Id10023="2026-02-01",
            community_va="yes",
            province=province,
        )
    for index, province in enumerate(["Alpha", "Beta", "Beta", "Beta"]):
        VASA.objects.create(
            instanceid=f"balanced-vasa-{index}",
            geopoint=f"-1{index}.2 2{index}.2",
            Id10023="2026-02-01",
            community_va="yes",
            province=province,
        )
    _make_user_with_perms(client, ["view_dashboard", "view_pii"])

    data = client.get(
        reverse("va_analytics:dashboard-api"),
        {"include_gps_points": "true", "map_view": "Province", "source": "all"},
    ).json()

    assert data["gps_total"] == 8
    assert data["gps_truncated"] is True
    assert len(data["gps_points"]) == 4
    assert {point["province"] for point in data["gps_points"]} == {
        "Alpha",
        "Beta",
    }
    assert {point["id"].split("_", 1)[0] for point in data["gps_points"]} == {
        "verbalautopsy",
        "vasa",
    }


@pytest.mark.django_db
def test_va_gps_uses_standard_geography_aliases_and_cluster_hierarchy(client):
    cache.clear()
    province = SRSClusterLocation.add_root(
        name="Derived Province", location_type="province", code="P1", status="Active"
    )
    district = province.add_child(
        name="D-One", location_type="district", code="D1", status="Active"
    )
    constituency = district.add_child(
        name="C-One",
        location_type="constituency",
        code="C1",
        status="Active",
    )
    ward = constituency.add_child(
        name="W-One", location_type="ward", code="W1", status="Active"
    )
    ea = ward.add_child(
        name="123456789", location_type="ea", code="EA1", status="Active"
    )
    VerbalAutopsy.objects.create(
        instanceid="cluster-derived-gps",
        geopoint="-14.5 28.3",
        Id10023="2026-02-01",
        community_va="yes",
        cluster=ea,
    )
    VerbalAutopsy.objects.create(
        instanceid="outside-derived-gps",
        geopoint="-15.0 29.0",
        Id10023="2026-02-01",
        community_va="yes",
        province="Outside",
    )
    _make_user_with_perms(client, ["view_dashboard", "view_pii"])

    data = client.get(
        reverse("va_analytics:dashboard-api"),
        {
            "include_gps_points": "true",
            "map_view": "EA",
            "geography_level": "province",
            "geography_value": "Derived",
        },
    ).json()

    assert data["gps_total"] == 1
    assert len(data["gps_points"]) == 1
    point = data["gps_points"][0]
    assert point["province"] == "Derived"
    assert point["district"] == "D-One"
    assert point["constituency"] == "C-One"
    assert point["ward"] == "W-One"
    assert point["ea"] == "123456789"
