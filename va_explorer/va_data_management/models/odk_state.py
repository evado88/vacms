from django.db import models
from django.utils import timezone


class ODKPullState(models.Model):
    STATUS_SUCCESS = "SUCCESS"
    STATUS_FAILED = "FAILED"
    STATUS_RUNNING = "RUNNING"
    STATUS_CHOICES = (
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
        (STATUS_RUNNING, "Running"),
    )

    form_id = models.CharField(max_length=255)
    project_id = models.IntegerField()
    last_submission_at = models.DateTimeField(null=True, blank=True)
    last_server_submission_at = models.DateTimeField(null=True, blank=True)
    last_run_started_at = models.DateTimeField(null=True, blank=True)
    last_run_finished_at = models.DateTimeField(null=True, blank=True)
    last_run_status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, blank=True
    )
    last_error = models.TextField(blank=True)
    last_counts = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("project_id", "form_id")
        indexes = [
            models.Index(fields=["form_id", "project_id"]),
        ]

    def mark_started(self):
        self.last_run_started_at = timezone.now()
        self.last_run_status = self.STATUS_RUNNING
        self.last_error = ""
        self.save(
            update_fields=["last_run_started_at", "last_run_status", "last_error"]
        )

    def mark_finished(
        self,
        status,
        counts=None,
        last_submission_at=None,
        last_server_submission_at=None,
        error=None,
    ):
        self.last_run_finished_at = timezone.now()
        self.last_run_status = status
        if counts is not None:
            self.last_counts = counts
        if last_submission_at is not None:
            self.last_submission_at = last_submission_at
        if last_server_submission_at is not None:
            self.last_server_submission_at = last_server_submission_at
        if error:
            self.last_error = error
        elif status == self.STATUS_SUCCESS:
            self.last_error = ""
        self.save(
            update_fields=[
                "last_run_finished_at",
                "last_run_status",
                "last_counts",
                "last_submission_at",
                "last_server_submission_at",
                "last_error",
            ]
        )


class ODKPullLock(models.Model):
    """Simple DB-backed lock to prevent overlapping pulls."""

    form_id = models.CharField(max_length=255, null=True, blank=True)
    project_id = models.IntegerField(null=True, blank=True)
    locked_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("project_id", "form_id")
        indexes = [
            models.Index(fields=["form_id", "project_id"]),
        ]

    def __str__(self):
        scope = self.form_id or "GLOBAL"
        return f"ODKPullLock<{scope}>"
