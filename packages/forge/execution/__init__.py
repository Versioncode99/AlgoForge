"""Execution lifecycle and authorization. Contains no order-placing code.

See `forge.execution.lifecycle` for why that absence is the point.
"""

from forge.execution.lifecycle import (
    EXECUTION_STAGES,
    LIVE_EXECUTION_AVAILABLE,
    LIVE_STAGES,
    RESEARCH_STAGES,
    Authorization,
    Stage,
    TransitionRefused,
    allowed_from,
    check_transition,
    requires_authorization,
)

__all__ = [
    "EXECUTION_STAGES",
    "LIVE_EXECUTION_AVAILABLE",
    "LIVE_STAGES",
    "RESEARCH_STAGES",
    "Authorization",
    "Stage",
    "TransitionRefused",
    "allowed_from",
    "check_transition",
    "requires_authorization",
]
