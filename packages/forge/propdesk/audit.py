"""The record of a consequential decision, with everything needed to review it.

§17 lists the fields. The list is long, and the length is the point: an audit row
that says "risk changed at 14:02" answers nothing a month later. This one carries
the state on both sides of the change, the configuration that was in force, the
strategy and its validation state, the policy state, the automation mode, the
reason, the disclosure version the operator had acknowledged, the approval
status, the system's decision and where execution ended up.

**Two things are deliberately absent.**

There is no field for a credential, a token or a session. `forge.propdesk.credentials`
makes that structural rather than a matter of discipline: a `Secret` refuses to
serialise and `CredentialRecord` refuses a public field named like one. A caller
that tried to put a password in `detail` would be putting a string there, and
`_REDACTED_KEYS` below catches the common shapes on the way in.

There is no `severity` or `score`. A number attached to an audit row invites
filtering by it, and the row that matters in an investigation is usually not the
one that looked important when it was written.

Written through `forge.propdesk.store`, whose table has no UPDATE and no DELETE.
It also mirrors to `forge.hedgefund.audit.AuditLog` when one is supplied, so a
fund operator reading one log is not missing desk decisions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel
from forge.propdesk.credentials import REDACTED, SENSITIVE_KEYS


class AuditAction(StrEnum):
    """What was done. A closed set: an audit log with free-text actions cannot
    be queried, and the first thing anybody does with one is ask "show me every
    risk change", which needs the vocabulary to be fixed."""

    RISK_MODE_CHANGED = "risk_mode_changed"
    RISK_BOUNDARIES_CHANGED = "risk_boundaries_changed"
    RISK_APPETITE_CHANGED = "risk_appetite_changed"
    RISK_ADJUSTED = "risk_adjusted"
    AI_CAPABILITIES_CHANGED = "ai_capabilities_changed"
    AUTONOMY_LEVEL_CHANGED = "autonomy_level_changed"
    DEPLOYMENT_EVALUATED = "deployment_evaluated"
    DEPLOYMENT_BLOCKED = "deployment_blocked"
    STRATEGY_ALLOCATED = "strategy_allocated"
    STRATEGY_SWITCHED = "strategy_switched"
    DISCLOSURE_ACKNOWLEDGED = "disclosure_acknowledged"
    COPY_GROUP_CHANGED = "copy_group_changed"
    ORDER_DISPATCHED = "order_dispatched"
    ORDER_REFUSED = "order_refused"


class Actor(StrEnum):
    HUMAN = "human"
    AI = "ai"
    #: The deterministic scaler or allocator acting under a configuration a
    #: person set. Not "ai": nothing inferred anything, and conflating the two
    #: would make "what did the AI do" unanswerable.
    SYSTEM = "system"


class ApprovalState(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


def _scrub(value: Any) -> Any:
    """Refuse to carry anything that looks like a secret into the record."""
    if isinstance(value, dict):
        return {
            key: REDACTED
            if any(marker in str(key).lower() for marker in SENSITIVE_KEYS)
            else _scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


class ConsequentialRecord(FrozenModel):
    """One reviewable decision."""

    action: AuditAction
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: Actor = Actor.HUMAN
    #: Who, by the name the surface knows them by. Never an email, a token or a
    #: session identifier.
    actor_name: str = ""
    account_uid: str = ""
    strategy_id: str = ""
    #: State before and after, as the caller sees it. Free-shaped because a risk
    #: change and an allocation change describe different things, and forcing
    #: them into one schema would mean recording less of both.
    previous: dict[str, Any] = Field(default_factory=dict)
    current: dict[str, Any] = Field(default_factory=dict)
    #: The risk configuration in force at the time.
    risk_mode: str = ""
    risk_fraction: float | None = None
    #: The strategy's validation state, verbatim from the judge.
    verdict: str = ""
    verdict_id: str = ""
    #: The programme policy's relevant permission, verbatim.
    policy_state: str = ""
    automation_mode: str = ""
    #: Why. Written by the caller from state it holds, never generated here.
    reason: str = ""
    disclosure_version: str = ""
    approval: ApprovalState = ApprovalState.NOT_REQUIRED
    approval_request_id: str = ""
    #: What the system decided, in its own vocabulary: cleared, blocked, held.
    decision: str = ""
    #: Where execution ended up, when the decision reached execution at all.
    execution_state: str = ""

    @field_validator("previous", "current", mode="before")
    @classmethod
    def _no_secrets(cls, value: Any) -> Any:
        return _scrub(value)

    @property
    def record_id(self) -> str:
        return stable_id(
            "desk-audit",
            {
                "action": self.action.value,
                "at": self.at.astimezone(UTC).isoformat(),
                "account": self.account_uid,
                "strategy": self.strategy_id,
                "reason": self.reason,
            },
        )

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["record_id"] = self.record_id
        return payload
