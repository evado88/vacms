from django.db import models
from simple_history.models import HistoricalRecords

from va_explorer.va_data_management.models.mixins import (
    SUBMISSIONDATE_THEN_TODAY,
    SubmissionTimestampMixin,
)


class CSADailyTracker(SubmissionTimestampMixin):
    """
    Model generated from CSA_DAILY_TRACKER.xlsx (ODK form definition)
    and CSA_DAILY_TRACKER.csv (ODK Central export), aligned in style with Death.
    """

    # Matches Pregnancy/PregnancyOutcome: fall back to the form's own date when
    # a submission arrives without ``submissiondate``.
    SUBMISSION_TIMESTAMP_SOURCES = SUBMISSIONDATE_THEN_TODAY

    # Persist the ODK CSV key so we can dedupe reliably (maps to CSV column: KEY)
    key = models.TextField(unique=True, db_index=True, null=True, blank=True)

    # ODK Central / export metadata (from CSA_DAILY_TRACKER.csv)
    submissiondate = models.TextField(
        "Submission Date", blank=True, null=True
    )  # SubmissionDate
    instanceid = models.TextField("Instance ID", blank=True, null=True)  # instanceID

    instancename = models.TextField(
        "Instance Name", blank=True, null=True
    )  # instanceName

    submitterid = models.TextField("Submitter ID", blank=True, null=True)  # SubmitterID
    submittername = models.TextField(
        "Submitter Name", blank=True, null=True
    )  # SubmitterName

    attachmentspresent = models.TextField(
        "Attachments Present", blank=True, null=True
    )  # AttachmentsPresent
    attachmentsexpected = models.TextField(
        "Attachments Expected", blank=True, null=True
    )  # AttachmentsExpected
    status = models.TextField("Status", blank=True, null=True)  # Status
    reviewstate = models.TextField("Review State", blank=True, null=True)  # ReviewState
    edits = models.TextField("Edits", blank=True, null=True)  # Edits
    formversion = models.TextField("Form Version", blank=True, null=True)  # FormVersion

    # ODK device fields (note: export includes both deviceid and DeviceID)
    deviceid = models.TextField("Device ID", blank=True, null=True)  # deviceid (survey)
    DeviceID = models.TextField(
        "Device ID (ODK Central)", blank=True, null=True
    )  # DeviceID (export)

    # Core form fields (survey)
    today = models.TextField("Date Recorded", blank=True, null=True)
    start = models.TextField("Form Start Time", blank=True, null=True)

    province = models.TextField("[Select province]", blank=True, null=True)
    district = models.TextField("[Select district]", blank=True, null=True)
    constituency = models.TextField("[Select Constituency]", blank=True, null=True)
    ward = models.TextField("[Select Ward]", blank=True, null=True)
    ea = models.TextField("[Select Enumeration Area]", blank=True, null=True)

    enumerator = models.TextField("Select your name", blank=True, null=True)

    # geopoint (ODK Central export splits geopoint into components)
    hh_gps_latitude = models.TextField(
        "Household GPS Latitude", blank=True, null=True
    )  # hh_gps-Latitude
    hh_gps_longitude = models.TextField(
        "Household GPS Longitude", blank=True, null=True
    )  # hh_gps-Longitude
    hh_gps_altitude = models.TextField(
        "Household GPS Altitude", blank=True, null=True
    )  # hh_gps-Altitude
    hh_gps_accuracy = models.TextField(
        "Household GPS Accuracy", blank=True, null=True
    )  # hh_gps-Accuracy

    visit_status = models.TextField("TR-02. Visit Status", blank=True, null=True)
    ref_reason = models.TextField("TR-03. Reason for Refusal", blank=True, null=True)
    HH_22F = models.TextField(
        "TR-03A. Other (Specify)", blank=True, null=True
    )  # from name "HH-22F"

    hn = models.TextField("TR.04. What is your name?", blank=True, null=True)
    hh = models.TextField(
        "TR.05. What is the full name of the head of household?", blank=True, null=True
    )

    preg = models.TextField(
        "TR-06. Since last visit has there been a pregnancy?", blank=True, null=True
    )
    num_preg = models.TextField("TR-07. How many pregnancies", blank=True, null=True)

    preg_outcome = models.TextField(
        "TR-08. Since last visit has there been a birth?", blank=True, null=True
    )
    num_outcome = models.TextField("TR-09. How many births", blank=True, null=True)

    death = models.TextField(
        "TR-10. Since last visit has there been a death?", blank=True, null=True
    )
    num_death = models.TextField("TR-11. How many deaths?", blank=True, null=True)

    rum = models.TextField("TR-12. Any rumours or concerns?", blank=True, null=True)
    sorce_rum = models.TextField("TR-13. Source of rumour/s", blank=True, null=True)
    other_rum = models.TextField("TR-13A. Other (Specify)", blank=True, null=True)
    desci = models.TextField("TR-14 Description of the rumour/s", blank=True, null=True)
    ru_num = models.TextField(
        "TR-15. How many people affected by the rumour?", blank=True, null=True
    )

    # Notes / end (exported columns exist even though they are ODK notes/end)
    note_preg = models.TextField(blank=True, null=True)
    note_outcome = models.TextField(blank=True, null=True)
    note_death = models.TextField(blank=True, null=True)
    end = models.TextField(blank=True, null=True)

    history = HistoricalRecords()

    class Meta:
        indexes = [
            # The rumour-surveillance export filters this column.
            models.Index(
                fields=["submitted_at"],
                name="csa_tracker_submitted_at_idx",
                condition=models.Q(submitted_at__isnull=False),
            ),
        ]

    def __str__(self):
        # keep it lightweight + consistent
        return f"{self.today} - {self.ea} - {self.enumerator}"
