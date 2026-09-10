"""The fund layer: the loop, the approvals a person acts on, and the audit trail.

Everything consequential in Hedge Fund mode passes through one of these three.
The loop says what the shape is, the queue is where AI-proposed actions wait for
a person, and the audit log is the record that makes either answerable
afterwards.
"""

from forge.hedgefund.approvals import (
    DEFAULT_TTL_SECONDS,
    ApprovalError,
    ApprovalQueue,
    ApprovalRequest,
    ApprovalStatus,
)
from forge.hedgefund.audit import MAX_PAYLOAD, AuditEntry, AuditLog, Outcome
from forge.hedgefund.loop import (
    LOOP,
    STAGES,
    Stage,
    StageSpec,
    StageState,
    StageStatus,
    describe,
    next_stage,
    unknown,
)

__all__ = [
    "DEFAULT_TTL_SECONDS",
    "LOOP",
    "MAX_PAYLOAD",
    "STAGES",
    "ApprovalError",
    "ApprovalQueue",
    "ApprovalRequest",
    "ApprovalStatus",
    "AuditEntry",
    "AuditLog",
    "Outcome",
    "Stage",
    "StageSpec",
    "StageState",
    "StageStatus",
    "describe",
    "next_stage",
    "unknown",
]
