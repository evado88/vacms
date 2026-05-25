from io import StringIO
from pathlib import Path

import pandas
import pytest
from django.core.management import call_command

from va_explorer.tests.factories import VerbalAutopsyFactory
from va_explorer.va_data_management.models import (
    CauseCodingIssue,
    Location,
    SRSClusterLocation,
    VerbalAutopsy,
)
from va_explorer.va_data_management.utils.loading import (
    get_va_summary_stats,
    load_records_from_dataframe,
)

pytestmark = pytest.mark.django_db


def test_loading_from_dataframe():
    # Location gets assigned w/ field hospital by name or by user's default location
    loc = Location.add_root(
        name="Test Location", key="test_location", location_type="facility"
    )

    data = [
        {
            "instanceid": "instance1",
            "Id10017": "name",
            "Id10018": "1",
            "Id10012": "2021-03-21",
            "instancename": "_Dec---name 1---2021-03-21",
            "testing-dashes-Id10007": "name 1",
            "Id10023": "03/01/2021",
            "hospital": "test_location",
        },
        {
            "instanceid": "instance2",
            "Id10017": "name",
            "Id10018": "2",
            "Id10012": "2021-03-22",
            "instancename": "_Dec---name 2---2021-03-22",
            "testing-dashes-Id10007": "name 2",
            "Id10023": "dk",
            "hospital": "test_location",
        },
    ]

    df = pandas.DataFrame.from_records(data)

    result = load_records_from_dataframe(df)

    assert len(result["created"]) == 2
    assert len(result["ignored"]) == 0

    assert result["created"][0].instanceid == data[0]["instanceid"]
    assert result["created"][0].Id10007 == data[0]["testing-dashes-Id10007"]
    assert result["created"][0].location == loc

    assert result["created"][1].instanceid == data[1]["instanceid"]
    assert result["created"][1].Id10007 == data[1]["testing-dashes-Id10007"]
    assert result["created"][1].location == loc


def test_loading_from_dataframe_with_community_va_field():
    Location.add_root(
        name="Test Location", key="test_location", location_type="facility"
    )

    data = [
        {
            "instanceid": "instance-community-1",
            "Id10017": "name",
            "Id10018": "1",
            "Id10012": "2021-03-21",
            "instancename": "_Dec---name 1---2021-03-21",
            "Id10023": "03/01/2021",
            "hospital": "test_location",
            "community_va": "yes",
        }
    ]
    df = pandas.DataFrame.from_records(data)

    result = load_records_from_dataframe(df)
    created = result["created"][0]

    assert len(result["created"]) == 1
    assert created.community_va == "yes"
    assert (
        VerbalAutopsy.objects.get(instanceid="instance-community-1").community_va
        == "yes"
    )


def test_loading_from_dataframe_ward_sets_community_va_yes():
    Location.add_root(
        name="Test Location", key="test_location", location_type="facility"
    )

    data = [
        {
            "instanceid": "instance-community-ward-1",
            "Id10017": "name",
            "Id10018": "1",
            "Id10012": "2021-03-21",
            "instancename": "_Dec---name 1---2021-03-21",
            "Id10023": "03/01/2021",
            "ward": "ward_1",
        }
    ]
    df = pandas.DataFrame.from_records(data)

    result = load_records_from_dataframe(df)
    created = result["created"][0]

    assert len(result["created"]) == 1
    assert created.community_va == "yes"
    assert (
        VerbalAutopsy.objects.get(instanceid="instance-community-ward-1").community_va
        == "yes"
    )


def test_loading_from_dataframe_sets_cluster_for_community_va():
    Location.add_root(
        name="Test Location", key="test_location", location_type="facility"
    )
    ward = SRSClusterLocation.add_root(
        name="Ward 10", location_type="ward", code="W10", status="Active"
    )

    data = [
        {
            "instanceid": "instance-community-cluster-1",
            "Id10017": "name",
            "Id10018": "1",
            "Id10012": "2021-03-21",
            "instancename": "_Dec---name 1---2021-03-21",
            "Id10023": "03/01/2021",
            "ward": "Ward 10",
        }
    ]
    df = pandas.DataFrame.from_records(data)

    result = load_records_from_dataframe(df)
    assert len(result["created"]) == 1
    created = VerbalAutopsy.objects.get(instanceid="instance-community-cluster-1")
    assert created.community_va == "yes"
    assert created.cluster_id == ward.id


def test_loading_from_dataframe_odk_community_true_and_ea_maps_cluster():
    ea = SRSClusterLocation.add_root(
        name="EA 2001", location_type="ea", code="EA2001", status="Active"
    )

    data = [
        {
            "instanceid": "instance-community-odk-ea-1",
            "Id10017": "name",
            "Id10018": "1",
            "Id10012": "2021-03-21",
            "instancename": "_Dec---name 1---2021-03-21",
            "Id10023": "03/01/2021",
            "community_va": "true",
            "ea": "EA 2001",
        }
    ]
    df = pandas.DataFrame.from_records(data)

    result = load_records_from_dataframe(df)
    assert len(result["created"]) == 1
    created = VerbalAutopsy.objects.get(instanceid="instance-community-odk-ea-1")
    assert created.community_va == "yes"
    assert created.cluster_id == ea.id


def test_loading_from_dataframe_area_sets_community_va_yes_and_cluster():
    Location.add_root(
        name="Test Location", key="test_location", location_type="facility"
    )
    ward = SRSClusterLocation.add_root(
        name="Area 51", location_type="ward", code="A51", status="Active"
    )

    data = [
        {
            "instanceid": "instance-community-area-1",
            "Id10017": "name",
            "Id10018": "1",
            "Id10012": "2021-03-21",
            "instancename": "_Dec---name 1---2021-03-21",
            "Id10023": "03/01/2021",
            "area": "Area 51",
        }
    ]
    df = pandas.DataFrame.from_records(data)

    result = load_records_from_dataframe(df)
    assert len(result["created"]) == 1
    created = VerbalAutopsy.objects.get(instanceid="instance-community-area-1")
    assert created.community_va == "yes"
    assert created.cluster_id == ward.id


def test_loading_from_dataframe_infers_community_from_markers_and_maps_cluster():
    province = SRSClusterLocation.add_root(
        name="Southern", location_type="province", code="P2", status="Active"
    )
    district = province.add_child(
        name="District 1", location_type="district", code="D1", status="Active"
    )
    ward = district.add_child(
        name="Ward 10", location_type="ward", code="W10", status="Active"
    )
    ea = ward.add_child(
        name="EA 2001", location_type="ea", code="EA2001", status="Active"
    )

    data = [
        {
            "instanceid": "instance-community-inferred-1",
            "Id10017": "name",
            "Id10018": "1",
            "Id10012": "2021-03-21",
            "instancename": "_Dec---name 1---2021-03-21",
            "Id10023": "03/01/2021",
            # community_va intentionally omitted
            "district": "District 1",
            "ward": "Ward 10",
            "ea": "EA 2001",
        }
    ]
    df = pandas.DataFrame.from_records(data)

    result = load_records_from_dataframe(df)
    assert len(result["created"]) == 1
    created = VerbalAutopsy.objects.get(instanceid="instance-community-inferred-1")
    assert created.community_va == "yes"
    assert created.cluster_id == ea.id


def test_loading_from_dataframe_uses_direct_cluster_code_for_community_va():
    ea = SRSClusterLocation.add_root(
        name="EA Name 1", location_type="ea", code="CL-001", status="Active"
    )

    data = [
        {
            "instanceid": "instance-community-direct-cluster-code-1",
            "Id10017": "name",
            "Id10018": "1",
            "Id10012": "2021-03-21",
            "instancename": "_Dec---name 1---2021-03-21",
            "Id10023": "03/01/2021",
            "community_va": "yes",
            "cluster_code": "CL-001",
        }
    ]
    df = pandas.DataFrame.from_records(data)

    result = load_records_from_dataframe(df)
    assert len(result["created"]) == 1
    created = VerbalAutopsy.objects.get(
        instanceid="instance-community-direct-cluster-code-1"
    )
    assert created.community_va == "yes"
    assert created.cluster_id == ea.id


def test_loading_from_dataframe_disambiguates_ea_using_district():
    province = SRSClusterLocation.add_root(
        name="Lusaka", location_type="province", code="P01", status="Active"
    )
    district_a = province.add_child(
        name="District A", location_type="district", code="D-A", status="Active"
    )
    district_b = province.add_child(
        name="District B", location_type="district", code="D-B", status="Active"
    )
    ea_a = district_a.add_child(
        name="EA Shared", location_type="ea", code="EA-100", status="Active"
    )
    district_b.add_child(
        name="EA Shared", location_type="ea", code="EA-200", status="Active"
    )

    data = [
        {
            "instanceid": "instance-community-ea-disambiguated-1",
            "Id10017": "name",
            "Id10018": "1",
            "Id10012": "2021-03-21",
            "instancename": "_Dec---name 1---2021-03-21",
            "Id10023": "03/01/2021",
            "community_va": "yes",
            "ea": "EA Shared",
            "district": "District A",
        }
    ]
    df = pandas.DataFrame.from_records(data)

    result = load_records_from_dataframe(df)
    assert len(result["created"]) == 1
    created = VerbalAutopsy.objects.get(
        instanceid="instance-community-ea-disambiguated-1"
    )
    assert created.community_va == "yes"
    assert created.cluster_id == ea_a.id


def test_loading_from_dataframe_community_admin_marker_without_cluster_is_allowed():
    province = SRSClusterLocation.add_root(
        name="Lusaka", location_type="province", code="P01", status="Active"
    )
    district = province.add_child(
        name="District A", location_type="district", code="D-A", status="Active"
    )
    district.add_child(
        name="EA Shared", location_type="ea", code="EA-100", status="Active"
    )
    district.add_child(
        name="EA Shared", location_type="ea", code="EA-200", status="Active"
    )

    data = [
        {
            "instanceid": "instance-community-unmatched-cluster-1",
            "Id10017": "name",
            "Id10018": "1",
            "Id10012": "2021-03-21",
            "instancename": "_Dec---name 1---2021-03-21",
            "Id10023": "03/01/2021",
            "community_va": "yes",
            "ea": "EA Shared",
        }
    ]
    df = pandas.DataFrame.from_records(data)

    result = load_records_from_dataframe(df)
    assert len(result["created"]) == 1
    created = VerbalAutopsy.objects.get(
        instanceid="instance-community-unmatched-cluster-1"
    )
    assert created.cluster is None
    assert not CauseCodingIssue.objects.filter(
        verbalautopsy_id=created.id,
        text="ERROR: unmatched EA/cluster for community VA",
        severity="error",
    ).exists()


def test_loading_from_dataframe_community_without_cluster_or_admin_markers_errors():
    data = [
        {
            "instanceid": "instance-community-no-geo-1",
            "Id10017": "name",
            "Id10018": "1",
            "Id10012": "2021-03-21",
            "instancename": "_Dec---name 1---2021-03-21",
            "Id10023": "03/01/2021",
            "community_va": "yes",
        }
    ]
    df = pandas.DataFrame.from_records(data)

    result = load_records_from_dataframe(df)
    assert len(result["created"]) == 1
    created = VerbalAutopsy.objects.get(instanceid="instance-community-no-geo-1")
    assert created.cluster is None
    assert CauseCodingIssue.objects.filter(
        verbalautopsy_id=created.id,
        text="ERROR: unmatched EA/cluster for community VA",
        severity="error",
    ).exists()


def test_loading_from_dataframe_missing_markers_leaves_community_va_unresolved():
    Location.add_root(
        name="Test Location", key="test_location", location_type="facility"
    )

    data = [
        {
            "instanceid": "instance-community-default-1",
            "Id10017": "name",
            "Id10018": "1",
            "Id10012": "2021-03-21",
            "instancename": "_Dec---name 1---2021-03-21",
            "Id10023": "03/01/2021",
        }
    ]
    df = pandas.DataFrame.from_records(data)

    result = load_records_from_dataframe(df)
    created = result["created"][0]

    assert len(result["created"]) == 1
    assert created.community_va is None
    assert (
        VerbalAutopsy.objects.get(
            instanceid="instance-community-default-1"
        ).community_va
        is None
    )


def test_loading_from_dataframe_area_and_hospital_sets_facility_va():
    loc = Location.add_root(
        name="Test Location", key="test_location", location_type="facility"
    )

    data = [
        {
            "instanceid": "instance-facility-area-hospital-1",
            "Id10017": "name",
            "Id10018": "1",
            "Id10012": "2021-03-21",
            "instancename": "_Dec---name 1---2021-03-21",
            "Id10023": "03/01/2021",
            "area": "Kanyama",
            "hospital": "test_location",
        }
    ]
    df = pandas.DataFrame.from_records(data)

    result = load_records_from_dataframe(df)
    created = result["created"][0]
    assert created.community_va == "no"
    assert created.location_id == loc.id


def test_loading_from_dataframe_with_ignored():
    # Location gets assigned automatically/randomly.
    # If that changes in loading.py we'll need to change that here too.
    Location.add_root(
        name="Test Location", key="test_location", location_type="facility"
    )

    data = [
        {
            "instanceid": "instance1",
            "Id10017": "name",
            "Id10018": "1",
            "Id10012": "2021-03-21",
            "testing-dashes-Id10007": "name 1",
            "instancename": "_Dec---name 1---2021-03-21",
        },
        {
            "instanceid": "instance2",
            "Id10017": "name",
            "Id10018": "2",
            "Id10012": "2021-03-22",
            "testing-dashes-Id10007": "name 2",
            "instancename": "_Dec---name 2---2021-03-22",
        },
    ]

    df = pandas.DataFrame.from_records(data)

    result = load_records_from_dataframe(df)

    assert len(result["created"]) == 2
    assert len(result["ignored"]) == 0
    assert result["created"][0].instanceid == data[0]["instanceid"]
    assert result["created"][1].instanceid == data[1]["instanceid"]

    # Run it again and it should ignore one of these records.

    data = [
        {
            "instanceid": "instance1",
            "Id10017": "name",
            "Id10018": "1",
            "Id10012": "2021-03-21",
            "testing-dashes-Id10007": "name 1",
            "instancename": "_Dec---name 1---2021-03-21",
        },
        {
            "instanceid": "instance4",
            "Id10017": "name",
            "Id10018": "4",
            "Id10012": "2021-03-24",
            "testing-dashes-Id10007": "name 4",
            "instancename": "_Dec---name 4---2021-03-24",
        },
    ]

    df = pandas.DataFrame.from_records(data)

    result = load_records_from_dataframe(df)

    assert len(result["created"]) == 1
    assert len(result["ignored"]) == 1
    assert result["ignored"][0].instanceid == data[0]["instanceid"]
    assert result["created"][0].instanceid == data[1]["instanceid"]


def test_loading_from_dataframe_with_key():
    # Location gets assigned automatically/randomly if hospital is not a facility
    # If that changes in loading.py it needs to change here too
    loc = Location.add_root(
        name="Test Location", key="test_location", location_type="facility"
    )

    data = [
        {
            "key": "instance1",
            "Id10017": "name",
            "Id10018": "1",
            "Id10012": "2021-03-21",
            "instancename": "_Dec---name 1---2021-03-21",
            "testing-dashes-Id10007": "name 1",
            "hospital": "test_location",
        },
        {
            "key": "instance2",
            "Id10017": "name",
            "Id10018": "2",
            "Id10012": "2021-03-22",
            "testing-dashes-Id10007": "name 2",
            "instancename": "_Dec---name 2---2021-03-22",
            "hospital": "home",
        },
    ]

    df = pandas.DataFrame.from_records(data)

    result = load_records_from_dataframe(df)

    assert len(result["created"]) == 2
    assert len(result["ignored"]) == 0

    assert result["created"][0].instanceid == data[0]["key"]
    assert result["created"][0].Id10007 == data[0]["testing-dashes-Id10007"]
    assert result["created"][0].location == loc

    assert result["created"][1].instanceid == data[1]["key"]
    assert result["created"][1].Id10007 == data[1]["testing-dashes-Id10007"]
    assert result["created"][1].location.name == "Unknown"


def test_load_va_csv_command(tmp_path):
    # Location gets assigned automatically/randomly if hospital is not a facility
    # If that changes in loading.py it needs to change here too
    Location.add_root(
        name="Test Location", key="test_location", location_type="facility"
    )

    test_data = tmp_path / "test-input-data.csv"
    test_data.write_text(
        "\n".join(
            [
                "instanceid,Id10017,Id10018,Id10012,instancename,testing-dashes-Id10007,hospital",
                "instance1,name,1,2021-03-21,_Dec---name 1---2021-03-21,name1,test_location",
                "instance2,name,2,2021-03-22,_Dec---name 2---2021-03-22,name2,test_location",
                "instance3,name,3,2021-03-23,_Dec---name 3---2021-03-23,name3,test_location",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert VerbalAutopsy.objects.count() == 0

    output = StringIO()
    call_command(
        "load_va_csv",
        str(test_data.absolute()),
        stdout=output,
        stderr=output,
    )

    output_text = output.getvalue().strip()
    assert (
        "Loaded 3 verbal autopsies from CSV "
        "(0 ignored, 0 enriched, 0 removed as outdated)" in output_text
    )
    assert "Created mix:" in output_text
    assert VerbalAutopsy.objects.get(instanceid="instance1").Id10007 == "name1"
    assert VerbalAutopsy.objects.get(instanceid="instance2").Id10007 == "name2"
    assert VerbalAutopsy.objects.get(instanceid="instance3").Id10007 == "name3"


def test_update_va_locations_command_updates_by_instanceid(tmp_path):
    Location.add_root(
        name="Test Facility", key="test_facility", location_type="facility"
    )
    ward = SRSClusterLocation.add_root(
        name="Ward 22", location_type="ward", code="W22", status="Active"
    )

    va = VerbalAutopsyFactory.create(
        instanceid="loc-instance-1",
        hospital="test_facility",
        community_va="no",
    )
    assert va.community_va == "no"

    csv_file = tmp_path / "va_location_updates.csv"
    csv_file.write_text(
        "instanceid,province,district,constituency,ward,ea,hospital\n"
        "loc-instance-1,Lusaka,Lusaka Central,Some Constituency,Ward 22,EA-001,\n",
        encoding="utf-8",
    )

    output = StringIO()
    call_command(
        "update_va_locations",
        str(csv_file),
        stdout=output,
        stderr=output,
    )

    va.refresh_from_db()
    assert va.province == "Lusaka"
    assert va.district == "Lusaka Central"
    assert va.constituency == "Some Constituency"
    assert va.ward == "Ward 22"
    assert va.ea == "EA-001"
    assert va.community_va == "yes"
    assert va.cluster_id == ward.id


def test_get_va_summary_stats_uses_cluster_for_community_eligibility():
    facility = Location.add_root(
        name="Facility 1", key="facility_1", location_type="facility"
    )
    ward = SRSClusterLocation.add_root(
        name="Ward 88", location_type="ward", code="W88", status="Active"
    )

    VerbalAutopsyFactory.create(
        instanceid="summary-facility-1",
        location=facility,
        cluster=None,
        community_va="no",
        Id10023="2021-03-21",
    )
    VerbalAutopsyFactory.create(
        instanceid="summary-community-1",
        location=None,
        cluster=ward,
        community_va="yes",
        Id10023="2021-03-21",
    )
    VerbalAutopsyFactory.create(
        instanceid="summary-community-missing-cluster-1",
        location=None,
        cluster=None,
        community_va="yes",
        Id10023="2021-03-21",
    )

    stats = get_va_summary_stats(VerbalAutopsy.objects.all(), cache_key=None)
    assert stats["total_vas"] == 3
    assert stats["ineligible_vas"] == 1


def test_loading_duplicate_vas(settings):
    settings.QUESTIONS_TO_AUTODETECT_DUPLICATES = (
        "Id10017, Id10018, Id10012, Id10019, Id10020, Id10021, Id10022, Id10023"
    )

    # va1 matches 2 records in 'test-duplicate-input-data.csv'
    # VA will not be marked as duplicate = True because it was created before loading
    # 'test-duplicate-input-data.csv'
    va1 = VerbalAutopsyFactory.create(
        Id10017="Bob",
        Id10018="Jones",
        Id10012="2021-03-22",
        Id10019="Male",
        Id10020="Yes",
        Id10021="dk",
        Id10022="Yes",
        Id10023="dk",
        instanceid="00",
        instancename="_Dec---Bob_Jones---2021-03-22",
    )

    # va2 matches 0 records in 'test-duplicate-input-data.csv'
    va2 = VerbalAutopsyFactory.create(
        Id10017="Nate",
        Id10018="Grey",
        Id10012="2012-03-22",
        Id10019="Male",
        Id10020="Yes",
        Id10021="dk",
        Id10022="Yes",
        Id10023="dk",
        instanceid="02",
        instancename="_Dec---Nate_Grey---2021-03-22",
    )

    # Find path to data file
    test_data = Path(__file__).parent / "test-duplicate-input-data.csv"

    output = StringIO()
    call_command(
        "load_va_csv",
        str(test_data.absolute()),
        stdout=output,
        stderr=output,
    )

    va1.refresh_from_db()
    va2.refresh_from_db()

    assert not va1.duplicate
    assert not va2.duplicate

    # Query for the VAs that match va1
    vas_duplicate_with_va1 = list(
        VerbalAutopsy.objects.filter(
            unique_va_identifier=va1.unique_va_identifier
        ).order_by("created")
    )

    assert len(vas_duplicate_with_va1) == 3

    # Assert that the oldest VA by created timestamp is not duplicate
    assert not vas_duplicate_with_va1.pop(0).duplicate

    # Assert that the rest are duplicate
    for va in vas_duplicate_with_va1:
        assert va.duplicate

    assert (
        VerbalAutopsy.objects.filter(
            unique_va_identifier=va2.unique_va_identifier
        ).count()
        == 1
    )


# TODO add tests for date of death, location, and age_group
