"""What an experiment intends to do, written down before it does it.

The engine's unit of work was a strategy specification: a template, some
parameters, a backtest. That is a thing to *run*, not a thing to *ask*, and the
difference shows up everywhere downstream — a proposal with no stated prediction
cannot be wrong, so a result cannot teach anything, so a campaign generates
candidates instead of learning.

A `ResearchPlan` is the question, the claim, the temporal design, the costs, and
what would count as success or failure — fixed before any number exists. The
gate below refuses plans that could not produce evidence whatever they returned.

**The gate is deterministic and it is not a model.** `review` reads the plan and
the campaign and returns findings a person can check. A model may *write* a
plan; nothing a model says can make a plan pass. That is the same boundary the
judge has, for the same reason.

**Preregistration is reused, not reinvented.** `forge.contracts.models.Preregistration`
already freezes a claim, content-hashes it, and is re-derived by G1 at judge
time. `ResearchPlan.preregister()` produces one of those, carrying the time
scope's fingerprint, so a moved window or a moved claim fails an existing gate
rather than a new one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from forge.contracts.hashing import content_hash, stable_id
from forge.contracts.models import FrozenModel, Preregistration
from forge.data.freshness import NOT_EVIDENCE, Freshness
from forge.research.timescope import TimeScope


def _scope_from(value: Any) -> TimeScope:
    """A scope from a model or from `as_dict` output.

    `propose_time_scope` returns a payload carrying derived keys, and a caller
    that hands that straight to the plan gate must not be refused for echoing
    back the numbers it was just given.
    """
    return value if isinstance(value, TimeScope) else TimeScope.from_payload(value)

#: A prediction shorter than this cannot name a direction and a magnitude, and a
#: claim that names neither cannot be wrong.
MIN_PREDICTION = 30

#: Frozen at a fixed instant for the same reason `preregistration_store` does:
#: a claim re-derived from the same content must hash identically at judge time,
#: or every candidate would fail G1 for the crime of time having passed.
FROZEN_AT = datetime(2026, 1, 1, tzinfo=UTC)


class PlanStatus(StrEnum):
    """What the gate decided. Four outcomes, because they need different actions."""

    ACCEPTED = "PLAN_ACCEPTED"
    #: The plan could not produce evidence whatever it returned.
    REJECTED = "PLAN_REJECTED"
    #: The plan is sound and the data is not there. Different from rejected:
    #: nothing is wrong with the research, and it becomes runnable when the
    #: data arrives, so it is kept rather than discarded.
    BLOCKED_DATA = "PLAN_BLOCKED_DATA"
    #: Defensible but consequential — it needs a person, not a refusal.
    REQUIRES_REVIEW = "PLAN_REQUIRES_REVIEW"


class Direction(StrEnum):
    """Which way the effect is claimed to go. `UNSIGNED` is a real answer."""

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    #: A claim about magnitude or dispersion rather than sign.
    UNSIGNED = "UNSIGNED"


class PilotOutcome(StrEnum):
    """What a pilot decided. **None of these is evidence about the market.**

    A pilot answers "is this worth more compute", which is a question about the
    research budget. It never answers "does this edge exist" — that is the
    judge's question and the judge asks it of a full experiment.
    """

    NO_SIGNAL = "NO_SIGNAL"
    PROMISING = "PROMISING"
    INCONCLUSIVE = "INCONCLUSIVE"
    #: The strategy did not run. Not a statement about the hypothesis at all.
    IMPLEMENTATION_FAILURE = "IMPLEMENTATION_FAILURE"
    BLOCKED = "BLOCKED"


#: Outcomes that permit spending the full experiment budget.
PROMOTES: frozenset[PilotOutcome] = frozenset({PilotOutcome.PROMISING, PilotOutcome.INCONCLUSIVE})


class PilotDesign(FrozenModel):
    """The cheap look that decides whether the expensive one is worth running.

    Hypothesis-aware by construction: the pilot carries **its own** time scope,
    rather than a rule like "two years first for everything". A claim about a
    macro release needs the releases in its pilot window; a claim about
    microstructure does not.
    """

    scope: TimeScope
    #: How many parameter settings the pilot may try. Kept small on purpose:
    #: a pilot that searches is not a pilot, it is a cheap experiment with an
    #: inflated trial count.
    max_configurations: int = Field(default=1, ge=1, le=8)
    #: What would make this worth continuing, stated before it runs.
    promote_if: str = Field(min_length=10, max_length=500)

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["scope"] = self.scope.as_dict()
        return payload


class PlanFinding(FrozenModel):
    """One thing the gate noticed, with the rule that produced it."""

    code: str
    severity: str  # "block" | "review" | "note"
    summary: str
    remedy: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ResearchPlan(FrozenModel):
    """The intent of one experiment, frozen before it runs."""

    campaign_id: str = ""
    #: The question, in a form somebody could disagree with.
    research_question: str = Field(min_length=20, max_length=500)
    hypothesis: str = Field(min_length=20, max_length=1000)
    mechanism: str = Field(min_length=20, max_length=1000)
    #: What result would abandon the claim.
    falsifiable_prediction: str = Field(min_length=MIN_PREDICTION, max_length=1000)
    expected_direction: Direction = Direction.UNSIGNED
    #: The observation that would end this line of research.
    invalidation_condition: str = Field(min_length=10, max_length=500)

    dataset: str = Field(min_length=1, max_length=120)
    instrument: str = Field(default="", max_length=20)
    timeframe: str = Field(default="1m", max_length=12)
    scope: TimeScope

    #: What the strategy is built from. Names, not code.
    features: tuple[str, ...] = ()
    construction: str = Field(default="", max_length=2000)
    parameters: dict[str, float] = Field(default_factory=dict)
    #: Set when the parameters may no longer move. The gate refuses a plan that
    #: is not frozen, because unfrozen parameters plus a result is a search.
    parameters_frozen: bool = False

    transaction_costs: str = Field(default="", max_length=300)
    slippage: str = Field(default="", max_length=300)

    pilot: PilotDesign | None = None
    #: Capabilities this plan needs beyond bars.
    data_requirements: tuple[str, ...] = ()
    #: The freshness state this plan will accept as evidence. Checked by the
    #: gate against `forge.data.freshness.Freshness`, because a field the gate
    #: ignores is configuration accepted and thrown away -- which is the defect
    #: this codebase has now shipped three times.
    freshness_requirement: str = "FRESH"

    success_criteria: str = Field(default="", max_length=500)
    failure_criteria: str = Field(default="", max_length=500)

    parent_hypothesis_id: str = ""
    #: Set when this plan exists because an earlier one failed in a named way.
    derived_from_failure: str = Field(default="", max_length=300)

    created_at: datetime
    created_by: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def _scope_matches_dataset(self) -> ResearchPlan:
        if self.scope.dataset != self.dataset:
            raise ValueError(
                f"the plan runs on '{self.dataset}' and its time scope selects from "
                f"'{self.scope.dataset}'. A window over the wrong archive is not a window."
            )
        if self.pilot is not None and self.pilot.scope.dataset != self.dataset:
            raise ValueError("the pilot's time scope selects from a different dataset")
        return self

    # ── identity ─────────────────────────────────────────────────────────────

    def fingerprint(self) -> str:
        """What this plan *is*. Excludes who wrote it and when."""
        return content_hash(
            {
                "question": self.research_question,
                "hypothesis": self.hypothesis,
                "mechanism": self.mechanism,
                "prediction": self.falsifiable_prediction,
                "dataset": self.dataset,
                "scope": self.scope.fingerprint(),
                "features": sorted(self.features),
                "construction": self.construction,
                "parameters": {k: self.parameters[k] for k in sorted(self.parameters)},
            }
        )

    @property
    def plan_id(self) -> str:
        return stable_id("plan", {"fingerprint": self.fingerprint()})

    def preregister(self) -> Preregistration:
        """The frozen claim, carrying this plan's window.

        Reuses the existing mechanism rather than adding one: G1 re-derives this
        hash at judge time and fails ``CLAIM_MOVED`` when the claim or the window
        has moved since.
        """
        settings = ", ".join(f"{k}={self.parameters[k]}" for k in sorted(self.parameters))
        falsification = self.falsifiable_prediction
        if settings:
            falsification = f"{falsification} Evaluated at {settings}."
        return Preregistration.freeze(
            hypothesis=self.hypothesis,
            mechanism=self.mechanism,
            falsification=falsification,
            frozen_at=FROZEN_AT,
            scope_fingerprint=self.scope.fingerprint(),
        )

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["scope"] = self.scope.as_dict()
        payload["pilot"] = self.pilot.as_dict() if self.pilot else None
        payload["plan_id"] = self.plan_id
        payload["fingerprint"] = self.fingerprint()
        return payload


class PlanVerdict(FrozenModel):
    """The gate's decision, with every finding that produced it."""

    status: PlanStatus
    findings: tuple[PlanFinding, ...] = ()
    reviewed_at: datetime

    @property
    def accepted(self) -> bool:
        return self.status is PlanStatus.ACCEPTED

    def reasons(self, severity: str) -> tuple[str, ...]:
        return tuple(f.summary for f in self.findings if f.severity == severity)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "accepted": self.accepted,
            "findings": [f.as_dict() for f in self.findings],
            "reviewed_at": self.reviewed_at.isoformat(),
        }


# ── the gate ─────────────────────────────────────────────────────────────────


def review(
    plan: ResearchPlan,
    *,
    campaign: Any = None,
    exhausted_fingerprints: frozenset[str] = frozenset(),
    available_capabilities: frozenset[str] | None = None,
) -> PlanVerdict:
    """Decide whether this plan may run. Deterministic; no model is consulted.

    The distinction the four statuses carry:

    * **REJECTED** — it could not produce evidence whatever it returned.
    * **BLOCKED_DATA** — the research is sound and the data is absent. Kept,
      because it becomes runnable when the data arrives.
    * **REQUIRES_REVIEW** — defensible and consequential. A person decides.
    * **ACCEPTED** — run it.
    """
    findings: list[PlanFinding] = []

    # 1. Could a result be wrong? Everything else is secondary to this.
    if not _names_an_observation(plan.falsifiable_prediction):
        findings.append(
            PlanFinding(
                code="unfalsifiable",
                severity="block",
                summary=(
                    "The prediction does not name an observation that would contradict "
                    "it, so no result can disagree with this plan."
                ),
                remedy="State what measurement, at what threshold, would abandon the claim.",
            )
        )
    if not plan.success_criteria or not plan.failure_criteria:
        findings.append(
            PlanFinding(
                code="undefined_outcome",
                severity="block",
                summary="Success and failure criteria must both be stated before the run.",
                remedy=(
                    "Say what result would support the claim and what result would end it. "
                    "Deciding afterwards is deciding from the answer."
                ),
            )
        )

    # 2. Parameters that can still move are a search, not an experiment.
    if plan.parameters and not plan.parameters_frozen:
        findings.append(
            PlanFinding(
                code="parameters_unfrozen",
                severity="block",
                summary=(
                    "The parameters are not frozen. A plan whose settings may change "
                    "after a result is a search with one of its outcomes reported."
                ),
                remedy="Freeze the parameters, or declare the sweep as the experiment.",
            )
        )

    # 3. Data.
    missing = _missing_capabilities(plan, campaign, available_capabilities)
    if missing:
        findings.append(
            PlanFinding(
                code="data_unavailable",
                severity="block",
                summary=(
                    f"This plan needs {', '.join(missing)}, which this installation "
                    "cannot serve."
                ),
                remedy=(
                    "Kept rather than discarded: it becomes runnable if the data arrives. "
                    "Nothing is substituted for it."
                ),
            )
        )

    # 4. Will this plan accept data it should not?
    requirement = plan.freshness_requirement.strip().upper()
    if requirement not in {str(state) for state in Freshness}:
        findings.append(
            PlanFinding(
                code="unknown_freshness",
                severity="block",
                summary=(
                    f"'{plan.freshness_requirement}' is not a freshness state. "
                    f"Known states: {', '.join(str(s) for s in Freshness)}."
                ),
                remedy="State which freshness this plan will accept as evidence.",
            )
        )
    elif Freshness(requirement) in NOT_EVIDENCE:
        findings.append(
            PlanFinding(
                code="inadmissible_freshness",
                severity="block",
                summary=(
                    f"This plan declares it will accept {requirement} data as evidence. "
                    "An informational surface may render a stale or degraded value; a "
                    "result may not rest on one."
                ),
                remedy=(
                    "Require FRESH or REFRESHING, or state the claim as an "
                    "observation rather than as evidence."
                ),
            )
        )

    # 5. Has this exact plan already been settled?
    if plan.fingerprint() in exhausted_fingerprints:
        findings.append(
            PlanFinding(
                code="already_settled",
                severity="block",
                summary=(
                    "An identical plan — same claim, same window, same construction — "
                    "has already been run and settled."
                ),
                remedy=(
                    "Change the mechanism, the construction or the window, and say why "
                    "the difference should matter."
                ),
            )
        )

    # 6. Consequential, not wrong.
    if plan.scope.coverage > 0.95 and plan.pilot is None:
        findings.append(
            PlanFinding(
                code="whole_reservoir_no_pilot",
                severity="review",
                summary=(
                    f"This plan consumes {plan.scope.coverage:.0%} of the available "
                    "history with no pilot first."
                ),
                remedy=(
                    "Sound for a claim that genuinely needs the whole record — a "
                    "seasonal effect does. Worth confirming it is not a default."
                ),
            )
        )
    if plan.scope.method.name.startswith("CROSS") and not plan.scope.segments:
        findings.append(
            PlanFinding(
                code="unnamed_regimes", severity="review",
                summary="A cross-regime plan that does not name its regimes.",
                remedy="Name the regimes and the rule that selected them.",
            )
        )
    if campaign is not None:
        settled, why = campaign.exhausted()
        if settled:
            findings.append(
                PlanFinding(
                    code="campaign_exhausted", severity="review",
                    summary=f"The campaign has stopped: {why}.",
                    remedy="Raise the budget deliberately, or let the campaign close.",
                )
            )

    # 7. Worth saying, not worth stopping for.
    if not plan.transaction_costs:
        findings.append(
            PlanFinding(
                code="costs_unstated", severity="note",
                summary="Transaction costs are not stated on the plan.",
                remedy="The backtest applies its own; stating them here makes the claim checkable.",
            )
        )
    if plan.derived_from_failure:
        findings.append(
            PlanFinding(
                code="failure_derived", severity="note",
                summary=(
                    "This plan exists because an earlier one failed: "
                    f"{plan.derived_from_failure}"
                ),
            )
        )

    return PlanVerdict(
        status=_status(findings), findings=tuple(findings), reviewed_at=datetime.now(UTC)
    )


def _status(findings: list[PlanFinding]) -> PlanStatus:
    blocks = [f for f in findings if f.severity == "block"]
    if blocks:
        # Data absence is kept apart from research that could not work. The
        # first becomes runnable later; the second never does.
        if all(f.code == "data_unavailable" for f in blocks):
            return PlanStatus.BLOCKED_DATA
        return PlanStatus.REJECTED
    if any(f.severity == "review" for f in findings):
        return PlanStatus.REQUIRES_REVIEW
    return PlanStatus.ACCEPTED


#: Words that turn a sentence into a measurement. A prediction has to point at
#: something countable; "the strategy will work" points at nothing.
_OBSERVABLE = (
    "expectancy", "sharpe", "return", "drawdown", "win rate", "hit rate",
    "profit factor", "t-stat", "p-value", "trades", "basis points", "bps",
    "percent", "%", "greater", "less", "above", "below", "exceed", "at least",
    "no more than", "positive", "negative", "zero",
)


def _names_an_observation(prediction: str) -> bool:
    lowered = prediction.lower()
    return any(word in lowered for word in _OBSERVABLE)


def _missing_capabilities(
    plan: ResearchPlan, campaign: Any, available: frozenset[str] | None
) -> tuple[str, ...]:
    required = {r.upper() for r in plan.data_requirements}
    if not required:
        return ()
    if campaign is not None:
        # `campaign` is typed Any so this module need not import the campaign
        # store to answer a question about capabilities. The annotation is what
        # keeps that loose type from leaking out of the function.
        unserved: tuple[str, ...] = tuple(campaign.serves(sorted(required)))
        return unserved
    if available is not None:
        return tuple(sorted(required - {c.upper() for c in available}))
    return ()


# ── pilots ───────────────────────────────────────────────────────────────────


def promotion(
    outcome: PilotOutcome, *, reason: str = ""
) -> tuple[bool, str]:
    """May this hypothesis have the full experiment budget?

    `INCONCLUSIVE` promotes. A pilot is small by construction, so "we could not
    tell" is the expected answer for a real effect measured on a short window —
    treating it as a refusal would reject exactly the hypotheses a pilot is too
    small to see. `NO_SIGNAL` does not promote: the pilot looked and found
    nothing where something was predicted.
    """
    if outcome in PROMOTES:
        return True, reason or f"the pilot returned {outcome}"
    if outcome is PilotOutcome.IMPLEMENTATION_FAILURE:
        return False, (
            "the strategy did not run, so the pilot says nothing about the hypothesis. "
            "Fix the implementation and pilot again — this is not a negative result."
        )
    if outcome is PilotOutcome.BLOCKED:
        return False, reason or "the pilot could not run on the available data"
    return False, reason or (
        "the pilot predicted an effect and measured none over its window"
    )
