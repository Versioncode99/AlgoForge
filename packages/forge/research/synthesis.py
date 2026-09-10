"""Turning a hypothesis into something that can actually be run.

A research engine that can only rearrange twelve templates is a parameter
search however it is described. This module is what makes structural discovery
real: it composes a :class:`~forge.strategy.ir.StrategyDefinition` — *data*,
not code — from a closed vocabulary of signal archetypes, and hands it to the
existing pipeline that already knows how to make a definition safe:

    validate_definition → to_python → assert_safe → verify_python → smoke test

**Nothing here writes Python.** The IR renders it, the static guard refuses
filesystem, network, subprocess and dynamic execution, and ``verify_python``
proves the rendered module reproduces the definition's own ledger bar for bar.
An agent choosing an archetype and a set of operands is choosing from a menu
whose every item has an implementation and a test behind it. That is the
difference between "the engine may design strategies" and "the engine may
execute whatever it likes", and it is why this module has no ``exec`` and no
string templating of source.

**The archetypes are structurally distinct, not cosmetically so.** A breakout on
a rolling high and a reversion to a session VWAP read different features, take
opposite sides, and make different claims about why the effect exists.
Substituting one for the other is a structural change by any definition; moving
a lookback from 20 to 25 is not, and the novelty gate in
:mod:`forge.research.novelty` is what tells them apart.

**Composition is deterministic given a seed.** The same hypothesis and the same
draw produce the same definition, so a discovery can be re-derived rather than
only observed. That is what makes a generated strategy reproducible research
instead of a lucky artifact.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from forge.strategy.ir import (
    Arithmetic,
    Combine,
    Compare,
    Constant,
    Cross,
    DefinitionProvenance,
    EntryRules,
    ExecutionAssumptions,
    ExitRules,
    Feature,
    FeatureRef,
    Level,
    ParamRef,
    SessionWindow,
    StrategyDefinition,
)
from forge.strategy.models import ParameterSpec

#: The reference instant stamped on generated definitions' provenance. Fixed so
#: that the same composition hashes identically across processes — a discovery
#: re-derived on another machine must be recognisable as the same strategy.
SYNTHESIS_EPOCH = datetime(2026, 9, 10, tzinfo=UTC)

#: Session windows in minutes from midnight UTC, matching `forge.strategy.blueprints`.
LONDON_OPEN = 7 * 60
LONDON_CUTOFF = 10 * 60 + 30
NEW_YORK_OPEN = 13 * 60 + 30
NEW_YORK_CUTOFF = 17 * 60
NEW_YORK_FLAT = 20 * 60

#: Named windows an archetype may be gated to. A closed set: an agent may pick a
#: session, not invent an arbitrary four hours whose significance nobody stated.
SESSIONS: dict[str, tuple[int, int, str]] = {
    "london_morning": (LONDON_OPEN, LONDON_CUTOFF, "London morning"),
    "new_york_morning": (NEW_YORK_OPEN, NEW_YORK_CUTOFF, "New York morning"),
    "regular_hours": (NEW_YORK_OPEN, NEW_YORK_FLAT, "New York regular hours"),
    "overnight": (NEW_YORK_FLAT, LONDON_OPEN, "Overnight, Globex"),
}


class SynthesisError(ValueError):
    """A definition could not be composed. The message says why."""


def _p(
    name: str, default: float, low: float, high: float, step: float, description: str
) -> ParameterSpec:
    return ParameterSpec(
        name=name, default=default, low=low, high=high, step=step, description=description
    )


@dataclass(frozen=True)
class Archetype:
    """One way of constructing a signal, with the claim it embodies.

    The ``mechanism`` and ``prediction`` are not decoration. A generated
    strategy carries them onto its `StrategySpec`, and G9 tests the prediction
    against a randomised-entry control. An archetype with a vague mechanism
    would produce candidates the mechanism gate cannot fail, which is the
    situation that gate was written to end.
    """

    key: str
    label: str
    family: str
    #: Why an effect of this shape would exist, economically or statistically.
    mechanism: str
    #: The claim, with ``{direction}`` substituted at composition time.
    claim: str
    #: What result would abandon it.
    prediction: str
    features: tuple[Feature, ...]
    parameters: tuple[ParameterSpec, ...]
    #: Builds the long entry condition. The short is its mirror.
    long_condition: Any
    short_condition: Any
    required_data: tuple[str, ...] = ("BARS",)
    #: Feature supplying the unit for stops and targets.
    risk_feature: str = "atr"
    #: True when the effect is claimed to persist rather than revert, which
    #: decides whether a trailing exit or a fixed target is coherent with it.
    continuation: bool = True

    def feature_names(self) -> frozenset[str]:
        return frozenset(f.kind for f in self.features)

    def signature(self) -> frozenset[str]:
        """What this construction reads *and how it combines it*.

        Feature kinds alone are not enough to tell two constructions apart. A
        plain range breakout and a compression-gated one read the same four
        features; what separates them is that the second conjoins a second
        comparison against a scaled ATR. A signature that ignores the condition
        tree would call them the same signal, and the novelty gate would then
        refuse a genuinely different construction as a parameter change.
        """
        return self.feature_names() | structure_tokens(self.long_condition)


# ── the archetype vocabulary ─────────────────────────────────────────────────
# Each entry is a distinct signal construction, not a re-parameterisation of its
# neighbour. Adding one is a deliberate act with a mechanism attached, the same
# bar `FamilyRegistry.create` and `TemplateStore.create` already hold.

_ATR = Feature(name="atr", kind="atr", args=(ParamRef(name="atr_length"),))
_ATR_PARAM = _p("atr_length", 14, 7, 40, 1, "Bars in the ATR used to size risk.")


def _archetypes() -> tuple[Archetype, ...]:
    return (
        Archetype(
            key="range_breakout",
            label="Rolling-range breakout",
            family="breakout",
            mechanism=(
                "Resting orders concentrate at the edges of a recent range. Clearing them "
                "removes the liquidity that was holding price inside it, so the resolution "
                "is fast and continues while the taking order is worked."
            ),
            claim=(
                "A close beyond the extreme of the prior N bars is followed by {direction} "
                "continuation over a short horizon, because the break itself removed the "
                "resting liquidity that had been absorbing the move."
            ),
            prediction=(
                "Continuation must be stronger when the break happens on above-median "
                "range expansion. If the quiet-break subset does as well or better, the "
                "liquidity-removal mechanism is wrong and the claim is abandoned."
            ),
            features=(
                _ATR,
                Feature(name="hi", kind="highest", args=(ParamRef(name="lookback"),), shift=1),
                Feature(name="lo", kind="lowest", args=(ParamRef(name="lookback"),), shift=1),
                Feature(name="px", kind="close"),
            ),
            parameters=(
                _ATR_PARAM,
                _p("lookback", 20, 8, 90, 2, "Bars forming the range that must break."),
            ),
            long_condition=Compare(
                op="gt", left=FeatureRef(name="px"), right=FeatureRef(name="hi")
            ),
            short_condition=Compare(
                op="lt", left=FeatureRef(name="px"), right=FeatureRef(name="lo")
            ),
        ),
        Archetype(
            key="range_compression_release",
            label="Compression release",
            family="volatility",
            mechanism=(
                "A range narrow relative to its own recent volatility means participants "
                "have stopped disagreeing about price. The disagreement resumes as a "
                "directional move, because the positions built during the quiet period all "
                "need the same side of the market at once."
            ),
            claim=(
                "When the recent high-low range compresses below a multiple of ATR, the "
                "subsequent break resolves {direction} with more force than a break from a "
                "normal range, because the compression itself built the imbalance."
            ),
            prediction=(
                "Breaks out of a compressed range must outperform breaks out of an "
                "uncompressed one on the same instrument and horizon. If compression adds "
                "nothing, the mechanism is wrong and this is abandoned."
            ),
            features=(
                _ATR,
                Feature(name="hi", kind="highest", args=(ParamRef(name="lookback"),), shift=1),
                Feature(name="lo", kind="lowest", args=(ParamRef(name="lookback"),), shift=1),
                Feature(name="px", kind="close"),
            ),
            parameters=(
                _ATR_PARAM,
                _p("lookback", 30, 10, 120, 5, "Bars forming the range."),
                # An N-bar range is several ATRs wide by construction — the
                # median for a 30-bar range against a 14-bar ATR sits near 3.9.
                # A threshold below about 2.3 is unreachable, and a template
                # that can never fire is not a hypothesis that can be wrong.
                _p(
                    "compression",
                    3.0,
                    2.0,
                    6.0,
                    0.1,
                    "Range width in ATR below which it is compressed.",
                ),
            ),
            long_condition=Combine(
                kind="all",
                of=(
                    Compare(
                        op="lt",
                        left=Arithmetic(
                            op="sub", left=FeatureRef(name="hi"), right=FeatureRef(name="lo")
                        ),
                        right=Arithmetic(
                            op="mul",
                            left=FeatureRef(name="atr"),
                            right=ParamRef(name="compression"),
                        ),
                    ),
                    Compare(op="gt", left=FeatureRef(name="px"), right=FeatureRef(name="hi")),
                ),
            ),
            short_condition=Combine(
                kind="all",
                of=(
                    Compare(
                        op="lt",
                        left=Arithmetic(
                            op="sub", left=FeatureRef(name="hi"), right=FeatureRef(name="lo")
                        ),
                        right=Arithmetic(
                            op="mul",
                            left=FeatureRef(name="atr"),
                            right=ParamRef(name="compression"),
                        ),
                    ),
                    Compare(op="lt", left=FeatureRef(name="px"), right=FeatureRef(name="lo")),
                ),
            ),
        ),
        Archetype(
            key="vwap_deviation",
            label="Session VWAP deviation",
            family="mean_reversion",
            mechanism=(
                "Session VWAP is the reference institutional execution is measured against, "
                "so flow is drawn back towards it. A deviation is compensation paid to "
                "whoever absorbs the imbalance, and it is repaid when the imbalance clears."
            ),
            claim=(
                "Price displaced more than k standard deviations from session VWAP reverts "
                "{direction} towards it, because the displacement is transient inventory "
                "pressure rather than a change in the level participants are benchmarked to."
            ),
            prediction=(
                "Reversion must be stronger from larger deviations. If the shallow-deviation "
                "subset reverts as reliably as the deep one, the inventory explanation is "
                "wrong and the claim is abandoned."
            ),
            features=(
                _ATR,
                Feature(name="vwap", kind="session_vwap"),
                Feature(name="vwsd", kind="session_vwap_sd"),
                Feature(name="px", kind="close"),
            ),
            parameters=(
                _ATR_PARAM,
                _p("bands", 2.0, 0.5, 4.0, 0.25, "Standard deviations from VWAP to fade."),
            ),
            long_condition=Compare(
                op="lt",
                left=FeatureRef(name="px"),
                right=Arithmetic(
                    op="sub",
                    left=FeatureRef(name="vwap"),
                    right=Arithmetic(
                        op="mul", left=FeatureRef(name="vwsd"), right=ParamRef(name="bands")
                    ),
                ),
            ),
            short_condition=Compare(
                op="gt",
                left=FeatureRef(name="px"),
                right=Arithmetic(
                    op="add",
                    left=FeatureRef(name="vwap"),
                    right=Arithmetic(
                        op="mul", left=FeatureRef(name="vwsd"), right=ParamRef(name="bands")
                    ),
                ),
            ),
            continuation=False,
        ),
        Archetype(
            key="opening_range_break",
            label="Opening-range break",
            family="session_structure",
            mechanism=(
                "The opening auction concentrates overnight information into a short window. "
                "The range it establishes is where the day's disagreement was resolved, and "
                "leaving it means new information has arrived that the auction did not price."
            ),
            claim=(
                "A break of the opening range resolves {direction} for the remainder of the "
                "session, because the range represents the auction's clearing band and "
                "leaving it signals flow the auction did not absorb."
            ),
            prediction=(
                "The effect must be confined to breaks that occur within the session and "
                "must not appear for equivalent breaks of an arbitrary range measured at a "
                "random time of day. If it appears at random times, it is not a session "
                "effect and the claim is abandoned."
            ),
            features=(
                _ATR,
                Feature(name="orh", kind="opening_range_high", args=(ParamRef(name="range_bars"),)),
                Feature(name="orl", kind="opening_range_low", args=(ParamRef(name="range_bars"),)),
                Feature(name="px", kind="close"),
            ),
            parameters=(
                _ATR_PARAM,
                _p("range_bars", 30, 5, 120, 5, "Bars after the open forming the range."),
            ),
            long_condition=Compare(
                op="gt", left=FeatureRef(name="px"), right=FeatureRef(name="orh")
            ),
            short_condition=Compare(
                op="lt", left=FeatureRef(name="px"), right=FeatureRef(name="orl")
            ),
            required_data=("BARS", "SESSION_CLOCK"),
        ),
        Archetype(
            key="volatility_expansion",
            label="Volatility expansion",
            family="volatility",
            mechanism=(
                "Variance is persistent, so a rise in realised volatility relative to its "
                "own recent level marks the start of a repositioning episode rather than a "
                "single shock. Repositioning takes time and moves price in one direction "
                "while it happens."
            ),
            claim=(
                "It is the *change* in realised volatility, not its level, that precedes "
                "{direction} movement: an expansion from a quiet base marks participants "
                "adjusting, and a high but stable level marks them already adjusted."
            ),
            prediction=(
                "Sorting by the ratio of fast to slow realised volatility must separate "
                "profitable from unprofitable entries more sharply than sorting by the level "
                "of realised volatility does. If the level split is at least as sharp, the "
                "expansion explanation is wrong."
            ),
            features=(
                _ATR,
                Feature(name="fast_vol", kind="realised_vol", args=(ParamRef(name="fast_window"),)),
                Feature(name="slow_vol", kind="realised_vol", args=(ParamRef(name="slow_window"),)),
                Feature(name="drift", kind="roc", args=(ParamRef(name="fast_window"),)),
            ),
            parameters=(
                _ATR_PARAM,
                _p("fast_window", 15, 5, 60, 5, "Bars in the fast volatility estimate."),
                _p("slow_window", 120, 60, 400, 20, "Bars in the slow volatility estimate."),
                _p("expansion", 1.4, 1.0, 3.0, 0.1, "Fast/slow ratio marking an expansion."),
            ),
            long_condition=Combine(
                kind="all",
                of=(
                    Compare(
                        op="gt",
                        left=FeatureRef(name="fast_vol"),
                        right=Arithmetic(
                            op="mul",
                            left=FeatureRef(name="slow_vol"),
                            right=ParamRef(name="expansion"),
                        ),
                    ),
                    Compare(op="gt", left=FeatureRef(name="drift"), right=Constant(value=0.0)),
                ),
            ),
            short_condition=Combine(
                kind="all",
                of=(
                    Compare(
                        op="gt",
                        left=FeatureRef(name="fast_vol"),
                        right=Arithmetic(
                            op="mul",
                            left=FeatureRef(name="slow_vol"),
                            right=ParamRef(name="expansion"),
                        ),
                    ),
                    Compare(op="lt", left=FeatureRef(name="drift"), right=Constant(value=0.0)),
                ),
            ),
        ),
        Archetype(
            key="trend_strength_gate",
            label="Trend-strength gated cross",
            family="momentum",
            mechanism=(
                "A moving-average cross fires constantly in a directionless market and "
                "rarely in a trending one. Gating it on measured directional strength "
                "separates the crosses generated by drift from those generated by noise."
            ),
            claim=(
                "A fast/slow moving-average cross predicts {direction} continuation only "
                "while measured directional strength is above a threshold, because below it "
                "the cross is an artefact of oscillation rather than a change in drift."
            ),
            prediction=(
                "The gated version must outperform the ungated one on the same crosses. If "
                "gating removes as much profit as loss, directional strength carries no "
                "information here and the claim is abandoned."
            ),
            features=(
                _ATR,
                Feature(name="fast_ma", kind="ema", args=(ParamRef(name="fast_length"),)),
                Feature(name="slow_ma", kind="ema", args=(ParamRef(name="slow_length"),)),
                Feature(name="strength", kind="adx", args=(ParamRef(name="adx_length"),)),
            ),
            parameters=(
                _ATR_PARAM,
                _p("fast_length", 12, 4, 40, 2, "Fast moving-average length."),
                _p("slow_length", 48, 20, 200, 4, "Slow moving-average length."),
                _p("adx_length", 14, 7, 40, 1, "Bars in the directional-strength estimate."),
                _p(
                    "min_strength",
                    22,
                    10,
                    45,
                    1,
                    "Directional strength below which no entry is taken.",
                ),
            ),
            long_condition=Combine(
                kind="all",
                of=(
                    Cross(
                        direction="above",
                        left=FeatureRef(name="fast_ma"),
                        right=FeatureRef(name="slow_ma"),
                    ),
                    Compare(
                        op="gt",
                        left=FeatureRef(name="strength"),
                        right=ParamRef(name="min_strength"),
                    ),
                ),
            ),
            short_condition=Combine(
                kind="all",
                of=(
                    Cross(
                        direction="below",
                        left=FeatureRef(name="fast_ma"),
                        right=FeatureRef(name="slow_ma"),
                    ),
                    Compare(
                        op="gt",
                        left=FeatureRef(name="strength"),
                        right=ParamRef(name="min_strength"),
                    ),
                ),
            ),
        ),
        Archetype(
            key="volume_shock",
            label="Volume-shock reaction",
            family="liquidity",
            mechanism=(
                "A bar trading far above its recent average volume is liquidity being taken "
                "with urgency. Whether the move continues or reclaims separates informed "
                "flow from a liquidation, and the two are distinguishable by what price does "
                "immediately after."
            ),
            claim=(
                "A volume shock accompanied by a directional move is followed by "
                "{direction} movement, because the urgency that produced the shock is "
                "incompletely satisfied by the bar that contained it."
            ),
            prediction=(
                "The effect must be absent on equivalent price moves that occur without a "
                "volume shock. If ordinary moves of the same size behave identically, volume "
                "carries no information here and the claim is abandoned."
            ),
            features=(
                _ATR,
                Feature(name="vol", kind="volume"),
                Feature(
                    name="avg_vol",
                    kind="volume_sma",
                    args=(ParamRef(name="volume_window"),),
                    shift=1,
                ),
                Feature(name="drift", kind="roc", args=(ParamRef(name="impulse_bars"),)),
            ),
            parameters=(
                _ATR_PARAM,
                _p("volume_window", 60, 20, 240, 10, "Bars in the average volume baseline."),
                _p(
                    "volume_shock",
                    2.5,
                    1.2,
                    6.0,
                    0.1,
                    "Multiple of average volume marking a shock.",
                ),
                _p(
                    "impulse_bars",
                    5,
                    2,
                    30,
                    1,
                    "Bars over which the accompanying move is measured.",
                ),
            ),
            long_condition=Combine(
                kind="all",
                of=(
                    Compare(
                        op="gt",
                        left=FeatureRef(name="vol"),
                        right=Arithmetic(
                            op="mul",
                            left=FeatureRef(name="avg_vol"),
                            right=ParamRef(name="volume_shock"),
                        ),
                    ),
                    Compare(op="gt", left=FeatureRef(name="drift"), right=Constant(value=0.0)),
                ),
            ),
            short_condition=Combine(
                kind="all",
                of=(
                    Compare(
                        op="gt",
                        left=FeatureRef(name="vol"),
                        right=Arithmetic(
                            op="mul",
                            left=FeatureRef(name="avg_vol"),
                            right=ParamRef(name="volume_shock"),
                        ),
                    ),
                    Compare(op="lt", left=FeatureRef(name="drift"), right=Constant(value=0.0)),
                ),
            ),
            required_data=("BARS", "VOLUME"),
        ),
        Archetype(
            key="displacement_reversion",
            label="Displacement reversion",
            family="mean_reversion",
            mechanism=(
                "A move far beyond what current volatility justifies is being paid for by "
                "whoever provides the liquidity to absorb it. That premium is repaid as "
                "price returns towards the level it was displaced from."
            ),
            claim=(
                "Price displaced more than k ATR from its own moving average reverts "
                "{direction} towards it, because the displacement is compensation for "
                "immediacy rather than a repricing."
            ),
            prediction=(
                "Reversion must weaken as the displacement persists across more bars. If a "
                "displacement held for many bars reverts as readily as a fresh one, this is "
                "trend rather than absorption and the claim is abandoned."
            ),
            features=(
                _ATR,
                Feature(name="anchor", kind="sma", args=(ParamRef(name="anchor_length"),)),
                Feature(name="px", kind="close"),
            ),
            parameters=(
                _ATR_PARAM,
                _p("anchor_length", 60, 20, 300, 10, "Bars in the reference average."),
                _p("displacement", 2.0, 0.5, 5.0, 0.25, "ATR multiples of displacement to fade."),
            ),
            long_condition=Compare(
                op="lt",
                left=FeatureRef(name="px"),
                right=Arithmetic(
                    op="sub",
                    left=FeatureRef(name="anchor"),
                    right=Arithmetic(
                        op="mul",
                        left=FeatureRef(name="atr"),
                        right=ParamRef(name="displacement"),
                    ),
                ),
            ),
            short_condition=Compare(
                op="gt",
                left=FeatureRef(name="px"),
                right=Arithmetic(
                    op="add",
                    left=FeatureRef(name="anchor"),
                    right=Arithmetic(
                        op="mul",
                        left=FeatureRef(name="atr"),
                        right=ParamRef(name="displacement"),
                    ),
                ),
            ),
            continuation=False,
        ),
        Archetype(
            key="horizon_divergence",
            label="Cross-horizon divergence",
            family="statistical",
            mechanism=(
                "Return persistence measured over two horizons need not agree. Disagreement "
                "means the short horizon is being driven by flow the long horizon has not "
                "priced, which is a measurable statistical property independent of any story "
                "about why it exists."
            ),
            claim=(
                "When short-horizon and long-horizon rate of change disagree in sign, the "
                "subsequent move follows the {direction} of the longer horizon, because the "
                "shorter one is transient flow rather than a change in drift."
            ),
            prediction=(
                "The long horizon must win. If resolution follows the short horizon at least "
                "as often, the transient-flow explanation is exactly backwards and the claim "
                "is abandoned rather than reversed without a reason."
            ),
            features=(
                _ATR,
                Feature(name="fast_roc", kind="roc", args=(ParamRef(name="fast_horizon"),)),
                Feature(name="slow_roc", kind="roc", args=(ParamRef(name="slow_horizon"),)),
            ),
            parameters=(
                _ATR_PARAM,
                _p("fast_horizon", 10, 3, 40, 1, "Bars in the short-horizon rate of change."),
                _p("slow_horizon", 90, 40, 400, 10, "Bars in the long-horizon rate of change."),
            ),
            long_condition=Combine(
                kind="all",
                of=(
                    Compare(op="gt", left=FeatureRef(name="slow_roc"), right=Constant(value=0.0)),
                    Compare(op="lt", left=FeatureRef(name="fast_roc"), right=Constant(value=0.0)),
                ),
            ),
            short_condition=Combine(
                kind="all",
                of=(
                    Compare(op="lt", left=FeatureRef(name="slow_roc"), right=Constant(value=0.0)),
                    Compare(op="gt", left=FeatureRef(name="fast_roc"), right=Constant(value=0.0)),
                ),
            ),
        ),
        Archetype(
            key="oscillator_exhaustion",
            label="Oscillator exhaustion",
            family="mean_reversion",
            mechanism=(
                "A bounded oscillator at an extreme means the recent bars have been almost "
                "entirely one-sided. One-sided flow exhausts the participants able to supply "
                "it, and the pause that follows is mechanical rather than informational."
            ),
            claim=(
                "A bounded momentum oscillator beyond its extreme threshold precedes "
                "{direction} movement, because one-sided flow has exhausted the participants "
                "willing to continue supplying it."
            ),
            prediction=(
                "The effect must be stronger at more extreme readings. If moderate readings "
                "produce the same subsequent return, the exhaustion mechanism is wrong and "
                "the claim is abandoned."
            ),
            features=(
                _ATR,
                Feature(name="osc", kind="rsi", args=(ParamRef(name="osc_length"),)),
            ),
            parameters=(
                _ATR_PARAM,
                _p("osc_length", 14, 5, 40, 1, "Bars in the oscillator."),
                _p("extreme", 25, 10, 40, 1, "Distance from the midpoint marking an extreme."),
            ),
            long_condition=Compare(
                op="lt",
                left=FeatureRef(name="osc"),
                right=Arithmetic(
                    op="sub", left=Constant(value=50.0), right=ParamRef(name="extreme")
                ),
            ),
            short_condition=Compare(
                op="gt",
                left=FeatureRef(name="osc"),
                right=Arithmetic(
                    op="add", left=Constant(value=50.0), right=ParamRef(name="extreme")
                ),
            ),
            continuation=False,
        ),
    )


def structure_tokens(condition: Any, depth: int = 0) -> frozenset[str]:
    """Shape tokens for one condition tree.

    Deliberately shallow and structural: it records that a condition is a
    two-way conjunction of a comparison and a multiplication, not what the
    numbers are. Two constructions differing only in a threshold produce
    identical token sets, which is exactly right — that difference is a
    parameter change.
    """
    if condition is None or depth > 6:
        return frozenset()
    kind = getattr(condition, "kind", "")
    if kind in {"all", "any", "not"}:
        tokens = {f"{kind}/{len(condition.of)}"}
        for child in condition.of:
            tokens |= structure_tokens(child, depth + 1)
        return frozenset(tokens)
    if kind == "compare":
        return frozenset(
            {f"cmp:{condition.op}"}
            | _operand_tokens(condition.left, depth + 1)
            | _operand_tokens(condition.right, depth + 1)
        )
    if kind == "cross":
        return frozenset(
            {f"cross:{condition.direction}"}
            | _operand_tokens(condition.left, depth + 1)
            | _operand_tokens(condition.right, depth + 1)
        )
    if kind == "session":
        return frozenset({"session_gate"})
    return frozenset({kind}) if kind else frozenset()


def _operand_tokens(operand: Any, depth: int = 0) -> frozenset[str]:
    if operand is None or depth > 6:
        return frozenset()
    kind = getattr(operand, "kind", "")
    if kind == "expr":
        return frozenset(
            {f"expr:{operand.op}"}
            | _operand_tokens(operand.left, depth + 1)
            | _operand_tokens(operand.right, depth + 1)
        )
    if kind == "feature":
        return frozenset({f"reads:{operand.name}"})
    if kind == "param":
        return frozenset({"param"})
    if kind == "const":
        return frozenset({"const"})
    return frozenset()


ARCHETYPES: dict[str, Archetype] = {a.key: a for a in _archetypes()}


def archetypes_for(family: str) -> list[Archetype]:
    """Archetypes belonging to a family, or all of them when it has none.

    A generated family has no archetypes of its own by definition, so it draws
    from the whole vocabulary — which is the point: a new family is a new
    *explanation*, and it has to be able to borrow a construction to test it.
    """
    matching = [a for a in ARCHETYPES.values() if a.family == family]
    return matching or list(ARCHETYPES.values())


@dataclass(frozen=True)
class Composition:
    """A generated definition and the record of how it was put together."""

    definition: StrategyDefinition
    archetype: str
    direction: str
    session: str
    exit_style: str
    seed: int
    features: frozenset[str] = field(default_factory=frozenset)

    def as_dict(self) -> dict[str, Any]:
        return {
            "definition_id": self.definition.definition_id,
            "definition_hash": self.definition.definition_hash,
            "archetype": self.archetype,
            "direction": self.direction,
            "session": self.session,
            "exit_style": self.exit_style,
            "seed": self.seed,
            "features": sorted(self.features),
        }


#: How a trade is left. Each is coherent with a different claim about the effect:
#: a continuation claim earns a trailing exit, a reversion claim earns a target
#: at the level it claims price returns to.
EXIT_STYLES = ("atr_bracket", "trailing", "time_stop", "session_flat")


def _exit_rules(
    archetype: Archetype, style: str, rng: random.Random
) -> tuple[ExitRules, tuple[ParameterSpec, ...]]:
    """Build the way out, plus whatever parameters it needs."""
    stop = Level(kind="feature", multiple=ParamRef(name="stop_atr"), feature=archetype.risk_feature)
    extra: list[ParameterSpec] = [
        _p("stop_atr", 2.0, 0.5, 6.0, 0.25, "Stop distance in ATR."),
        _p("max_bars", 30, 5, 240, 5, "Hard time stop, in bars."),
    ]

    if style == "trailing":
        extra.append(_p("trail_atr", 2.0, 0.5, 6.0, 0.25, "Trailing distance in ATR."))
        return (
            ExitRules(
                stop=stop,
                trailing=Level(
                    kind="feature",
                    multiple=ParamRef(name="trail_atr"),
                    feature=archetype.risk_feature,
                ),
                max_bars=ParamRef(name="max_bars"),
            ),
            tuple(extra),
        )
    if style == "time_stop":
        return ExitRules(stop=stop, max_bars=ParamRef(name="max_bars")), tuple(extra)
    if style == "session_flat":
        return (
            ExitRules(
                stop=stop,
                max_bars=ParamRef(name="max_bars"),
                flat_by_minute=NEW_YORK_FLAT,
            ),
            tuple(extra),
        )
    extra.append(_p("target_atr", 3.0, 0.5, 10.0, 0.25, "Target distance in ATR."))
    return (
        ExitRules(
            stop=stop,
            target=Level(
                kind="feature", multiple=ParamRef(name="target_atr"), feature=archetype.risk_feature
            ),
            max_bars=ParamRef(name="max_bars"),
        ),
        tuple(extra),
    )


def compose(
    *,
    archetype: Archetype | str,
    family: str | None = None,
    symbol: str = "NQ",
    timeframe: str = "1m",
    seed: int = 0,
    direction: str | None = None,
    session: str | None = None,
    exit_style: str | None = None,
    hypothesis: str | None = None,
    prediction: str | None = None,
    mechanism: str | None = None,
    derived_from: str = "synthesis",
    note: str = "",
) -> Composition:
    """Compose one executable definition.

    Every free choice — direction, session gate, exit style — is drawn from the
    seeded generator, so the composition is reproducible from
    ``(archetype, seed)`` alone. Choices supplied explicitly override the draw,
    which is how a follow-up hypothesis pins the one dimension it is actually
    asking about and lets the rest vary.
    """
    arch = ARCHETYPES[archetype] if isinstance(archetype, str) else archetype
    rng = random.Random(seed)

    direction = direction or rng.choice(("both", "long", "short"))
    if direction not in {"both", "long", "short"}:
        raise SynthesisError(f"Unknown direction '{direction}'.")
    session = session if session is not None else rng.choice(("", "", *SESSIONS))
    if session and session not in SESSIONS:
        raise SynthesisError(f"Unknown session '{session}'. Known: {', '.join(SESSIONS)}.")
    exit_style = exit_style or (
        rng.choice(("trailing", "time_stop", "session_flat"))
        if arch.continuation
        else rng.choice(("atr_bracket", "time_stop"))
    )
    if exit_style not in EXIT_STYLES:
        raise SynthesisError(f"Unknown exit style '{exit_style}'.")

    exit_rules, exit_params = _exit_rules(arch, exit_style, rng)

    window = None
    if session:
        start, end, label = SESSIONS[session]
        window = SessionWindow(start_minute=start, end_minute=end, label=label)

    entry = EntryRules(
        long=arch.long_condition if direction in {"both", "long"} else None,
        short=arch.short_condition if direction in {"both", "short"} else None,
        session=window,
    )

    stated_direction = {
        "both": "directional",
        "long": "upward",
        "short": "downward",
    }[direction]
    claim = (hypothesis or arch.claim).format(direction=stated_direction)
    if session:
        claim += f" Tested only inside the {SESSIONS[session][2]} window."

    # Parameters are the union of the archetype's and the exit's, deduplicated
    # by name with the archetype winning — an archetype that declares its own
    # stop sizing means it, and the exit must not quietly redefine it.
    by_name: dict[str, ParameterSpec] = {p.name: p for p in exit_params}
    by_name.update({p.name: p for p in arch.parameters})

    definition = StrategyDefinition(
        name=f"{arch.label} · {symbol}",
        family=family or arch.family,
        symbol=symbol,
        timeframe=timeframe,
        hypothesis=claim,
        falsifiable_prediction=prediction or arch.prediction,
        features=arch.features,
        entry=entry,
        exit=exit_rules,
        parameters=tuple(by_name.values()),
        execution=ExecutionAssumptions(),
        provenance=DefinitionProvenance(
            author="algoforge-research",
            created_at=SYNTHESIS_EPOCH,
            derived_from=derived_from,
            note=note or (mechanism or arch.mechanism)[:400],
        ),
    )
    return Composition(
        definition=definition,
        archetype=arch.key,
        direction=direction,
        session=session,
        exit_style=exit_style,
        seed=seed,
        features=frozenset(f.kind for f in arch.features),
    )


def structural_variants(
    archetype: Archetype | str, *, count: int = 4, symbol: str = "NQ", base_seed: int = 0
) -> list[Composition]:
    """Several structurally distinct compositions of one archetype.

    Distinct in the sense the novelty gate uses: the direction taken, the
    session gated to, and the way the trade is left all change what the
    strategy *is*, not what its numbers are. Deduplicated by definition hash,
    so a draw that happens to repeat an earlier one is dropped rather than
    counted twice.
    """
    seen: set[str] = set()
    variants: list[Composition] = []
    for offset in range(count * 4):
        candidate = compose(archetype=archetype, symbol=symbol, seed=base_seed + offset)
        if candidate.definition.definition_hash in seen:
            continue
        seen.add(candidate.definition.definition_hash)
        variants.append(candidate)
        if len(variants) >= count:
            break
    return variants


def mechanism_summary(archetype: Archetype | str) -> str:
    arch = ARCHETYPES[archetype] if isinstance(archetype, str) else archetype
    return arch.mechanism


def required_data(archetypes: Sequence[Archetype | str]) -> tuple[str, ...]:
    needed: set[str] = set()
    for item in archetypes:
        arch = ARCHETYPES[item] if isinstance(item, str) else item
        needed.update(arch.required_data)
    return tuple(sorted(needed))
