"""VASA rows in the VA line list + the VASA detail page (SA/repeat/DQ tabs)."""

import pytest
from django.contrib.auth.models import Permission
from django.test import Client

from va_explorer.tests.factories import (
    CauseCodingIssueFactory,
    GroupFactory,
    LocationFactory,
    UserFactory,
    VASAFactory,
    VerbalAutopsyFactory,
)
from va_explorer.va_data_management.models import (
    VASACareSeekingNeonate,
    VASASocialAutopsy,
)
from va_explorer.va_data_management.vasa.model_fields import SA_FIELD_DEFS

pytestmark = pytest.mark.django_db


def _viewer():
    can_view_record = Permission.objects.filter(codename="view_verbalautopsy").first()
    can_view_pii = Permission.objects.filter(codename="view_pii").first()
    group = GroupFactory.create(permissions=[can_view_record, can_view_pii])
    user = UserFactory.create(groups=[group])
    client = Client()
    client.force_login(user=user)
    return user, client


def test_line_list_includes_vasa_rows_with_source_and_routing():
    _user, client = _viewer()
    va = VerbalAutopsyFactory.create(Id10017="VA Victim", Id10010="VA Interviewer")
    vasa = VASAFactory.create(Id10017="VASA Victim", Id10010="VASA Interviewer")

    response = client.get("/va_data_management/")
    assert response.status_code == 200
    content = response.content.decode()

    # Both records appear, tagged by source, and VASA routes to its own detail.
    assert "VA Victim" in content
    assert "VASA Victim" in content
    assert ">Source<" in content
    assert ">VASA<" in content
    assert f"/va_data_management/vasa/{vasa.id}" in content
    assert f"/va_data_management/show/{va.id}" in content


def test_line_list_deceased_filter_applies_to_vasa():
    _user, client = _viewer()
    VerbalAutopsyFactory.create(Id10017="VA", Id10018="Person")
    match = VASAFactory.create(Id10017="Unique", Id10018="Vasaname")

    response = client.get("/va_data_management/", {"deceased": "Unique Vasaname"})
    assert response.status_code == 200
    content = response.content.decode()
    assert "Unique" in content
    assert f"/va_data_management/vasa/{match.id}" in content
    # The non-matching VA is filtered out of both legs.
    assert "VA Person" not in content


def test_vasa_detail_renders_sa_repeat_and_dq_tabs():
    _user, client = _viewer()
    vasa = VASAFactory.create(Id10017="VASA Victim", instrument="neonate")

    sa_field = SA_FIELD_DEFS[0][1]
    VASASocialAutopsy.objects.create(vasa=vasa, **{sa_field: "SA_TAB_VALUE"})
    VASACareSeekingNeonate.objects.create(vasa=vasa, key="cs-1", parent_key=vasa.key)
    CauseCodingIssueFactory.create(
        vasa=vasa, text="An error occurred", severity="error", algorithm="InterVA5"
    )

    response = client.get(f"/va_data_management/vasa/{vasa.id}")
    assert response.status_code == 200
    content = response.content.decode()

    assert "Social Autopsy" in content
    assert "SA_TAB_VALUE" in content
    assert "Care-Seeking (Neonate) (1)" in content
    assert "Data Quality" in content
    assert "An error occurred" in content


def test_vasa_detail_respects_location_scoping():
    can_view_record = Permission.objects.filter(codename="view_verbalautopsy").first()
    group = GroupFactory.create(permissions=[can_view_record])

    province = LocationFactory.create()
    allowed = province.add_child(name="Allowed", location_type="district")
    blocked = province.add_child(name="Blocked", location_type="district")
    allowed_facility = allowed.add_child(name="AF", location_type="facility")
    blocked_facility = blocked.add_child(name="BF", location_type="facility")

    user = UserFactory.create(groups=[group], location_restrictions=[allowed])
    client = Client()
    client.force_login(user=user)

    in_scope = VASAFactory.create(location=allowed_facility)
    out_scope = VASAFactory.create(location=blocked_facility)

    assert client.get(f"/va_data_management/vasa/{in_scope.id}").status_code == 200
    assert client.get(f"/va_data_management/vasa/{out_scope.id}").status_code == 404
