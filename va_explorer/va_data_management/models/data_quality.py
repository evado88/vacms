from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone


class DataQualityIssue(models.Model):
    # ----- Issue types (extendable) -----
    DUPLICATE = "duplicate"
    INCOMPLETE = "incomplete"
    CONSENT = "consent"
    DURATION = "duration"
    TIMELINESS = "timeliness"

    ISSUE_CHOICES = [
        (DUPLICATE, "Duplicate"),
        (INCOMPLETE, "Incomplete"),
        (CONSENT, "Consent"),
        (DURATION, "Duration"),
        (TIMELINESS, "Timeliness"),
    ]

    # ----- Status -----
    OPEN = "open"
    RESOLVED = "resolved"
    MUTED = "muted"
    STATUS_CHOICES = [
        (OPEN, "Open"),
        (RESOLVED, "Resolved"),
        (MUTED, "Muted"),
    ]

    # What kind of issue and for which model class
    issue_type = models.CharField(max_length=32, choices=ISSUE_CHOICES, db_index=True)
    target_model = models.ForeignKey(
        ContentType, on_delete=models.PROTECT, related_name="dq_issues", db_index=True
    )

    # Stable unique signature to dedupe upserts (see utils/dq.py)
    signature = models.CharField(max_length=64, unique=True, db_index=True)

    # Optional short title; free text for UI
    title = models.CharField(max_length=255, blank=True)

    # Machine-usable metadata
    keys = models.JSONField(
        default=dict, blank=True
    )  # e.g., {"ea":"EA-123", "hun":"...", ...}
    details = models.JSONField(
        default=dict, blank=True
    )  # e.g., {"subtype":"exact_fields","count":3,...}

    # Lifecycle
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=OPEN, db_index=True
    )
    detected_at = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dq_issues_resolved",
    )

    class Meta:
        indexes = [
            models.Index(fields=["issue_type", "target_model", "status"]),
        ]

    def mark_resolved(self, by_user=None):
        self.status = self.RESOLVED
        self.resolved_at = timezone.now()
        self.resolved_by = by_user
        self.save(update_fields=["status", "resolved_at", "resolved_by", "updated"])


class DataQualityIssueMember(models.Model):
    """
    Members participating in an issue (e.g., the set of rows that form a duplicate group).
    Always the same model class as issue.target_model.
    """

    issue = models.ForeignKey(
        DataQualityIssue, on_delete=models.CASCADE, related_name="members"
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    class Meta:
        unique_together = (("issue", "content_type", "object_id"),)
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def save(self, *args, **kwargs):
        if self.issue_id and self.issue.target_model_id != self.content_type_id:
            raise ValueError("Member model must match issue.target_model")
        return super().save(*args, **kwargs)
