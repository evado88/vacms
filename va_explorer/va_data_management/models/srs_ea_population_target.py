from django.db import models


class SRSEAPopulationTarget(models.Model):
    """Reference data: per-enumeration-area (EA) population and household targets.

    Sourced from the SRS EA population targets CSV. Keyed by ``geoid`` (an 11-digit
    enumeration-area identifier) so the loader can upsert incrementally. The location
    hierarchy is stored as denormalized text, mirroring how ``Household`` records
    keep province/district/constituency/ward — this table is standalone reference
    data and is not linked to the location tree.
    """

    RURAL = "Rural"
    URBAN = "Urban"
    RURAL_URBAN_CHOICES = (
        (RURAL, "Rural"),
        (URBAN, "Urban"),
    )

    geoid = models.CharField(max_length=20, unique=True, db_index=True)

    province_name = models.CharField(max_length=100, blank=True, default="")
    province_code = models.CharField(max_length=4, blank=True, default="")
    district_name = models.CharField(max_length=100, blank=True, default="")
    district_code = models.CharField(max_length=8, blank=True, default="")
    constituency_name = models.CharField(max_length=100, blank=True, default="")
    constituency_code = models.CharField(max_length=8, blank=True, default="")
    ward_name = models.CharField(max_length=100, blank=True, default="")
    ward_code = models.CharField(max_length=10, blank=True, default="")

    region_code = models.CharField(max_length=2, blank=True, default="")
    rural_urban = models.CharField(
        max_length=10, choices=RURAL_URBAN_CHOICES, blank=True, default=""
    )

    population_target = models.PositiveIntegerField()
    household_target = models.PositiveIntegerField()

    comments = models.TextField(blank=True, default="")
    source_filename = models.CharField(max_length=255, blank=True, default="")

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["geoid"]
        verbose_name = "SRS EA population target"
        verbose_name_plural = "SRS EA population targets"
        indexes = [
            models.Index(fields=["district_code"]),
            models.Index(fields=["ward_code"]),
        ]

    def __str__(self):
        return f"{self.geoid} — {self.ward_name}"
