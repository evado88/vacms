from django.db import models


class ClusterLocationCodes(models.Model):
    ward = models.CharField(max_length=100)
    ward_code = models.CharField(max_length=10, blank=True, null=True, unique=True)
    cluster_code = models.CharField(max_length=10, blank=True, null=True)

    def __str__(self):
        return f"{self.cluster_code} - {self.ward}"
