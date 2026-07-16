from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords


class MatchStatus(models.TextChoices):
    """Outcome of evaluating a Verbal Autopsy against the Death register."""

    MATCHED_AUTO = "matched_auto", "Automatically matched"
    MATCHED_MANUAL = "matched_manual", "Manually confirmed"
    PENDING_REVIEW = "pending_review", "Pending manual review"
    AMBIGUOUS = "ambiguous", "Ambiguous (multiple close candidates)"
    UNMATCHED = "unmatched", "Unmatched"
    NO_DEATH_EXPECTED = "no_death_expected", "No Death record expected"
    REJECTED = "rejected", "Rejected match"
    SKIPPED = "skipped", "Skipped"
    ERROR = "error", "Matching error"


class MatchMethod(models.TextChoices):
    """How the linked Death record was identified."""

    EXACT_DEATH_ID = "exact_death_id", "Exact Death ID"
    EVENT_BRIDGE = "event_bridge", "Case Management event"
    FUZZY_DEMOGRAPHIC = "fuzzy_demographic", "Fuzzy demographic"
    MANUAL = "manual", "Manual"


class VADeathMatch(models.Model):
    """The single source of truth linking one Verbal Autopsy to a Death record.

    Modelled on ``HouseholdDuplicate`` (dedicated audit model + simple-history)
    rather than adding columns to the 200+-field VA tables. Exactly one of
    ``verbal_autopsy`` / ``vasa`` is set (one match row per VA). ``death`` is a
    nullable FK (a single Death may legitimately back both a VerbalAutopsy and a
    VASA for the same deceased person). The link is only authoritative when
    ``status`` is one of the matched states; review/ambiguous/unmatched rows
    record the evaluation without asserting a link.
    """

    # --- The Verbal Autopsy side (exactly one set) ---
    verbal_autopsy = models.OneToOneField(
        "va_data_management.VerbalAutopsy",
        related_name="va_death_match",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    vasa = models.OneToOneField(
        "va_data_management.VASA",
        related_name="va_death_match",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )

    # --- The Death side ---
    death = models.ForeignKey(
        "va_data_management.Death",
        related_name="va_matches",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # Retained for audit if the Death row is later removed.
    death_id_snapshot = models.TextField(blank=True, null=True)

    status = models.CharField(
        max_length=32,
        choices=MatchStatus.choices,
        default=MatchStatus.UNMATCHED,
        db_index=True,
    )
    method = models.CharField(
        max_length=32, choices=MatchMethod.choices, blank=True, null=True
    )
    score = models.FloatField(default=0.0)
    # Gap between the best and second-best candidate (guards ambiguous matches).
    score_separation = models.FloatField(default=0.0)
    is_manual = models.BooleanField(default=False)
    source = models.CharField(max_length=64, blank=True, null=True)
    algorithm_version = models.CharField(max_length=32, blank=True, null=True)

    # --- Explanation blobs (auditability) ---
    supporting_fields = models.JSONField(default=list, blank=True)
    contradictory_fields = models.JSONField(default=list, blank=True)
    missing_fields = models.JSONField(default=list, blank=True)
    alternatives = models.JSONField(default=list, blank=True)

    # --- Lifecycle ---
    matched_at = models.DateTimeField(null=True, blank=True)
    last_evaluated_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="va_death_matches_confirmed",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="va_death_matches_reviewed",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    rejected_reason = models.TextField(blank=True, null=True)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    # last_evaluated_at churns on every sweep; keep it out of the audit trail.
    history = HistoricalRecords(excluded_fields=["last_evaluated_at", "updated"])

    class Meta:
        verbose_name = "VA–Death match"
        verbose_name_plural = "VA–Death matches"
        indexes = [
            models.Index(fields=["death"], name="vadeathmatch_death_idx"),
            models.Index(fields=["status"], name="vadeathmatch_status_idx"),
        ]
        constraints = [
            # Exactly one VA side must be set.
            models.CheckConstraint(
                check=(
                    models.Q(verbal_autopsy__isnull=False, vasa__isnull=True)
                    | models.Q(verbal_autopsy__isnull=True, vasa__isnull=False)
                ),
                name="vadeathmatch_exactly_one_va",
            ),
        ]

    @property
    def va(self):
        """The Verbal Autopsy this row evaluates (VerbalAutopsy or VASA)."""
        return self.verbal_autopsy or self.vasa

    @property
    def is_linked(self):
        return self.death_id is not None and self.status in {
            MatchStatus.MATCHED_AUTO,
            MatchStatus.MATCHED_MANUAL,
        }

    def __str__(self):
        target = self.va
        return f"[{self.status}] {target} -> {self.death} ({self.score:.0f}%)"
