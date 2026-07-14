"""VA -> Death record matching engine.

Public API (import from here, not the submodules):

    from va_explorer.va_data_management.utils.matching import (
        evaluate_va,
        auto_link_verbal_autopsy_to_death,
        run_matching,
        MatchResult,
    )
"""

from .results import MatchResult
from .service import (
    auto_link_verbal_autopsy_to_death,
    evaluate_va,
    run_matching,
    schedule_link_for_va_ids,
)

__all__ = [
    "MatchResult",
    "evaluate_va",
    "auto_link_verbal_autopsy_to_death",
    "run_matching",
    "schedule_link_for_va_ids",
]
