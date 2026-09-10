"""What is this experiment worth, and what will it cost?

A research budget spent in draw order is spent badly. Two candidates can differ
by two orders of magnitude in what they would teach and by one in what they
would cost, and nothing in the engine previously compared them — the next
candidate was whichever one the random number generator produced.

This module ranks. It estimates the **information value** of a proposal and its
**compute cost**, and prefers high information at low cost. Nothing here decides
whether an idea is *true*; it decides what order to find out in.

**The estimate is honest about being an estimate.** Every term is derived from
something already recorded — the novelty score from
:mod:`forge.research.novelty`, the evidence gap from the frontier state, the
cost from the actual grid size and bar count the run would use. None of it is a
prediction of profitability, and the module has no route to the judge. The one
number it produces orders a queue and nothing else.

**Why an explicit evidence gap.** The value of an experiment is highest where
the current answer is *least* settled, and the frontier states already encode
exactly that: ``UNKNOWN`` and ``UNTESTED`` are pure gap, ``INCONCLUSIVE`` is
almost as large a gap because the question was asked and not answered,
``VALIDATED`` and ``FAILED`` are nearly closed. Reading the gap from the
frontier rather than inventing a prior is what keeps this from being a second
opinion about the same evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forge.research.frontier import FrontierState, SearchKind

#: How much room each frontier state leaves for an experiment to teach
#: something. ``BLOCKED_BY_DATA`` is zero because nothing here can run it, not
#: because the question is uninteresting.
EVIDENCE_GAP: dict[FrontierState, float] = {
    FrontierState.UNKNOWN: 1.00,
    FrontierState.UNTESTED: 0.95,
    FrontierState.INCONCLUSIVE: 0.80,
    FrontierState.PARTIALLY_EXPLORED: 0.65,
    FrontierState.PROMISING: 0.60,
    FrontierState.EXHAUSTED: 0.15,
    FrontierState.FAILED: 0.10,
    FrontierState.VALIDATED: 0.10,
    FrontierState.BLOCKED_BY_DATA: 0.00,
}

#: How much of the research programme each category can move. A family
#: discovery that works changes what the system can look for at all; a
#: parameter refinement changes one number. Multiplicative on the value side.
KIND_LEVERAGE: dict[SearchKind, float] = {
    SearchKind.FAMILY: 1.00,
    SearchKind.MECHANISM: 0.90,
    SearchKind.HYPOTHESIS: 0.75,
    SearchKind.STRUCTURAL: 0.55,
    SearchKind.PARAMETER: 0.25,
}

#: Cost of one full backtest pass over the development slice, in arbitrary
#: units. Everything else is expressed as a multiple of it, so the numbers stay
#: comparable when the bar count changes.
UNIT_COST = 1.0

#: Diminishing returns on repeated experiments against the same question. The
#: tenth parameter draw against one hypothesis teaches far less than the first,
#: and this is the term that says so.
REPEAT_DECAY = 0.75


@dataclass(frozen=True)
class CostEstimate:
    """What running this proposal would consume.

    Expressed in backtest-equivalents rather than seconds, because seconds
    depend on the machine and this ranking must be reproducible across them.
    """

    backtests: float
    bars: int
    #: True when the candidate would also run the validation grid, which is by
    #: far the largest single cost in a cycle.
    includes_validation: bool

    @property
    def units(self) -> float:
        # A backtest's cost is very nearly linear in bars, and the grid runs one
        # backtest per configuration over the development slice.
        return round(max(UNIT_COST, self.backtests) * max(1.0, self.bars / 100_000), 4)

    def as_dict(self) -> dict[str, Any]:
        return {
            "backtests": round(self.backtests, 2),
            "bars": self.bars,
            "includes_validation": self.includes_validation,
            "units": self.units,
        }


def estimate_cost(
    *,
    bars: int,
    grid_size: int = 1,
    determinism_bars: int = 6_000,
    includes_validation: bool = False,
) -> CostEstimate:
    """Cost of one research cycle, counted in backtest passes.

    A cycle always pays for a development pass, a determinism re-run over a
    short window, and — when it survives screening — a validation pass. The
    parameter grid is the expensive part when it runs at all, which is why it is
    counted separately rather than folded into a constant.
    """
    passes = 1.0 + (determinism_bars / max(1, bars))
    if includes_validation:
        passes += 1.0 + max(0, grid_size)
    return CostEstimate(
        backtests=passes, bars=max(1, int(bars)), includes_validation=includes_validation
    )


@dataclass(frozen=True)
class InformationValue:
    """The ranking score and every term that produced it.

    The terms are kept because a ranking nobody can interrogate is a ranking
    nobody should trust. The interface shows them when a candidate is inspected.
    """

    novelty: float
    evidence_gap: float
    leverage: float
    repeat_penalty: float
    data_available: bool
    cost: CostEstimate

    @property
    def expected_gain(self) -> float:
        """How much this experiment could teach, before cost.

        Zero when the data does not exist. Not "small" — zero: an experiment
        that cannot be run teaches nothing whatever its other terms say, and
        letting a high-novelty blocked proposal outrank a runnable one is how a
        queue fills with work that will never start.
        """
        if not self.data_available:
            return 0.0
        return round(self.novelty * self.evidence_gap * self.leverage * self.repeat_penalty, 6)

    @property
    def priority(self) -> float:
        """Expected gain per unit of compute. The number the queue sorts on."""
        return round(self.expected_gain / max(0.25, self.cost.units), 6)

    def as_dict(self) -> dict[str, Any]:
        return {
            "novelty": round(self.novelty, 4),
            "evidence_gap": round(self.evidence_gap, 4),
            "leverage": round(self.leverage, 4),
            "repeat_penalty": round(self.repeat_penalty, 4),
            "data_available": self.data_available,
            "expected_gain": self.expected_gain,
            "priority": self.priority,
            "cost": self.cost.as_dict(),
        }


def value_of(
    *,
    novelty: float,
    state: FrontierState,
    kind: SearchKind,
    prior_experiments: int,
    data_available: bool,
    cost: CostEstimate,
) -> InformationValue:
    """Score one proposal.

    ``prior_experiments`` is how many times this *question* has already been
    run, not how many times the template has. That distinction is the point:
    forty parameter draws against one hypothesis should score the fortieth far
    below the first, and only a per-question count can say so.
    """
    return InformationValue(
        novelty=max(0.0, min(1.0, float(novelty))),
        evidence_gap=EVIDENCE_GAP.get(state, 0.5),
        leverage=KIND_LEVERAGE.get(kind, 0.5),
        repeat_penalty=REPEAT_DECAY ** max(0, int(prior_experiments)),
        data_available=bool(data_available),
        cost=cost,
    )


def rank(scored: list[tuple[Any, InformationValue]]) -> list[tuple[Any, InformationValue]]:
    """Highest priority first, ties broken towards the cheaper experiment.

    Cheap-first on a tie is deliberate. Two proposals expected to teach the same
    amount are not equivalent: running the cheaper one first means the more
    expensive one can be re-ranked against what the first one found.
    """
    return sorted(scored, key=lambda pair: (-pair[1].priority, pair[1].cost.units))
