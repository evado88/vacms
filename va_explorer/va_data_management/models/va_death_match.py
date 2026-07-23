from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.utils import OperationalError, ProgrammingError
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
    NRC = "nrc", "National ID (NRC)"
    NAME_NRC_EXACT = "name_nrc_exact", "Exact name + NRC"
    NAME_NRC_PROBABILISTIC = (
        "name_nrc_probabilistic",
        "Probabilistic name + NRC",
    )
    DEATH_REG_NO = "death_reg_no", "Death registration number"
    EXACT_COMPOSITE = "exact_composite", "Exact composite (name+sex+DOD+district)"
    FUZZY_DEMOGRAPHIC = "fuzzy_demographic", "Fuzzy demographic"  # legacy (v1)
    PROBABILISTIC = "probabilistic", "Probabilistic score"
    MANUAL = "manual", "Manual"


class ReviewMode(models.TextChoices):
    DESK = "desk", "Desk review"
    IN_PERSON = "in_person", "In-person review"


class ReviewReason(models.TextChoices):
    NRC_NAME_MISSING = "nrc_match_name_missing", "NRC match with missing name"
    NRC_NAME_CONFLICT = "nrc_match_name_conflict", "NRC match with name conflict"
    NRC_GIVEN_NAME_CONFLICT = (
        "nrc_match_given_name_conflict",
        "NRC and surname match with given-name conflict",
    )
    NRC_DUPLICATE = "nrc_duplicate", "NRC appears on multiple Death records"
    HARD_CONTRADICTION = "hard_contradiction", "Hard demographic contradiction"
    CLOSE_CANDIDATES = "close_candidates", "Top candidates are too close"
    SCORE_REVIEW_BAND = "score_review_band", "Score is in the review band"
    PREVIOUS_LINK_CHANGED = "previous_link_changed", "Previous link changed"


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
    # Per-field contribution snapshot (list of FieldComparison dicts).
    comparisons = models.JSONField(default=list, blank=True)
    # Which blocking passes surfaced the winning candidate (v2 union blocking).
    block_sources = models.JSONField(default=list, blank=True)
    # Name of the MatchingConfig row the evaluation ran under.
    config_version = models.CharField(max_length=64, blank=True, null=True)
    # Effective config and generated-crosswalk revision make an evaluation
    # reproducible after mutable admin configuration changes.
    config_snapshot = models.JSONField(default=dict, blank=True)
    crosswalk_version = models.CharField(max_length=128, blank=True, null=True)
    review_reason = models.CharField(
        max_length=64,
        choices=ReviewReason.choices,
        blank=True,
        null=True,
        db_index=True,
    )
    review_mode = models.CharField(
        max_length=32, choices=ReviewMode.choices, blank=True, null=True, db_index=True
    )
    last_error = models.JSONField(default=dict, blank=True)
    decision_version = models.PositiveIntegerField(default=0)

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
            models.Index(
                fields=["review_mode", "review_reason"],
                name="vadeathmatch_review_idx",
            ),
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
            models.CheckConstraint(
                check=(
                    ~models.Q(
                        status__in=[
                            MatchStatus.MATCHED_AUTO,
                            MatchStatus.MATCHED_MANUAL,
                        ]
                    )
                    | models.Q(death__isnull=False)
                ),
                name="vadeathmatch_linked_has_death",
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


class MatchingConfig(models.Model):
    """Tunable weights/thresholds for the VA<->Death matching engine.

    Exactly one row is active at a time; ``active()`` merges it over the
    engine/settings defaults so a partially-filled row is safe. Every change is
    audited via simple-history (the spec requires a config audit trail), so
    recalibration is: edit the row (admin), run ``link_deaths_to_vas
    --calibrate``, compare, then apply.
    """

    name = models.CharField(max_length=64, unique=True, default="default")
    is_active = models.BooleanField(default=False)
    # Partial overrides; anything not set here falls back to defaults.
    weights = models.JSONField(default=dict, blank=True)
    thresholds = models.JSONField(default=dict, blank=True)
    algorithm_version = models.CharField(max_length=32, default="2.0")
    notes = models.TextField(blank=True, null=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="matching_configs_updated",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    history = HistoricalRecords(excluded_fields=["updated"])

    class Meta:
        verbose_name = "Matching configuration"
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="matchingconfig_single_active",
            ),
        ]

    @classmethod
    def active(cls, name=None):
        """Return the merged engine config dict (never raises).

        ``{"name", "weights", "thresholds", "algorithm_version"}`` — DB row
        values overlaid on the defaults from ``utils.matching.config``.
        """
        from va_explorer.va_data_management.utils.matching.config import (
            default_thresholds,
            default_weights,
        )

        merged = {
            "name": "defaults",
            "weights": default_weights(),
            "thresholds": default_thresholds(),
            "algorithm_version": "2.0",
        }
        row = None
        try:
            qs = (
                cls.objects.filter(name=name)
                if name
                else cls.objects.filter(is_active=True)
            )
            row = qs.first()
        except (OperationalError, ProgrammingError):  # pragma: no cover - pre-migration
            row = None
        if name and row is None:
            raise cls.DoesNotExist(f"MatchingConfig '{name}' does not exist.")
        if row is not None:
            merged["name"] = row.name
            merged["weights"].update(row.weights or {})
            merged["thresholds"].update(row.thresholds or {})
            merged["algorithm_version"] = row.algorithm_version
        return merged

    def __str__(self):
        flag = " (active)" if self.is_active else ""
        return f"MatchingConfig {self.name}{flag} v{self.algorithm_version}"

    def save(self, *args, **kwargs):
        if self.pk:
            prior = type(self).objects.filter(pk=self.pk).first()
            changed_revision = prior and any(
                (
                    prior.name != self.name,
                    prior.weights != self.weights,
                    prior.thresholds != self.thresholds,
                    prior.algorithm_version != self.algorithm_version,
                )
            )
            if (
                changed_revision
                and VADeathMatch.objects.filter(config_version=prior.name).exists()
            ):
                raise ValidationError(
                    "A matching config referenced by evaluations is immutable; "
                    "create and activate a new named revision instead."
                )
        return super().save(*args, **kwargs)


class VADeathMatchRejection(models.Model):
    """A reviewer-rejected (VA, Death) pair — never re-proposed by the engine.

    ``VADeathMatch`` is one row per VA holding only the *current* proposal, so
    a rejection must live in its own pair table to survive the next evaluation
    proposing a different death (and to accumulate multiple rejections per VA).
    """

    verbal_autopsy = models.ForeignKey(
        "va_data_management.VerbalAutopsy",
        related_name="death_match_rejections",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    vasa = models.ForeignKey(
        "va_data_management.VASA",
        related_name="death_match_rejections",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    death = models.ForeignKey(
        "va_data_management.Death",
        related_name="va_match_rejections",
        on_delete=models.CASCADE,
    )
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="va_death_match_rejections",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    reason = models.TextField(blank=True, null=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "VA–Death match rejection"
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(verbal_autopsy__isnull=False, vasa__isnull=True)
                    | models.Q(verbal_autopsy__isnull=True, vasa__isnull=False)
                ),
                name="vadeathrej_exactly_one_va",
            ),
            models.UniqueConstraint(
                fields=["verbal_autopsy", "death"],
                condition=models.Q(verbal_autopsy__isnull=False),
                name="vadeathrej_unique_va_death",
            ),
            models.UniqueConstraint(
                fields=["vasa", "death"],
                condition=models.Q(vasa__isnull=False),
                name="vadeathrej_unique_vasa_death",
            ),
        ]

    @classmethod
    def rejected_death_pks(cls, va) -> set:
        """PKs of deaths a reviewer rejected for this VA/VASA."""
        if va._meta.model_name == "vasa":
            qs = cls.objects.filter(vasa=va)
        else:
            qs = cls.objects.filter(verbal_autopsy=va)
        return set(qs.values_list("death_id", flat=True))

    @property
    def va(self):
        return self.verbal_autopsy or self.vasa

    def __str__(self):
        return f"Rejected: {self.va} -x-> {self.death}"


class VADeathMatchDecision(models.Model):
    """Immutable audit event for a human linkage decision."""

    class Action(models.TextChoices):
        CONFIRM = "confirm", "Confirm"
        REJECT = "reject", "Reject"
        UNLINK = "unlink", "Unlink"
        REASSIGN = "reassign", "Reassign"

    match = models.ForeignKey(
        VADeathMatch,
        related_name="decisions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    va_model = models.CharField(max_length=32)
    va_pk = models.PositiveIntegerField()
    va_identifier = models.TextField(blank=True, null=True)
    action = models.CharField(max_length=16, choices=Action.choices)
    prior_death = models.ForeignKey(
        "va_data_management.Death",
        related_name="va_link_decisions_from",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    new_death = models.ForeignKey(
        "va_data_management.Death",
        related_name="va_link_decisions_to",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    prior_death_id_snapshot = models.TextField(blank=True, null=True)
    new_death_id_snapshot = models.TextField(blank=True, null=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="va_death_match_decisions",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    reason = models.TextField(blank=True, null=True)
    evidence = models.JSONField(default=dict, blank=True)
    config_snapshot = models.JSONField(default=dict, blank=True)
    config_version = models.CharField(max_length=64, blank=True, null=True)
    crosswalk_version = models.CharField(max_length=128, blank=True, null=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "VA-Death match decision"
        ordering = ["-created", "-pk"]
        indexes = [
            models.Index(fields=["va_model", "va_pk"], name="vadeathdecision_va_idx"),
            models.Index(
                fields=["action", "created"], name="vadeathdecision_action_idx"
            ),
        ]

    def __str__(self):
        return f"{self.action}: {self.va_model}#{self.va_pk}"

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("VA-Death match decisions are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("VA-Death match decisions are immutable.")
