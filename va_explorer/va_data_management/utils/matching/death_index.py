"""Per-run in-memory index over the Death register for union blocking.

Death register columns are free text (dates, names, geography), so most of the
spec's blocking passes cannot be expressed in SQL. ``DeathIndex.build()``
makes one streaming pass over the register, builds a ``LinkageProfile`` per
death and hangs small dict indexes off it. Built once per ``run_matching``
invocation; the single-VA ingestion path uses ``build_for_profile`` which
bounds the scan with the SQL prefilters the register *can* answer (geography),
trading the geography-free DOD block for a small working set.
"""

from datetime import timedelta

from django.db.models import Q

from . import normalization as N
from .profiles import build_death_profile


def _death_only_fields() -> list:
    """Death columns the profile builder touches (keeps ``.only()`` honest).

    Derived from the active crosswalk so a crosswalk change can never leave a
    needed column deferred (which would N+1 the index build).
    """
    from va_explorer.va_data_management.linkage import active_concepts
    from va_explorer.va_data_management.models import Death

    model_fields = {f.name for f in Death._meta.fields}
    fields = {
        "id",
        "key",
        "death_id",
        "province",
        "district",
        "constituency",
        "ward",
        "ea",
    }
    for spec in active_concepts().values():
        de_field = spec.get("de_field")
        if de_field and de_field in model_fields:
            fields.add(de_field)
    return sorted(fields)


class DeathIndex:
    def __init__(self):
        self.deaths: dict = {}  # pk -> Death instance
        self.profiles: dict = {}  # pk -> LinkageProfile
        self.by_death_id: dict = {}
        self.by_nrc: dict = {}  # nrc -> [pk]
        self.by_reg_no: dict = {}  # reg no -> [pk]
        self.by_name_key: dict = {}  # frozenset(name tokens) -> [pk]
        self.by_ea: dict = {}
        self.by_constituency_ward: dict = {}
        self.by_district_year: dict = {}
        self.by_district: dict = {}
        self.by_province: dict = {}
        self.by_dod: dict = {}  # date -> [pk]
        self.by_initials_year: dict = {}
        self.by_phonetic_district: dict = {}
        self.size = 0

    # ------------------------------------------------------------------ build
    @classmethod
    def build(cls, queryset=None) -> "DeathIndex":
        from va_explorer.va_data_management.models import Death

        qs = queryset if queryset is not None else Death.objects.all()
        index = cls()
        for death in qs.only(*_death_only_fields()).iterator(chunk_size=1000):
            index.add(death)
        return index

    @classmethod
    def build_for_profile(cls, profile, cap=5000) -> "DeathIndex":
        """Bounded index for a single VA: geography-prefiltered via SQL.

        Loses the geography-free DOD block for records whose geography doesn't
        overlap the VA's; the batch pathway (full index) does not.
        """
        from va_explorer.va_data_management.models import Death

        filters = Q()
        if profile.ea:
            filters |= Q(ea__icontains=profile.ea)
        if profile.ward:
            filters |= Q(ward__iexact=profile.ward)
        if profile.district:
            filters |= Q(district__iexact=profile.district)
        if profile.province:
            filters |= Q(province__iexact=profile.province)
        if profile.death_id:
            filters |= Q(death_id__iexact=profile.death_id)
        if profile.nrc:
            filters |= Q(DE_08__isnull=False)
        qs = Death.objects.filter(filters) if filters else Death.objects.none()
        return cls.build(qs.order_by("pk")[:cap])

    def add(self, death):
        profile = build_death_profile(death)
        pk = death.pk
        self.deaths[pk] = death
        self.profiles[pk] = profile
        self.size += 1

        if profile.death_id:
            self.by_death_id.setdefault(profile.death_id, pk)
        if profile.nrc:
            self.by_nrc.setdefault(profile.nrc, []).append(pk)
        if profile.death_reg_no:
            self.by_reg_no.setdefault(profile.death_reg_no.upper(), []).append(pk)
        if profile.name_tokens:
            self.by_name_key.setdefault(profile.name_tokens, []).append(pk)
        if profile.ea:
            self.by_ea.setdefault(profile.ea, []).append(pk)

        district = N.norm_key(profile.district)
        province = N.norm_key(profile.province)
        constituency = N.norm_key(profile.constituency)
        ward = N.norm_key(profile.ward)
        if district:
            self.by_district.setdefault(district, []).append(pk)
        if province:
            self.by_province.setdefault(province, []).append(pk)
        if constituency and ward:
            self.by_constituency_ward.setdefault((constituency, ward), []).append(pk)
        if profile.dod:
            self.by_dod.setdefault(profile.dod, []).append(pk)
            if district:
                self.by_district_year.setdefault(
                    (district, profile.dod.year), []
                ).append(pk)
            if profile.initials:
                self.by_initials_year.setdefault(
                    (profile.initials, profile.dod.year), []
                ).append(pk)
        if profile.surname_phonetic and district:
            self.by_phonetic_district.setdefault(
                (profile.surname_phonetic, district), []
            ).append(pk)

    # ----------------------------------------------------------------- lookup
    def dod_window_pks(self, day, window_days) -> list:
        """PKs of deaths whose (day-precise) DOD is within ±window_days."""
        if day is None:
            return []
        pks = []
        for offset in range(-window_days, window_days + 1):
            pks.extend(self.by_dod.get(day + timedelta(days=offset), []))
        return pks

    def death(self, pk):
        return self.deaths.get(pk)

    def profile(self, pk):
        return self.profiles.get(pk)
