"""How the research budget is divided, and how that division adapts.

The engine used to spend 100% of its compute in one category: same hypothesis,
different parameters. Not by decision — there was no decision, because there was
no category. This module makes the split explicit, configurable and observable,
which is the difference between a research programme and a random walk with
good bookkeeping.

Five buckets, defaulting to the allocation the research model calls for:

===========================  ====  ========================================
``EXPLORE_HYPOTHESIS``       35%   New hypotheses and new mechanisms.
``DISCOVER_FAMILY``          25%   New families and the templates for them.
``ADVANCE_PROMISING``        20%   Existing research that is going somewhere.
``REFINE_PARAMETERS``        10%   Tuning a claim that already has evidence.
``ROBUSTNESS``               10%   Validation, ablation, replication.
===========================  ====  ========================================

**Why weights and a draw, rather than a schedule.** Eight workers running a
fixed rota would give every bucket the same *cadence*, which is not the same as
the same *share* — a family discovery costs several times a parameter draw, and
a rota would quietly hand it several times its budget. Drawing each cycle from
the weights, and recording what was actually spent, means the realised split can
be compared against the intended one and reported honestly when they differ.

**Adaptation is bounded and stated.** ``adapt`` re-weights from what the frontier
actually contains — a backlog of promising-but-untested work pulls budget
towards advancing it; family proposals that keep colliding with existing
families pull budget away from discovery. Every adjustment is capped at
``MAX_DRIFT`` from the configured weights, so a run cannot drift into spending
everything on one bucket, and every adjustment carries a reason string that the
interface shows. The configured weights are never overwritten: adaptation
produces a new allocation and the original stays as the thing it is measured
against.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from forge.research.frontier import FrontierState, SearchKind


class Bucket(StrEnum):
    """What a unit of research compute is being spent on."""

    EXPLORE_HYPOTHESIS = "EXPLORE_HYPOTHESIS"
    DISCOVER_FAMILY = "DISCOVER_FAMILY"
    ADVANCE_PROMISING = "ADVANCE_PROMISING"
    REFINE_PARAMETERS = "REFINE_PARAMETERS"
    ROBUSTNESS = "ROBUSTNESS"


#: The starting allocation.
DEFAULT_WEIGHTS: dict[Bucket, float] = {
    Bucket.EXPLORE_HYPOTHESIS: 0.35,
    Bucket.DISCOVER_FAMILY: 0.25,
    Bucket.ADVANCE_PROMISING: 0.20,
    Bucket.REFINE_PARAMETERS: 0.10,
    Bucket.ROBUSTNESS: 0.10,
}

#: Which search category each bucket produces. Recorded on every candidate, so
#: "what did this campaign actually do" is answerable from the experiment rows.
BUCKET_KIND: dict[Bucket, SearchKind] = {
    Bucket.EXPLORE_HYPOTHESIS: SearchKind.HYPOTHESIS,
    Bucket.DISCOVER_FAMILY: SearchKind.FAMILY,
    Bucket.ADVANCE_PROMISING: SearchKind.STRUCTURAL,
    Bucket.REFINE_PARAMETERS: SearchKind.PARAMETER,
    Bucket.ROBUSTNESS: SearchKind.PARAMETER,
}

#: No bucket may move further than this from its configured weight, in either
#: direction. Adaptation is a tilt, not a takeover.
MAX_DRIFT = 0.15

#: No bucket is ever starved completely. A campaign that stopped exploring
#: entirely because exploration was going badly would never find out that it had
#: stopped being right.
MIN_WEIGHT = 0.03


@dataclass(frozen=True)
class ResearchAllocation:
    """Intended weights, and the operations that draw from them."""

    weights: dict[Bucket, float]

    @classmethod
    def default(cls) -> ResearchAllocation:
        return cls(weights=dict(DEFAULT_WEIGHTS))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, float] | None) -> ResearchAllocation:
        """Build from configuration, filling in anything not named.

        An unrecognised key is an error rather than a silent no-op: a campaign
        configured with ``"explore": 0.6`` and no complaint would run at the
        default allocation while its operator believed otherwise.
        """
        if not raw:
            return cls.default()
        weights = dict(DEFAULT_WEIGHTS)
        for key, value in raw.items():
            try:
                bucket = Bucket(str(key).upper())
            except ValueError as exc:
                raise ValueError(
                    f"Unknown research bucket '{key}'. Valid buckets: "
                    f"{', '.join(b.value for b in Bucket)}."
                ) from exc
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"Weight for '{key}' must be between 0 and 1.")
            weights[bucket] = float(value)
        return cls(weights=weights).normalised()

    def normalised(self) -> ResearchAllocation:
        """Weights summing to one, with nothing starved to zero."""
        floored = {bucket: max(MIN_WEIGHT, value) for bucket, value in self.weights.items()}
        total = sum(floored.values())
        return replace(self, weights={b: v / total for b, v in floored.items()})

    def draw(self, rng: random.Random) -> Bucket:
        """One bucket, sampled in proportion to its weight."""
        normalised = self.normalised()
        roll = rng.random()
        cumulative = 0.0
        for bucket in Bucket:
            cumulative += normalised.weights[bucket]
            if roll < cumulative:
                return bucket
        return Bucket.REFINE_PARAMETERS

    def as_dict(self) -> dict[str, float]:
        normalised = self.normalised()
        return {str(bucket): round(normalised.weights[bucket], 4) for bucket in Bucket}


@dataclass(frozen=True)
class AllocationAdvice:
    """An adapted allocation and the evidence that produced it."""

    allocation: ResearchAllocation
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"weights": self.allocation.as_dict(), "reasons": list(self.reasons)}


@dataclass(frozen=True)
class FrontierSignal:
    """The state of the search, as the allocator sees it.

    Assembled by the caller from the frontier and the campaign counters, rather
    than this module reaching into either — which keeps it a pure function of
    its inputs and therefore testable without a database.
    """

    #: Frontier items in each state.
    state_counts: Mapping[str, int]
    #: Proposals refused as duplicates, out of proposals made.
    duplicate_proposals: int = 0
    total_proposals: int = 0
    #: Candidates waiting for validation because they earned it.
    validation_backlog: int = 0
    #: Experiments run so far in this campaign.
    experiments: int = 0
    #: Distinct mechanisms the campaign has actually explored.
    mechanisms: int = 0

    @property
    def duplicate_rate(self) -> float:
        return self.duplicate_proposals / self.total_proposals if self.total_proposals else 0.0

    @property
    def promising_untested(self) -> int:
        return int(self.state_counts.get(str(FrontierState.PROMISING), 0))

    @property
    def open_questions(self) -> int:
        return int(self.state_counts.get(str(FrontierState.UNTESTED), 0)) + int(
            self.state_counts.get(str(FrontierState.UNKNOWN), 0)
        )

    @property
    def blocked(self) -> int:
        return int(self.state_counts.get(str(FrontierState.BLOCKED_BY_DATA), 0))


def adapt(base: ResearchAllocation, signal: FrontierSignal) -> AllocationAdvice:
    """Tilt the allocation towards where the evidence says the value is.

    Four adjustments, each with a stated trigger. They compose additively and
    are then clamped to ``MAX_DRIFT`` of the configured weight, so no
    combination of triggers can produce a runaway.
    """
    weights = dict(base.normalised().weights)
    reasons: list[str] = []

    # 1. A backlog of promising work is the highest-value thing there is: the
    #    evidence already exists and is going unused.
    if signal.promising_untested >= 3:
        shift = min(0.10, 0.02 * signal.promising_untested)
        weights[Bucket.ADVANCE_PROMISING] += shift
        weights[Bucket.EXPLORE_HYPOTHESIS] -= shift
        reasons.append(
            f"{signal.promising_untested} promising questions are waiting; moved "
            f"{shift:.0%} from exploration to advancing them"
        )

    # 2. Family proposals colliding with existing families means the discovery
    #    bucket is producing restatements. Spend it on hypotheses instead — the
    #    space of new claims within known mechanisms is much larger.
    if signal.total_proposals >= 8 and signal.duplicate_rate > 0.5:
        shift = min(0.10, 0.12 * signal.duplicate_rate)
        weights[Bucket.DISCOVER_FAMILY] -= shift
        weights[Bucket.EXPLORE_HYPOTHESIS] += shift
        reasons.append(
            f"{signal.duplicate_rate:.0%} of proposals were duplicates; moved {shift:.0%} "
            "out of family discovery"
        )

    # 3. A validation backlog is compute already earned and not yet spent.
    if signal.validation_backlog >= 2:
        shift = min(0.08, 0.03 * signal.validation_backlog)
        weights[Bucket.ROBUSTNESS] += shift
        weights[Bucket.REFINE_PARAMETERS] -= shift
        reasons.append(
            f"{signal.validation_backlog} candidates have earned validation; moved "
            f"{shift:.0%} to robustness"
        )

    # 4. Many experiments against few mechanisms is the exact failure this whole
    #    module exists to prevent, so it gets the strongest correction.
    if signal.experiments >= 40 and signal.mechanisms <= 3:
        shift = 0.12
        weights[Bucket.REFINE_PARAMETERS] -= shift
        weights[Bucket.DISCOVER_FAMILY] += shift / 2
        weights[Bucket.EXPLORE_HYPOTHESIS] += shift / 2
        reasons.append(
            f"{signal.experiments} experiments across only {signal.mechanisms} mechanisms — "
            "the search is deep and narrow; moved 12% out of parameter refinement"
        )

    configured = base.normalised().weights
    clamped = {
        bucket: max(
            configured[bucket] - MAX_DRIFT,
            min(configured[bucket] + MAX_DRIFT, value),
        )
        for bucket, value in weights.items()
    }
    return AllocationAdvice(
        allocation=ResearchAllocation(weights=clamped).normalised(),
        reasons=tuple(reasons) or ("evidence matches the configured allocation; no tilt applied",),
    )


def realised(spend: Sequence[str] | Mapping[str, int]) -> dict[str, float]:
    """What share each bucket actually consumed.

    Compared against the intended allocation everywhere the interface reports
    campaign progress, because an allocation nobody checks is a comment.
    """
    counts: dict[str, int] = {str(bucket): 0 for bucket in Bucket}
    if isinstance(spend, Mapping):
        for key, value in spend.items():
            counts[str(key)] = counts.get(str(key), 0) + int(value)
    else:
        for key in spend:
            counts[str(key)] = counts.get(str(key), 0) + 1
    total = sum(counts.values())
    if not total:
        return {bucket: 0.0 for bucket in counts}
    return {bucket: round(value / total, 4) for bucket, value in counts.items()}
