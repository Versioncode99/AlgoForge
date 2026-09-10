"""What a failure suggests.

A failed experiment used to become one word: ``FAILED``. That word is the end of
a search and the middle of a piece of research, and the difference is the
subject of this module.

When ``momentum_breakout`` loses money in every regime except low volatility,
the useful output is not "momentum breakout failed". It is a question:

    *Is the edge conditional on volatility expansion rather than volatility
    level?*

That question is falsifiable, testable on the data already loaded, and would not
have been asked without the failure. Generating it is the difference between a
system that eliminates candidates and one that expands its own frontier.

**Every generated question is a question.** It enters the hypothesis graph as
``UNTESTED`` with an edge back to what suggested it, and it passes exactly the
checks a human-authored hypothesis passes: a statement, a mechanism, a
falsifiable prediction, a declared data requirement, and a novelty check against
everything already on the frontier. A failure is not permitted to promote its
own successor.

**Generation is deterministic.** The rules below map an observed failure
signature — the failure class, the gate that broke, the conditional structure in
the result — onto a question template with the observation substituted in. No
model is required, which means this works offline, produces the same follow-up
twice, and can be tested. A model may later propose additional follow-ups
through the same admission path; it cannot replace this one, because a research
loop whose continuation depends on a credential is a research loop that stops.

**What it will not do.** It will not generate a follow-up from a bookkeeping or
infrastructure failure, because a consumed holdout and a full disk say nothing
about the market. It will not generate one from ``LOOKAHEAD`` or ``SAFETY``
either: those are defects in the code, and the correct follow-up is a fix, not a
hypothesis.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from forge.memory.research import FailureClass
from forge.research.frontier import SearchKind


@dataclass(frozen=True)
class Observation:
    """What was seen, in the terms a follow-up rule can read.

    Assembled by the caller from the verdict, the result and the experiment row.
    Every field is something the engine already has at the moment a candidate
    finishes; nothing here needs a new measurement.
    """

    family: str
    mechanism: str
    hypothesis: str
    template: str
    #: The classified failure, or ``None`` when the candidate was inconclusive
    #: rather than refuted.
    failure_class: FailureClass | None = None
    #: The judge gate that failed, e.g. ``"G4"``.
    gate: str | None = None
    #: Free-text describing where the effect concentrated, when the result
    #: carried a conditional structure. Quoted verbatim into the follow-up so a
    #: reader can trace the sentence back.
    concentration: str = ""
    #: Session, regime or volatility condition the effect was confined to.
    condition: str = ""
    trades: int = 0
    net_pnl: float = 0.0
    #: Whether the run produced a positive development result that failed later.
    was_profitable: bool = False


@dataclass(frozen=True)
class FollowUp:
    """A derived question, ready for the novelty check and the graph."""

    statement: str
    prediction: str
    mechanism: str
    family: str
    search_kind: SearchKind
    #: Why this question follows from what was seen. Recorded on the edge.
    rationale: str
    required_data: tuple[str, ...] = ("BARS",)

    def as_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "falsifiable_prediction": self.prediction,
            "mechanism": self.mechanism,
            "family": self.family,
            "search_kind": str(self.search_kind),
            "rationale": self.rationale,
            "required_data": list(self.required_data),
        }


#: Failure classes that say nothing about the market and therefore generate
#: nothing. Kept as a set so the reason is stated once rather than as four
#: scattered conditionals.
BARREN = frozenset(
    {
        FailureClass.BOOKKEEPING,
        FailureClass.INFRASTRUCTURE,
        FailureClass.DATA,
        FailureClass.LOOKAHEAD,
        FailureClass.SAFETY,
    }
)


def _conditional_followups(obs: Observation) -> list[FollowUp]:
    """Questions arising from an effect that was confined to some condition.

    This is the family the research model's three examples all belong to: an
    edge that appeared only somewhere is evidence about the *somewhere*, and the
    follow-up asks whether the condition is the mechanism rather than a filter.
    """
    if not obs.condition:
        return []
    condition = obs.condition.strip().rstrip(".")
    where = obs.concentration.strip() or f"the effect was confined to {condition}"
    return [
        FollowUp(
            statement=(
                f"The edge attributed to {obs.family} is conditional on {condition} rather "
                f"than present throughout. Observed: {where}. If the condition is the "
                "mechanism rather than a filter on it, a signal built from the condition "
                "itself should carry the effect without the original entry rule."
            ),
            prediction=(
                f"Conditioning entries on {condition} directly must produce a stronger "
                "per-trade expectancy than the original rule does inside the same "
                "condition. If it does not, the condition is a filter and not the cause, "
                "and this explanation is abandoned."
            ),
            mechanism=(
                f"Whatever produces {condition} is also what produces the imbalance the "
                "original rule was picking up incidentally, so measuring it directly "
                "should dominate measuring its side effect."
            ),
            family=obs.family,
            search_kind=SearchKind.MECHANISM,
            rationale=f"the result concentrated in {condition}",
        ),
        FollowUp(
            statement=(
                f"The effect in {obs.family} depends on the rate of change of {condition} "
                "rather than on its level. A state that is high and stable and a state that "
                "is rising through the same value are different regimes, and only the "
                "second involves participants repositioning."
            ),
            prediction=(
                f"Splitting the sample by the change in {condition} over the preceding "
                "window must separate the profitable trades from the unprofitable ones "
                "more sharply than splitting by its level does. If the level split is at "
                "least as sharp, the rate-of-change explanation is wrong."
            ),
            mechanism=(
                "Repositioning flow is generated by transitions, not by states. A level "
                "carries no information about whether anyone is currently adjusting to it."
            ),
            family=obs.family,
            search_kind=SearchKind.HYPOTHESIS,
            rationale=f"level-versus-expansion ambiguity in the {condition} result",
        ),
    ]


def _class_followups(obs: Observation) -> list[FollowUp]:
    """Questions arising from how the candidate failed."""
    failure = obs.failure_class
    if failure is None or failure in BARREN:
        return []

    if failure is FailureClass.NO_TRADES:
        return [
            FollowUp(
                statement=(
                    f"The {obs.family} signal as specified never triggers on this instrument "
                    "because its threshold is calibrated to a distribution the instrument "
                    "does not have. A threshold expressed in units of the instrument's own "
                    "realised dispersion should trigger at a usable rate."
                ),
                prediction=(
                    "Expressing the trigger as a multiple of trailing realised volatility "
                    "must produce a trade count within the testable range across the whole "
                    "sample. If it still produces none, the signal does not describe "
                    "anything that happens in this series and is abandoned."
                ),
                mechanism=(
                    "A fixed threshold encodes an assumption about scale; a normalised one "
                    "encodes only the shape of the claim, which is what was being tested."
                ),
                family=obs.family,
                search_kind=SearchKind.STRUCTURAL,
                rationale="the specified threshold produced no trades at all",
            )
        ]

    if failure in {FailureClass.INSUFFICIENT_SAMPLE}:
        return [
            FollowUp(
                statement=(
                    f"The {obs.family} effect is real but rare, and the sample is too small "
                    "to measure at the horizon tested. If the mechanism operates on a "
                    "shorter horizon, the same claim tested over a shorter holding period "
                    "should produce enough observations to measure."
                ),
                prediction=(
                    "Shortening the holding period must raise the trade count above the "
                    "measurable threshold without eliminating the per-trade edge. If the "
                    "edge disappears entirely at the shorter horizon, the effect was an "
                    "artefact of the long hold and the claim is abandoned."
                ),
                mechanism=(
                    "An effect driven by order absorption resolves over the interval the "
                    "parent order takes to work, which is shorter than the holding period "
                    "this test assumed."
                ),
                family=obs.family,
                search_kind=SearchKind.HYPOTHESIS,
                rationale="too few trades to measure at the tested horizon",
            )
        ]

    if failure is FailureClass.NEGATIVE_EXPECTANCY:
        return [
            FollowUp(
                statement=(
                    f"The {obs.family} signal identifies the right event and takes the wrong "
                    "side of it. If the move it detects is exhaustion rather than "
                    "initiation, the same detector with the opposite sign should carry a "
                    "positive expectancy over the same trades."
                ),
                prediction=(
                    "Inverting the entry direction while holding the detector, the holding "
                    "period and the costs fixed must produce a positive expectancy net of "
                    "the same costs. If the inverted version also loses, the detector is "
                    "identifying noise and both directions are abandoned."
                ),
                mechanism=(
                    "A large move against thin liquidity can be either informed flow "
                    "continuing or a liquidation completing. The two are indistinguishable "
                    "in price alone and predict opposite subsequent returns."
                ),
                family=obs.family,
                search_kind=SearchKind.MECHANISM,
                rationale=(
                    "a consistent negative expectancy, which is a sign error rather than noise"
                ),
            )
        ]

    if failure in {FailureClass.ROBUSTNESS, FailureClass.WALK_FORWARD}:
        return [
            FollowUp(
                statement=(
                    f"The {obs.family} effect is regime-dependent rather than absent: it held "
                    "in some folds and not others. If the folds where it held share an "
                    "identifiable market state, a regime filter derived from that state "
                    "should restore out-of-sample stability."
                ),
                prediction=(
                    "Conditioning on the regime that distinguishes the successful folds must "
                    "raise walk-forward efficiency above the threshold that was missed. If "
                    "efficiency does not improve, the fold-to-fold variation was sampling "
                    "noise and the effect is abandoned."
                ),
                mechanism=(
                    "Participation and volatility regimes persist for weeks at a time, so an "
                    "effect generated by one regime's flow appears and disappears with it "
                    "rather than decaying smoothly."
                ),
                family=obs.family,
                search_kind=SearchKind.HYPOTHESIS,
                rationale="the effect held in some walk-forward folds and not others",
            )
        ]

    if failure is FailureClass.RISK:
        return [
            FollowUp(
                statement=(
                    f"The {obs.family} effect has a positive expectancy that is destroyed by "
                    "the shape of its loss distribution rather than by its mean. If the "
                    "losses cluster in an identifiable state, excluding that state should "
                    "preserve the expectancy at a survivable drawdown."
                ),
                prediction=(
                    "Excluding the state the largest losses occur in must reduce maximum "
                    "drawdown by more than it reduces net expectancy. If expectancy falls "
                    "faster than drawdown, the losses were the price of the edge and the "
                    "claim is abandoned."
                ),
                mechanism=(
                    "Liquidity provision earns a small premium most of the time and pays it "
                    "back in a few episodes; the episodes are the strategy, not an anomaly "
                    "in it."
                ),
                family=obs.family,
                search_kind=SearchKind.HYPOTHESIS,
                rationale="acceptable expectancy with an unacceptable loss distribution",
            )
        ]

    if failure is FailureClass.SELECTION_INTEGRITY:
        return [
            FollowUp(
                statement=(
                    f"The apparent {obs.family} result is a selection artefact of the search "
                    "rather than an effect in the data. If it is real, it should appear in a "
                    "pre-committed single configuration with no neighbourhood searched "
                    "around it."
                ),
                prediction=(
                    "A single pre-registered configuration, chosen before looking and never "
                    "adjusted, must produce a positive out-of-sample result. If it does not, "
                    "the effect was produced by the search and is abandoned."
                ),
                mechanism=(
                    "Selecting the best of many configurations produces a positive in-sample "
                    "result from noise alone; the only test that distinguishes it from an "
                    "effect is one with nothing to select from."
                ),
                family=obs.family,
                search_kind=SearchKind.HYPOTHESIS,
                rationale="the result did not survive selection-integrity deflation",
            )
        ]

    return []


def derive(obs: Observation, *, limit: int = 3) -> list[FollowUp]:
    """Every question this observation suggests, most specific first.

    Returns an empty list when nothing follows — a barren failure class, or a
    result with no structure to read. An empty list is a real answer and is
    recorded as such: "this failure suggested nothing" is different from "nobody
    asked what it suggested".
    """
    followups = _conditional_followups(obs) + _class_followups(obs)
    seen: set[str] = set()
    unique: list[FollowUp] = []
    for item in followups:
        signature = item.statement[:120].lower()
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(item)
    return unique[: max(0, limit)]


def derive_many(observations: Sequence[Observation], *, limit: int = 6) -> list[FollowUp]:
    """Follow-ups across a batch, deduplicated by statement.

    Used at the end of a campaign cycle, where several candidates may have
    failed the same way and should produce one question rather than five.
    """
    collected: list[FollowUp] = []
    seen: set[str] = set()
    for observation in observations:
        for item in derive(observation):
            signature = item.statement[:120].lower()
            if signature in seen:
                continue
            seen.add(signature)
            collected.append(item)
            if len(collected) >= limit:
                return collected
    return collected
