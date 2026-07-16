"""Hand-maintained rules for the Death (E_DEATH) <-> WHO-VA linkage crosswalk.

This file is the human-confirmation record for the generated crosswalk: only
seed rows whose ``status`` is ``confirmed`` become active linkage concepts in
``linkage_map.CONCEPTS``. The ``generate_linkage_crosswalk`` command verifies
every row against the XLSForms (field exists, WHO Ids exist, label similarity)
and demotes rows whose Death-side field is missing to ``absent_in_form`` —
those stay in the artifact so a future E_DEATH revision picks them up
automatically without code changes.

Verified against E_DEATH v1.2.4 + znphi_va_who_v1_5_2_7 labels (2026-07-16):

* ``DE-08`` IS the deceased's NRC — the XLSForm label reads "DE-08 NRC NUMBER
  for the Deceased"; the ``Death`` model's verbose name dropped the word "NRC",
  which historically caused it to be confused with ``DE-23``.
* ``DE-23`` is the **informant's** NRC (it sits in the informant block
  DE-19..DE-25) and must never be compared against ``Id10073``.
* ``DE-03A``/``DE-02A`` nickname fields do not exist in E_DEATH v1.2.4.
* E_DEATH has no death-registration-number field (``DE-30`` is a yes/no flag),
  so the ``death_reg_no`` concept has no Death-side source yet.
"""

# --- Seed crosswalk -----------------------------------------------------------
# concept -> row. ``de_field`` uses the XLSForm spelling ("DE-03"); the
# generator converts it to the Death model attribute ("DE_03"). ``who_ids`` are
# the WHO-form field names carrying the concept (VASA columns resolve through
# the existing vasa/field_map.py, never re-derived here). ``status``:
#   confirmed      — drives linking (human-verified)
#   proposed       — emitted for review only, never used by the engine
#   excluded       — must never be compared (kept to document why)
# ``rule`` documents how the concept is used by the engine.
SEED_CROSSWALK = {
    "deceased_name": {
        "de_field": "DE-03",
        "who_ids": ["Id10017", "Id10018"],
        "status": "confirmed",
        "rule": "name token-set; VA value is Id10017 + Id10018 concatenated",
    },
    "deceased_nickname": {
        "de_field": "DE-03A",  # not in E_DEATH v1.2.4; self-activates if added
        "who_ids": ["Id10017", "Id10018"],
        "status": "confirmed",
        "rule": "supporting name evidence only",
    },
    "household_head_name": {
        "de_field": "DE-02",
        "who_ids": ["Id10061"],
        "status": "confirmed",
        "rule": "supporting evidence; Id10061 is 'name of the father' (often the "
        "household head); VASA carries it as Q1209 (WHO_name Id10061)",
    },
    "household_head_nickname": {
        "de_field": "DE-02A",  # not in E_DEATH v1.2.4
        "who_ids": ["Id10061"],
        "status": "confirmed",
        "rule": "supporting name evidence only",
    },
    "mother_name": {
        "de_field": None,  # derived: DE-03 may read "Baby of <mother>"
        "who_ids": ["Id10062"],
        "status": "confirmed",
        "rule": "derived: parse 'Baby of <mother>' out of DE-03 and compare "
        "against Id10062 (neonate/child deaths)",
    },
    "sex": {
        "de_field": "DE-05",
        "who_ids": ["Id10019"],
        "status": "confirmed",
        "rule": "choice-map category; disagreement is a hard contradiction",
    },
    "dob": {
        "de_field": "DE-04",
        "who_ids": ["Id10021", "Id10020"],
        "status": "confirmed",
        "rule": "date with partial precision; Id10020 gates whether DOB is known",
    },
    "dod": {
        "de_field": "DE-06",
        "who_ids": ["Id10023", "Id10023_a", "Id10023_b", "Id10024"],
        "status": "confirmed",
        "rule": "date distance bands; Id10024 provides year-only precision",
    },
    "age": {
        "de_field": None,  # derived from DE-04/DE-06 delta
        "who_ids": ["ageInDays", "ageInYears", "age_group"],
        "status": "confirmed",
        "rule": "derived age-in-days at death; compared with relative tolerance",
    },
    "place_of_death": {
        "de_field": "DE-07",
        "who_ids": ["Id10058"],
        "status": "confirmed",
        "rule": "choice-map category (facility/community/transit)",
    },
    "usual_residence": {
        "de_field": "DE-22",
        "who_ids": ["Id10055"],
        "status": "proposed",
        "rule": "DE-22 is the INFORMANT's residential address, not the "
        "deceased's — needs data-owner confirmation before use",
    },
    "nrc": {
        "de_field": "DE-08",
        "who_ids": ["Id10073"],
        "status": "confirmed",
        "rule": "deterministic when valid + unique; DE-23 (informant NRC) is "
        "explicitly excluded",
    },
    "ssn": {
        "de_field": "DE-10",
        "who_ids": [],
        "status": "proposed",
        "rule": "no WHO counterpart found by label analysis",
    },
    "nationality": {
        "de_field": "DE-09",
        "who_ids": ["Id10052"],
        "status": "confirmed",
        "rule": "choice-map category (citizen/foreign); low weight",
    },
    "marital_status": {
        "de_field": "DE-11",
        "who_ids": ["Id10059"],
        "status": "confirmed",
        "rule": "choice-map category; low weight",
    },
    "occupation": {
        "de_field": "DE-12",
        "who_ids": ["Id10066"],
        "status": "confirmed",
        "rule": "free-text token overlap; low weight",
    },
    "education": {
        "de_field": "DE-13",
        "who_ids": ["Id10063"],
        "status": "confirmed",
        "rule": "choice-map category; low weight",
    },
    "died_pregnant": {
        "de_field": "DE-16",
        "who_ids": ["Id10305", "Id10310"],
        "status": "confirmed",
        "rule": "pregnancy flag (Id10310 is the negation); female adults only",
    },
    "died_childbirth": {
        "de_field": "DE-17",
        "who_ids": ["Id10312"],
        "status": "confirmed",
        "rule": "pregnancy flag; female adults only",
    },
    "died_postpartum42": {
        "de_field": "DE-18",
        "who_ids": ["Id10306", "Id10315"],
        "status": "confirmed",
        "rule": "pregnancy flag (42 days / 6 weeks); female adults only",
    },
    "informant_name": {
        "de_field": "DE-19",
        "who_ids": ["Id10007"],
        "status": "confirmed",
        "rule": "supporting evidence only (same informant strengthens a match)",
    },
    "informant_relationship": {
        "de_field": "DE-25",
        "who_ids": ["Id10008"],
        "status": "confirmed",
        "rule": "supporting evidence only",
    },
    "death_registered": {
        "de_field": "DE-30",
        "who_ids": ["Id10069_a", "Id10462"],
        "status": "confirmed",
        "rule": "yes/no agreement; weak supporting evidence",
    },
    "death_reg_no": {
        "de_field": None,  # E_DEATH v1.2.4 has no registration-number field
        "who_ids": ["Id10070"],
        "status": "confirmed",
        "rule": "deterministic when both sides present; currently inert (no "
        "E_DEATH source field)",
    },
    "gps": {
        "de_field": "DE-34",
        "who_ids": ["geopoint"],
        "status": "confirmed",
        "rule": "haversine distance bands",
    },
    # Geography: same field names on both sides (Death stores names, VA stores
    # codes — resolved through GEO_CODE_MAPS / ClusterLocationCodes).
    "province": {
        "de_field": "province",
        "who_ids": ["province"],
        "status": "confirmed",
        "rule": "blocking + scoring",
    },
    "district": {
        "de_field": "district",
        "who_ids": ["district"],
        "status": "confirmed",
        "rule": "blocking + scoring; facility VA derives district from location FK",
    },
    "constituency": {
        "de_field": "constituency",
        "who_ids": ["constituency"],
        "status": "confirmed",
        "rule": "blocking",
    },
    "ward": {
        "de_field": "ward",
        "who_ids": ["ward"],
        "status": "confirmed",
        "rule": "blocking + scoring",
    },
    "ea": {
        "de_field": "ea",
        "who_ids": ["ea"],
        "status": "confirmed",
        "rule": "blocking + scoring (digit-normalised exact)",
    },
}

# Death-side fields that must never enter deceased-identity comparison, with
# the reason (documented so nobody "fixes" them back in).
EXCLUDED_DE_FIELDS = {
    "DE-20": "informant date of birth (not the deceased)",
    "DE-21": "informant sex (not the deceased)",
    "DE-23": "informant NRC — NOT the deceased's NRC (that is DE-08)",
    "DE-24": "informant nationality (deceased nationality is DE-09)",
    "DE-26": "informant telephone number",
    "DE-27": "VA interview scheduling date",
    "DE-28": "contact person name",
    "DE-29": "contact person telephone number",
}

# Optional per-concept overrides applied after label analysis (e.g. to force a
# mapping the analyser gets wrong). Keyed by concept; values replace seed keys.
MANUAL_OVERRIDES: dict = {}

# --- Choice category maps -----------------------------------------------------
# concept -> ordered {category: [label patterns]}. A choice maps to the first
# category any of whose patterns' tokens are a subset of the normalised label's
# tokens. Unmatched labels (DK/Ref/unknown) resolve to no category (ignored).
# Order matters: more specific categories first (e.g. "transit" before
# "facility" so "on route to hospital" is not caught by "hospital").
CATEGORY_MAPS = {
    "sex": {
        "male": ["male"],
        "female": ["female"],
    },
    "place_of_death": {
        "transit": ["on route"],
        "facility": ["health facility", "hospital"],
        "community": ["community", "home", "other"],
    },
    "marital_status": {
        "single": ["never married", "single", "too young to be married"],
        "partner": ["cohabiting", "life partner", "living together"],
        "married": ["married"],
        "divorced": ["divorced", "separated"],
        "widowed": ["widowed"],
    },
    "education": {
        "none": ["not been to school", "no formal education"],
        "primary": ["primary"],
        "secondary": ["secondary"],
        "higher": ["tertiary", "higher than secondary"],
    },
    "nationality": {
        "foreign": ["non zambian", "foreign national"],
        "citizen": ["zambian", "citizen at birth", "naturalized citizen"],
    },
    "died_pregnant": {"yes": ["yes"], "no": ["no"]},
    "died_childbirth": {"yes": ["yes"], "no": ["no"]},
    "died_postpartum42": {"yes": ["yes"], "no": ["no"]},
    "death_registered": {"yes": ["yes"], "no": ["no"]},
}

# Concept keywords used by the label analyser to boost DE <-> WHO candidates
# whose labels share these terms (helps sparse labels like "Sex of the deceased").
CONCEPT_KEYWORDS = {
    "deceased_name": ["name", "deceased"],
    "household_head_name": ["name", "head", "household", "father"],
    "mother_name": ["name", "mother"],
    "sex": ["sex"],
    "dob": ["date", "birth", "born"],
    "dod": ["date", "death", "die", "died"],
    "place_of_death": ["place", "death", "die", "where"],
    "nrc": ["nrc", "national", "identification", "number"],
    "nationality": ["nationality", "citizenship"],
    "marital_status": ["marital"],
    "occupation": ["occupation", "work"],
    "education": ["education", "school", "schooling"],
    "died_pregnant": ["pregnant", "labour"],
    "died_childbirth": ["childbirth", "labour", "delivery"],
    "died_postpartum42": ["42", "weeks", "pregnancy", "delivery"],
    "informant_name": ["informant", "respondent", "name"],
    "informant_relationship": ["relationship", "deceased"],
    "death_registered": ["register", "registration", "certificate"],
    "usual_residence": ["residence", "address"],
}
