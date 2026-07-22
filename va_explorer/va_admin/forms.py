from django import forms
from django.core.exceptions import ValidationError

from va_explorer.va_data_management.utils.matching.config import (
    default_thresholds,
    validate_config,
)


class LinkageConfigurationForm(forms.Form):
    """Create an immutable, active revision of the linkage engine settings."""

    revision_name = forms.SlugField(
        max_length=64,
        label="Configuration revision name",
        help_text="A new revision is created and activated; prior evaluations retain their original settings.",
    )
    auto = forms.FloatField(
        min_value=0,
        max_value=100,
        label="Auto-link score threshold (%)",
        help_text="If the best candidate reaches this score, the record can be linked automatically.",
    )
    review = forms.FloatField(
        min_value=0,
        max_value=100,
        label="Manual review score threshold (%)",
        help_text="The best candidate is sent for review when its score reaches this value but remains below the auto-link threshold.",
    )
    min_sep = forms.FloatField(
        min_value=0,
        label="Minimum best-vs-next score gap",
        help_text="The best candidate must exceed the next-best candidate by at least this many score points before automatic linking.",
    )
    tie_margin = forms.FloatField(
        min_value=0,
        label="Close-candidate review gap",
        help_text="Candidates within this score gap are always routed to review.",
    )
    tol_dod = forms.FloatField(
        min_value=0,
        label="Date-of-death tolerance (days)",
        help_text="Records are treated as a possible death match when the dates of death fall within this many days.",
    )
    age_tol_years = forms.FloatField(
        min_value=0,
        label="Age tolerance (years)",
        help_text="Age differences within this tolerance contribute partial supporting evidence to the match score.",
    )
    dod_block_days = forms.FloatField(
        min_value=0,
        label="Candidate date search window (days)",
        help_text="Adds deaths within this many days to the candidate pool; candidates may also enter through other matching blocks.",
    )
    max_candidates_per_block = forms.IntegerField(
        min_value=1,
        label="Maximum candidates per matching block",
        help_text="Caps how many candidate records each blocking key may contribute before scoring starts.",
    )
    max_total_candidates = forms.IntegerField(
        min_value=1,
        label="Maximum total candidates per record",
        help_text="Stops the matcher from evaluating too many candidates for a single record.",
    )
    name_nrc_probabilistic_auto_enabled = forms.BooleanField(
        required=False,
        label="Allow automatic name/NRC-only links",
        help_text="Keep disabled until local validation confirms it is sufficiently precise.",
    )
    name_nrc_auto_similarity = forms.FloatField(
        min_value=0,
        max_value=100,
        label="Name/NRC auto-link similarity (%)",
        help_text="When the name/NRC-only score reaches this level, the record can auto-link if the feature is enabled.",
    )
    name_nrc_surname_min_similarity = forms.FloatField(
        min_value=0,
        max_value=100,
        label="Minimum surname similarity (%)",
        help_text="Surname similarity must meet this floor before the name/NRC shortcut is considered.",
    )
    name_nrc_given_min_similarity = forms.FloatField(
        min_value=0,
        max_value=100,
        label="Minimum given-name similarity (%)",
        help_text="Given-name similarity must meet this floor before the name/NRC shortcut is considered.",
    )
    contradiction_penalty_fraction = forms.FloatField(
        min_value=0,
        max_value=1,
        label="Contradictory evidence penalty",
        help_text="Fraction of the score removed when evidence conflicts (0 to 1).",
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Revision notes",
        help_text="Use this to record why the revision was created or what changed.",
    )

    threshold_fields = (
        "auto",
        "review",
        "min_sep",
        "tie_margin",
        "tol_dod",
        "age_tol_years",
        "dod_block_days",
        "max_candidates_per_block",
        "max_total_candidates",
        "name_nrc_probabilistic_auto_enabled",
        "name_nrc_auto_similarity",
        "name_nrc_surname_min_similarity",
        "name_nrc_given_min_similarity",
        "contradiction_penalty_fraction",
    )

    def __init__(self, *args, config=None, **kwargs):
        initial = kwargs.pop("initial", {})
        if config:
            thresholds = config["thresholds"]
            config_initial = {name: thresholds[name] for name in self.threshold_fields}
            config_initial.update(initial)
            initial = config_initial
        kwargs["initial"] = initial
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "form-control")
        self.fields["name_nrc_probabilistic_auto_enabled"].widget.attrs[
            "class"
        ] = "custom-control-input"

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        try:
            validate_config(
                {
                    "weights": {},
                    "thresholds": {
                        **default_thresholds(),
                        **{name: cleaned[name] for name in self.threshold_fields},
                    },
                }
            )
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        except Exception as exc:
            raise forms.ValidationError(str(exc)) from exc
        return cleaned

    def thresholds(self):
        return {name: self.cleaned_data[name] for name in self.threshold_fields}
