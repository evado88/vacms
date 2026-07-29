# flake8: noqa: N815 - generated social-autopsy fields mirror the VASA form names
"""VASA (Verbal + Social Autopsy) models.

``VASA`` is a concrete sibling of :class:`VerbalAutopsy`: it inherits every WHO
``Idxxxxx`` column and the shared metadata/geo helpers from
:class:`BaseVerbalAutopsy`, so a VASA record produces coding input identically
shaped to a VA record (WHO Ids absent from the VASA form are simply blank).

Social-autopsy answers (empty ``WHO_name``) never live on ``VASA`` itself — they
are stored on the separate :class:`VASASocialAutopsy` record and the three
care-seeking repeat models, all foreign-keyed to ``VASA``. That physical
separation guarantees SA data can never leak into the coding input built from
``VASA``'s own fields.

The social-autopsy and repeat field definitions are generated from the XLSForm
(:mod:`va_explorer.va_data_management.vasa.model_fields`); regenerate them with
``manage.py generate_vasa_mapping``.
"""

from django.db import models
from django.db.models import Q
from simple_history.models import HistoricalRecords

from ..vasa.model_fields import REPEAT_FIELD_DEFS, SA_FIELD_DEFS
from .verbal_autopsy import BaseVerbalAutopsy, Location

INSTRUMENT_NEONATE = "neonate"
INSTRUMENT_CHILD = "child"
INSTRUMENT_ADULT = "adult"
INSTRUMENT_MATERNAL = "maternal"
INSTRUMENT_CHOICES = [
    (INSTRUMENT_NEONATE, "Neonate"),
    (INSTRUMENT_CHILD, "Child"),
    (INSTRUMENT_ADULT, "Adult"),
    (INSTRUMENT_MATERNAL, "Maternal"),
]


class VASA(BaseVerbalAutopsy):
    """A Verbal + Social Autopsy record (WHO-VA-shaped columns inherited)."""

    class Meta:
        verbose_name = "VASA record"
        verbose_name_plural = "VASA records"
        indexes = [
            models.Index(fields=["instanceid"], name="vasa_instanceid_idx"),
            models.Index(fields=["key"], name="vasa_key_idx"),
            models.Index(fields=["instrument"], name="vasa_instrument_idx"),
            models.Index(fields=["deleted_at"], name="vasa_deleted_at_idx"),
            models.Index(
                fields=["submitted_at"],
                name="vasa_submitted_at_idx",
                condition=Q(
                    deleted_at__isnull=True,
                    submitted_at__isnull=False,
                ),
            ),
        ]

    location = models.ForeignKey(
        Location, related_name="vasa_records", on_delete=models.CASCADE, null=True
    )
    cluster = models.ForeignKey(
        "SRSClusterLocation",
        related_name="vasa_records",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    # ODK submission KEY; repeat children join on this via their parent_key.
    key = models.TextField("ODK KEY", blank=True)
    # Which WHO instrument section was completed (neonate/child/adult/maternal).
    instrument = models.CharField(max_length=10, choices=INSTRUMENT_CHOICES, blank=True)
    # Discriminates VASA from VA anywhere the two are surfaced together.
    source = models.TextField(default="VASA", blank=True)
    # Raw-code provenance for crosswalk-decoded select answers:
    # {"values": {pseudo: raw}, "meta": {"crosswalk_version", "form_id"}}.
    # editable=False keeps it out of model_to_dict() (coding input) and VASAForm.
    raw_answers = models.JSONField(default=dict, blank=True, editable=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    def save(self, *args, **kwargs):
        # Mirror VerbalAutopsy: persist the resolved community/facility tag.
        self.community_va = self.community_va_normalized
        super().save(*args, **kwargs)

    def __str__(self):
        return f"VASA {self.instrument or '?'} - {self.instanceid or self.key}"


def _social_autopsy_attrs() -> dict:
    attrs = {
        "__module__": __name__,
        "__qualname__": "VASASocialAutopsy",
        "__doc__": (
            "Social-autopsy answers for a VASA record (empty-WHO_name questions). "
            "Never fed to the coding pipeline."
        ),
        "vasa": models.OneToOneField(
            VASA,
            related_name="social_autopsy",
            on_delete=models.CASCADE,
            primary_key=True,
        ),
        "created": models.DateTimeField(auto_now_add=True),
        "updated": models.DateTimeField(auto_now=True),
        "__str__": lambda self: f"Social autopsy for {self.vasa_id}",
    }
    for _column, field_name, label in SA_FIELD_DEFS:
        attrs[field_name] = models.TextField(label, blank=True)
    return attrs


# Built dynamically because the ~550 SA columns are generated from the XLSForm.
VASASocialAutopsy = type("VASASocialAutopsy", (models.Model,), _social_autopsy_attrs())


def _care_seeking_attrs(related_name: str, group_key: str) -> dict:
    attrs = {
        "__module__": __name__,
        "vasa": models.ForeignKey(
            VASA, related_name=related_name, on_delete=models.CASCADE
        ),
        # ODK child-row KEY / PARENT_KEY (idempotent upsert + parent linkage).
        "key": models.TextField("ODK KEY", blank=True),
        "parent_key": models.TextField("ODK PARENT_KEY", blank=True),
        "created": models.DateTimeField(auto_now_add=True),
        "updated": models.DateTimeField(auto_now=True),
    }
    for _column, field_name, label in REPEAT_FIELD_DEFS[group_key]:
        attrs[field_name] = models.TextField(label, blank=True)
    return attrs


def _build_care_seeking_model(class_name: str, related_name: str, group_key: str):
    attrs = _care_seeking_attrs(related_name, group_key)
    attrs["__qualname__"] = class_name

    class Meta:
        indexes = [
            models.Index(fields=["key"]),
            models.Index(fields=["parent_key"]),
        ]

    attrs["Meta"] = Meta
    attrs["__str__"] = (
        lambda self: f"{class_name} {self.key} (parent {self.parent_key})"
    )
    return type(class_name, (models.Model,), attrs)


# One model per care-seeking repeat group (neonate / child / adult pathways).
VASACareSeekingNeonate = _build_care_seeking_model(
    "VASACareSeekingNeonate", "care_seeking_neonate", "br_N2213"
)
VASACareSeekingChild = _build_care_seeking_model(
    "VASACareSeekingChild", "care_seeking_child", "AD_C3253_1"
)
VASACareSeekingAdult = _build_care_seeking_model(
    "VASACareSeekingAdult", "care_seeking_adult", "ar_A4252"
)
