"""Default weights/thresholds for the v2 matching engine.

The live values come from ``MatchingConfig.active()`` (DB row overlaid on these
defaults); settings remain the fallback so the engine works before the config
row exists. Weights are per comparison-vector field; the score is a percentage
of agreement over comparable fields, so they need not sum to any fixed total.
"""

from django.conf import settings


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
    }


def resolve_config(name=None) -> dict:
    """The merged engine config (thin wrapper over ``MatchingConfig.active``)."""
    from va_explorer.va_data_management.models import MatchingConfig

    return MatchingConfig.active(name=name)
