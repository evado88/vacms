"""Crosswalk-driven linkage profiles (Stage 0 normalisation).

A ``LinkageProfile`` is the normalised feature vector of one record — Death,
VerbalAutopsy or VASA — keyed by linkage *concept* rather than by form field.
Field access is resolved through the generated crosswalk
(``linkage/linkage_map.py``): only concepts with ``status == "confirmed"``
participate, so a form revision or a crosswalk correction changes behaviour
without touching engine code (nickname concepts, for example, are dormant
until E_DEATH gains the fields).
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from va_explorer.va_data_management.linkage import active_concepts

from . import normalization as N

# Instrument/age-group canonical values.
NEONATE = "neonate"
CHILD = "child"
ADULT = "adult"

_NEONATE_MAX_DAYS = 28
_CHILD_MAX_DAYS = int(12 * 365.25)


@dataclass
class LinkageProfile:
    tool: str  # "community_va" | "facility_va" | "vasa" | "death"
    name_raw: Optional[str] = None
    name_tokens: frozenset = frozenset()
    nickname_tokens: frozenset = frozenset()
    household_head: Optional[str] = None
    mother_name: Optional[str] = None  # explicit mother's-name field
    baby_of: Optional[str] = None  # mother parsed out of "Baby of <mother>"
    sex: Optional[str] = None
    dob: Optional[date] = None
    dob_precision: Optional[str] = None
    dod: Optional[date] = None
    dod_precision: Optional[str] = None
    age_days: Optional[int] = None
    age_group: Optional[str] = None
    place_category: Optional[str] = None
    marital_category: Optional[str] = None
    education_category: Optional[str] = None
    nationality_category: Optional[str] = None
    occupation: Optional[str] = None
    informant_name: Optional[str] = None
    death_registered: Optional[str] = None  # "yes" | "no"
    pregnancy_flags: dict = field(default_factory=dict)
    nrc: Optional[str] = None
    death_reg_no: Optional[str] = None
    death_id: Optional[str] = None
    gps: Optional[tuple] = None
    province: Optional[str] = None
    district: Optional[str] = None
    constituency: Optional[str] = None
    ward: Optional[str] = None
    ea: Optional[str] = None
    surname_phonetic: Optional[str] = None
    initials: Optional[str] = None

    @property
    def dod_year(self) -> Optional[int]:
        return self.dod.year if self.dod else None


def _first(record, attrs):
    for attr in attrs:
        value = N.norm_text(getattr(record, attr, None))
        if value:
            return value
    return None


class _Concepts:
    """Concept -> field-name resolution over the confirmed crosswalk rows."""

    def __init__(self):
        self.concepts = active_concepts()

    def who(self, concept, record):
        spec = self.concepts.get(concept)
        return _first(record, spec["who_ids"]) if spec else None

    def death(self, concept, record):
        spec = self.concepts.get(concept)
        de_field = spec.get("de_field") if spec else None
        return _first(record, [de_field]) if de_field else None


def _age_group_from_days(age_days) -> Optional[str]:
    if age_days is None:
        return None
    if age_days <= _NEONATE_MAX_DAYS:
        return NEONATE
    if age_days < _CHILD_MAX_DAYS:
        return CHILD
    return ADULT


def _finish(profile: LinkageProfile) -> LinkageProfile:
    profile.name_tokens = N.name_token_set(profile.name_raw)
    profile.baby_of = N.parse_baby_of(profile.name_raw)
    profile.surname_phonetic = N.surname_phonetic(profile.name_raw)
    profile.initials = N.name_initials(profile.name_raw)
    if (
        profile.age_days is None
        and profile.dob
        and profile.dod
        and profile.dob_precision == "day"
        and profile.dod_precision == "day"
    ):
        delta = (profile.dod - profile.dob).days
        if 0 <= delta <= 130 * 366:
            profile.age_days = delta
    if profile.age_group is None:
        profile.age_group = _age_group_from_days(profile.age_days)
    return profile


# --- Verbal Autopsy / VASA side -------------------------------------------------


def _va_tool(va) -> str:
    if va._meta.model_name == "vasa":
        return "vasa"
    return "community_va" if getattr(va, "is_community_va", True) else "facility_va"


def _va_dod(cx, va):
    spec = cx.concepts.get("dod")
    for attr in spec["who_ids"] if spec else []:
        value = N.norm_text(getattr(va, attr, None))
        if not value:
            continue
        day, precision = N.parse_partial_date(value)
        if day is not None:
            # Id10024 is "year of death" — never day-precise even when it
            # parses as a full date.
            if attr == "Id10024":
                return date(day.year, 1, 1), "year"
            return day, precision
    return None, None


def _va_age(va):
    # to_int_age caps at 130 (years); ageInDays needs its own range guard.
    raw = N.norm_text(getattr(va, "ageInDays", None))
    if raw is not None:
        try:
            days = int(float(raw))
            if 0 <= days <= 130 * 366:
                return days
        except ValueError:
            pass
    years = N.to_int_age(getattr(va, "ageInYears", None))
    if years is not None:
        return int(years * 365.25)
    return None


def _va_district(va, tool):
    district = N.resolve_geo_name("district", getattr(va, "district", None))
    if district or tool != "facility_va":
        return district
    # Facility VAs carry no district; derive it from the Location FK ancestry.
    location = getattr(va, "location", None)
    try:
        while location is not None:
            if (getattr(location, "location_type", "") or "").lower() == "district":
                return N.norm_text(location.name)
            location = location.get_parent()
    except Exception:
        return None
    return None


def build_va_profile(va) -> LinkageProfile:
    cx = _Concepts()
    tool = _va_tool(va)
    p = LinkageProfile(tool=tool)

    first = N.norm_text(getattr(va, "Id10017", None)) or ""
    surname = N.norm_text(getattr(va, "Id10018", None)) or ""
    p.name_raw = f"{first} {surname}".strip() or None
    p.household_head = cx.who("household_head_name", va)
    p.mother_name = cx.who("mother_name", va)
    p.informant_name = cx.who("informant_name", va)
    p.occupation = cx.who("occupation", va)

    p.sex = N.category_for("sex", "who", cx.who("sex", va)) or N.normalize_sex(
        cx.who("sex", va)
    )
    p.dob, p.dob_precision = N.parse_partial_date(cx.who("dob", va))
    p.dod, p.dod_precision = _va_dod(cx, va)
    p.age_days = _va_age(va)

    instrument = N.norm_key(getattr(va, "instrument", None))
    if instrument in {NEONATE, CHILD}:
        p.age_group = instrument
    elif instrument in {ADULT, "maternal"}:
        p.age_group = ADULT

    p.place_category = N.category_for(
        "place_of_death", "who", cx.who("place_of_death", va)
    )
    p.marital_category = N.category_for(
        "marital_status", "who", cx.who("marital_status", va)
    )
    p.education_category = N.category_for("education", "who", cx.who("education", va))
    p.nationality_category = N.category_for(
        "nationality", "who", cx.who("nationality", va)
    )
    p.death_registered = N.category_for(
        "death_registered", "who", cx.who("death_registered", va)
    )

    p.pregnancy_flags = {
        "pregnant": N.category_for("died_pregnant", "who", _first(va, ["Id10305"])),
        "childbirth": N.category_for("died_childbirth", "who", _first(va, ["Id10312"])),
        "postpartum42": N.category_for(
            "died_postpartum42", "who", _first(va, ["Id10306", "Id10315"])
        ),
    }
    # Id10310 confirms she was NEITHER pregnant NOR recently delivered.
    negation = N.category_for("died_pregnant", "who", _first(va, ["Id10310"]))
    if negation == "yes":
        p.pregnancy_flags = {k: v or "no" for k, v in p.pregnancy_flags.items()}

    p.nrc = N.normalize_nrc(cx.who("nrc", va))
    p.death_reg_no = N.norm_text(cx.who("death_reg_no", va))
    if va._meta.model_name == "vasa":
        try:
            sa = va.social_autopsy
        except Exception:
            sa = None
        p.death_id = N.normalize_death_id(getattr(sa, "DeathID", None)) if sa else None

    p.gps = N.parse_gps(getattr(va, "geopoint", None))
    p.province = N.resolve_geo_name("province", getattr(va, "province", None))
    p.district = _va_district(va, tool)
    p.constituency = N.resolve_geo_name(
        "constituency", getattr(va, "constituency", None)
    )
    p.ward = N.resolve_geo_name("ward", getattr(va, "ward", None))
    p.ea = N.normalize_ea(getattr(va, "ea", None))
    return _finish(p)


# --- Death side -----------------------------------------------------------------


def build_death_profile(death) -> LinkageProfile:
    cx = _Concepts()
    p = LinkageProfile(tool="death")

    p.name_raw = cx.death("deceased_name", death)
    p.nickname_tokens = N.name_token_set(cx.death("deceased_nickname", death))
    p.household_head = cx.death("household_head_name", death)
    p.informant_name = cx.death("informant_name", death)
    p.occupation = cx.death("occupation", death)

    p.sex = N.category_for("sex", "death", cx.death("sex", death)) or N.normalize_sex(
        cx.death("sex", death)
    )
    p.dob, p.dob_precision = N.parse_partial_date(cx.death("dob", death))
    p.dod, p.dod_precision = N.parse_partial_date(cx.death("dod", death))

    p.place_category = N.category_for(
        "place_of_death", "death", cx.death("place_of_death", death)
    )
    p.marital_category = N.category_for(
        "marital_status", "death", cx.death("marital_status", death)
    )
    p.education_category = N.category_for(
        "education", "death", cx.death("education", death)
    )
    p.nationality_category = N.category_for(
        "nationality", "death", cx.death("nationality", death)
    )
    p.death_registered = N.category_for(
        "death_registered", "death", cx.death("death_registered", death)
    )
    p.pregnancy_flags = {
        "pregnant": N.category_for(
            "died_pregnant", "death", cx.death("died_pregnant", death)
        ),
        "childbirth": N.category_for(
            "died_childbirth", "death", cx.death("died_childbirth", death)
        ),
        "postpartum42": N.category_for(
            "died_postpartum42", "death", cx.death("died_postpartum42", death)
        ),
    }

    p.nrc = N.normalize_nrc(cx.death("nrc", death))
    p.death_reg_no = N.norm_text(cx.death("death_reg_no", death))
    p.death_id = N.normalize_death_id(getattr(death, "death_id", None))

    p.gps = N.parse_gps(cx.death("gps", death))
    p.province = N.norm_text(getattr(death, "province", None))
    p.district = N.norm_text(getattr(death, "district", None))
    p.constituency = N.norm_text(getattr(death, "constituency", None))
    p.ward = N.norm_text(getattr(death, "ward", None))
    p.ea = N.normalize_ea(getattr(death, "ea", None))
    return _finish(p)
