from django.db import models
from simple_history.models import HistoricalRecords


class Pregnancy(models.Model):

    # Persist the CSV key so we can dedupe reliably
    key = models.TextField(unique=True, db_index=True, null=True, blank=True)

    submissiondate = models.TextField(blank=True, null=True)
    deviceid = models.TextField(blank=True)
    today = models.TextField(blank=True)
    start = models.TextField(blank=True)
    province = models.TextField("Select province", blank=True, null=True)
    district = models.TextField("Select district", blank=True, null=True)
    constituency = models.TextField("Select Constituency", blank=True, null=True)
    ward = models.TextField("Select Ward", blank=True, null=True)
    ea = models.TextField("Select Enumeration Area", blank=True, null=True)
    supervisor = models.TextField("Select your Supervisor name", blank=True, null=True)
    enumerator = models.TextField("Select your name", blank=True, null=True)
    PE_02 = models.TextField("PE_02. Name of the respondent", blank=True, null=True)

    PE_03 = models.TextField(
        "PE_03. Did respondent give consent?", blank=True, null=True
    )
    consented = models.TextField("Pregnancy Notification", blank=True, null=True)
    PE_04 = models.TextField("PE_04. Household", blank=True, null=True)
    PE_05 = models.TextField(
        "PE_05. What is the name of the head of this household?", blank=True, null=True
    )
    PE_06 = models.TextField("PE_06. Name of Pregnant woman", blank=True, null=True)
    PE_07 = models.TextField("PE_07. What is her Date of Birth?", blank=True, null=True)
    PE_07A = models.IntegerField(
        "PE_07. How old was she at her last birthday?", blank=True, null=True
    )
    PE_08 = models.TextField(
        "PE_08. What is her current marital status?", blank=True, null=True
    )

    PE_09 = models.TextField(
        "PE_09. Do you know her date of  last menstrual period?", blank=True, null=True
    )
    PE_09A = models.TextField(
        "PE_09A. When was her last menstrual period?", blank=True, null=True
    )
    PE_10 = models.TextField(
        "PE_10. Do you know her expected date of delivery?", blank=True, null=True
    )
    PE_10A = models.TextField(
        "PE_10A. When is  she  expected date of delivery (EDD)?", blank=True, null=True
    )
    PE_11 = models.TextField(
        "PE_11. Does  she have any of the following medical conditions?",
        blank=True,
        null=True,
    )
    PE_11_other = models.TextField(
        "PE_11. Specify other medical conditions.", blank=True, null=True
    )
    PE_12 = models.TextField(
        "PE_12. Has she  had any complications in her previous pregnancies?",
        blank=True,
        null=True,
    )
    PE_12A = models.TextField(
        "PE_12. Has  she had any of the following complications in her previous pregnancies?",
        blank=True,
    )
    PE_12_other = models.TextField(
        "PE_12. Specify other medical conditions.", blank=True, null=True
    )
    PE_13 = models.TextField(
        "PE_13. Has she  been diagnosed with any complications during this pregnancy?",
        blank=True,
    )
    PE_13_specify = models.TextField(
        "PE_13. Specify Complications", blank=True, null=True
    )
    PE_14 = models.TextField(
        "PE_14. Is there a history of genetic conditions or pregnancy complications in your family?",
        blank=True,
    )
    PE_14_specify = models.TextField(
        "PE_14. Specify histrory of genetic conditions", blank=True, null=True
    )
    PE_15 = models.TextField(
        "PE_15. Does she 'use any form of tobacco (smoked or smokeless)?",
        blank=True,
        null=True,
    )
    PE_16 = models.TextField("PE_16. Does she consume alcohol?", blank=True, null=True)
    PE_17 = models.TextField(
        "PE_17. How frequent  does she consume alcohol?", blank=True, null=True
    )
    PE_18 = models.TextField(
        "PE_18. Do you have an Antenatal card?", blank=True, null=True
    )
    antenatal_card_details = models.TextField(
        "Atenatal Card Details", blank=True, null=True
    )

    PE_20 = models.TextField(
        "PE_20. Record the Name of the Facility", blank=True, null=True
    )
    PE_21 = models.TextField(
        "PE_21. Record the First Antenatal Visit", blank=True, null=True
    )
    PE_22 = models.IntegerField(
        "PE_22. Record number of Antenatal Visits", blank=True, null=True
    )
    PE_23 = models.TextField("PE_23. Record HIV Status", blank=True, null=True)
    PE_24 = models.TextField("PE_24. Record Treatment status", blank=True, null=True)
    PE_25 = models.TextField(
        "PE_25. Capture GPS Cordinates for the Household", blank=True, null=True
    )
    end = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    class Meta:
        indexes = [
            # Optimise date-range filtering in analytics views
            models.Index(fields=["today"], name="pregnancy_today_idx"),
            models.Index(fields=["created"], name="pregnancy_created_idx"),
            # Submission date used for trend series grouping
            models.Index(fields=["submissiondate"], name="pregnancy_submission_idx"),
        ]

    def __str__(self):
        return f"{self.enumerator} - {self.PE_06} - {self.ward}"
