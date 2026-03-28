import io
import json
import zipfile

import pytest
from django.contrib.auth.models import Permission
from django.test import Client, RequestFactory

from va_explorer.tests.factories import (
    GroupFactory,
    LocationFactory,
    UserFactory,
    VerbalAutopsyFactory,
)
from va_explorer.users.models import User
from va_explorer.va_data_management.constants import REDACTED_STRING
from va_explorer.va_data_management.models import (
    CauseOfDeath,
    Death,
    Household,
    Location,
    Pregnancy,
    PregnancyOutcome,
    SRSClusterLocation,
    VerbalAutopsy,
)
from va_explorer.va_export.forms import VADownloadForm

pytestmark = pytest.mark.django_db

CSV_ZIP_FILE_NAME = "export.csv.zip"
JSON_ZIP_FILE_NAME = "export.json.zip"
CSV_FILE_NAME = "va_download.csv"
JSON_FILE_NAME = "va_download.json"
FILE_CONTENT_TYPE = "application/zip"
POST_URL = "/va_export/verbalautopsy/"


def build_test_db():
    # Build locations
    province = LocationFactory.create()
    district1 = province.add_child(name="District1", location_type="district")
    facility_a = district1.add_child(
        name="Facility1",
        location_type="facility",
        key="facility_1",
        path_string=f"{province.name} Province/District1 District/Facility1",
    )
    district2 = province.add_child(name="District2", location_type="district")
    facility_b = district2.add_child(
        name="Facility1",
        location_type="facility",
        key="facility_1",
        path_string=f"{province.name} Province/District2 District/Facility1",
    )
    facility_c = district2.add_child(
        name="Facility2",
        location_type="facility",
        key="facility_2",
        path_string=f"{province.name} Province/District2 District/Facility2",
    )
    district2.add_child(
        name="Empty Facility",
        location_type="facility",
        key="empty",
        path_string=f"{province.name} Province/District2 District/Empty Facility",
    )

    srs_province = SRSClusterLocation.add_root(
        name=province.name, location_type="province"
    )
    srs_district1 = srs_province.add_child(name="District1", location_type="district")
    srs_district2 = srs_province.add_child(name="District2", location_type="district")
    srs_facility_a = srs_district1.add_child(name="Facility1", location_type="facility")
    srs_facility_b = srs_district2.add_child(name="Facility1", location_type="facility")
    srs_facility_c = srs_district2.add_child(name="Facility2", location_type="facility")
    srs_empty = srs_district2.add_child(name="Empty Facility", location_type="facility")

    # create VAs
    va1 = VerbalAutopsyFactory.create(location=facility_a, Id10023="2019-01-01")
    va2 = VerbalAutopsyFactory.create(location=facility_b, Id10023="2019-01-03")
    va3 = VerbalAutopsyFactory.create(location=facility_c, Id10023="2019-01-09")
    va4 = VerbalAutopsyFactory.create(location=facility_a, Id10023="2020-04-01")

    # Create CODs and assign to VAs
    CauseOfDeath.objects.create(cause="cod_b", settings={}, verbalautopsy=va1)
    CauseOfDeath.objects.create(cause="cod_a", settings={}, verbalautopsy=va2)
    CauseOfDeath.objects.create(cause="cod_b", settings={}, verbalautopsy=va3)
    CauseOfDeath.objects.create(cause="cod_a", settings={}, verbalautopsy=va4)

    # Build admin that can download data without location or PII restrictions
    can_download_data = Permission.objects.filter(codename="download_data").first()
    can_view_pii = Permission.objects.filter(codename="view_pii").first()

    admin_group = GroupFactory.create(permissions=[can_download_data, can_view_pii])
    non_admin_group = GroupFactory.create(permissions=[can_download_data])
    UserFactory.create(name="admin", groups=[admin_group])

    # build a non-admin user that can download but can't view pii
    UserFactory.create(name="no_pii", groups=[non_admin_group])

    return {
        "province": province,
        "district1": district1,
        "facility_a": facility_a,
        "facility_b": facility_b,
        "facility_c": facility_c,
        "srs_province": srs_province,
        "srs_district1": srs_district1,
        "srs_district2": srs_district2,
        "srs_facility_a": srs_facility_a,
        "srs_facility_b": srs_facility_b,
        "srs_facility_c": srs_facility_c,
        "srs_empty": srs_empty,
    }


class TestAPIView:
    def test_household_csv_download(self, user: User):
        build_test_db()
        Household.objects.create(key="hh-export-1", today="2026-01-01", province="Central")

        c = Client()
        c.force_login(user=user)

        response = c.post(POST_URL, data={"dataset": "household", "format": "csv"})
        assert response.status_code == 200
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert response.headers["content-disposition"] == "attachment; filename=household.csv.zip"

        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")
            assert "household_download.csv" in zipped_file.namelist()
            assert len(zipped_file.open("household_download.csv").readlines()) >= 2
        finally:
            zipped_file.close()
            f.close()

    def test_pregnancy_csv_download(self, user: User):
        build_test_db()
        Pregnancy.objects.create(key="preg-export-1", today="2026-01-02", PE_06="Jane Doe")

        c = Client()
        c.force_login(user=user)

        response = c.post(POST_URL, data={"dataset": "pregnancy", "format": "csv"})
        assert response.status_code == 200
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert response.headers["content-disposition"] == "attachment; filename=pregnancy.csv.zip"

        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")
            assert "pregnancy_download.csv" in zipped_file.namelist()
            assert len(zipped_file.open("pregnancy_download.csv").readlines()) >= 2
        finally:
            zipped_file.close()
            f.close()

    def test_pregnancy_outcome_csv_download(self, user: User):
        build_test_db()
        PregnancyOutcome.objects.create(key="po-export-1", today="2026-01-03", PO_04="Mother")

        c = Client()
        c.force_login(user=user)

        response = c.post(POST_URL, data={"dataset": "pregnancy_outcome", "format": "csv"})
        assert response.status_code == 200
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert response.headers["content-disposition"] == "attachment; filename=pregnancy_outcome.csv.zip"

        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")
            assert "pregnancy_outcome_download.csv" in zipped_file.namelist()
            assert len(zipped_file.open("pregnancy_outcome_download.csv").readlines()) >= 2
        finally:
            zipped_file.close()
            f.close()

    def test_death_csv_download(self, user: User):
        build_test_db()
        Death.objects.create(key="death-export-1", today="2026-01-04", DE_03="Decedent")

        c = Client()
        c.force_login(user=user)

        response = c.post(POST_URL, data={"dataset": "death", "format": "csv"})
        assert response.status_code == 200
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert response.headers["content-disposition"] == "attachment; filename=death.csv.zip"

        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")
            assert "death_download.csv" in zipped_file.namelist()
            assert len(zipped_file.open("death_download.csv").readlines()) >= 2
        finally:
            zipped_file.close()
            f.close()

    def test_csv_download(self, user: User):
        build_test_db()

        c = Client()
        c.force_login(user=user)

        response = c.post(POST_URL, data={"format": "csv"})
        assert response.status_code == 200

        # Django 3.2 has response.headers. For now, we'll access them per below
        # See https://docs.djangoproject.com/en/4.0/ref/request-response/#django.http.HttpResponse.headers
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == f"attachment; filename={CSV_ZIP_FILE_NAME}"
        )

        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")
            # Add one for the variable name header in the csv
            assert len(zipped_file.open(CSV_FILE_NAME).readlines()) == 5
        finally:
            zipped_file.close()
            f.close()

    def test_json_download(self, user: User):
        build_test_db()

        c = Client()
        c.force_login(user=user)

        response = c.post(POST_URL, data={"format": "json"})
        assert response.status_code == 200

        # Django 3.2 has response.headers. For now, we'll access them per below
        # See https://docs.djangoproject.com/en/4.0/ref/request-response/#django.http.HttpResponse.headers
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == f"attachment; filename={JSON_ZIP_FILE_NAME}"
        )

        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")

            json_data = json.loads(zipped_file.read(JSON_FILE_NAME))
            assert json_data["count"] == 4
        finally:
            zipped_file.close()
            f.close()

    def test_download_csv_with_no_matching_vas(self, user: User):
        test_data = build_test_db()
        # only download data from "No VA Facility", which will have no matching VAs
        no_va_facility = Location.objects.get(name="Empty Facility")

        c = Client()
        c.force_login(user=user)

        response = c.post(
            POST_URL,
            data={"format": "csv", "locations": test_data["srs_empty"].pk},
        )
        assert response.status_code == 200

        # Django 3.2 has response.headers. For now, we'll access them per below
        # See https://docs.djangoproject.com/en/4.0/ref/request-response/#django.http.HttpResponse.headers
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == f"attachment; filename={CSV_ZIP_FILE_NAME}"
        )

        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")
            # The single line is the variable name header in the csv
            assert len(zipped_file.open(CSV_FILE_NAME).readlines()) == 1
        finally:
            zipped_file.close()
            f.close()

    def test_download_json_with_no_matching_vas(self, user: User):
        test_data = build_test_db()
        # only download data from "No VA Facility", which will have no matching VAs
        no_va_facility = Location.objects.get(name="Empty Facility")

        c = Client()
        c.force_login(user=user)

        response = c.post(
            POST_URL,
            data={"format": "json", "locations": test_data["srs_empty"].pk},
        )
        assert response.status_code == 200

        # Django 3.2 has response.headers. For now, we'll access them per below
        # See https://docs.djangoproject.com/en/4.0/ref/request-response/#django.http.HttpResponse.headers
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == f"attachment; filename={JSON_ZIP_FILE_NAME}"
        )

        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")

            json_data = json.loads(zipped_file.read(JSON_FILE_NAME))
            assert json_data["count"] == 0
        finally:
            zipped_file.close()
            f.close()

    def test_location_filtering(self, user: User):
        test_data = build_test_db()
        # only download data from location a
        province = Location.objects.get(location_type="province")
        facility_1 = Location.objects.get(
            path_string=f"{province.name} Province/District1 District/Facility1"
        )

        c = Client()
        c.force_login(user=user)

        response = c.post(
            POST_URL,
            data={"format": "csv", "locations": test_data["srs_facility_a"].pk},
        )
        assert response.status_code == 200

        # Django 3.2 has response.headers. For now, we'll access them per below
        # See https://docs.djangoproject.com/en/4.0/ref/request-response/#django.http.HttpResponse.headers
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == f"attachment; filename={CSV_ZIP_FILE_NAME}"
        )

        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")
            # Add one for the variable name header in the csv
            assert len(zipped_file.open(CSV_FILE_NAME).readlines()) == 3
        finally:
            zipped_file.close()
            f.close()

    def test_location_filtering_includes_cluster_community_vas(self, user: User):
        test_data = build_test_db()
        srs_constituency = test_data["srs_district1"].add_child(
            name="Constituency 1", location_type="constituency"
        )
        srs_ward = srs_constituency.add_child(name="Ward 1", location_type="ward")
        srs_ea = srs_ward.add_child(name="EA 1001", location_type="ea")

        community_va = VerbalAutopsyFactory.create(
            location=None,
            cluster=srs_ea,
            community_va="yes",
            province=test_data["province"].name,
            district="District1",
            constituency="Constituency 1",
            ward="Ward 1",
            ea="EA 1001",
            Id10023="2020-06-01",
        )
        CauseOfDeath.objects.create(
            cause="cod_a", settings={}, verbalautopsy=community_va
        )

        c = Client()
        c.force_login(user=user)

        response = c.post(
            POST_URL,
            data={"format": "csv", "locations": test_data["srs_district1"].pk},
        )
        assert response.status_code == 200

        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")
            # District1 contains 2 facility VAs in fixtures plus 1 community VA.
            assert len(zipped_file.open(CSV_FILE_NAME).readlines()) == 4
        finally:
            zipped_file.close()
            f.close()

    def test_id_filter_includes_community_va_without_facility_location(self, user: User):
        test_data = build_test_db()
        srs_constituency = test_data["srs_district1"].add_child(
            name="Constituency 2", location_type="constituency"
        )
        srs_ward = srs_constituency.add_child(name="Ward 2", location_type="ward")
        srs_ea = srs_ward.add_child(name="EA 2002", location_type="ea")

        community_va = VerbalAutopsyFactory.create(
            location=None,
            cluster=srs_ea,
            community_va="yes",
            province=test_data["province"].name,
            district="District1",
            constituency="Constituency 2",
            ward="Ward 2",
            ea="EA 2002",
            Id10023="2020-07-01",
        )
        CauseOfDeath.objects.create(
            cause="cod_a", settings={}, verbalautopsy=community_va
        )

        c = Client()
        c.force_login(user=user)

        response = c.post(POST_URL, data={"format": "csv", "ids": str(community_va.pk)})
        assert response.status_code == 200

        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")
            # Header + one explicitly selected VA.
            assert len(zipped_file.open(CSV_FILE_NAME).readlines()) == 2
        finally:
            zipped_file.close()
            f.close()

    def test_cod_filtering(self, user: User):
        build_test_db()
        # only download data from location a
        cod_name = "cod_a"

        c = Client()
        c.force_login(user=user)

        response = c.post(POST_URL, data={"format": "csv", "causes": cod_name})
        assert response.status_code == 200

        # Django 3.2 has response.headers. For now, we'll access them per below
        # See https://docs.djangoproject.com/en/4.0/ref/request-response/#django.http.HttpResponse.headers
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == f"attachment; filename={CSV_ZIP_FILE_NAME}"
        )

        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")
            # Add one for the variable name header in the csv
            assert len(zipped_file.open(CSV_FILE_NAME).readlines()) == 3
        finally:
            zipped_file.close()
            f.close()

    def test_time_filter(self, user: User):
        build_test_db()
        # only download data from location a
        start_date, end_date = "2020-01-01", "2021-01-01"

        c = Client()
        c.force_login(user=user)

        response = c.post(
            POST_URL,
            data={"format": "csv", "start_date": start_date, "end_date": end_date},
        )
        assert response.status_code == 200

        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")
            # Add one for the variable name header in the csv
            assert len(zipped_file.open(CSV_FILE_NAME).readlines()) == 2
        finally:
            zipped_file.close()
            f.close()

    def test_combined_filter_csv(self, user: User):
        test_data = build_test_db()
        # 1. Download from facility A after 1/1/2020 with COD_a in CSV format.
        # Assert only VA 4 is downloaded
        start_date = "2020-01-01"
        province = Location.objects.get(location_type="province")
        loc_a = Location.objects.get(
            path_string=f"{province.name} Province/District1 District/Facility1"
        )
        cod_name = "cod_a"

        query_data = {
            "format": "csv",
            "start_date": start_date,
            "locations": test_data["srs_facility_a"].pk,
            "causes": cod_name,
        }

        c = Client()
        c.force_login(user=user)

        response = c.post(POST_URL, data=query_data)
        assert response.status_code == 200

        # confirm correct number of VAs downloaded
        db_ct = VerbalAutopsy.objects.filter(
            Id10023__gte=start_date, causes__cause=cod_name, location__id=loc_a.pk
        ).count()

        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")
            # Add one for the variable name header in the csv
            assert len(zipped_file.open(CSV_FILE_NAME).readlines()) == db_ct + 1
        finally:
            zipped_file.close()
            f.close()

    def test_combined_filter_json(self, user: User):
        test_data = build_test_db()
        # 2. Download data from facility A from before 2020 with COD b in
        # JSON format. Assert only VA 1 is downloaded
        # NOTE: assumes records are stored in a wrapper with 'count' and 'record' keys.
        # If this structure changes, need to update this test

        end_date = "2020-01-01"
        province = Location.objects.get(location_type="province")
        loc_a = Location.objects.get(
            path_string=f"{province.name} Province/District1 District/Facility1"
        )
        cod_name = "cod_b"

        query_data = {
            "format": "json",
            "end_date": end_date,
            "locations": test_data["srs_facility_a"].pk,
            "causes": cod_name,
        }

        c = Client()
        c.force_login(user=user)

        # confirm correct number of VAs downloaded
        db_ct = VerbalAutopsy.objects.filter(
            Id10023__lte=end_date, causes__cause=cod_name, location__id=loc_a.pk
        ).count()

        response = c.post(POST_URL, data=query_data)
        assert response.status_code == 200

        # Django 3.2 has response.headers. For now, we'll access them per below
        # See https://docs.djangoproject.com/en/4.0/ref/request-response/#django.http.HttpResponse.headers
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == f"attachment; filename={JSON_ZIP_FILE_NAME}"
        )

        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")

            json_data = json.loads(zipped_file.read(JSON_FILE_NAME))
            assert json_data["count"] == db_ct
        finally:
            zipped_file.close()
            f.close()

        assert response.status_code == 200

    def test_redacted_download(self, rf: RequestFactory):
        build_test_db()
        user = User.objects.get(name="no_pii")

        c = Client()
        c.force_login(user=user)

        response = c.post(POST_URL, data={"format": "csv"})
        assert response.status_code == 200

        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")
            # Add one for the variable name header in the csv
            assert len(zipped_file.open(CSV_FILE_NAME).readlines()) == 5
            assert REDACTED_STRING in str(zipped_file.read(CSV_FILE_NAME))
        finally:
            zipped_file.close()
            f.close()

    def test_download_via_form(self, user: User):
        build_test_db()
        # filter by id of last location in test db
        loc_id = SRSClusterLocation.objects.last().pk
        download_form = VADownloadForm(
            {
                "action": "download",
                "format": "csv",
                "end_date": "2020-01-01",
                "location": str(loc_id),
            }
        )

        assert download_form.is_valid()
