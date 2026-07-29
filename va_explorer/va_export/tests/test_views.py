import csv
import io
import json
import logging
import zipfile
from datetime import datetime
from datetime import timezone as dt_timezone

import pytest
from django.contrib.auth.models import Permission
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

from va_explorer.tests.factories import (
    GroupFactory,
    LocationFactory,
    UserFactory,
    VASAFactory,
    VerbalAutopsyFactory,
)
from va_explorer.users.models import User
from va_explorer.va_data_management.constants import REDACTED_STRING
from va_explorer.va_data_management.models import (
    CauseOfDeath,
    CSADailyTracker,
    Death,
    Household,
    Location,
    ODKFormChoice,
    Pregnancy,
    PregnancyOutcome,
    SRSClusterLocation,
    VerbalAutopsy,
)
from va_explorer.va_data_management.utils.submission_timestamp import export_timezone
from va_explorer.va_export.forms import VADownloadForm
from va_explorer.va_export.profiles import ABRIDGED_VA_EXPORT_COLUMNS

pytestmark = pytest.mark.django_db

CSV_ZIP_FILE_NAME = "export.csv.zip"
JSON_ZIP_FILE_NAME = "export.json.zip"
CSV_FILE_NAME = "va_download.csv"
JSON_FILE_NAME = "va_download.json"
FILE_CONTENT_TYPE = "application/zip"
POST_URL = "/va_export/verbalautopsy/"
DEFAULT_EXPORT_DATES = {
    "start_date": "1901-01-01",
    "end_date": "9999-12-31",
}


@pytest.fixture
def user() -> User:
    user = UserFactory.create()
    user.can_download_data = True
    return user


def _post_export(client, data=None):
    payload = {**DEFAULT_EXPORT_DATES, **(data or {})}
    return client.post(POST_URL, data=payload)


def _export_day_start(year, month, day):
    """Midnight on a local export day, as the UTC instant the query uses."""
    return datetime(year, month, day, tzinfo=export_timezone()).astimezone(
        dt_timezone.utc
    )


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
    va1 = VerbalAutopsyFactory.create(
        location=facility_a,
        Id10023="2019-01-01",
        submissiondate="2019-01-01T12:00:00Z",
    )
    va2 = VerbalAutopsyFactory.create(
        location=facility_b,
        Id10023="2019-01-03",
        submissiondate="2019-01-03T12:00:00Z",
    )
    va3 = VerbalAutopsyFactory.create(
        location=facility_c,
        Id10023="2019-01-09",
        submissiondate="2019-01-09T12:00:00Z",
    )
    va4 = VerbalAutopsyFactory.create(
        location=facility_a,
        Id10023="2020-04-01",
        submissiondate="2020-04-01T12:00:00Z",
    )

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
        "va1": va1,
        "va2": va2,
        "va3": va3,
        "va4": va4,
    }


def _response_content(response):
    if not hasattr(response, "_export_test_content"):
        response._export_test_content = (
            b"".join(response.streaming_content)
            if response.streaming
            else response.content
        )
    return response._export_test_content


def _read_csv_text(response):
    f = io.BytesIO(_response_content(response))
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


def _zip_csv_rows(response, member_name):
    with zipfile.ZipFile(io.BytesIO(_response_content(response))) as archive:
        text = archive.read(member_name).decode()
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
        assert f'action="{reverse("va_export:download_form")}"' in body
        assert 'id="exportValidationWarning"' in body
        assert 'id="submit-form" class="btn btn-primary hidden"' not in body
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
                **DEFAULT_EXPORT_DATES,
            }
        )

        assert form.is_valid()
        assert form.cleaned_data["va_record_type"] == "abridged"

    def test_download_form_explains_va_submission_date_filter(self):
        form = VADownloadForm()

        assert "inclusive ODK submission date" in form.fields["start_date"].help_text
        assert "inclusive ODK submission date" in form.fields["end_date"].help_text

    def test_download_form_ignores_va_record_type_for_non_va_dataset(self):
        form = VADownloadForm(
            {
                "dataset": "household",
                "action": "download",
                "format": "csv",
                "va_record_type": "full",
                **DEFAULT_EXPORT_DATES,
            }
        )

        assert form.is_valid()
        assert form.cleaned_data["va_record_type"] is None

    def test_api_requires_download_permission(self):
        client = Client()
        client.force_login(user=UserFactory.create())

        response = _post_export(client, data={"format": "csv"})

        assert response.status_code == 403

    def test_api_requires_csrf_token(self, user: User):
        client = Client(enforce_csrf_checks=True)
        client.force_login(user=user)

        response = client.post(POST_URL, data=DEFAULT_EXPORT_DATES)

        assert response.status_code == 403

    def test_api_rejects_invalid_dataset_and_date_range(self, user: User):
        client = Client()
        client.force_login(user=user)

        invalid_dataset = _post_export(client, data={"dataset": "not-a-dataset"})
        reversed_dates = _post_export(
            client,
            data={"start_date": "2026-02-01", "end_date": "2026-01-01"},
        )
        missing_dates = client.post(POST_URL, data={"format": "csv"})

        assert invalid_dataset.status_code == 400
        assert "dataset" in invalid_dataset.json()["errors"]
        assert reversed_dates.status_code == 400
        assert "end_date" in reversed_dates.json()["errors"]
        assert missing_dates.status_code == 400
        assert {"start_date", "end_date"}.issubset(missing_dates.json()["errors"])

    def test_html_form_post_downloads_without_javascript(self, user: User):
        client = Client()
        client.force_login(user=user)

        response = client.post(
            reverse("va_export:download_form"),
            data={
                "dataset": "verbalautopsy",
                "va_record_type": "abridged",
                "action": "download",
                "format": "csv",
                **DEFAULT_EXPORT_DATES,
            },
        )

        assert response.status_code == 200
        assert response.streaming
        assert response.headers["content-disposition"].startswith("attachment;")
        with zipfile.ZipFile(io.BytesIO(_response_content(response))) as archive:
            assert CSV_FILE_NAME in archive.namelist()

    @pytest.mark.parametrize("record_type", ["abridged", "full"])
    def test_html_form_exports_uncoded_vasa_for_both_record_types(
        self, user: User, record_type
    ):
        test_data = build_test_db()
        vasa = VASAFactory.create(
            location=test_data["facility_a"],
            Id10019="female",
            Id10023="2020-06-01",
            instrument="adult",
            raw_answers={"values": {"Q1601": "2"}},
        )
        client = Client()
        client.force_login(user=user)

        response = client.post(
            reverse("va_export:download_form"),
            data={
                "dataset": "verbalautopsy",
                "va_record_type": record_type,
                "action": "download",
                "format": "csv",
                **DEFAULT_EXPORT_DATES,
            },
        )

        assert response.status_code == 200
        rows = _csv_rows(response)
        vasa_rows = [row for row in rows if row["source"] == "VASA"]
        assert len(vasa_rows) == 1
        assert int(vasa_rows[0]["id"]) == vasa.pk
        assert vasa_rows[0]["instrument"] == "adult"
        assert vasa_rows[0]["Id10019"] == "female"
        assert vasa_rows[0]["cause"] == ""
        header = _csv_header(response)
        assert "raw_answers" not in header
        assert ("Id10007" in header) is (record_type == "full")

    def test_html_form_post_renders_validation_errors(self, user: User):
        client = Client()
        client.force_login(user=user)

        response = client.post(
            reverse("va_export:download_form"),
            data={
                "dataset": "verbalautopsy",
                "va_record_type": "abridged",
                "action": "download",
                "format": "csv",
                "start_date": "2026-02-01",
                "end_date": "2026-01-01",
            },
        )

        assert response.status_code == 200
        assert not response.streaming
        assert b"Latest Date must be on or after Earliest Date." in response.content

    def test_household_csv_download(self, user: User):
        build_test_db()
        Household.objects.create(
            key="hh-export-1", today="2026-01-01", province="Central"
        )

        c = Client()
        c.force_login(user=user)

        response = _post_export(c, data={"dataset": "household", "format": "csv"})
        assert response.status_code == 200
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == "attachment; filename=household.csv.zip"
        )

        try:
            f = io.BytesIO(_response_content(response))
            zipped_file = zipfile.ZipFile(f, "r")
            assert "household_download.csv" in zipped_file.namelist()
            assert len(zipped_file.open("household_download.csv").readlines()) >= 2
        finally:
            zipped_file.close()
            f.close()

    def test_explicit_location_filter_uses_full_tree_lineage(self, user: User):
        province = SRSClusterLocation.add_root(
            name="Lineage Province", location_type="province"
        )
        district_a = province.add_child(
            name="Lineage District A", location_type="district"
        )
        district_b = province.add_child(
            name="Lineage District B", location_type="district"
        )
        ward_a = district_a.add_child(name="Shared Ward", location_type="ward")
        district_b.add_child(name="Shared Ward", location_type="ward")
        Household.objects.create(
            key="hh-lineage-a",
            today="2026-01-01",
            province="Lineage Province",
            district="Lineage District A",
            ward="Shared Ward",
        )
        Household.objects.create(
            key="hh-lineage-b",
            today="2026-01-01",
            province="Lineage Province",
            district="Lineage District B",
            ward="Shared Ward",
        )
        client = Client()
        client.force_login(user=user)

        response = _post_export(
            client,
            data={"dataset": "household", "locations": ward_a.pk},
        )

        rows = _zip_csv_rows(response, "household_download.csv")
        assert [row["key"] for row in rows] == ["hh-lineage-a"]

    def test_user_location_scope_uses_full_tree_lineage(self, user: User):
        province = LocationFactory.create(
            name="Scoped Province", location_type="province"
        )
        district_a = province.add_child(
            name="Scoped District A", location_type="district"
        )
        district_b = province.add_child(
            name="Scoped District B", location_type="district"
        )
        district_a.add_child(name="Shared Ward", location_type="ward")
        district_b.add_child(name="Shared Ward", location_type="ward")
        Household.objects.create(
            key="hh-scope-a",
            today="2026-01-01",
            province="Scoped Province",
            district="Scoped District A",
            ward="Shared Ward",
        )
        Household.objects.create(
            key="hh-scope-b",
            today="2026-01-01",
            province="Scoped Province",
            district="Scoped District B",
            ward="Shared Ward",
        )
        user.location_restrictions.set([district_a])
        client = Client()
        client.force_login(user=user)

        response = _post_export(client, data={"dataset": "household"})

        rows = _zip_csv_rows(response, "household_download.csv")
        assert [row["key"] for row in rows] == ["hh-scope-a"]

    def test_non_va_download_redacts_pii(self, user: User):
        Household.objects.create(
            key="hh-pii",
            today="2026-01-01",
            respondent="Private Name",
            hh_gps="-15.4000 28.3000",
        )
        client = Client()
        client.force_login(user=user)

        response = _post_export(client, data={"dataset": "household", "format": "csv"})

        with zipfile.ZipFile(io.BytesIO(_response_content(response))) as archive:
            text = archive.read("household_download.csv").decode()
        rows = list(csv.DictReader(io.StringIO(text)))
        assert rows[0]["respondent"] == REDACTED_STRING
        assert rows[0]["hh_gps"] == REDACTED_STRING

    def test_non_va_download_preserves_pii_for_authorized_user(self, user: User):
        user.can_view_pii = True
        Household.objects.create(
            key="hh-pii-authorized",
            today="2026-01-01",
            respondent="Visible Name",
            hh_gps="-15.4000 28.3000",
        )
        client = Client()
        client.force_login(user=user)

        response = _post_export(client, data={"dataset": "household"})

        with zipfile.ZipFile(io.BytesIO(_response_content(response))) as archive:
            text = archive.read("household_download.csv").decode()
        rows = list(csv.DictReader(io.StringIO(text)))
        assert rows[0]["respondent"] == "Visible Name"
        assert rows[0]["hh_gps"] == "-15.4000 28.3000"

    def test_rumor_surveillance_download_decodes_location_and_redacts_gps(
        self, user: User
    ):
        ODKFormChoice.objects.create(
            form_name="csa_daily_tracker",
            field_name="province",
            value="5",
            label="Central Province",
        )
        CSADailyTracker.objects.create(
            key="rumor-export-1",
            today="2026-01-05",
            province="05",
            desci="A test report",
            hh_gps_latitude="-15.4000",
            hh_gps_longitude="28.3000",
            hh_gps_altitude="1200",
            hh_gps_accuracy="4",
        )
        client = Client()
        client.force_login(user=user)

        response = _post_export(
            client,
            data={"dataset": "rumor_surveillance", "format": "json"},
        )

        assert response.status_code == 200
        assert response.streaming
        assert response.headers["content-disposition"] == (
            "attachment; filename=rumor_surveillance.csv.zip"
        )
        with zipfile.ZipFile(io.BytesIO(_response_content(response))) as archive:
            text = archive.read("rumor_surveillance_download.csv").decode()
        rows = list(csv.DictReader(io.StringIO(text)))
        assert len(rows) == 1
        assert rows[0]["province"] == "Central Province"
        assert rows[0]["hh_gps_latitude"] == REDACTED_STRING
        assert rows[0]["hh_gps_longitude"] == REDACTED_STRING

    def test_rumor_location_filter_uses_full_tree_lineage(self, user: User):
        province = SRSClusterLocation.add_root(
            name="Rumor Province", location_type="province"
        )
        district_a = province.add_child(
            name="Rumor District A", location_type="district"
        )
        district_b = province.add_child(
            name="Rumor District B", location_type="district"
        )
        ward_a = district_a.add_child(name="Shared Ward", location_type="ward")
        district_b.add_child(name="Shared Ward", location_type="ward")
        CSADailyTracker.objects.create(
            key="rumor-lineage-a",
            today="2026-01-05",
            province="Rumor Province",
            district="Rumor District A",
            ward="Shared Ward",
            desci="Included report",
        )
        CSADailyTracker.objects.create(
            key="rumor-lineage-b",
            today="2026-01-05",
            province="Rumor Province",
            district="Rumor District B",
            ward="Shared Ward",
            desci="Excluded report",
        )
        client = Client()
        client.force_login(user=user)

        response = _post_export(
            client,
            data={"dataset": "rumor_surveillance", "locations": ward_a.pk},
        )

        rows = _zip_csv_rows(response, "rumor_surveillance_download.csv")
        assert [row["key"] for row in rows] == ["rumor-lineage-a"]

    def test_pregnancy_csv_download(self, user: User):
        build_test_db()
        Pregnancy.objects.create(
            key="preg-export-1", today="2026-01-02", PE_06="Jane Doe"
        )

        c = Client()
        c.force_login(user=user)

        response = _post_export(c, data={"dataset": "pregnancy", "format": "csv"})
        assert response.status_code == 200
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == "attachment; filename=pregnancy.csv.zip"
        )

        try:
            f = io.BytesIO(_response_content(response))
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

        response = _post_export(
            c, data={"dataset": "pregnancy_outcome", "format": "csv"}
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == "attachment; filename=pregnancy_outcome.csv.zip"
        )

        try:
            f = io.BytesIO(_response_content(response))
            zipped_file = zipfile.ZipFile(f, "r")
            assert "pregnancy_outcome_download.csv" in zipped_file.namelist()
            assert (
                len(zipped_file.open("pregnancy_outcome_download.csv").readlines()) >= 2
            )
        finally:
            zipped_file.close()
            f.close()

    def test_death_csv_download(self, user: User, monkeypatch):
        build_test_db()
        monkeypatch.setattr(
            "va_explorer.va_data_management.tasks.assign_death_ids_task.delay",
            lambda: None,
        )
        Death.objects.create(key="death-export-1", today="2026-01-04", DE_03="Decedent")

        c = Client()
        c.force_login(user=user)

        response = _post_export(c, data={"dataset": "death", "format": "csv"})
        assert response.status_code == 200
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == "attachment; filename=death.csv.zip"
        )

        try:
            f = io.BytesIO(_response_content(response))
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

        response = _post_export(c, data={"format": "csv"})
        assert response.status_code == 200
        assert response.streaming
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["cache-control"] == "private, no-store"

        # Django 3.2 has response.headers. For now, we'll access them per below
        # See https://docs.djangoproject.com/en/4.0/ref/request-response/#django.http.HttpResponse.headers
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == f"attachment; filename={CSV_ZIP_FILE_NAME}"
        )

        try:
            f = io.BytesIO(_response_content(response))
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

        response = _post_export(c, data={"format": "json"})
        assert response.status_code == 200

        # Django 3.2 has response.headers. For now, we'll access them per below
        # See https://docs.djangoproject.com/en/4.0/ref/request-response/#django.http.HttpResponse.headers
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == f"attachment; filename={JSON_ZIP_FILE_NAME}"
        )

        try:
            f = io.BytesIO(_response_content(response))
            zipped_file = zipfile.ZipFile(f, "r")

            json_data = json.loads(zipped_file.read(JSON_FILE_NAME))
            assert json_data["count"] == 4
            assert len(json.loads(json_data["records"])) == 4
        finally:
            zipped_file.close()
            f.close()

    def test_download_csv_with_no_matching_vas(self, user: User):
        test_data = build_test_db()
        # only download data from "No VA Facility", which will have no matching VAs
        Location.objects.get(name="Empty Facility")

        c = Client()
        c.force_login(user=user)

        response = _post_export(
            c,
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
            f = io.BytesIO(_response_content(response))
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

        response = _post_export(
            c,
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
            f = io.BytesIO(_response_content(response))
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

        response = _post_export(
            c,
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
            f = io.BytesIO(_response_content(response))
            zipped_file = zipfile.ZipFile(f, "r")
            # Add one for the variable name header in the csv
            assert len(zipped_file.open(CSV_FILE_NAME).readlines()) == 3
        finally:
            zipped_file.close()
            f.close()

    def test_va_export_respects_user_location_tree(self, user: User):
        test_data = build_test_db()
        allowed_vasa = VASAFactory.create(
            location=test_data["facility_a"], Id10023="2020-06-01"
        )
        VASAFactory.create(location=test_data["facility_b"], Id10023="2020-06-01")
        user.location_restrictions.set([test_data["district1"]])
        client = Client()
        client.force_login(user=user)

        response = _post_export(client, data={"va_record_type": "abridged"})

        exported_records = {
            (row["source"], int(row["id"])) for row in _csv_rows(response)
        }
        assert exported_records == {
            ("VA", test_data["va1"].pk),
            ("VA", test_data["va4"].pk),
            ("VASA", allowed_vasa.pk),
        }

    def test_va_export_includes_vasa_records(self, user: User):
        test_data = build_test_db()
        vasa = VASAFactory.create(
            location=test_data["facility_a"],
            Id10023="2020-06-01",
            instrument="adult",
            raw_answers={"values": {"A4001": "1"}},
        )
        CauseOfDeath.objects.create(
            cause="cod_vasa",
            algorithm="InterVA5",
            settings={},
            vasa=vasa,
        )
        client = Client()
        client.force_login(user=user)

        response = _post_export(
            client, data={"format": "csv", "va_record_type": "abridged"}
        )

        assert response.status_code == 200
        header = _csv_header(response)
        rows = _csv_rows(response)
        vasa_rows = [row for row in rows if row["source"] == "VASA"]
        assert len(rows) == 5
        assert len(vasa_rows) == 1
        assert int(vasa_rows[0]["id"]) == vasa.pk
        assert vasa_rows[0]["instrument"] == "adult"
        assert vasa_rows[0]["cause"] == "cod_vasa"
        assert "source" in header
        assert "instrument" in header
        assert "raw_answers" not in header

        full_response = _post_export(
            client,
            data={
                "format": "csv",
                "va_record_type": "full",
                "vasa_ids": str(vasa.pk),
            },
        )
        assert "raw_answers" not in _csv_header(full_response)

    def test_va_export_filters_apply_to_vasa_records(self, user: User):
        test_data = build_test_db()
        included = VASAFactory.create(
            location=test_data["facility_a"],
            Id10023="2020-06-01",
            submissiondate="2020-06-01T12:00:00Z",
        )
        wrong_location = VASAFactory.create(
            location=test_data["facility_b"],
            Id10023="2020-06-01",
            submissiondate="2020-06-01T12:00:00Z",
        )
        wrong_date = VASAFactory.create(
            location=test_data["facility_a"],
            Id10023="2020-06-01",
            submissiondate="2019-06-01T12:00:00Z",
        )
        wrong_cause = VASAFactory.create(
            location=test_data["facility_a"],
            Id10023="2020-07-01",
            submissiondate="2020-07-01T12:00:00Z",
        )
        for vasa, cause in (
            (included, "cod_a"),
            (wrong_location, "cod_a"),
            (wrong_date, "cod_a"),
            (wrong_cause, "cod_b"),
        ):
            CauseOfDeath.objects.create(cause=cause, settings={}, vasa=vasa)

        client = Client()
        client.force_login(user=user)
        response = _post_export(
            client,
            data={
                "format": "csv",
                "va_record_type": "abridged",
                "start_date": "2020-01-01",
                "end_date": "2021-01-01",
                "locations": test_data["srs_district1"].pk,
                "causes": "cod_a",
            },
        )

        assert response.status_code == 200
        exported_vasa_ids = {
            int(row["id"]) for row in _csv_rows(response) if row["source"] == "VASA"
        }
        assert exported_vasa_ids == {included.pk}

    @pytest.mark.parametrize("record_type", ["abridged", "full"])
    @override_settings(VA_EXPORT_TIME_ZONE="Africa/Lusaka")
    def test_va_export_uses_inclusive_submission_dates_for_both_sources(
        self, user: User, record_type
    ):
        # Timestamps are stored in UTC but bucketed into local calendar days
        # (Africa/Lusaka, UTC+2), so 2024-05-01..2024-05-31 inclusive means
        # [2024-04-30T22:00Z, 2024-05-31T22:00Z). Each fixture below is written
        # as its UTC instant with the local wall clock it represents.
        facility = LocationFactory.create(location_type="facility")
        # 2024-05-01 00:00:00 local — first instant in range. Its *UTC* date is
        # 30 April, so this row only appears under local-day bucketing.
        first_instant_va = VerbalAutopsyFactory.create(
            location=facility,
            Id10023="",
            submissiondate="2024-04-30T22:00:00Z",
        )
        CauseOfDeath.objects.create(
            cause="Boundary cause",
            algorithm="InterVA5",
            settings={},
            verbalautopsy=first_instant_va,
        )
        # 2024-06-01 00:00:00 local — the exclusive upper bound itself. Death
        # date is inside the range; submission date is not.
        death_only_va = VerbalAutopsyFactory.create(
            location=facility,
            Id10023="2024-05-15",
            submissiondate="2024-05-31T22:00:00Z",
        )
        # 2024-05-31 23:59:59 local — last instant in range.
        last_instant_vasa = VASAFactory.create(
            location=facility,
            Id10023="dk",
            submissiondate="2024-05-31T21:59:59Z",
        )
        mid_range_vasa = VASAFactory.create(
            location=facility,
            Id10023="1990-01-01",
            submissiondate="2024-05-15T12:00:00+00:00",
        )
        # 2024-04-30 23:59:59 local — one second before the range.
        before_range_vasa = VASAFactory.create(
            location=facility,
            Id10023="2024-05-15",
            submissiondate="2024-04-30T21:59:59Z",
        )
        client = Client()
        client.force_login(user=user)

        response = _post_export(
            client,
            data={
                "va_record_type": record_type,
                "start_date": "2024-05-01",
                "end_date": "2024-05-31",
            },
        )

        rows = _csv_rows(response)
        assert [(row["source"], int(row["id"])) for row in rows] == [
            ("VA", first_instant_va.pk),
            ("VASA", last_instant_vasa.pk),
            ("VASA", mid_range_vasa.pk),
        ]
        assert ("VA", death_only_va.pk) not in {
            (row["source"], int(row["id"])) for row in rows
        }
        assert ("VASA", before_range_vasa.pk) not in {
            (row["source"], int(row["id"])) for row in rows
        }
        assert rows[0]["Id10023"] == ""
        assert rows[0]["cause"] == "Boundary cause"
        assert rows[1]["Id10023"] == "dk"
        assert rows[1]["cause"] == ""
        assert rows[0]["submitted_at"].startswith("2024-04-30T22:00:00")
        assert "submitted_at" in _csv_header(response)
        assert ("submissiondate" in _csv_header(response)) is (record_type == "full")

    @override_settings(VA_EXPORT_TIME_ZONE="UTC")
    def test_va_export_day_boundaries_follow_the_export_timezone_setting(
        self, user: User
    ):
        # Same instant, different bucketing: 2024-04-30T22:00Z is 1 May in
        # Lusaka but still 30 April in UTC, so it drops out of a May range.
        facility = LocationFactory.create(location_type="facility")
        late_april_utc_va = VerbalAutopsyFactory.create(
            location=facility,
            Id10023="",
            submissiondate="2024-04-30T22:00:00Z",
        )
        client = Client()
        client.force_login(user=user)

        response = _post_export(
            client,
            data={"start_date": "2024-05-01", "end_date": "2024-05-31"},
        )

        assert ("VA", late_april_utc_va.pk) not in {
            (row["source"], int(row["id"])) for row in _csv_rows(response)
        }

    def test_va_export_log_reports_records_with_no_canonical_timestamp(
        self, user: User, caplog
    ):
        # A record with no submitted_at can never satisfy submitted_at__gte, so
        # it is invisible to every dated download. The log must say so rather
        # than leaving an unexplained shortfall.
        facility = LocationFactory.create(location_type="facility")
        undated_va = VerbalAutopsyFactory.create(location=facility)
        VerbalAutopsy.all_objects.filter(pk=undated_va.pk).update(
            submissiondate="15/05/2026", submitted_at=None
        )
        client = Client()
        client.force_login(user=user)

        with caplog.at_level(logging.INFO, logger="va_explorer.va_export.views"):
            response = _post_export(client)

        rows = _csv_rows(response)
        assert ("VA", undated_va.pk) not in {
            (row["source"], int(row["id"])) for row in rows
        }
        assert any("undated_va=1" in message for message in caplog.messages)

    def test_undated_uncoded_vasa_is_included_until_a_cause_filter_is_selected(
        self, user: User
    ):
        test_data = build_test_db()
        uncoded_vasa = VASAFactory.create(
            location=test_data["facility_a"],
            Id10023="2020-06-01",
            instrument="adult",
        )
        undated_vasa = VASAFactory.create(
            location=test_data["facility_a"], Id10023="", instrument="adult"
        )
        client = Client()
        client.force_login(user=user)

        unfiltered_rows = _csv_rows(_post_export(client))
        exported_records = {(row["source"], int(row["id"])) for row in unfiltered_rows}
        assert ("VASA", uncoded_vasa.pk) in exported_records
        assert ("VASA", undated_vasa.pk) in exported_records

        filtered_rows = _csv_rows(_post_export(client, data={"causes": "cod_a"}))
        assert ("VASA", uncoded_vasa.pk) not in {
            (row["source"], int(row["id"])) for row in filtered_rows
        }
        assert ("VASA", undated_vasa.pk) not in {
            (row["source"], int(row["id"])) for row in filtered_rows
        }

    def test_vasa_with_multiple_causes_exports_one_ordered_row_per_cause(
        self, user: User
    ):
        test_data = build_test_db()
        vasa = VASAFactory.create(
            location=test_data["facility_a"], Id10023="2020-06-01"
        )
        CauseOfDeath.objects.create(
            cause="First cause", algorithm="InterVA5", settings={}, vasa=vasa
        )
        CauseOfDeath.objects.create(
            cause="Second cause", algorithm="InSilicoVA", settings={}, vasa=vasa
        )
        client = Client()
        client.force_login(user=user)

        rows = _csv_rows(_post_export(client, data={"vasa_ids": str(vasa.pk)}))

        assert [(row["cause"], row["cause_algorithm"]) for row in rows] == [
            ("First cause", "InterVA5"),
            ("Second cause", "InSilicoVA"),
        ]

    def test_source_specific_ids_do_not_collide(self, user: User):
        test_data = build_test_db()
        va = test_data["va1"]
        vasa = VASAFactory.create(
            id=va.pk,
            location=test_data["facility_a"],
            Id10023="2019-01-01",
        )
        CauseOfDeath.objects.create(cause="cod_vasa", settings={}, vasa=vasa)
        assert va.pk == vasa.pk

        client = Client()
        client.force_login(user=user)

        va_rows = _csv_rows(_post_export(client, data={"ids": str(va.pk)}))
        vasa_rows = _csv_rows(client.post(POST_URL, data={"vasa_ids": str(vasa.pk)}))

        assert [(row["source"], int(row["id"])) for row in va_rows] == [("VA", va.pk)]
        assert [(row["source"], int(row["id"])) for row in vasa_rows] == [
            ("VASA", vasa.pk)
        ]

    def test_json_va_export_count_includes_vasa_records(self, user: User):
        test_data = build_test_db()
        vasa = VASAFactory.create(
            location=test_data["facility_a"], Id10023="2020-06-01"
        )
        CauseOfDeath.objects.create(cause="cod_vasa", settings={}, vasa=vasa)
        client = Client()
        client.force_login(user=user)

        response = _post_export(client, data={"format": "json"})

        with zipfile.ZipFile(io.BytesIO(_response_content(response))) as archive:
            json_data = json.loads(archive.read(JSON_FILE_NAME))
        records = json.loads(json_data["records"])
        assert json_data["count"] == 5
        assert [record["source"] for record in records] == [
            "VA",
            "VA",
            "VA",
            "VA",
            "VASA",
        ]
        assert records[-1]["id"] == vasa.pk
        assert records[-1]["submitted_at"].startswith("2026-01-15T12:00:00")
        assert records[-1]["submissiondate"] == "2026-01-15T12:00:00Z"

    def test_va_export_log_identifies_submission_date_basis(self, user: User, caplog):
        client = Client()
        client.force_login(user=user)

        with caplog.at_level(logging.INFO, logger="va_explorer.va_export.views"):
            response = _post_export(client)

        assert response.status_code == 200
        _response_content(response)
        assert any("date_basis=submitted_at" in message for message in caplog.messages)

    def test_vasa_pii_is_redacted_and_provenance_is_not_exported(self):
        test_data = build_test_db()
        vasa = VASAFactory.create(
            location=test_data["facility_a"],
            Id10017="Private",
            Id10018="Person",
            Id10023="2020-06-01",
            geopoint="-15.4000 28.3000",
            raw_answers={"values": {"A4001": "Private"}},
        )
        client = Client()
        client.force_login(user=User.objects.get(name="no_pii"))

        response = _post_export(
            client,
            data={
                "format": "csv",
                "va_record_type": "full",
                "vasa_ids": str(vasa.pk),
            },
        )

        rows = _csv_rows(response)
        assert len(rows) == 1
        assert rows[0]["source"] == "VASA"
        assert rows[0]["Id10017"] == REDACTED_STRING
        assert rows[0]["Id10018"] == REDACTED_STRING
        assert rows[0]["geopoint"] == REDACTED_STRING
        assert "raw_answers" not in _csv_header(response)

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

        response = _post_export(
            c,
            data={"format": "csv", "locations": test_data["srs_district1"].pk},
        )
        assert response.status_code == 200

        try:
            f = io.BytesIO(_response_content(response))
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

        response = _post_export(c, data={"format": "csv", "ids": str(community_va.pk)})
        assert response.status_code == 200

        try:
            f = io.BytesIO(_response_content(response))
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

        response = _post_export(c, data={"format": "csv", "causes": cod_name})
        assert response.status_code == 200

        # Django 3.2 has response.headers. For now, we'll access them per below
        # See https://docs.djangoproject.com/en/4.0/ref/request-response/#django.http.HttpResponse.headers
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == f"attachment; filename={CSV_ZIP_FILE_NAME}"
        )

        try:
            f = io.BytesIO(_response_content(response))
            zipped_file = zipfile.ZipFile(f, "r")
            # Add one for the variable name header in the csv
            assert len(zipped_file.open(CSV_FILE_NAME).readlines()) == 3
        finally:
            zipped_file.close()
            f.close()

    def test_repeated_cause_and_location_values_are_all_applied(self, user: User):
        test_data = build_test_db()
        cause_ids = [
            CauseOfDeath.objects.filter(cause=cause)
            .values_list("pk", flat=True)
            .first()
            for cause in ("cod_a", "cod_b")
        ]
        client = Client()
        client.force_login(user=user)

        response = _post_export(
            client,
            data={
                "causes": cause_ids,
                "locations": [
                    test_data["srs_district1"].pk,
                    test_data["srs_district2"].pk,
                ],
            },
        )

        assert response.status_code == 200
        assert len(_csv_rows(response)) == 4

    def test_time_filter(self, user: User):
        build_test_db()
        start_date, end_date = "2020-01-01", "2021-01-01"

        c = Client()
        c.force_login(user=user)

        response = _post_export(
            c,
            data={"format": "csv", "start_date": start_date, "end_date": end_date},
        )
        assert response.status_code == 200

        try:
            f = io.BytesIO(_response_content(response))
            zipped_file = zipfile.ZipFile(f, "r")
            # Add one for the variable name header in the csv
            assert len(zipped_file.open(CSV_FILE_NAME).readlines()) == 2
        finally:
            zipped_file.close()
            f.close()

    def test_combined_filter_csv(self, user: User):
        test_data = build_test_db()
        # Download submissions from facility A after 1/1/2020 with COD_a.
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

        response = _post_export(c, data=query_data)
        assert response.status_code == 200

        # confirm correct number of VAs downloaded. The lower bound is local
        # midnight in the export timezone, not UTC midnight.
        db_ct = VerbalAutopsy.objects.filter(
            submitted_at__gte=_export_day_start(2020, 1, 1),
            causes__cause=cod_name,
            location__id=loc_a.pk,
        ).count()

        try:
            f = io.BytesIO(_response_content(response))
            zipped_file = zipfile.ZipFile(f, "r")
            # Add one for the variable name header in the csv
            assert len(zipped_file.open(CSV_FILE_NAME).readlines()) == db_ct + 1
        finally:
            zipped_file.close()
            f.close()

    def test_combined_filter_json(self, user: User):
        test_data = build_test_db()
        # Download submissions from facility A through 2020-01-01 with COD b in
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

        # confirm correct number of VAs downloaded. end_date is inclusive, so
        # the exclusive bound is local midnight starting the following day.
        db_ct = VerbalAutopsy.objects.filter(
            submitted_at__lt=_export_day_start(2020, 1, 2),
            causes__cause=cod_name,
            location__id=loc_a.pk,
        ).count()

        response = _post_export(c, data=query_data)
        assert response.status_code == 200

        # Django 3.2 has response.headers. For now, we'll access them per below
        # See https://docs.djangoproject.com/en/4.0/ref/request-response/#django.http.HttpResponse.headers
        assert response.headers["content-type"] == FILE_CONTENT_TYPE
        assert (
            response.headers["content-disposition"]
            == f"attachment; filename={JSON_ZIP_FILE_NAME}"
        )

        try:
            f = io.BytesIO(_response_content(response))
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

        response = _post_export(c, data={"format": "csv"})
        assert response.status_code == 200

        try:
            f = io.BytesIO(_response_content(response))
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
                "start_date": "1901-01-01",
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
                **DEFAULT_EXPORT_DATES,
            }
        )
        assert submitted_form.is_valid()

    def test_abridged_export_contains_only_profile_columns(self, user: User):
        build_test_db()
        c = Client()
        c.force_login(user=user)

        response = _post_export(c, data={"format": "csv", "va_record_type": "abridged"})
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
            _post_export(c, data={"format": "csv", "va_record_type": "abridged"})
        )
        full_header = _csv_header(
            _post_export(c, data={"format": "csv", "va_record_type": "full"})
        )

        # Id10007 is a full questionnaire/PII field outside the abridged profile.
        assert "Id10007" in full_header
        assert "Id10007" not in abridged_header

    def test_full_export_returns_wide_record(self, user: User):
        build_test_db()
        c = Client()
        c.force_login(user=user)

        response = _post_export(c, data={"format": "csv", "va_record_type": "full"})
        assert response.status_code == 200

        header = _csv_header(response)
        # Full export is the wide record and a superset of the abridged columns.
        assert len(header) > len(ABRIDGED_VA_EXPORT_COLUMNS)
        for col in ("id", "cause", "province"):
            assert col in header

        # Row count is unchanged from the legacy full-record behaviour.
        try:
            f = io.BytesIO(_response_content(response))
            zipped_file = zipfile.ZipFile(f, "r")
            assert len(zipped_file.open(CSV_FILE_NAME).readlines()) == 5
        finally:
            zipped_file.close()
            f.close()

    def test_filters_apply_to_both_record_types(self, user: User):
        build_test_db()
        c = Client()
        c.force_login(user=user)

        # A 2020 submission-date window matches exactly one fixture VA (va4).
        row_counts = {}
        for record_type in ("abridged", "full"):
            response = _post_export(
                c,
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

        response = _post_export(
            c,
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
            location=test_data["facility_a"],
            Id10023="2021-03-03",
            geopoint="-15.4000 28.3000",
        )

        c = Client()
        c.force_login(user=user)

        response = _post_export(
            c,
            data={"format": "csv", "va_record_type": "abridged", "ids": str(va.pk)},
        )
        assert response.status_code == 200

        rows = _csv_rows(response)
        assert len(rows) == 1
        # Missing CoD exports cleanly with blank cause cells.
        assert rows[0]["cause"] == ""
        assert rows[0]["cause_algorithm"] == ""
        assert rows[0]["geopoint"] == REDACTED_STRING

    def test_missing_record_type_defaults_to_full_and_invalid_value_is_rejected(
        self, user: User
    ):
        build_test_db()
        c = Client()
        c.force_login(user=user)

        # Omitted entirely -> full record (backward compatible).
        omitted_header = _csv_header(_post_export(c, data={"format": "csv"}))
        assert "Id10007" in omitted_header

        # An explicit invalid value is rejected rather than silently changing scope.
        invalid_response = _post_export(
            c, data={"format": "csv", "va_record_type": "garbage"}
        )
        assert invalid_response.status_code == 400
        assert "va_record_type" in invalid_response.json()["errors"]
