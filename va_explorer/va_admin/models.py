from django.conf import settings
from django.db import models


class AdminPanelAlert(models.Model):
    INTERACTION = "interaction"
    SECURITY = "security"
    SYSTEM = "system"

    CATEGORY_CHOICES = (
        (INTERACTION, "Interaction"),
        (SECURITY, "Security"),
        (SYSTEM, "System"),
    )

    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4
    SEVERITY_CHOICES = (
        (LOW, "Low"),
        (MEDIUM, "Medium"),
        (HIGH, "High"),
        (CRITICAL, "Critical"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="admin_panel_alerts",
    )
    category = models.CharField(max_length=32, choices=CATEGORY_CHOICES, db_index=True)
    severity = models.PositiveSmallIntegerField(
        choices=SEVERITY_CHOICES, default=MEDIUM, db_index=True
    )
    title = models.CharField(max_length=255)
    summary = models.CharField(max_length=512, blank=True)
    details = models.TextField(blank=True)
    context = models.JSONField(default=dict, blank=True)

    path = models.CharField(max_length=512, blank=True)
    ip_address = models.CharField(max_length=64, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at", "-severity")

    def __str__(self):
        return f"[{self.get_category_display()}:{self.get_severity_display()}] {self.title}"
