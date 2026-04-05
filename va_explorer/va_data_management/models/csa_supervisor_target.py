from django.db import models


class CSASupervisorTargetMap(models.Model):
    csa_name_raw = models.TextField(blank=True, null=True)
    csa_display_name = models.TextField(db_index=True)
    csa_name_normalized = models.TextField(unique=True)
    supervisor_name = models.TextField(blank=True, default="")
    csa_target = models.PositiveIntegerField(blank=True, null=True)
    source_filename = models.CharField(max_length=255, blank=True, default="")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "CSA supervisor/target map"
        verbose_name_plural = "CSA supervisor/target maps"

    def __str__(self):
        return f"{self.csa_display_name} -> {self.supervisor_name or '-'}"
