import pandas as pd
import pytest
from django.core.management import call_command

from va_explorer.va_data_management.models import SocialMobilization
from va_explorer.va_data_management.odk.service import import_dataframe_records

pytestmark = pytest.mark.django_db

# Header + one row mirroring the updated ODK Central export (SRS Social Mobilization form v1.1.0)
SAMPLE_HEADER = (
    "SubmissionDate,deviceid,today,start,end,event_date,province,district,constituency,"
    "ward,ea,health_facility,village_neighbourhood,event_gps-Latitude,event_gps-Longitude,"
    "event_gps-Altitude,event_gps-Accuracy,mobilizer_name,mobilizer_role,event_type,"
    "event_description,household_identifier,refusal_reluctance_identified,day_number,"
    "people_over_15,children_5_14,children_12_59months,rumor_description,rumor_source,"
    "community_group_name,person_name,offline_channel_name,online_page_group_name,"
    "refusal_reluctance_managed,instanceID,instanceName,KEY,SubmitterID,SubmitterName,"
    "AttachmentsPresent,AttachmentsExpected,Status,ReviewState,DeviceID,Edits,FormVersion"
)
SAMPLE_ROW = (
    "2026-06-16T19:56:14.813Z,collect:MoO7bLEw6rs4OhmA,16/06/2026,"
    "2026-06-16T11:04:03.969+02:00,2026-06-16T21:56:01.793+02:00,16/06/2026,"
    "copperbelt,ndola,2103,210307,2103072018,nkhwazi_urban_hcentre,Yengwe,"
    "-12.9439033,28.641365,1319.8,5,Nathan mwandia,csa,household,Census,Yengwe,"
    "no,2,4,0,1,,,,,,,no,"
    "uuid:77279d66-8069-47c1-b6c4-f31c3cab1c93,"
    "SM_copperbelt_ndola_Nathan mwandia_2026-06-16,"
    "uuid:77279d66-8069-47c1-b6c4-f31c3cab1c93,"
    "7,SRS,0,0,,,collect:MoO7bLEw6rs4OhmA,0,1.1.0"
)


def _write_csv(tmp_path, row=SAMPLE_ROW):
    csv_path = tmp_path / "SOCIAL_MOBILIZATION.csv"
    csv_path.write_text(f"{SAMPLE_HEADER}\n{row}\n")
    return str(csv_path)


def test_load_command_creates_record(tmp_path):
    call_command("load_social_mobilization", _write_csv(tmp_path))

    assert SocialMobilization.objects.count() == 1
    record = SocialMobilization.objects.get(
        key="uuid:77279d66-8069-47c1-b6c4-f31c3cab1c93"
    )
    assert record.province == "copperbelt"
    assert record.district == "ndola"
    assert record.health_facility == "nkhwazi_urban_hcentre"
    assert record.village_neighbourhood == "Yengwe"
    assert record.constituency == "2103"
    assert record.ward == "210307"
    assert record.ea == "2103072018"
    assert record.mobilizer_name == "Nathan mwandia"
    assert record.mobilizer_role == "csa"
    assert record.event_type == "household"
    assert record.event_date == "16/06/2026"
    assert record.people_over_15 == "4"
    assert record.children_5_14 == "0"
    assert record.children_12_59months == "1"
    assert record.refusal_reluctance_identified == "no"
    assert record.refusal_reluctance_managed == "no"
    assert record.instanceid == "uuid:77279d66-8069-47c1-b6c4-f31c3cab1c93"
    assert record.formversion == "1.1.0"


def test_load_command_upserts_by_key(tmp_path):
    call_command("load_social_mobilization", _write_csv(tmp_path))

    updated_row = SAMPLE_ROW.replace("Yengwe,", "Kwacha,", 1)
    call_command("load_social_mobilization", _write_csv(tmp_path, row=updated_row))

    # Same key -> no duplicate row, fields updated in place.
    assert SocialMobilization.objects.count() == 1
    record = SocialMobilization.objects.get(
        key="uuid:77279d66-8069-47c1-b6c4-f31c3cab1c93"
    )
    assert record.village_neighbourhood == "Kwacha"


def test_gps_columns_loaded(tmp_path):
    call_command("load_social_mobilization", _write_csv(tmp_path))

    record = SocialMobilization.objects.get(
        key="uuid:77279d66-8069-47c1-b6c4-f31c3cab1c93"
    )
    assert record.event_gps_latitude == "-12.9439033"
    assert record.event_gps_longitude == "28.641365"
    assert record.event_gps_altitude == "1319.8"
    assert record.event_gps_accuracy == "5"


def test_import_dataframe_records_routes_to_social_mobilization():
    df = pd.DataFrame(
        [
            {
                "KEY": "sm-1",
                "today": "2026-06-16",
                "people_over_15": "4",
                "refusal_reluctance_identified": "no",
                "event_type": "household",
            },
        ]
    )

    counts = import_dataframe_records("social_mobilization", df)

    assert counts == {"created": 1, "updated": 0, "ignored": 0, "duplicate_rows": 0}
    created = SocialMobilization.objects.get(key="sm-1")
    assert created.today == "2026-06-16"
    assert created.people_over_15 == "4"
    assert created.refusal_reluctance_identified == "no"
    assert created.event_type == "household"
