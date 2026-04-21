from django.conf import settings
from django.db import models


class AdminCommandRun(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_command_runs",
    )
    command_id = models.CharField(max_length=128)
    management_command = models.CharField(max_length=128)
    inputs = models.JSONField(default=dict, blank=True)
    filenames = models.JSONField(default=list, blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()
    duration_ms = models.PositiveIntegerField(default=0)
    ok = models.BooleanField(default=False)
    output_excerpt = models.TextField(blank=True, default="")
    output_full = models.TextField(blank=True, default="")
    output_file_path = models.CharField(max_length=512, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        permissions = (
            ("can_run_admin_commands", "Can run admin panel management commands"),
        )

    def __str__(self):
        return f"{self.management_command} ({'ok' if self.ok else 'failed'})"
