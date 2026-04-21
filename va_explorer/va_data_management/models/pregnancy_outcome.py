from django.db import models
from simple_history.models import HistoricalRecords

from .pregnancy import Pregnancy


class PregnancyOutcome(models.Model):
    # UUID-style primary key (string)
    key = models.CharField(max_length=100, unique=True, db_index=True)

    cluster = models.ForeignKey("SRSClusterLocation", on_delete=models.SET_NULL, null=True, blank=True)
    
    # Store pregnancy id as a text (or UUID) field, not as a foreign key
    pregnancy_id = models.TextField("Pregnancy ID", blank=True, null=True)

    submissiondate = models.TextField(blank=True, null=True)
    deviceid = models.TextField("nan", blank=True, null=True)
    today = models.TextField("nan", blank=True, null=True)
    start = models.TextField("nan", blank=True, null=True)
    province = models.TextField("[Select province]", blank=True, null=True)
    district = models.TextField("[Select district]", blank=True, null=True)
    constituency = models.TextField("[Select Constituency]", blank=True, null=True)
    ward = models.TextField("[Select Ward]", blank=True, null=True)
    ea = models.TextField("[Select Enumeration Area]", blank=True, null=True)
    supervisor = models.TextField("select the name of your supervisor", blank=True, null=True)
    enumerator = models.TextField("Select your name", blank=True, null=True)

    consent = models.TextField("Did respondent give consent?", blank=True, null=True)
    PO_01 = models.TextField("PO_01. Name of the respondent", blank=True, null=True)
    PO_02 = models.TextField("PO_02. Household", blank=True, null=True)
    PO_03 = models.TextField("PO_03. What is the name of the head of the household?", blank=True)
    po_group = models.TextField("Pregnancy Outcome", blank=True, null=True)

    PO_04 = models.TextField("PO_04. Name of the mother", blank=True, null=True)
    PO_05 = models.TextField("PO_05. What is her Date of Birth?", blank=True, null=True)
    PO_06 = models.TextField("PO_06. NRC NUMBER ", blank=True, null=True)
    PO_07 = models.TextField("PO_07. Nationality status?", blank=True, null=True)
    PO_08 = models.TextField("PO_08. What is her Social Security Number?", blank=True, null=True)
    PO_09 = models.TextField("PO_09. What is her current marital status?", blank=True, null=True)
    PO_10 = models.TextField("PO_10. What is her Occupation?", blank=True, null=True)
    PO_11 = models.TextField("PO_11. What is her highest level of education", blank=True)
    PO_11A = models.TextField("PO_11A. Does she have a phone number?", blank=True, null=True)
    PO_14 = models.TextField("PO_14. Please give me her Phone Number", blank=True, null=True)
    PO_15 = models.TextField("PO_15. Do you have an Antenatal card?", blank=True, null=True)

    PO_16 = models.TextField("PO_16. Please provide me with the Antenatal card", blank=True)
    PO_17 = models.TextField("PO_17. Record the Name of the Facility", blank=True, null=True)
    PO_18 = models.TextField("PO_18. Record the First Antenatal Visit", blank=True, null=True)
    PO_19 = models.TextField("PO_19. Record the Last  Antenatal Visits", blank=True, null=True)
    PO_20 = models.TextField("PO_20. Record the number  of Antenatal Visits", blank=True)
    PO_21 = models.TextField("PO_21. Record HIV Status", blank=True, null=True)
    PO_22 = models.TextField("PO_22. Record Treatment status", blank=True, null=True)

    PO_23 = models.TextField("PO_23. Name of the Father", blank=True, null=True)
    PO_24 = models.TextField("PO_24. What is his Date of Birth?", blank=True, null=True)
    PO_25 = models.TextField("PO_25. NRC NUMBER", blank=True, null=True)
    PO_26 = models.TextField("PO_26. Nationality Status", blank=True, null=True)
    PO_27 = models.TextField("PO_27. What is his Social Security Number?", blank=True, null=True)
    PO_28 = models.TextField("PO_28. What is his current marital status?", blank=True, null=True)
    PO_29 = models.TextField("PO_29. What is his Occupation?", blank=True, null=True)
    PO_30 = models.TextField("PO_30. What is his highest level of education", blank=True)
    PO_31 = models.TextField("PO_31. Does he have a phone number?", blank=True, null=True)
    PO_31A = models.TextField("PO_31A. Please give me his Phone Number", blank=True, null=True)
    
    informant = models.TextField("Who is Providing the details?", blank=True, null=True)
    PO_32 = models.TextField("PO_32. What is your name?", blank=True, null=True)
    PO_33 = models.TextField("PO_33. What is your Date of Birth?", blank=True, null=True)
    PO_34 = models.TextField("PO_34. Informant SEX", blank=True, null=True)
    PO_35 = models.TextField("PO_35. Informant Nationality", blank=True, null=True)
    PO_36 = models.TextField("PO_36. What is your NRC Number?", blank=True, null=True)
    PO_37 = models.TextField("PO_37. District", blank=True, null=True)
    PO_38 = models.TextField("PO_38. What is the name of the Chief/Chieftainess?", blank=True)
    PO_39 = models.TextField("PO_39. What is the residential address/village name?", blank=True)
    PO_40 = models.TextField("PO_40. What is your relationship to the child?", blank=True)
    PO_41 = models.TextField("PO_41. Date of delivery or when pregnancy ended", blank=True)
    PO_42 = models.TextField("PO_42. What was the type of delivery?", blank=True, null=True)
    PO_43 = models.TextField("PO_43. Where was the place of birth?", blank=True, null=True)
    PO_44 = models.TextField("PO_44. How long did this pregnancy last in Weeks or Months?", blank=True)
    PO_44A = models.TextField("PO_44A. Enter number.", blank=True, null=True)
    PO_45 = models.TextField("PO_45. Was this a multiple birth?", blank=True, null=True)
    PO_46 = models.TextField("PO_46. Was the baby born alive, born dead or lost before birth?", blank=True)
    PO_47 = models.TextField("PO_47. How many were born alive (cry, move, or breathe)?", blank=True)
    PO_47A = models.TextField("PO_47A. How many were born dead(stillbirth)?", blank=True)
    PO_47B = models.TextField("PO_47B. Did the baby cry, move, or breathe?", blank=True, null=True)
    PO_48 = models.TextField("PO_48. What was the type of still  birth?", blank=True, null=True)
    num_children = models.TextField("nan", blank=True, null=True)

    PO_51A_01 = models.TextField("PO_51A_01. Record of birth", blank=True, null=True)
    PO_51A_02 = models.TextField("PO_51A_02. Mother's NRC (Front)", blank=True, null=True)
    PO_51A_02A = models.TextField("PO_51A_02. Mother's NRC (Back)", blank=True, null=True)
    PO_51A_03 = models.TextField("PO_51A_03. Father's NRC (Front)", blank=True, null=True)
    PO_51A_03A = models.TextField("PO_51A_03. Father's NRC (Back)", blank=True, null=True)
    PO_51A_04 = models.TextField("PO_51A_04. Informant's NRC (Front)", blank=True, null=True)
    PO_51A_04A = models.TextField("PO_51A_04. Informant's NRC (Back)", blank=True, null=True)
    PO_51A_05 = models.TextField("PO_51A_05. Under 5 if no 06. Record of Birth", blank=True)
    PO_51A_06 = models.TextField("PO_51A_06. Completed Birth Notification form", blank=True)

    PO_51B_01 = models.TextField("PO_51B_01. Affidavit of independent witness ", blank=True)
    PO_51B_02 = models.TextField("PO_51B_02. Record of birth", blank=True, null=True)
    PO_51B_03 = models.TextField("PO_51B_03. Mother's NRC (Front)", blank=True, null=True)
    PO_51B_03A = models.TextField("PO_51B_03. Mother's NRC (Back)", blank=True, null=True)
    PO_51B_04 = models.TextField("PO_51B_04. Father's NRC (Front)", blank=True, null=True)
    PO_51B_04A = models.TextField("PO_51B_04. Father's NRC (Back)", blank=True, null=True)
    PO_51B_05 = models.TextField("PO_51B_05. Informant's NRC (Front)", blank=True, null=True)
    PO_51B_05A = models.TextField("PO_51B_05. Informant's NRC (Back)", blank=True, null=True)
    PO_51B_06 = models.TextField("PO_51B_06. Under 5 if no record of birth", blank=True, null=True)
    PO_51B_07 = models.TextField("PO_51B_07. Record of Birth", blank=True, null=True)
    PO_51B_08 = models.TextField("PO_51B_08. Completed Birth Notification form", blank=True)
    gps = models.TextField("Capture GPS Cordinates", blank=True, null=True)
    end = models.TextField("nan", blank=True, null=True)
    history = HistoricalRecords()

    class Meta:
        indexes = [
            # Optimise date-range filtering in analytics views
            models.Index(fields=["today"], name="pregnancy_outcome_today_idx"),
            # Delivery date used for outcome reporting
            models.Index(fields=["PO_41"], name="pregnancy_outcome_delivery_idx"),
        ]

class PregnancyOutcomeBaby(models.Model):
    pregnancy_outcome = models.ForeignKey(
        PregnancyOutcome,        
        to_field='key',
        db_column='parent_key',
        on_delete=models.CASCADE,
        related_name="babies"
    )

    PO_49A = models.TextField("PO_49A. Identification of baby", blank=True, null=True)
    PO_49B = models.TextField("PO_49B. Full name", blank=True, null=True)
    PO_49C = models.TextField("PO_49C. Sex of the child", blank=True, null=True)
    PO_49D = models.TextField("PO_49D. Birth weight (Kilograms)", blank=True, null=True)
    PO_49E = models.TextField("PO_49E. Was this Birth Registered with the Department of National Registration, Passports and Citizenship (DNRPC)?", blank=True)
    PO_50 = models.TextField("PO_50. Do you know the procedure for death Registration and/or Certification as required by DNRPC ?", blank=True)

    history = HistoricalRecords()

    def __str__(self):
        return f"Baby of {self.pregnancy_outcome.key} - {self.po_49b or 'Unnamed'}"