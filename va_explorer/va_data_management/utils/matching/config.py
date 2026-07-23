"""Default weights/thresholds for the v2 matching engine.

The live values come from ``MatchingConfig.active()`` (DB row overlaid on these
defaults); settings remain the fallback so the engine works before the config
row exists. Weights are per comparison-vector field; the score is a percentage
of agreement over comparable fields, so they need not sum to any fixed total.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _cfg(name, default):
    return getattr(settings, name, default)


def default_weights() -> dict:
    return {
        "name": 28,
        "dod": 22,
        "dob_or_age": 12,
        "sex": 10,
        "mother_name": 8,
        "ea": 8,
        "ward": 6,
        "gps": 5,
        "place_of_death": 4,
        "district": 3,
        "pregnancy": 3,
        # Supporting-only signals (bonus-capped or low weight).
        "household_head": 3,
        "informant": 2,
        "marital_status": 1,
        "education": 1,
        "occupation": 1,
        "nationality": 1,
    }


def default_thresholds() -> dict:
    return {
        "auto": _cfg("VA_DEATH_MATCH_AUTO_THRESHOLD", 90),
        "review": _cfg("VA_DEATH_MATCH_REVIEW_THRESHOLD", 80),
        "min_sep": _cfg("VA_DEATH_MATCH_MIN_SEPARATION", 5),
        # Ties within this margin of the best score go to review (AMBIGUOUS).
        "tie_margin": _cfg("VA_DEATH_MATCH_MIN_SEPARATION", 5),
        "tol_dod": _cfg("VA_DEATH_MATCH_MAX_DOD_TOLERANCE_DAYS", 3),
        "age_tol_years": _cfg("VA_DEATH_MATCH_MAX_AGE_TOLERANCE_YEARS", 3),
        # Union blocking: DOD block half-width (spec: ±60 days).
        "dod_block_days": _cfg("VA_DEATH_MATCH_DOD_BLOCK_DAYS", 60),
        "max_candidates": _cfg("VA_DEATH_MATCH_MAX_CANDIDATES", 50),
        "max_candidates_per_block": _cfg(
            "VA_DEATH_MATCH_MAX_CANDIDATES_PER_BLOCK", 200
        ),
        "max_total_candidates": _cfg("VA_DEATH_MATCH_MAX_TOTAL_CANDIDATES", 300),
        # Conservative shadow-mode starting values. Activate only after labelled
        # holdout validation reaches the approved precision target.
        "name_nrc_auto_similarity": _cfg("VA_DEATH_MATCH_NAME_NRC_AUTO_SIMILARITY", 95),
        "name_nrc_surname_min_similarity": _cfg(
            "VA_DEATH_MATCH_NAME_NRC_SURNAME_MIN_SIMILARITY", 95
        ),
        "name_nrc_given_min_similarity": _cfg(
            "VA_DEATH_MATCH_NAME_NRC_GIVEN_MIN_SIMILARITY", 90
        ),
        "name_nrc_probabilistic_auto_enabled": _cfg(
            "VA_DEATH_MATCH_NAME_NRC_PROBABILISTIC_AUTO_ENABLED", False
        ),
        "contradiction_penalty_fraction": _cfg(
            "VA_DEATH_MATCH_CONTRADICTION_PENALTY_FRACTION", 0.5
        ),
    }


def validate_config(config: dict) -> dict:
    """Validate the effective matching configuration before evaluating records."""
    weights = config.get("weights") or {}
    thresholds = config.get("thresholds") or {}
    unknown_weights = set(weights) - set(default_weights())
    unknown_thresholds = set(thresholds) - set(default_thresholds())
    if unknown_weights:
        raise ImproperlyConfigured(
            f"MatchingConfig contains unknown weights: {sorted(unknown_weights)}"
        )
    if unknown_thresholds:
        raise ImproperlyConfigured(
            "MatchingConfig contains unknown thresholds: "
            f"{sorted(unknown_thresholds)}"
        )
    for key, value in weights.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise ImproperlyConfigured(
                f"MatchingConfig weight '{key}' must be a non-negative number."
            )

    bounded = {
        "auto",
        "review",
        "name_nrc_auto_similarity",
        "name_nrc_surname_min_similarity",
        "name_nrc_given_min_similarity",
    }
    for key in bounded:
        value = thresholds.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ImproperlyConfigured(
                f"MatchingConfig threshold '{key}' must be numeric."
            )
        if not 0 <= value <= 100:
            raise ImproperlyConfigured(
                f"MatchingConfig threshold '{key}' must be between 0 and 100."
            )
    if thresholds["review"] > thresholds["auto"]:
        raise ImproperlyConfigured(
            "MatchingConfig review threshold cannot exceed auto threshold."
        )
    for key in (
        "min_sep",
        "tie_margin",
        "tol_dod",
        "age_tol_years",
        "dod_block_days",
        "max_candidates",
        "max_candidates_per_block",
        "max_total_candidates",
    ):
        value = thresholds.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise ImproperlyConfigured(
                f"MatchingConfig threshold '{key}' must be a non-negative number."
            )
    if (
        thresholds["max_candidates_per_block"] < 1
        or thresholds["max_total_candidates"] < 1
    ):
        raise ImproperlyConfigured("MatchingConfig candidate limits must be positive.")
    if not isinstance(thresholds["name_nrc_probabilistic_auto_enabled"], bool):
        raise ImproperlyConfigured(
            "MatchingConfig threshold 'name_nrc_probabilistic_auto_enabled' "
            "must be boolean."
        )
    penalty = thresholds["contradiction_penalty_fraction"]
    if (
        not isinstance(penalty, (int, float))
        or isinstance(penalty, bool)
        or not 0 <= penalty <= 1
    ):
        raise ImproperlyConfigured(
            "MatchingConfig threshold 'contradiction_penalty_fraction' must "
            "be between 0 and 1."
        )
    return config


def resolve_config(name=None) -> dict:
    """The merged engine config (thin wrapper over ``MatchingConfig.active``)."""
    from va_explorer.va_data_management.models import MatchingConfig

    return validate_config(MatchingConfig.active(name=name))
