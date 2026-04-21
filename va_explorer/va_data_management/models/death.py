from django.db import models
from simple_history.models import HistoricalRecords
from .household_census import SRSClusterLocation

class Death(models.Model):
    
    # Persist the CSV key so we can dedupe reliably
    key = models.TextField(unique=True, db_index=True, null=True, blank=True)
    
    #new field  for event from cms. Null if va interview not scheduled
    eventid = models.TextField("Event ID", blank=True, null=True)

    submissiondate = models.TextField(blank=True, null=True)
    deviceid = models.TextField("Device ID", blank=True, null=True)
    today = models.TextField("Date Recorded", blank=True, null=True)
    start = models.TextField("Form Start Time", blank=True, null=True)
    province = models.TextField("[Select province]", blank=True, null=True)
    district = models.TextField("[Select district]", blank=True, null=True)
    constituency = models.TextField("[Select Constituency]", blank=True, null=True)
    ward = models.TextField("[Select Ward]", blank=True, null=True)
    ea = models.TextField("[Select Enumeration Area]", blank=True, null=True)
    supervisor = models.TextField("Select the name of your supervisor", blank=True, null=True)
    enumerator = models.TextField("Select your name", blank=True, null=True)
    # ADDING DEATH ID FIELD
    death_id = models.TextField("DEATH ID", blank=True, null=True)
    
    DE_01 = models.TextField("DE-01 Household ID", blank=True, null=True)
    consent = models.TextField("Did respondent give consent?", blank=True, null=True)
    DE_02 = models.TextField("DE-02 What is the name of the head of the household?", blank=True, null=True)
    
    DE_03 = models.TextField("DE-03 Name of the deceased", blank=True, null=True)
    DE_04 = models.TextField("DE-04 Date of Birth of the deceased", blank=True, null=True)
    DE_05 = models.TextField("DE-05 Sex of the deceased", blank=True, null=True)
    DE_06 = models.TextField("DE-06 Date of death of the deceased", blank=True, null=True)
    DE_07 = models.TextField("DE-07 Place of death", blank=True, null=True)
    DE_08 = models.TextField("DE-08  NUMBER for the Deceased", blank=True, null=True)
    DE_09 = models.TextField("DE-09 Nationality status?", blank=True, null=True)
    DE_10 = models.TextField("DE-10 What is ${DE-03}'s Social Security Number?", blank=True, null=True)
    DE_11 = models.TextField("DE-11 Marital status of the deceased", blank=True, null=True)
    DE_12 = models.TextField("DE-12 Occupation of the deceased", blank=True, null=True)
    DE_13 = models.TextField("DE-13 What was ${DE-03}'s highest level of education?", blank=True, null=True)
    DE_14 = models.TextField("DE-14 Briefly write a 10 word summary of circumstances around death (provide examples)", blank=True, null=True)
    informant = models.TextField("Who is providing the details?", blank=True, null=True)
    
    DE_15 = models.TextField("DE-15 What was the cause of death?", blank=True, null=True)
    DE_16 = models.TextField("DE-16 Did  ${DE-03} die Pregnant?", blank=True, null=True)
    DE_17 = models.TextField("DE-17 Did  ${DE-03} die during childbirth?", blank=True, null=True)
    DE_18 = models.TextField("DE-18 Did  ${DE-03} die within 42 days after the end of a Pregnancy or childbirth?", blank=True, null=True)
    DE_19 = models.TextField("DE-19 Name of Informant", blank=True, null=True)
    DE_20 = models.TextField("DE-20 DOB of Informant", blank=True, null=True)
    DE_21 = models.TextField("DE-21 Sex of Infomant", blank=True, null=True)
    DE_22 = models.TextField("DE-22 Residential Address of Informant", blank=True, null=True)
    DE_23 = models.TextField("DE-23 NRC Number", blank=True, null=True)
    DE_24 = models.TextField("DE-24 Nationality", blank=True, null=True)
    DE_25 = models.TextField("DE-25 What is your relationship to the deceased?", blank=True, null=True)
    DE_26 = models.TextField("DE-26 Please give me your telephone contact number", blank=True, null=True)
    DE_27 = models.TextField("DE-27 When is the appropriate day for the verbal autopsy interview?", blank=True, null=True)
    DE_28 = models.TextField("DE-28 Please provide me the name of the contact person", blank=True, null=True)
    DE_29 = models.TextField("DE-29 Please Provide a telephone contact number of the contact person", blank=True, null=True)
    DE_30 = models.TextField("DE-30 Did you register  ${DE-03}'s death with DNRPC ?", blank=True, null=True)
    DE_31 = models.TextField("DE-31 Do you know the procedure for death Registration and/or Certification as required by DNRPC ?", blank=True, null=True)
        
    DE_32_mccd = models.TextField("MCCD", blank=True, null=True)
    DE_32_coroners_report = models.TextField("CORONERS REPORT", blank=True, null=True)
    DE_32_burial_permit = models.TextField("BURIAL DERMIT", blank=True, null=True)
    DE_32_Original_nrc_of_the_deceased = models.TextField("ORIGINAL NRC OF THE DECEASED", blank=True, null=True)
    DE_32_police_report_loss_of_nrc = models.TextField("POLICE REPORT FOR LOSS OF NRC", blank=True, null=True)
    DE_32_affidavit_for_less_of_nrc = models.TextField("AFFIDAVIT FOR LOSS OF NRC", blank=True, null=True)
        
    DE_33_bid_form = models.TextField("BID form", blank=True, null=True)
    DE_33_letter_from_a_traditional_leader = models.TextField("LETTER FROM A TRADITIONAL LEADER", blank=True, null=True)
    DE_33_original_nrc = models.TextField("ORIGINAL NRC OF THE DECEASED", blank=True, null=True)
    DE_33_informant_nrc = models.TextField("CODY OF NRC FOR THE INFORMANT", blank=True, null=True)
    DE_33_police_report_loss_of_nrc = models.TextField("POLICE REPORT FOR LOSS OF NRC", blank=True, null=True)
    DE_33_affidavit_loss_of_nrc = models.TextField("AFFIDAVIT FOR LOSS OF NRC", blank=True, null=True)

    DE_34 = models.TextField("DE-34 Capture GPS Cordinates for the Household", blank=True, null=True)
    history = HistoricalRecords()

    class Meta:
        indexes = [
            # Optimise date-range filtering in analytics views
            models.Index(fields=["today"], name="death_today_idx"),
            models.Index(fields=["death_id"], name="death_id_idx"),
        ]

    def __str__(self):
        return f'{self.DE_03} - {self.DE_23} - {self.ward}'
