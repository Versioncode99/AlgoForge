"""The fund as a loop, not a set of screens.

A quantitative fund is not one algorithm. It is a sequence that runs
continuously: data becomes research, research becomes a signal, validation
decides whether the signal is real, portfolio construction turns it into a size,
risk and the pre-trade gate decide whether that size may trade, execution fills
it, operations books it, performance measures it, and what performance finds
goes back into research.

Declaring the sequence here — rather than implying it through navigation order —
buys two things. The interface can draw the loop from the same declaration the
API reports state against, so a stage cannot appear in one and not the other. And
every stage has a *state*, which is what makes the command centre a status board
rather than a diagram: "Validation — 1 WARNING" is a fact about the fund, and
"Validation" on its own is a label.

Nothing here computes anything. It is the vocabulary and the shape; the states
are supplied by the subsystems that actually know.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Stage(StrEnum):
    DATA = "data"
    RESEARCH = "research"
    ALPHA = "alpha"
    VALIDATION = "validation"
    PORTFOLIO = "portfolio"
    RISK = "risk"
    GATE = "gate"
    EXECUTION = "execution"
    OPERATIONS = "operations"
    PERFORMANCE = "performance"
    FEEDBACK = "feedback"


class StageStatus(StrEnum):
    """What a stage is doing, or what is wrong with it.

    `UNKNOWN` is present and is not a synonym for idle. A stage whose subsystem
    could not be reached has not been measured, and drawing it as quiet would
    tell the operator the loop is fine when nobody looked.
    """

    IDLE = "idle"
    RUNNING = "running"
    READY = "ready"
    WARNING = "warning"
    BLOCKED = "blocked"
    HALTED = "halted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class StageSpec:
    stage: Stage
    label: str
    purpose: str
    #: The mode section this stage opens. Every stage has one: a stage on the
    #: diagram that cannot be opened is decoration.
    route: str
    #: What the stage hands the next one. Stated because the loop's value is in
    #: the hand-offs, and a reader should be able to see what flows.
    produces: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "label": self.label,
            "purpose": self.purpose,
            "route": self.route,
            "produces": self.produces,
        }


LOOP: tuple[StageSpec, ...] = (
    StageSpec(
        Stage.DATA, "Data",
        "Datasets, their coverage and whether they can be read point-in-time.",
        "data", "a dataset with a stated as-of and a stated provenance",
    ),
    StageSpec(
        Stage.RESEARCH, "Research",
        "Hypotheses, the features that test them, and the experiments that ran.",
        "research", "a falsifiable hypothesis with a frozen specification",
    ),
    StageSpec(
        Stage.ALPHA, "Alpha",
        "Candidate signals, their mechanism, and how they compare to what is already held.",
        "alpha", "a candidate signal with a backtest behind it",
    ),
    StageSpec(
        Stage.VALIDATION, "Validation",
        "The G0-G13 gate ladder. PASS, FAIL, and INCONCLUSIVE for never measured.",
        "validation", "a verdict, or a refusal to issue one",
    ),
    StageSpec(
        Stage.PORTFOLIO, "Portfolio",
        "Covariance, constraints and costs turning forecasts into sizes.",
        "portfolio", "a target portfolio and the rebalance that reaches it",
    ),
    StageSpec(
        Stage.RISK, "Risk",
        "Exposure, leverage, concentration and tail, measured against enforceable limits.",
        "risk", "a risk assessment, and breaches if there are any",
    ),
    StageSpec(
        Stage.GATE, "Pre-Trade",
        "Every proposed order checked against restrictions, limits and market state.",
        "gate", "cleared orders, and blocked ones with reasons",
    ),
    StageSpec(
        Stage.EXECUTION, "Execution",
        "Cleared orders routed through the OMS to an adapter. Simulated in this build.",
        "execution", "fills, and what they cost against arrival",
    ),
    StageSpec(
        Stage.OPERATIONS, "Operations",
        "The book, reconciliation, job health and the audit trail.",
        "operations", "a reconciled book, or a named discrepancy",
    ),
    StageSpec(
        Stage.PERFORMANCE, "Performance",
        "Attribution: where the return came from and what risk generated it.",
        "performance", "attribution by strategy, instrument and factor",
    ),
    StageSpec(
        Stage.FEEDBACK, "Feedback",
        "What performance and execution found, returned to research as evidence.",
        "research", "evidence that changes the next hypothesis",
    ),
)

STAGES: dict[Stage, StageSpec] = {spec.stage: spec for spec in LOOP}


def next_stage(stage: Stage) -> Stage:
    """The stage that follows. `FEEDBACK` returns to `RESEARCH`, closing the loop.

    Written as a function rather than a field on `StageSpec` so the cycle is
    stated in one place; a `next` field would let the loop be broken by editing
    a single entry and nothing would notice.
    """
    if stage is Stage.FEEDBACK:
        return Stage.RESEARCH
    order = [spec.stage for spec in LOOP]
    return order[order.index(stage) + 1]


@dataclass(frozen=True)
class StageState:
    """One stage's current state, as the command centre draws it."""

    stage: Stage
    status: StageStatus
    #: A short phrase: "3 running", "1 warning", "within limits", "0 blocked".
    summary: str
    #: A number worth showing beside the label, when there is one.
    count: int | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        spec = STAGES[self.stage]
        return {
            **spec.as_dict(),
            "status": self.status.value,
            "summary": self.summary,
            "count": self.count,
            "detail": self.detail,
        }


def unknown(stage: Stage, reason: str) -> StageState:
    """A stage nobody could measure, which is different from a quiet one."""
    return StageState(
        stage=stage, status=StageStatus.UNKNOWN, summary="not measured", detail=reason
    )


def describe() -> list[dict[str, Any]]:
    """The loop itself, for a caller that wants the shape without the state."""
    return [spec.as_dict() for spec in LOOP]
