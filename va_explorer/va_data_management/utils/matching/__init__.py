"""VA -> Death record matching engine.

Public API (import from here, not the submodules):

    from va_explorer.va_data_management.utils.matching import (
        evaluate_va,
        auto_link_verbal_autopsy_to_death,
        run_matching,
        MatchResult,
    )
"""

from .config import resolve_config
from .death_index import DeathIndex
from .profiles import LinkageProfile, build_death_profile, build_va_profile
from .results import MatchResult
from .service import (
    auto_link_verbal_autopsy_to_death,
    evaluate_va,
    matching_enabled,
    matching_mode,
    run_matching,
    schedule_link_for_va_ids,
)

__all__ = [
    "MatchResult",
    "LinkageProfile",
    "DeathIndex",
    "build_va_profile",
    "build_death_profile",
    "resolve_config",
    "evaluate_va",
    "auto_link_verbal_autopsy_to_death",
    "matching_enabled",
    "matching_mode",
    "run_matching",
    "schedule_link_for_va_ids",
]
