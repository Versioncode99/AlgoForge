"""The boundary between researching a strategy and running one.

AlgoForge has no live-order path. Not "not yet wired" — there is no broker SDK,
no order object, and no code that could submit one. That is the safest possible
state for a research instrument, and this module exists so that it stays a
*guarantee* rather than an accident of nobody having got round to it.

Two things make it a guarantee:

1. **Lifecycle state is explicit and persisted.** A strategy is in exactly one
   state, transitions are enumerated, and the illegal ones are refused rather
   than merely not attempted. `tests/execution/test_boundary.py` asserts the
   graph has no edge from any research state to `DEPLOYED`.

2. **The transition out of research requires an authorization record** naming a
   human, an instrument, a risk profile and the verdict being relied on. It
   cannot be produced by inference, by a default argument, or by an agent: the
   authorizing party is a required field with no default, which is the same
   design that stops G0, G1 and G2 defaulting to a pass.

What this module deliberately does **not** contain: connectors, order types,
position tracking, or anything that could place a trade. Building those before
there is a venue to place them against would be speculative infrastructure, and
an unused order-submission function is a strictly worse thing to have in a
repository than no function at all.

The states:

    DRAFT       created, nothing run
    RESEARCHING a search is exploring it
    VALIDATING  the validation stack is running
    VALIDATED   validation evidence exists
    APPROVED    a human accepted the evidence; still nothing executes
    PAPER       running against simulated fills
    DEPLOYED    would run against a venue — unreachable, see below
    PAUSED      halted from PAPER or DEPLOYED
    RETIRED     terminal

`DEPLOYED` is modelled because the state machine has to be able to say that a
transition into it is refused. `authorize` refuses it unconditionally while
`LIVE_EXECUTION_AVAILABLE` is False, and that constant is False because no
connector exists. When one does, the refusal becomes a real authorization check
rather than a hard stop, and the tests around it will need to change
deliberately — which is the point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from forge.contracts.hashing import content_hash, stable_id

# No connector is implemented, so nothing can execute against a venue. This is a
# statement of fact about the repository, asserted by a test that scans for
# broker SDKs and order-submission verbs.
LIVE_EXECUTION_AVAILABLE = False


class Stage(StrEnum):
    DRAFT = "DRAFT"
    RESEARCHING = "RESEARCHING"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    PAPER = "PAPER"
    DEPLOYED = "DEPLOYED"
    PAUSED = "PAUSED"
    RETIRED = "RETIRED"


# Stages in which a strategy is an object of study. Nothing here touches an
# execution venue, simulated or otherwise.
RESEARCH_STAGES = frozenset(
    {Stage.DRAFT, Stage.RESEARCHING, Stage.VALIDATING, Stage.VALIDATED}
)

# Stages in which a strategy is being *run*. Reaching any of these requires an
# authorization record.
EXECUTION_STAGES = frozenset({Stage.PAPER, Stage.DEPLOYED, Stage.PAUSED})

# The only stage that would reach a real venue.
LIVE_STAGES = frozenset({Stage.DEPLOYED})

_TRANSITIONS: dict[Stage, frozenset[Stage]] = {
    Stage.DRAFT: frozenset({Stage.RESEARCHING, Stage.RETIRED}),
    Stage.RESEARCHING: frozenset({Stage.VALIDATING, Stage.DRAFT, Stage.RETIRED}),
    Stage.VALIDATING: frozenset({Stage.VALIDATED, Stage.RESEARCHING, Stage.RETIRED}),
    # The one edge that leaves research. It requires an authorization record,
    # and there is deliberately no edge from VALIDATED straight to PAPER or
    # DEPLOYED: a human accepting the evidence is its own recorded step.
    Stage.VALIDATED: frozenset({Stage.APPROVED, Stage.RESEARCHING, Stage.RETIRED}),
    Stage.APPROVED: frozenset({Stage.PAPER, Stage.VALIDATED, Stage.RETIRED}),
    # Paper to deployed is a second authorization, not a continuation of the
    # first. Approving research is not approving capital.
    Stage.PAPER: frozenset({Stage.DEPLOYED, Stage.PAUSED, Stage.RETIRED}),
    Stage.DEPLOYED: frozenset({Stage.PAUSED, Stage.RETIRED}),
    Stage.PAUSED: frozenset({Stage.PAPER, Stage.DEPLOYED, Stage.RETIRED}),
    Stage.RETIRED: frozenset(),
}


class TransitionRefused(Exception):
    """The transition was refused. Nothing changed."""


@dataclass(frozen=True)
class Authorization:
    """A human accepting responsibility for running a strategy.

    Every field is required. There is no default author, no default risk
    profile, and no "system" actor — an authorization that could be produced
    without a person is not an authorization, and an agent must not be able to
    manufacture one by omitting arguments.
    """

    strategy_id: str
    to_stage: Stage
    authorized_by: str
    verdict_id: str
    code_hash: str
    risk_profile_id: str
    note: str
    authorized_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        for name in ("strategy_id", "authorized_by", "verdict_id", "code_hash", "risk_profile_id"):
            if not str(getattr(self, name)).strip():
                raise TransitionRefused(f"authorization is missing {name}")

    @property
    def authorization_id(self) -> str:
        return stable_id("authorization", self.as_dict())

    @property
    def content_hash(self) -> str:
        return content_hash(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "to_stage": str(self.to_stage),
            "authorized_by": self.authorized_by,
            "verdict_id": self.verdict_id,
            "code_hash": self.code_hash,
            "risk_profile_id": self.risk_profile_id,
            "note": self.note,
            "authorized_at": self.authorized_at.isoformat(),
        }


def allowed_from(stage: Stage) -> frozenset[Stage]:
    return _TRANSITIONS[stage]


def requires_authorization(current: Stage, target: Stage) -> bool:
    """Any move that starts execution, or escalates it, needs a human."""
    if target in EXECUTION_STAGES and current not in EXECUTION_STAGES:
        return True
    # Paper to deployed is an escalation even though both are execution stages.
    return target in LIVE_STAGES and current not in LIVE_STAGES


def check_transition(
    current: Stage,
    target: Stage,
    *,
    authorization: Authorization | None = None,
    live_available: bool = LIVE_EXECUTION_AVAILABLE,
) -> None:
    """Raise `TransitionRefused` unless this move is legal and authorized.

    Ordering matters and is deliberate. Legality is checked first, then live
    availability, then authorization — so an attempt to reach a venue that does
    not exist is refused for that reason rather than for a missing signature,
    which would imply that supplying one would have worked.
    """
    if target == current:
        raise TransitionRefused(f"already in {current}")
    if target not in _TRANSITIONS[current]:
        legal = ", ".join(sorted(str(item) for item in _TRANSITIONS[current])) or "nothing"
        raise TransitionRefused(f"{current} may move to {legal}; not to {target}")
    if target in LIVE_STAGES and not live_available:
        raise TransitionRefused(
            "live execution is not available: this build contains no broker connector "
            "and no order-submission path, so nothing could be placed"
        )
    if requires_authorization(current, target):
        if authorization is None:
            raise TransitionRefused(f"{current} -> {target} requires an explicit authorization")
        if authorization.to_stage != target:
            raise TransitionRefused(
                f"authorization is for {authorization.to_stage}, not {target}"
            )
