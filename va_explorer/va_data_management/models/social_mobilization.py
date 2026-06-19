from django.db import models
from simple_history.models import HistoricalRecords

from va_explorer.va_data_management.models.household_census import RepeatGroupQuerySet


class SocialMobilization(models.Model):
    """
    Model generated from the Social Mobilization SRS tally ODK form definition
    and its ODK Central CSV export, aligned in style with CSADailyTracker.
    """

    # Persist the ODK CSV key so we can dedupe reliably (maps to CSV column: KEY)
    key = models.TextField(unique=True, db_index=True, null=True, blank=True)

    # ODK Central / export metadata (from SOCIAL_MOBILIZATION CSV)
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
    end = models.TextField("Form End Time", blank=True, null=True)

    # Location (stored as text, mirroring CSADailyTracker)
    province = models.TextField("Province", blank=True, null=True)
    district = models.TextField("District", blank=True, null=True)
    health_facility = models.TextField("Health Facility", blank=True, null=True)
    village_neighbourhood = models.TextField(
        "Village / Neighbourhood", blank=True, null=True
    )

    # Extended location fields (new form)
    event_date = models.TextField("Event Date", blank=True, null=True)
    constituency = models.TextField("Constituency", blank=True, null=True)
    ward = models.TextField("Ward", blank=True, null=True)
    ea = models.TextField("Enumeration Area", blank=True, null=True)

    # GPS (split columns from ODK geopoint, new form)
    event_gps_latitude = models.TextField("GPS Latitude", blank=True, null=True)
    event_gps_longitude = models.TextField("GPS Longitude", blank=True, null=True)
    event_gps_altitude = models.TextField("GPS Altitude", blank=True, null=True)
    event_gps_accuracy = models.TextField("GPS Accuracy", blank=True, null=True)

    # Mobilizer / event metadata (new form)
    mobilizer_name = models.TextField("Mobilizer Name", blank=True, null=True)
    mobilizer_role = models.TextField("Mobilizer Role", blank=True, null=True)
    event_type = models.TextField("Event Type", blank=True, null=True)
    event_description = models.TextField("Event Description", blank=True, null=True)
    household_identifier = models.TextField(
        "Household Identifier", blank=True, null=True
    )

    # Daily tally fields
    day_number = models.TextField("Day Number", blank=True, null=True)
    households_visited = models.TextField("Households Visited", blank=True, null=True)
    people_over_15 = models.TextField("People Over 15", blank=True, null=True)
    children_5_14 = models.TextField("Children 5-14", blank=True, null=True)
    children_12_59months = models.TextField(
        "Children 12-59 Months", blank=True, null=True
    )
    refusal_identified = models.TextField("Refusals Identified", blank=True, null=True)
    refusal_managed = models.TextField("Refusals Managed", blank=True, null=True)
    schools_reached = models.TextField("Schools Reached", blank=True, null=True)
    religious_leaders_reached = models.TextField(
        "Religious Leaders Reached", blank=True, null=True
    )

    # Refusal / reluctance flags (yes/no, new form replaces numeric counts above)
    refusal_reluctance_identified = models.TextField(
        "Refusal / Reluctance Identified", blank=True, null=True
    )
    refusal_reluctance_managed = models.TextField(
        "Refusal / Reluctance Managed", blank=True, null=True
    )

    # Rumour tracking
    rumor_description = models.TextField("Rumour Description", blank=True, null=True)
    rumor_source = models.TextField("Rumour Source", blank=True, null=True)
    rumor_source_other = models.TextField(
        "Rumour Source (Other)", blank=True, null=True
    )
    children_affected = models.TextField(
        "Children Affected by Rumour", blank=True, null=True
    )
    refusals_from_rumor = models.TextField(
        "Refusals From Rumour", blank=True, null=True
    )

    # Community outreach channel fields (new form)
    community_group_name = models.TextField(
        "Community Group Name", blank=True, null=True
    )
    person_name = models.TextField("Person Name", blank=True, null=True)
    offline_channel_name = models.TextField(
        "Offline Channel Name", blank=True, null=True
    )
    online_page_group_name = models.TextField(
        "Online Page / Group Name", blank=True, null=True
    )

    history = HistoricalRecords()

    class Meta:
        indexes = [
            # Optimise date-range filtering in analytics views
            models.Index(fields=["today"], name="socmob_today_idx"),
        ]

    def __str__(self):
        # keep it lightweight + consistent
        return f"{self.today} - {self.village_neighbourhood} - {self.health_facility}"


class RumorLog(models.Model):
    """Each row is one rumour reported during a Social Mobilization event.

    Linked to the parent submission via SocialMobilization.key (the ODK KEY field),
    mirroring the PregnancyOutcomeBaby / HouseholdDeath repeat-group pattern.
    """

    social_mobilization = models.ForeignKey(
        SocialMobilization,
        to_field="key",
        db_column="parent_key",
        on_delete=models.CASCADE,
        related_name="rumor_logs",
    )
    key = models.CharField(
        max_length=200, unique=True, db_index=True, null=True, blank=True
    )

    rumor_description = models.TextField("Rumour Description", blank=True, null=True)
    rumor_source = models.TextField("Rumour Source", blank=True, null=True)
    community_group_name = models.TextField(
        "Community Group Name", blank=True, null=True
    )
    person_name = models.TextField("Person Name", blank=True, null=True)
    offline_channel_name = models.TextField(
        "Offline Channel Name", blank=True, null=True
    )
    online_page_group_name = models.TextField(
        "Online Page / Group Name", blank=True, null=True
    )

    is_duplicate = models.BooleanField(default=False, db_index=True)
    duplicate_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="duplicates",
    )

    objects = RepeatGroupQuerySet.as_manager()
    history = HistoricalRecords()

    def __str__(self):
        return (
            f"RumorLog({self.social_mobilization_id}) - {self.rumor_description or '—'}"
        )
