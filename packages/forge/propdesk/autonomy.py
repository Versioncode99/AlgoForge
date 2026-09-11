"""Autonomous deployment: one gate list, three things that happen after it.

The temptation in a feature like this is to make the autonomy level select how
carefully to check. That is exactly backwards, and this module is built so that
it cannot be written that way even by accident: `MANDATORY_GATES` is a single
module-level tuple, every level evaluates that same object, and
`test_full_autonomy_does_not_shorten_the_gate_list` asserts identity rather than
equality — so a future edit that builds a shorter list for the autonomous path
fails rather than ships.

    OFF                  the recommendation is recorded and nothing happens.
    APPROVAL_REQUIRED    it goes to the existing approval queue, where a person
                         acts on it and it executes as them.
    FULLY_AUTONOMOUS     it proceeds — but only if every gate passed, which is
                         the same condition the other two levels are judged by.

**The approval queue is not reimplemented.** `forge.hedgefund.approvals`
already does the hard parts: a request holds no capability, approving executes
through the same registry the interface uses so arguments re-validate against
present state, and a request expires rather than running late on stale prices.
This module produces the request; that one owns it.

**A standing consequence worth stating plainly.**
`forge.execution.lifecycle.LIVE_EXECUTION_AVAILABLE` is `False` and
`check_transition` refuses `DEPLOYED` outright, naming the missing connector. So
autonomous *live* deployment is not merely unimplemented here — it is refused by
a control this module does not own, cannot modify, and consults as a gate like
any other.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from forge.contracts.models import FrozenModel
from forge.propdesk.allocation import HealthGrade, StrategyHealth, grade_health
from forge.propdesk.consent import DisclosureKey
from forge.propdesk.policy import Permission
from forge.propdesk.risk import RiskMode


class AutonomyError(Exception):
    """A deployment request was malformed, with a reason to show verbatim."""


class AutonomyLevel(StrEnum):
    OFF = "off"
    APPROVAL_REQUIRED = "approval_required"
    FULLY_AUTONOMOUS = "fully_autonomous"


LEVEL_LABEL: dict[AutonomyLevel, str] = {
    AutonomyLevel.OFF: "Off",
    AutonomyLevel.APPROVAL_REQUIRED: "Approval required",
    AutonomyLevel.FULLY_AUTONOMOUS: "Fully autonomous",
}

LEVEL_DETAIL: dict[AutonomyLevel, str] = {
    AutonomyLevel.OFF: (
        "Recommendations are recorded and shown. Nothing is deployed without you doing "
        "it yourself."
    ),
    AutonomyLevel.APPROVAL_REQUIRED: (
        "A recommendation that clears every mandatory control is put to you for "
        "approval. Approving runs it as you, against the state that exists at that "
        "moment; a request that sits too long expires rather than running late."
    ),
    AutonomyLevel.FULLY_AUTONOMOUS: (
        "A recommendation that clears every mandatory control proceeds without you. "
        "The controls are identical to the other two levels; the only difference is "
        "that you are not asked."
    ),
}


class GateKind(StrEnum):
    VALIDATION = "validation"
    EVIDENCE_TIER = "evidence_tier"
    ROBUSTNESS = "robustness"
    STRATEGY_HEALTH = "strategy_health"
    ACCOUNT_BINDING = "account_binding"
    PROP_POLICY = "prop_policy"
    RISK_ENVELOPE = "risk_envelope"
    INSTRUMENT = "instrument"
    CONNECTION = "connection"
    PRETRADE = "pretrade"
    ACCOUNT_BUFFER = "account_buffer"
    LIFECYCLE = "lifecycle"
    DISCLOSURE = "disclosure"


class Gate(FrozenModel):
    """One mandatory condition, and what it is asking."""

    kind: GateKind
    name: str
    question: str


#: Every condition autonomous deployment requires. One tuple, evaluated
#: identically at every level. Order is the order the panel lists them, which is
#: roughly cheapest-to-check first so a reader sees the obvious refusals before
#: the subtle ones.
MANDATORY_GATES: tuple[Gate, ...] = (
    Gate(
        kind=GateKind.VALIDATION,
        name="Validated",
        question="has the judge returned PASS for this strategy?",
    ),
    Gate(
        kind=GateKind.EVIDENCE_TIER,
        name="Evidence tier",
        question="is the evidence out-of-sample, holdout or forward rather than a sweep?",
    ),
    Gate(
        kind=GateKind.ROBUSTNESS,
        name="Robustness",
        question=(
            "were walk-forward stability and path robustness measured, and did both survive?"
        ),
    ),
    Gate(
        kind=GateKind.STRATEGY_HEALTH,
        name="Strategy health",
        question="is the strategy graded healthy rather than drifting or unproven?",
    ),
    Gate(
        kind=GateKind.ACCOUNT_BINDING,
        name="Account",
        question="is the target account bound to a connection AlgoForge knows about?",
    ),
    Gate(
        kind=GateKind.PROP_POLICY,
        name="Firm policy",
        question="does the recorded programme policy permit automated orders on this account?",
    ),
    Gate(
        kind=GateKind.RISK_ENVELOPE,
        name="Risk envelope",
        question="is a risk configuration in force, and does it permit a non-zero size?",
    ),
    Gate(
        kind=GateKind.INSTRUMENT,
        name="Instrument",
        question="is the instrument permitted on this account and mapped for its provider?",
    ),
    Gate(
        kind=GateKind.CONNECTION,
        name="Connection",
        question="is the execution connection live?",
    ),
    Gate(
        kind=GateKind.PRETRADE,
        name="Pre-trade",
        question="did a representative order clear the pre-trade gate?",
    ),
    Gate(
        kind=GateKind.ACCOUNT_BUFFER,
        name="Buffer",
        question="does the account have enough buffer to its loss floor to pay for a contract?",
    ),
    Gate(
        kind=GateKind.LIFECYCLE,
        name="Lifecycle",
        question="is the lifecycle transition to this stage legal and available?",
    ),
    Gate(
        kind=GateKind.DISCLOSURE,
        name="Disclosure",
        question="has the current autonomous-deployment disclosure been acknowledged?",
    ),
)

GATE_BY_KIND: dict[GateKind, Gate] = {gate.kind: gate for gate in MANDATORY_GATES}


class GateOutcome(FrozenModel):
    """What one gate found.

    Three-valued in the same way the judge is: `unknown` is not a pass and not a
    failure of the strategy — it is a statement that the question could not be
    answered, which needs a different response from the operator.
    """

    kind: GateKind
    passed: bool
    unknown: bool = False
    detail: str = ""

    @model_validator(mode="after")
    def _unknown_never_passes(self) -> GateOutcome:
        if self.unknown and self.passed:
            raise ValueError(
                f"{self.kind.value} reported unknown and passed; an unanswered question "
                "is not a pass"
            )
        return self

    @property
    def name(self) -> str:
        return GATE_BY_KIND[self.kind].name

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["name"] = self.name
        payload["question"] = GATE_BY_KIND[self.kind].question
        return payload


class DeploymentFacts(FrozenModel):
    """Everything the gates read, gathered by the caller from the real sources.

    Every field is optional and every absent one produces `unknown`, never a
    pass. That is what makes this safe to hand to a caller that could not
    reach one of its sources: a broken connection to the judge produces a
    blocked deployment rather than an unvalidated one.
    """

    strategy_id: str
    account_uid: str
    symbol: str = ""
    #: The judge's decision, verbatim: PASS / FAIL / INCONCLUSIVE.
    verdict: str | None = None
    verdict_id: str = ""
    #: `forge.contracts.models.RunRecord.tier`.
    evidence_tier: str = ""
    walk_forward_survives: bool | None = None
    paths_robust: bool | None = None
    health: StrategyHealth | None = None
    account_bound: bool | None = None
    connection_live: bool | None = None
    #: `forge.propdesk.policy.PropProgramPolicy.automation`.
    automation_permission: Permission | None = None
    instrument_permitted: bool | None = None
    instrument_mapped: bool | None = None
    risk_mode: RiskMode | None = None
    #: Contracts the risk layer permits right now. Zero blocks; `None` is unknown.
    permitted_contracts: int | None = None
    pretrade_cleared: bool | None = None
    pretrade_detail: str = ""
    buffer: float | None = None
    expected_drawdown_per_contract: float | None = None
    #: `forge.execution.lifecycle.check_transition` raised nothing.
    lifecycle_allowed: bool | None = None
    lifecycle_detail: str = ""
    disclosure_acknowledged: bool | None = None
    disclosure_version: str = ""


def _unknown(kind: GateKind, detail: str) -> GateOutcome:
    return GateOutcome(kind=kind, passed=False, unknown=True, detail=detail)


def _evaluate_gate(gate: Gate, facts: DeploymentFacts) -> GateOutcome:
    kind = gate.kind
    if kind is GateKind.VALIDATION:
        if facts.verdict is None:
            return _unknown(kind, "this strategy has never been judged")
        decision = facts.verdict.upper()
        if decision == "PASS":
            return GateOutcome(
                kind=kind, passed=True, detail=f"verdict {facts.verdict_id or 'PASS'}"
            )
        if decision == "INCONCLUSIVE":
            return _unknown(
                kind,
                "the judge returned INCONCLUSIVE, which is not a pass: something it "
                "needed could not be measured",
            )
        return GateOutcome(kind=kind, passed=False, detail="the judge returned FAIL")

    if kind is GateKind.EVIDENCE_TIER:
        if not facts.evidence_tier:
            return _unknown(kind, "no evidence tier is recorded for this strategy's run")
        permitted = facts.evidence_tier in {"TRUTH_OOS", "HOLDOUT", "FORWARD"}
        return GateOutcome(
            kind=kind,
            passed=permitted,
            detail=(
                f"tier {facts.evidence_tier}"
                if permitted
                else f"tier {facts.evidence_tier} is in-sample; only out-of-sample, "
                "holdout and forward evidence may be deployed on"
            ),
        )

    if kind is GateKind.ROBUSTNESS:
        if facts.walk_forward_survives is None or facts.paths_robust is None:
            missing = (
                "walk-forward stability"
                if facts.walk_forward_survives is None
                else "path robustness"
            )
            return _unknown(kind, f"{missing} was not measured")
        if facts.walk_forward_survives and facts.paths_robust:
            return GateOutcome(kind=kind, passed=True, detail="walk-forward and paths both survive")
        failed = []
        if not facts.walk_forward_survives:
            failed.append("walk-forward stability")
        if not facts.paths_robust:
            failed.append("path robustness")
        return GateOutcome(
            kind=kind, passed=False, detail=f"{' and '.join(failed)} did not survive"
        )

    if kind is GateKind.STRATEGY_HEALTH:
        if facts.health is None:
            return _unknown(kind, "no strategy health record was supplied")
        grade = grade_health(facts.health)
        return GateOutcome(
            kind=kind,
            passed=grade is HealthGrade.HEALTHY,
            detail=f"graded {grade.value}",
        )

    if kind is GateKind.ACCOUNT_BINDING:
        if facts.account_bound is None:
            return _unknown(kind, "whether this account is bound to a connection is unknown")
        return GateOutcome(
            kind=kind,
            passed=facts.account_bound,
            detail="bound" if facts.account_bound else "this account is not bound to a connection",
        )

    if kind is GateKind.PROP_POLICY:
        permission = facts.automation_permission
        if permission is None or permission is Permission.UNKNOWN:
            return _unknown(
                kind,
                "no programme policy records whether automated orders are permitted on "
                "this account. An unrecorded rule is not permission.",
            )
        return GateOutcome(
            kind=kind,
            passed=permission is Permission.ALLOWED,
            detail=f"automation is {permission.value}",
        )

    if kind is GateKind.RISK_ENVELOPE:
        if facts.risk_mode is None or facts.permitted_contracts is None:
            return _unknown(kind, "no risk configuration is in force for this account")
        if facts.permitted_contracts <= 0:
            return GateOutcome(
                kind=kind,
                passed=False,
                detail="the risk layer permits no contracts on this account right now",
            )
        return GateOutcome(
            kind=kind,
            passed=True,
            detail=f"{facts.risk_mode.value} mode permits {facts.permitted_contracts} contract(s)",
        )

    if kind is GateKind.INSTRUMENT:
        if facts.instrument_permitted is None or facts.instrument_mapped is None:
            return _unknown(
                kind,
                "whether this instrument is permitted on the account, or mapped for its "
                "provider, is unknown",
            )
        if facts.instrument_permitted and facts.instrument_mapped:
            return GateOutcome(
                kind=kind, passed=True, detail=f"{facts.symbol} is permitted and mapped"
            )
        reason = (
            "not permitted on this account"
            if not facts.instrument_permitted
            else "not mapped for this account's provider"
        )
        return GateOutcome(kind=kind, passed=False, detail=f"{facts.symbol} is {reason}")

    if kind is GateKind.CONNECTION:
        if facts.connection_live is None:
            return _unknown(kind, "the execution connection's state is unknown")
        return GateOutcome(
            kind=kind,
            passed=facts.connection_live,
            detail="live" if facts.connection_live else "the execution connection is not live",
        )

    if kind is GateKind.PRETRADE:
        if facts.pretrade_cleared is None:
            return _unknown(kind, "no representative order was screened")
        return GateOutcome(
            kind=kind,
            passed=facts.pretrade_cleared,
            detail=facts.pretrade_detail
            or ("cleared" if facts.pretrade_cleared else "refused by the pre-trade gate"),
        )

    if kind is GateKind.ACCOUNT_BUFFER:
        if facts.buffer is None or facts.expected_drawdown_per_contract is None:
            return _unknown(
                kind,
                "the account's buffer, or the strategy's estimated drawdown per "
                "contract, is unknown",
            )
        if facts.expected_drawdown_per_contract <= 0:
            return _unknown(kind, "the estimated drawdown per contract is not positive")
        enough = facts.buffer >= facts.expected_drawdown_per_contract
        return GateOutcome(
            kind=kind,
            passed=enough,
            detail=(
                f"{facts.buffer:,.2f} covers an estimated "
                f"{facts.expected_drawdown_per_contract:,.2f} per contract"
                if enough
                else f"{facts.buffer:,.2f} does not cover one contract's estimated "
                f"{facts.expected_drawdown_per_contract:,.2f} drawdown"
            ),
        )

    if kind is GateKind.LIFECYCLE:
        if facts.lifecycle_allowed is None:
            return _unknown(kind, "the lifecycle transition was not checked")
        return GateOutcome(
            kind=kind,
            passed=facts.lifecycle_allowed,
            detail=facts.lifecycle_detail or ("legal" if facts.lifecycle_allowed else "refused"),
        )

    if facts.disclosure_acknowledged is None:
        return _unknown(kind, "whether the disclosure was acknowledged is unknown")
    return GateOutcome(
        kind=kind,
        passed=facts.disclosure_acknowledged,
        detail=(
            f"acknowledged, version {facts.disclosure_version}"
            if facts.disclosure_acknowledged
            else "the current autonomous-deployment disclosure has not been acknowledged"
        ),
    )


class Outcome(StrEnum):
    """What happens next, having evaluated the gates."""

    BLOCKED = "blocked"
    #: Cleared, but the level records it and stops.
    RECORDED = "recorded"
    #: Cleared, and submitted to the approval queue.
    AWAITING_APPROVAL = "awaiting_approval"
    #: Cleared, and may proceed without a person.
    PROCEED = "proceed"


class DeploymentDecision(FrozenModel):
    """One evaluation of one strategy onto one account."""

    strategy_id: str
    account_uid: str
    level: AutonomyLevel
    outcome: Outcome
    gates: tuple[GateOutcome, ...]
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    disclosure_key: DisclosureKey = DisclosureKey.AUTONOMOUS_DEPLOYMENT

    @property
    def cleared(self) -> bool:
        return all(gate.passed for gate in self.gates)

    @property
    def blocking(self) -> tuple[GateOutcome, ...]:
        return tuple(gate for gate in self.gates if not gate.passed)

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(f"{gate.name}: {gate.detail}" for gate in self.blocking)

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["gates"] = [gate.as_dict() for gate in self.gates]
        payload["cleared"] = self.cleared
        payload["reasons"] = list(self.reasons)
        payload["level_label"] = LEVEL_LABEL[self.level]
        return payload


def evaluate(
    facts: DeploymentFacts,
    *,
    level: AutonomyLevel,
    now: datetime | None = None,
) -> DeploymentDecision:
    """Run every mandatory gate, then decide what the level permits.

    The gate list does not depend on `level`, and the loop below reads
    `MANDATORY_GATES` directly rather than a filtered copy. That is the property
    the tests assert, and it is why the level can only ever change what happens
    *after* every gate has passed.
    """
    gates = tuple(_evaluate_gate(gate, facts) for gate in MANDATORY_GATES)
    cleared = all(gate.passed for gate in gates)
    if not cleared:
        outcome = Outcome.BLOCKED
    elif level is AutonomyLevel.OFF:
        outcome = Outcome.RECORDED
    elif level is AutonomyLevel.APPROVAL_REQUIRED:
        outcome = Outcome.AWAITING_APPROVAL
    else:
        outcome = Outcome.PROCEED
    return DeploymentDecision(
        strategy_id=facts.strategy_id,
        account_uid=facts.account_uid,
        level=level,
        outcome=outcome,
        gates=gates,
        at=now or datetime.now(UTC),
    )


def describe_levels() -> list[dict[str, str]]:
    return [
        {"level": level.value, "label": LEVEL_LABEL[level], "detail": LEVEL_DETAIL[level]}
        for level in AutonomyLevel
    ]


def describe_gates() -> list[dict[str, str]]:
    """The mandatory gate list, for the panel that promises it does not shorten."""
    return [gate.model_dump(mode="json") for gate in MANDATORY_GATES]
