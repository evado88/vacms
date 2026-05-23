import pandas as pd
import pytest
from django.core.management import call_command

from va_explorer.va_data_management.models import SocialMobilization
from va_explorer.va_data_management.odk.service import import_dataframe_records

pytestmark = pytest.mark.django_db

# Header + one row mirroring the ODK Central export (SOCIAL_MOBILIZATION_SRS_TALLY.csv)
SAMPLE_HEADER = (
    "SubmissionDate,deviceid,today,start,end,province,district,health_facility,"
    "village_neighbourhood,day_number,households_visited,people_over_15,children_5_14,"
    "children_12_59months,refusal_identified,refusal_managed,schools_reached,"
    "religious_leaders_reached,rumor_description,rumor_source,rumor_source_other,"
    "children_affected,refusals_from_rumor,instanceID,instanceName,KEY,SubmitterID,"
    "SubmitterName,AttachmentsPresent,AttachmentsExpected,Status,ReviewState,DeviceID,"
    "Edits,FormVersion"
)
SAMPLE_ROW = (
    "2026-05-23T12:23:57.494Z,collect:MoO7bLEw6rs4OhmA,23/05/2026,"
    "2026-05-23T13:07:55.660+02:00,2026-05-23T14:23:56.926+02:00,copperbelt,mpongwe,"
    "mpongwe_lyobeka_health_post,David village,1,2,10,7,3,2,1,5,4,,,,,,"
    "uuid:f2733da5-626c-472a-b861-d087ee4fccd2,SM_2026-05-23_mpongwe,"
    "uuid:f2733da5-626c-472a-b861-d087ee4fccd2,30,App test,0,0,,,"
    "collect:MoO7bLEw6rs4OhmA,0,1.1.0"
)


def _write_csv(tmp_path, row=SAMPLE_ROW):
    csv_path = tmp_path / "SOCIAL_MOBILIZATION.csv"
    csv_path.write_text(f"{SAMPLE_HEADER}\n{row}\n")
    return str(csv_path)


def test_load_command_creates_record(tmp_path):
    call_command("load_social_mobilization", _write_csv(tmp_path))

    assert SocialMobilization.objects.count() == 1
    record = SocialMobilization.objects.get(
        key="uuid:f2733da5-626c-472a-b861-d087ee4fccd2"
    )
    assert record.province == "copperbelt"
    assert record.health_facility == "mpongwe_lyobeka_health_post"
    assert record.village_neighbourhood == "David village"
    assert record.households_visited == "2"
    assert record.instanceid == "uuid:f2733da5-626c-472a-b861-d087ee4fccd2"
    assert record.formversion == "1.1.0"


def test_load_command_upserts_by_key(tmp_path):
    call_command("load_social_mobilization", _write_csv(tmp_path))

    updated_row = SAMPLE_ROW.replace("David village", "Updated village")
    call_command("load_social_mobilization", _write_csv(tmp_path, row=updated_row))

    # Same key -> no duplicate row, fields updated in place.
    assert SocialMobilization.objects.count() == 1
    record = SocialMobilization.objects.get(
        key="uuid:f2733da5-626c-472a-b861-d087ee4fccd2"
    )
    assert record.village_neighbourhood == "Updated village"


def test_import_dataframe_records_routes_to_social_mobilization():
    df = pd.DataFrame(
        [
            {"KEY": "sm-1", "today": "2026-01-01", "households_visited": "4"},
        ]
    )

    counts = import_dataframe_records("social_mobilization", df)

    assert counts == {"created": 1, "updated": 0, "ignored": 0, "duplicate_rows": 0}
    created = SocialMobilization.objects.get(key="sm-1")
    assert created.today == "2026-01-01"
    assert created.households_visited == "4"
