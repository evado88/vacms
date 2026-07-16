"""VASA rows in the VA line list + the VASA detail page (SA/repeat/DQ tabs)."""

import re

import pandas as pd
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
    VASACareSeekingAdult,
    VASACareSeekingChild,
    VASACareSeekingNeonate,
    VASASocialAutopsy,
)
from va_explorer.va_data_management.utils.vasa_loading import (
    load_vasa_from_dataframes,
)
from va_explorer.va_data_management.vasa.model_fields import (
    SA_FIELD_DEFS,
    SA_VALUE_LABELS,
)

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
    assert "Care-Seeking" in content
    assert "Care-Seeking (1)" not in content
    assert "Care-Seeking (Neonate)" not in content
    assert "Care-Seeking (Child)" not in content
    assert "Care-Seeking (Adult)" not in content
    assert "Data Quality" in content
    assert "An error occurred" in content


def test_vasa_detail_only_renders_care_seeking_fields_for_instrument():
    _user, client = _viewer()
    vasa = VASAFactory.create(instrument="neonate")
    VASACareSeekingNeonate.objects.create(
        vasa=vasa, key="neonate-row", N2213_2="NEONATE_PROVIDER"
    )
    # These rows should not leak into the single tab when the record is a
    # neonate, even if malformed/imported data has populated another repeat.
    VASACareSeekingChild.objects.create(
        vasa=vasa, key="child-row", C3253_2="CHILD_PROVIDER"
    )
    VASACareSeekingAdult.objects.create(
        vasa=vasa, key="adult-row", A4252_2="ADULT_PROVIDER"
    )

    response = client.get(f"/va_data_management/vasa/{vasa.id}")
    assert response.status_code == 200
    content = response.content.decode()

    assert "NEONATE_PROVIDER" in content
    assert "CHILD_PROVIDER" not in content
    assert "ADULT_PROVIDER" not in content


def _field_row(content, field_name):
    """The rendered detail-table row for one field (WHO and SA tabs share the
    ``<tr ...><td><b>NAME</b></td>...<td>VALUE</td></tr>`` shape)."""
    match = re.search(
        rf"<tr[^>]*>\s*<td><b>{field_name}</b>.*?</tr>", content, flags=re.DOTALL
    )
    assert match is not None, f"no rendered row for {field_name}"
    return match.group(0)


def test_vasa_detail_shows_decoded_values_not_raw_codes():
    _user, client = _viewer()
    vasa = VASAFactory.create(instanceid="uuid:decoded-detail")
    # Ingest coded answers through the real loader: Q1601=2 -> Id10019=female,
    # A4006=1 -> Id10064=yes.
    load_vasa_from_dataframes(
        pd.DataFrame(
            [
                {
                    "instanceID": vasa.instanceid,
                    "KEY": vasa.instanceid,
                    "Q1601": "2",
                    "A4006": "1",
                }
            ]
        )
    )

    response = client.get(f"/va_data_management/vasa/{vasa.id}")
    assert response.status_code == 200
    content = response.content.decode()

    sex_row = _field_row(content, "Id10019")
    assert "female" in sex_row
    assert re.search(r"[>\"]\s*2\s*[<\"]", sex_row) is None  # no bare raw code
    yes_row = _field_row(content, "Id10064")
    assert "yes" in yes_row
    assert re.search(r"[>\"]\s*1\s*[<\"]", yes_row) is None


def test_vasa_detail_decodes_sa_select_codes_to_labels():
    _user, client = _viewer()
    vasa = VASAFactory.create(instrument="adult")
    # Q1203 is a select-one SA question stored as a raw code.
    label = SA_VALUE_LABELS["Q1203"]["labels"]["1"]
    VASASocialAutopsy.objects.create(vasa=vasa, Q1203="1")

    response = client.get(f"/va_data_management/vasa/{vasa.id}")
    assert response.status_code == 200
    content = response.content.decode()

    row = _field_row(content, "Q1203")
    assert label in row
    assert re.search(r">\s*1\s*<", row) is None  # code not shown


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
