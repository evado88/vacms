from django.contrib.contenttypes.fields import GenericRelation
from django.db import models
from simple_history.models import HistoricalRecords
from treebeard.mp_tree import MP_Node

from va_explorer.va_data_management.models.data_quality import DataQualityIssueMember


class RepeatGroupQuerySet(models.QuerySet):
    """Shared queryset for ODK repeat-group child records (members, deaths, babies).

    The default ``objects`` manager still returns every row so loading and
    duplicate detection see all records; ``.canonical()`` is the explicit
    opt-in used at count/display sites so each duplicate set counts once.
    """

    def canonical(self):
        return self.filter(is_duplicate=False)

    def duplicates(self):
        return self.filter(is_duplicate=True)


class SRSClusterLocation(MP_Node):
    name = models.TextField()
    location_type = models.TextField()  # e.g., "province", "district", etc.
    code = models.TextField(blank=True, null=True)  # for EA or cluster codes
    status = models.TextField(blank=True, null=True)  # e.g., "Active"
    node_order_by = ["name"]

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.location_type})"

    def get_descendant_ids(self):
        return [descendant.id for descendant in self.get_descendants()]

    def parent_id(self):
        return self.get_parent().id if self.get_parent() else None


class Household(models.Model):
    # UUID-style primary key (string)
    key = models.CharField(max_length=100, unique=True, db_index=True)

    cluster = models.ForeignKey(
        "SRSClusterLocation", on_delete=models.SET_NULL, null=True, blank=True
    )

    dq_members = GenericRelation(
        DataQualityIssueMember,
        content_type_field="content_type",
        object_id_field="object_id",
        related_query_name="household",
    )

    # Metadata and geo/admin data
    submissiondate = models.TextField(blank=True, null=True)
    deviceid = models.TextField("nan", blank=True, null=True)
    today = models.TextField("nan", blank=True, null=True)
    start = models.TextField("nan", blank=True, null=True)
    end = models.TextField("nan", blank=True, null=True)
    province = models.TextField("Province", blank=True, null=True)
    district = models.TextField("District", blank=True, null=True)
    constituency = models.TextField("Constituency", blank=True, null=True)
    ward = models.TextField("Ward", blank=True, null=True)
    rural_urban = models.TextField("Rural/urban", blank=True, null=True)
    ea = models.TextField("EA", blank=True, null=True)
    visits = models.TextField("INTERVIEWER VISITS", blank=True, null=True)
    next_visit_date = models.TextField("Next Visit Date", blank=True, null=True)
    result_list = models.TextField("Result", blank=True, null=True)
    hh_gps = models.TextField("GPS Cordinates for the Household", blank=True, null=True)
    submit_time = models.TextField("nan", blank=True, null=True)

    # Household identifiers and descriptors
    code_input = models.TextField("Survey building number (SBN)", blank=True, null=True)
    household = models.TextField("Is there a household?", blank=True, null=True)
    description = models.TextField("Enter building description", blank=True, null=True)
    household_descriptors = models.TextField("nan", blank=True, null=True)
    hun = models.TextField("Housing Unit Number (HUN)", blank=True, null=True)
    hhn = models.TextField("Household  Number (HHN)", blank=True, null=True)
    household_type = models.TextField("Type of household", blank=True, null=True)
    locality_name = models.TextField(
        "What is the village or locality name?", blank=True, null=True
    )
    address = models.TextField(
        "What is the residential address/ village?", blank=True, null=True
    )
    name_of_chief = models.TextField(
        "What is the name of the chief/chieftainess?", blank=True, null=True
    )
    respondent = models.TextField("Respondent's Name", blank=True, null=True)
    result_other = models.TextField("Other result (Specify)", blank=True, null=True)

    # PERSONNEL
    enumerator = models.TextField("Enumerator", blank=True, null=True)
    supervisor = models.TextField("Supervisor", blank=True, null=True)
    consent = models.TextField("Did respondent give consent?", blank=True, null=True)

    # Household details
    HH_01 = models.TextField("HH-01. Name of Head of Household ", blank=True, null=True)
    HH_02 = models.TextField(
        "HH-02. Please give me the number or persons who usually live in this household and the visitors who stayed here last night",
        blank=True,
        null=True,
    )

    # Housing and asset indicators
    HH_16 = models.TextField("HH-16. TYPE OF HOUSING", blank=True, null=True)
    HH_16A = models.TextField("HH-61A. Other (Specify)", blank=True, null=True)
    HH_17 = models.TextField(
        "HH-17. What is the main type of material used for the roof?",
        blank=True,
        null=True,
    )
    HH_17A = models.TextField("HH-17A.Other (Specify)", blank=True, null=True)
    HH_18 = models.TextField(
        "HH-18. What are the walls of this housing unit mainly ", blank=True, null=True
    )
    HH_18A = models.TextField("HH-18A. Other (Specify)", blank=True, null=True)
    HH_19 = models.TextField(
        "HH-19. What is the floor of this housing unit mainly", blank=True, null=True
    )
    HH_19A = models.TextField("HH-19A. Other (Specify)", blank=True, null=True)
    HH_20 = models.TextField(
        "HH-20. Is this housing unit occupied by one or more households?",
        blank=True,
        null=True,
    )
    HH_21 = models.TextField(
        "HH-21. How many households occupy this housing unit?", blank=True, null=True
    )
    HH_22 = models.TextField(
        "HH-22. What is the main source of water for drinking for this household?",
        blank=True,
    )
    HH_22A = models.TextField(
        "HH-22A. In the last month, has there been any time when your household did not have sufficient quantities of drinking water when needed?",
        blank=True,
        null=True,
    )
    HH_22B = models.TextField(
        "HH-22B. In the past two weeks, was the water from your main source not available for at least one full day?",
        blank=True,
        null=True,
    )
    HH_22C = models.TextField(
        "HH-22C. Do you do anything to make the water safer to drink?",
        blank=True,
        null=True,
    )
    HH_22D = models.TextField(
        "HH-22D. What do you usually do to make the water safer to drink?",
        blank=True,
        null=True,
    )
    HH_22E = models.TextField(
        "HH-22E. How do you store your drinking water? ", blank=True, null=True
    )
    HH_22F = models.TextField("HH-22F. Other (Specify)", blank=True, null=True)
    HH_23 = models.TextField(
        "HH-23. What is the main source of water supply for other purposes such as cooking and handwashing? ",
        blank=True,
        null=True,
    )
    HH_24 = models.TextField(
        "HH-24. How many rooms does this housing unit have excluding passage ways, verandas, lobbies, bathrooms and toilet rooms?",
        blank=True,
        null=True,
    )
    HH_25 = models.TextField(
        "HH-25. How many living rooms and bedrooms does this housing unit have?",
        blank=True,
        null=True,
    )
    HH_26 = models.TextField(
        "HH-26. How many rooms are used for sleeping in this housing unit?",
        blank=True,
        null=True,
    )
    HH_27 = models.TextField(
        "HH-27. How many persons usually sleep in the housing unit, including those that are not part of the household?",
        blank=True,
        null=True,
    )
    HH_28 = models.TextField(
        "HH-28. Does this housing unit have a bathoom?", blank=True, null=True
    )
    HH_29 = models.TextField(
        "HH-29. What is the main type of toilet used by members of this household?",
        blank=True,
        null=True,
    )
    HH_29A = models.TextField("HH-29A. Other (Specify)", blank=True, null=True)
    HH_30 = models.TextField(
        "HH-30. Is this toilet inside or outside the housing unit? ",
        blank=True,
        null=True,
    )
    HH_31 = models.TextField(
        "HH-31. Is this toilet exclusively used by members of this household?",
        blank=True,
        null=True,
    )
    HH_31A = models.TextField(
        "31_31A. Including your own household, how many households use this toilet facility?",
        blank=True,
        null=True,
    )
    HH_32 = models.TextField(
        "HH-32. Does this housing unit have a kitchen?", blank=True, null=True
    )
    HH_33 = models.TextField(
        "HH-33. What is the main source of energy for lighting for this household?",
        blank=True,
        null=True,
    )
    HH_33A = models.TextField("HH-33A. Other (Specify)", blank=True, null=True)
    HH_34 = models.TextField(
        "HH-34. What is the main source of energy for cooking for this household?",
        blank=True,
        null=True,
    )
    HH_34A = models.TextField("HH-34A. Other (Specify)", blank=True, null=True)
    HH_35 = models.TextField(
        "HH-35. What is the main source of energy for heating for this household?",
        blank=True,
        null=True,
    )
    HH_35A = models.TextField("HH-35A. Other (Specify)", blank=True, null=True)

    HH_36_group = models.TextField(
        "HH-36. Does your household have/own any of the following?",
        blank=True,
        null=True,
    )
    HH_36_Bed = models.TextField("Bed", blank=True, null=True)
    HH_36_Blanket = models.TextField("Blanket", blank=True, null=True)
    HH_36_Table = models.TextField("Table", blank=True, null=True)
    HH_36_Sofa = models.TextField("Sofa", blank=True, null=True)
    HH_36_Stove = models.TextField("Stove", blank=True, null=True)
    HH_36_Fridge = models.TextField("Fridge", blank=True, null=True)
    HH_36_Decorder = models.TextField("Decorder", blank=True, null=True)
    HH_36_Radio = models.TextField("Radio", blank=True, null=True)
    HH_36_Non_smart_Television = models.TextField(
        "Non_smart Television", blank=True, null=True
    )
    HH_36_Smart_Television = models.TextField("Smart Television", blank=True, null=True)
    HH_36_Desktop_Laptop_Computer = models.TextField(
        "Desktop/Laptop Computer", blank=True, null=True
    )
    HH_36_Landline = models.TextField("Landline", blank=True, null=True)
    HH_36_Access_to_internet_away_from_home = models.TextField(
        "Access to internet away from home", blank=True, null=True
    )
    HH_36_Access_to_internet_at_Home = models.TextField(
        "Access to internet at Home", blank=True, null=True
    )
    HH_36_Generator = models.TextField("Generator", blank=True, null=True)
    HH_36_Wheelbarrow = models.TextField("Wheelbarrow", blank=True, null=True)
    HH_36_Bicycle = models.TextField("Bicycle", blank=True, null=True)
    HH_36_Motor_Vehicle = models.TextField("Motor Vehicle", blank=True, null=True)
    HH_36_Motorcycle = models.TextField("Motorcycle", blank=True, null=True)
    HH_36_Scotch_Cart = models.TextField("Scotch Cart", blank=True, null=True)
    HH_36_Motorised_Boat = models.TextField("Motorised Boat", blank=True, null=True)
    HH_36_Non_motorised_Boat = models.TextField(
        "Non_motorised Boat", blank=True, null=True
    )
    HH_36_Fishing_net = models.TextField("Fishing net", blank=True, null=True)
    HH_36_Grain_Grinder = models.TextField("Grain Grinder", blank=True, null=True)
    HH_36_Hoe = models.TextField("Hoe", blank=True, null=True)
    HH_36_Plough = models.TextField("Plough", blank=True, null=True)
    HH_36_Tractor = models.TextField("Tractor", blank=True, null=True)
    HH_36_Hammer_Mill = models.TextField("Hammer Mill", blank=True, null=True)
    HH_36_Agricultural_Land = models.TextField(
        "Agricultural Land", blank=True, null=True
    )

    HH_37 = models.TextField(
        "HH-37. Does this household own any livestock, herds, other farm animals, or poultry?",
        blank=True,
        null=True,
    )
    HH_37A_animals_group = models.TextField(
        "HH_37A. How many of the following animals does this household own?",
        blank=True,
        null=True,
    )
    HH_37A_traditional_cattle = models.TextField(
        "Traditional Cattle", blank=True, null=True
    )
    HH_37A_diary_cattle = models.TextField("Dairy Cattle", blank=True, null=True)
    HH_37A_horses_donkeys_mule = models.TextField(
        "Hourses/Donkeys/Mule", blank=True, null=True
    )
    HH_37A_beef_cattle = models.TextField("Beef Cattle", blank=True, null=True)
    HH_37A_goats = models.TextField("Goats", blank=True, null=True)
    HH_37A_sheeps = models.TextField("Sheep", blank=True, null=True)
    HH_37A_chicken = models.TextField("Chicken", blank=True, null=True)
    HH_37A_pigs = models.TextField("Pigs", blank=True, null=True)
    HH_37A_rabbits_other_poultry = models.TextField(
        "Rabbits/Other poultry", blank=True, null=True
    )
    HH_38 = models.TextField(
        "HH-38. How is the household refuse (garbage) disposed?", blank=True, null=True
    )
    HH_38A = models.TextField("HH-38A. Other (Specify)", blank=True, null=True)
    HH_39 = models.TextField(
        "HH-39. Is this housing unit owned by any member of this household?",
        blank=True,
        null=True,
    )
    HH_40 = models.TextField(
        "HH-40. How was this housing unit acquired?", blank=True, null=True
    )
    HH_41 = models.TextField(
        "HH-41. Is this housing unit provided free by the employer,friend or relative of any menber of this household? ",
        blank=True,
        null=True,
    )
    HH_42 = models.TextField(
        "HH-42. Is this housing unit rented from the employer of any member of this household?",
        blank=True,
        null=True,
    )
    HH_43 = models.TextField(
        "HH-43. Who is the employer that provides the housing unit",
        blank=True,
        null=True,
    )
    HH_44 = models.TextField(
        "HH-44. From whom is this housing unit rented? ", blank=True, null=True
    )
    HH_45 = models.TextField(
        "HH-45. How long does your household intend to stay in this area?",
        blank=True,
        null=True,
    )
    HH_46 = models.TextField(
        "HH-46. Has there been a pregnancy in this household from January 2025 to date?",
        blank=True,
        null=True,
    )
    HH_46A = models.TextField("HH-46A. How many pregnancies?", blank=True, null=True)
    HH_47 = models.TextField(
        "HH-47. Has there been a live birth or still birth in this household from January 2025 to date?",
        blank=True,
        null=True,
    )
    HH_47A = models.TextField("HH-47A. How many births?", blank=True, null=True)
    HH_48 = models.TextField(
        "HH-48. Has there been a death in this household from January 2025 to date?",
        blank=True,
        null=True,
    )
    death_size = models.TextField(
        "HH-48A. Total number of deaths?", blank=True, null=True
    )
    HH_15 = models.TextField(
        "HH-15. How how long do you intend to stay in this area? Or at this householouhsehold?",
        blank=True,
        null=True,
    )

    history = HistoricalRecords()

    def __str__(self):
        return f"Household ({self.province}-{self.district}-{self.ward}-{self.hhn}-{self.hhn})"


class HouseholdMember(models.Model):
    household = models.ForeignKey(
        Household,
        to_field="key",
        db_column="parent_key",
        on_delete=models.CASCADE,
        related_name="members",
    )

    key = models.CharField(
        max_length=200, unique=True, db_index=True, null=True, blank=True
    )
    HH_03 = models.TextField("HH-03. Household Member Details", blank=True, null=True)
    first_name = models.TextField("First name", blank=True, null=True)
    last_name = models.TextField("Last name", blank=True, null=True)
    fullname = models.TextField("nan", blank=True, null=True)
    HH_04 = models.TextField(
        "HH-04. What is the relationship to the head of the household? ",
        blank=True,
        null=True,
    )
    HH_05 = models.TextField("HH-05. Is member male or Female?", blank=True, null=True)
    HH_06 = models.TextField(
        "HH-06. Indicte his/her Membership Status", blank=True, null=True
    )
    HH_07 = models.TextField(
        "HH-07. What is his/her Date of Birth?", blank=True, null=True
    )
    HH_08 = models.TextField(
        "HH_08. How old was he/she at the last birthday?", blank=True
    )
    age_check = models.TextField("Calculated age (from DOB)", blank=True, null=True)
    HH_09 = models.TextField(
        "HH_09. What is his/her current marital status?", blank=True
    )
    HH_10 = models.TextField(
        "HH_10. How old was he/she when he/she first got married/started cohabiting/living together?",
        blank=True,
    )
    HH_11 = models.TextField(
        "HH-11. What is his/her Nationality status", blank=True, null=True
    )
    HH_12 = models.TextField(
        "HH_12. Does he/she have any of the following medical conditions?", blank=True
    )
    HH_13 = models.TextField(
        "HH_13. In the past two weeks, has he/she experienced any of the following Infectious Health Conditions?",
        blank=True,
    )
    HH_14 = models.TextField(
        "HH-14. Is he/she currently pregnant?", blank=True, null=True
    )

    is_duplicate = models.BooleanField(default=False, db_index=True)
    duplicate_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="duplicates",
    )
    dq_members = GenericRelation(
        DataQualityIssueMember,
        content_type_field="content_type",
        object_id_field="object_id",
        related_query_name="household_member",
    )

    objects = RepeatGroupQuerySet.as_manager()

    def __str__(self):
        return f"({self.household.hhn}){self.first_name} {self.last_name}"


class HouseholdDeath(models.Model):
    household = models.ForeignKey(
        Household,
        to_field="key",
        db_column="parent_key",
        on_delete=models.CASCADE,
        related_name="deaths",
    )
    key = models.CharField(
        max_length=200, unique=True, db_index=True, null=True, blank=True
    )
    deathname = models.TextField("First name of deceased", blank=True, null=True)
    sirname = models.TextField("Surname of deceased", blank=True, null=True)
    nickdeath = models.TextField(
        "Other known name (nickname) of deceased", blank=True, null=True
    )
    HH_48E = models.TextField(
        "HH-48E. Date of birth of deceased", blank=True, null=True
    )
    age = models.TextField("Age of deceased (calculated)", blank=True, null=True)
    HH_48F = models.TextField("HH-48F. Sex of deceased", blank=True, null=True)
    HH_48G = models.TextField("HH-48G. Date of death", blank=True, null=True)

    is_duplicate = models.BooleanField(default=False, db_index=True)
    duplicate_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="duplicates",
    )
    dq_members = GenericRelation(
        DataQualityIssueMember,
        content_type_field="content_type",
        object_id_field="object_id",
        related_query_name="household_death",
    )

    objects = RepeatGroupQuerySet.as_manager()

    def __str__(self):
        return f"({self.household.hhn}) {self.deathname} {self.sirname}"


class HouseholdDuplicate(models.Model):
    """
    A group of Household rows that appear to be duplicates by a specific rule.
    We keep a stable signature so repeated runs can upsert cleanly.
    """

    KIND_EXACT = "exact_fields"
    KIND_TIME = "ea_hun_hhn_time"
    KIND_ADMIN = "admin_hun_hhn"
    KIND_CHOICES = [
        (KIND_EXACT, "Exact fields"),
        (KIND_TIME, "EA+HUN+HHN within time window"),
        (KIND_ADMIN, "Admin + HUN/HHN identical"),
    ]

    kind = models.CharField(max_length=32, choices=KIND_CHOICES, db_index=True)
    # Human-meaningful keys that defined the grouping; JSON so we can store EA/HUN/HHN etc.
    keys = models.JSONField(default=dict, blank=True)
    # Extra run details e.g., window, first_seen, last_seen, counts.
    details = models.JSONField(default=dict, blank=True)

    # Stable unique signature over (kind + members + keys) to dedupe upserts.
    signature = models.CharField(max_length=64, unique=True, db_index=True)

    # The households in this duplicate group.
    households = models.ManyToManyField(
        "va_data_management.Household", related_name="duplicate_groups"
    )

    # Lifecycle
    resolved = models.BooleanField(
        default=False, help_text="Manually resolved or no longer detected."
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Household duplicate group"
        verbose_name_plural = "Household duplicate groups"

    def __str__(self):
        return f"[{self.kind}] size={self.households.count()} sig={self.signature[:8]}"
