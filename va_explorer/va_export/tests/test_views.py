import csv
import io
import json
import zipfile

import pytest
from django.contrib.auth.models import Permission
from django.test import Client, RequestFactory
from django.urls import reverse

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
from va_explorer.va_export.profiles import ABRIDGED_VA_EXPORT_COLUMNS

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


def _read_csv_text(response):
    f = io.BytesIO(response.content)
    try:
        zipped_file = zipfile.ZipFile(f, "r")
        return zipped_file.read(CSV_FILE_NAME).decode()
    finally:
        f.close()


def _csv_header(response):
    text = _read_csv_text(response)
    return next(csv.reader(io.StringIO(text)))


def _csv_rows(response):
    text = _read_csv_text(response)
    return list(csv.DictReader(io.StringIO(text)))


class TestAPIView:
    def test_download_form_renders_split_export_configuration_panels(self):
        permission = Permission.objects.filter(codename="download_data").first()
        group = GroupFactory.create(permissions=[permission] if permission else [])
        user = UserFactory.create(groups=[group])
        client = Client()
        client.force_login(user=user)

        response = client.get(reverse("va_export:download_form"))

        assert response.status_code == 200
        body = response.content.decode()
        assert "va-export-config-panels" in body
        assert body.count("va-export-config-panel") >= 2
        for wrapper_id in (
            "div_id_dataset",
            "div_id_va_record_type",
            "div_id_action",
            "div_id_start_date",
            "div_id_end_date",
            "div_id_locations",
            "div_id_causes",
            "div_id_export_config",
            "div_id_format",
        ):
            assert wrapper_id in body
        assert "Abridged VA (Location, Demographics, CoD, PII)" in body
        assert "Full VA Record" in body
        right_panel_start = body.index(
            "va-export-config-panel va-export-config-panel--right"
        )
        assert right_panel_start < body.index("div_id_action")

    def test_va_record_type_is_rendered_in_disableable_group_box(self):
        # The "disable VA Record Type when dataset != Verbal Autopsy" behaviour in
        # va_export.js (updateFormForDatasetSelected) targets the <fieldset>
        # #va-record-type-group, toggling its native `disabled` (which cascades to
        # the radios) and checking #id_va_record_type_0. Lock that DOM contract so
        # a future crispy/template change can't silently break the toggle.
        permission = Permission.objects.filter(codename="download_data").first()
        group = GroupFactory.create(permissions=[permission] if permission else [])
        user = UserFactory.create(groups=[group])
        client = Client()
        client.force_login(user=user)

        body = client.get(reverse("va_export:download_form")).content.decode()

        # The group box is a fieldset (so `disabled` cascades) with a legend title.
        assert '<fieldset id="va-record-type-group"' in body
        group_start = body.index('id="va-record-type-group"')
        assert "VA Record Type" in body  # legend title
        # The toggle's target radios live inside the group box.
        assert 'name="va_record_type"' in body
        assert group_start < body.index('id="id_va_record_type_0"')
        assert group_start < body.index('id="id_va_record_type_1"')

    def test_download_form_defaults_to_abridged_va_record_type(self):
        form = VADownloadForm(
            {
                "dataset": "verbalautopsy",
                "action": "download",
                "format": "csv",
            }
        )

        assert form.is_valid()
        assert form.cleaned_data["va_record_type"] == "abridged"

    def test_download_form_ignores_va_record_type_for_non_va_dataset(self):
        form = VADownloadForm(
            {
                "dataset": "household",
                "action": "download",
                "format": "csv",
                "va_record_type": "full",
            }
        )

        assert form.is_valid()
        assert form.cleaned_data["va_record_type"] is None

    def test_household_csv_download(self, user: User):
        build_test_db()
        Household.objects.create(
            key="hh-export-1", today="2026-01-01", province="Central"
        )

        c = Client()
        c.force_login(user=user)

        response = c.post(POST_URL, data={"dataset": "household", "format": "csv"})
        assert response.status_code == 200
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == "attachment; filename=household.csv.zip"
        )

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
        Pregnancy.objects.create(
            key="preg-export-1", today="2026-01-02", PE_06="Jane Doe"
        )

        c = Client()
        c.force_login(user=user)

        response = c.post(POST_URL, data={"dataset": "pregnancy", "format": "csv"})
        assert response.status_code == 200
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == "attachment; filename=pregnancy.csv.zip"
        )

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
        PregnancyOutcome.objects.create(
            key="po-export-1", today="2026-01-03", PO_04="Mother"
        )

        c = Client()
        c.force_login(user=user)

        response = c.post(
            POST_URL, data={"dataset": "pregnancy_outcome", "format": "csv"}
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == "attachment; filename=pregnancy_outcome.csv.zip"
        )

        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")
            assert "pregnancy_outcome_download.csv" in zipped_file.namelist()
            assert (
                len(zipped_file.open("pregnancy_outcome_download.csv").readlines()) >= 2
            )
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
        assert (
            response.headers["content-disposition"]
            == "attachment; filename=death.csv.zip"
        )

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
        Location.objects.get(name="Empty Facility")

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
        Location.objects.get(name="Empty Facility")

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
        Location.objects.get(
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

    def test_id_filter_includes_community_va_without_facility_location(
        self, user: User
    ):
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
        test_data = build_test_db()
        loc_id = test_data["srs_district1"].pk
        download_form = VADownloadForm(
            {
                "dataset": "verbalautopsy",
                "action": "download",
                "format": "csv",
                "end_date": "2020-01-01",
                "locations": [str(loc_id)],
            }
        )

        assert download_form.is_valid()

    def test_download_form_location_options_are_limited_to_admin_levels(self):
        country = SRSClusterLocation.add_root(name="Zambia", location_type="country")
        province = country.add_child(name="Lusaka", location_type="province")
        district = province.add_child(name="Lusaka District", location_type="district")
        constituency = district.add_child(name="Mandevu", location_type="constituency")
        ward = constituency.add_child(name="Ward 1", location_type="ward")
        ea = ward.add_child(name="EA 1001", location_type="ea")
        facility = district.add_child(name="Clinic 1", location_type="facility")
        unknown = province.add_child(name="Unknown", location_type="district")

        form = VADownloadForm()

        option_ids = set(form.fields["locations"].queryset.values_list("id", flat=True))
        assert {province.id, district.id, constituency.id, ward.id}.issubset(option_ids)
        assert {country.id, ea.id, facility.id, unknown.id}.isdisjoint(option_ids)

        submitted_form = VADownloadForm(
            {
                "dataset": "verbalautopsy",
                "action": "download",
                "format": "csv",
                "locations": [str(ward.id)],
            }
        )
        assert submitted_form.is_valid()

    def test_abridged_export_contains_only_profile_columns(self, user: User):
        build_test_db()
        c = Client()
        c.force_login(user=user)

        response = c.post(
            POST_URL, data={"format": "csv", "va_record_type": "abridged"}
        )
        assert response.status_code == 200

        header = _csv_header(response)
        # Every abridged column must come from the profile (nothing else leaks in).
        assert set(header).issubset(set(ABRIDGED_VA_EXPORT_COLUMNS))
        # Representative identifier, location, demographic and CoD columns present.
        for expected in ("id", "province", "Id10019", "cause", "cause_algorithm"):
            assert expected in header

    def test_abridged_export_excludes_full_questionnaire_fields(self, user: User):
        build_test_db()
        c = Client()
        c.force_login(user=user)

        abridged_header = _csv_header(
            c.post(POST_URL, data={"format": "csv", "va_record_type": "abridged"})
        )
        full_header = _csv_header(
            c.post(POST_URL, data={"format": "csv", "va_record_type": "full"})
        )

        # Id10007 is a full questionnaire/PII field outside the abridged profile.
        assert "Id10007" in full_header
        assert "Id10007" not in abridged_header

    def test_full_export_returns_wide_record(self, user: User):
        build_test_db()
        c = Client()
        c.force_login(user=user)

        response = c.post(POST_URL, data={"format": "csv", "va_record_type": "full"})
        assert response.status_code == 200

        header = _csv_header(response)
        # Full export is the wide record and a superset of the abridged columns.
        assert len(header) > len(ABRIDGED_VA_EXPORT_COLUMNS)
        for col in ("id", "cause", "province"):
            assert col in header

        # Row count is unchanged from the legacy full-record behaviour.
        try:
            f = io.BytesIO(response.content)
            zipped_file = zipfile.ZipFile(f, "r")
            assert len(zipped_file.open(CSV_FILE_NAME).readlines()) == 5
        finally:
            zipped_file.close()
            f.close()

    def test_filters_apply_to_both_record_types(self, user: User):
        build_test_db()
        c = Client()
        c.force_login(user=user)

        # A 2020 date window matches exactly one fixture VA (va4, 2020-04-01).
        row_counts = {}
        for record_type in ("abridged", "full"):
            response = c.post(
                POST_URL,
                data={
                    "format": "csv",
                    "va_record_type": record_type,
                    "start_date": "2020-01-01",
                    "end_date": "2021-01-01",
                },
            )
            assert response.status_code == 200
            row_counts[record_type] = len(_csv_rows(response))

        # The date filter applies identically regardless of record type.
        assert row_counts["abridged"] == 1
        assert row_counts["full"] == 1

    def test_abridged_export_populates_cod_fields(self, user: User):
        test_data = build_test_db()
        va = VerbalAutopsyFactory.create(
            location=test_data["facility_a"], Id10023="2021-02-02"
        )
        CauseOfDeath.objects.create(
            cause="Malaria",
            algorithm="InterVA5",
            settings={},
            verbalautopsy=va,
        )

        c = Client()
        c.force_login(user=user)

        response = c.post(
            POST_URL,
            data={"format": "csv", "va_record_type": "abridged", "ids": str(va.pk)},
        )
        assert response.status_code == 200

        rows = _csv_rows(response)
        assert len(rows) == 1
        assert rows[0]["cause"] == "Malaria"
        assert rows[0]["cause_algorithm"] == "InterVA5"

    def test_abridged_export_handles_missing_cod(self, user: User):
        test_data = build_test_db()
        # VA with no related CauseOfDeath record.
        va = VerbalAutopsyFactory.create(
            location=test_data["facility_a"], Id10023="2021-03-03"
        )

        c = Client()
        c.force_login(user=user)

        response = c.post(
            POST_URL,
            data={"format": "csv", "va_record_type": "abridged", "ids": str(va.pk)},
        )
        assert response.status_code == 200

        rows = _csv_rows(response)
        assert len(rows) == 1
        # Missing CoD exports cleanly with blank cause cells.
        assert rows[0]["cause"] == ""
        assert rows[0]["cause_algorithm"] == ""

    def test_missing_or_invalid_record_type_defaults_to_full(self, user: User):
        build_test_db()
        c = Client()
        c.force_login(user=user)

        # Omitted entirely -> full record (backward compatible).
        omitted_header = _csv_header(c.post(POST_URL, data={"format": "csv"}))
        assert "Id10007" in omitted_header

        # Unrecognised value -> never silently abridged; falls back to full.
        invalid_header = _csv_header(
            c.post(POST_URL, data={"format": "csv", "va_record_type": "garbage"})
        )
        assert "Id10007" in invalid_header
