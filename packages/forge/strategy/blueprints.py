"""Strategies stated as data, ready to be composed or edited.

The twelve Python templates are the older half of the library and stay exactly
as they are: they are what a person writes when the idea does not fit a schema.
These are the other half — the same kinds of idea expressed as a
:class:`StrategyDefinition`, which buys three things a function body cannot
offer.

* **The levels are visible.** A definition says where its stop is, so the chart
  can draw it and the trade ledger can record the one that was actually in
  force. A Python `exit_signal` that returns the string ``"stop"`` knows where
  the stop was and has no way to say.
* **They can be edited without writing code.** "The same thing but on ES, and
  no entries after 11:30" is a field change.
* **They can be exported.** The IR is what a Pine or NinjaScript generator would
  read; a function body would have to be understood first.

Each one carries a real mechanism and a real falsifiable prediction, for the
same reason the Python templates do: the judge's mechanism gate has nothing to
test against otherwise, and a hypothesis that cannot be wrong is not one.

**Session times are UTC**, and the constants below say what that means in the
trading day. A window written in local time and read as UTC is a strategy
trading the wrong four hours, which backtests perfectly well and means nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime

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

#: The reference instant for the shipped blueprints' provenance. Fixed rather
#: than `now()` so a blueprint's definition_hash is stable across processes —
#: two installations shipping the same blueprint must agree that it is the same
#: strategy.
SHIPPED_AT = datetime(2026, 9, 8, tzinfo=UTC)

# Session windows, in minutes from midnight UTC. Written out rather than
# inlined, because "480" is not readable and "London open" is.
LONDON_OPEN = 7 * 60  # 08:00 London in winter, 07:00 UTC
LONDON_CUTOFF = 10 * 60 + 30  # 11:30 London — the prompt's "no entries after"
LONDON_FLAT = 15 * 60  # 16:00 London, before the US close
NEW_YORK_OPEN = 13 * 60 + 30  # 09:30 New York
NEW_YORK_CUTOFF = 17 * 60  # 13:00 New York
NEW_YORK_FLAT = 20 * 60  # 16:00 New York, the cash close


def _provenance(note: str) -> DefinitionProvenance:
    return DefinitionProvenance(
        author="algoforge",
        created_at=SHIPPED_AT,
        derived_from="blueprint",
        note=note,
    )


LONDON_BREAKOUT = StrategyDefinition(
    name="London Breakout",
    family="breakout",
    symbol="NQ",
    timeframe="1m",
    hypothesis=(
        "The first hour of the London session establishes a range that resting liquidity "
        "collects around. A break of that range is disproportionately liquidity-taking "
        "flow which must be absorbed over subsequent bars, so continuation should follow "
        "the break rather than immediate reversion."
    ),
    falsifiable_prediction=(
        "Continuation must be stronger when the break occurs while realised volatility is "
        "below its own median. If the high-volatility half performs equally or better, the "
        "absorption mechanism is wrong and this is abandoned regardless of aggregate P&L."
    ),
    features=(
        Feature(
            name="range_high",
            kind="opening_range_high",
            args=(ParamRef(name="range_bars"),),
            description="High of the first N bars of the session.",
        ),
        Feature(
            name="range_low",
            kind="opening_range_low",
            args=(ParamRef(name="range_bars"),),
            description="Low of the first N bars of the session.",
        ),
        Feature(name="atr14", kind="atr", args=(Constant(value=14),)),
        Feature(name="last_close", kind="close"),
        Feature(
            name="since_open",
            kind="bars_since_session_open",
            description="Used to refuse a break before the range has formed.",
        ),
    ),
    entry=EntryRules(
        long=Combine(
            kind="all",
            of=(
                # The range must have finished forming. Without this the first
                # bar of the session breaks its own one-bar range, every day.
                Compare(
                    op="gte",
                    left=FeatureRef(name="since_open"),
                    right=ParamRef(name="range_bars"),
                ),
                Compare(
                    op="gt",
                    left=FeatureRef(name="last_close"),
                    right=FeatureRef(name="range_high"),
                ),
            ),
        ),
        short=Combine(
            kind="all",
            of=(
                Compare(
                    op="gte",
                    left=FeatureRef(name="since_open"),
                    right=ParamRef(name="range_bars"),
                ),
                Compare(
                    op="lt",
                    left=FeatureRef(name="last_close"),
                    right=FeatureRef(name="range_low"),
                ),
            ),
        ),
        session=SessionWindow(
            start_minute=LONDON_OPEN,
            end_minute=LONDON_CUTOFF,
            label="London open to 11:30 London",
        ),
    ),
    exit=ExitRules(
        stop=Level(kind="feature", multiple=ParamRef(name="stop_atr"), feature="atr14"),
        target=Level(kind="feature", multiple=ParamRef(name="target_atr"), feature="atr14"),
        max_bars=ParamRef(name="max_bars"),
        # Flat before the US close. A breakout thesis about the London session
        # has nothing to say about holding through the New York afternoon.
        flat_by_minute=LONDON_FLAT,
    ),
    parameters=(
        ParameterSpec(
            name="range_bars",
            default=30,
            low=10,
            high=90,
            step=10,
            description="Bars of the session used to form the opening range.",
        ),
        ParameterSpec(
            name="stop_atr",
            default=1.5,
            low=0.5,
            high=4.0,
            step=0.5,
            description="Stop distance as a multiple of ATR(14).",
        ),
        ParameterSpec(
            name="target_atr",
            default=3.0,
            low=1.0,
            high=8.0,
            step=1.0,
            description="Target distance as a multiple of ATR(14).",
        ),
        ParameterSpec(
            name="max_bars",
            default=120,
            low=20,
            high=400,
            step=20,
            description="Hard time stop, in bars.",
        ),
    ),
    execution=ExecutionAssumptions(),
    provenance=_provenance(
        "The worked example from the product brief: NQ London breakout, ATR risk, "
        "no entries after 11:30 London."
    ),
)


VWAP_REVERSION = StrategyDefinition(
    name="Session VWAP Reversion",
    family="mean_reversion",
    symbol="NQ",
    timeframe="1m",
    hypothesis=(
        "Price displaced several volume-weighted standard deviations from the session VWAP "
        "is disproportionately the result of impatient flow into a thin book, and should "
        "revert toward the session's volume-weighted average as liquidity replenishes."
    ),
    falsifiable_prediction=(
        "Reversion must be measurably stronger in the low-volatility half of the sample "
        "than in the high-volatility half, because absorption is easier in a calm book. "
        "Equal or inverted behaviour disproves the stated mechanism."
    ),
    features=(
        Feature(name="vwap", kind="session_vwap"),
        Feature(name="vwap_sd", kind="session_vwap_sd"),
        Feature(name="atr14", kind="atr", args=(Constant(value=14),)),
        Feature(name="last_close", kind="close"),
        Feature(name="since_open", kind="bars_since_session_open"),
    ),
    entry=EntryRules(
        long=Combine(
            kind="all",
            of=(
                # A VWAP built from four bars is not a session average.
                Compare(op="gte", left=FeatureRef(name="since_open"), right=Constant(value=60)),
                Compare(
                    op="lt",
                    left=FeatureRef(name="last_close"),
                    right=Arithmetic(
                        op="sub",
                        left=FeatureRef(name="vwap"),
                        right=Arithmetic(
                            op="mul",
                            left=ParamRef(name="entry_sd"),
                            right=FeatureRef(name="vwap_sd"),
                        ),
                    ),
                ),
            ),
        ),
        short=Combine(
            kind="all",
            of=(
                Compare(op="gte", left=FeatureRef(name="since_open"), right=Constant(value=60)),
                Compare(
                    op="gt",
                    left=FeatureRef(name="last_close"),
                    right=Arithmetic(
                        op="add",
                        left=FeatureRef(name="vwap"),
                        right=Arithmetic(
                            op="mul",
                            left=ParamRef(name="entry_sd"),
                            right=FeatureRef(name="vwap_sd"),
                        ),
                    ),
                ),
            ),
        ),
    ),
    exit=ExitRules(
        # The thesis is reversion to the average, so the exit is the average.
        signal=Combine(
            kind="any",
            of=(
                Cross(
                    direction="above",
                    left=FeatureRef(name="last_close"),
                    right=FeatureRef(name="vwap"),
                ),
                Cross(
                    direction="below",
                    left=FeatureRef(name="last_close"),
                    right=FeatureRef(name="vwap"),
                ),
            ),
        ),
        stop=Level(kind="feature", multiple=ParamRef(name="stop_atr"), feature="atr14"),
        max_bars=ParamRef(name="max_bars"),
    ),
    parameters=(
        ParameterSpec(
            name="entry_sd",
            default=2.0,
            low=1.0,
            high=4.0,
            step=0.5,
            description="Displacement from VWAP, in volume-weighted standard deviations.",
        ),
        ParameterSpec(
            name="stop_atr",
            default=2.0,
            low=0.5,
            high=5.0,
            step=0.5,
            description="Stop distance as a multiple of ATR(14).",
        ),
        ParameterSpec(
            name="max_bars",
            default=90,
            low=15,
            high=300,
            step=15,
            description="Hard time stop, in bars.",
        ),
    ),
    provenance=_provenance("Session-anchored mean reversion with an ATR stop."),
)


TREND_PULLBACK = StrategyDefinition(
    name="New York Trend Pullback",
    family="trend_following",
    symbol="NQ",
    timeframe="1m",
    hypothesis=(
        "In a market already trending on the session timeframe, a shallow pullback toward "
        "the shorter moving average is where resting trend-following interest is filled. "
        "Entering on the resumption should capture the continuation that interest causes, "
        "rather than the noise a fresh breakout would."
    ),
    falsifiable_prediction=(
        "The edge must be concentrated where ADX is above its threshold. If entries taken "
        "while ADX is below threshold perform equally well, the trend condition is doing "
        "no work and the stated mechanism is wrong."
    ),
    features=(
        Feature(name="fast", kind="sma", args=(ParamRef(name="fast_length"),)),
        Feature(name="slow", kind="sma", args=(ParamRef(name="slow_length"),)),
        Feature(name="adx14", kind="adx", args=(Constant(value=14),)),
        Feature(name="atr14", kind="atr", args=(Constant(value=14),)),
        Feature(name="last_close", kind="close"),
    ),
    entry=EntryRules(
        long=Combine(
            kind="all",
            of=(
                Compare(
                    op="gt", left=FeatureRef(name="fast"), right=FeatureRef(name="slow")
                ),
                Compare(
                    op="gte",
                    left=FeatureRef(name="adx14"),
                    right=ParamRef(name="adx_floor"),
                ),
                # The pullback, and its resumption: price crossed back above the
                # fast average having been below it.
                Cross(
                    direction="above",
                    left=FeatureRef(name="last_close"),
                    right=FeatureRef(name="fast"),
                ),
            ),
        ),
        short=Combine(
            kind="all",
            of=(
                Compare(
                    op="lt", left=FeatureRef(name="fast"), right=FeatureRef(name="slow")
                ),
                Compare(
                    op="gte",
                    left=FeatureRef(name="adx14"),
                    right=ParamRef(name="adx_floor"),
                ),
                Cross(
                    direction="below",
                    left=FeatureRef(name="last_close"),
                    right=FeatureRef(name="fast"),
                ),
            ),
        ),
        session=SessionWindow(
            start_minute=NEW_YORK_OPEN,
            end_minute=NEW_YORK_CUTOFF,
            label="New York open to 13:00 New York",
        ),
    ),
    exit=ExitRules(
        # A trailing stop rather than a fixed target: the thesis is that the
        # trend continues, and a fixed target would cap exactly the outcome the
        # hypothesis predicts.
        trailing=Level(kind="feature", multiple=ParamRef(name="trail_atr"), feature="atr14"),
        max_bars=ParamRef(name="max_bars"),
        flat_by_minute=NEW_YORK_FLAT,
    ),
    parameters=(
        ParameterSpec(name="fast_length", default=20, low=5, high=60, step=5),
        ParameterSpec(name="slow_length", default=100, low=40, high=300, step=20),
        ParameterSpec(
            name="adx_floor",
            default=25.0,
            low=10.0,
            high=50.0,
            step=5.0,
            description="Minimum ADX(14) for the market to count as trending.",
        ),
        ParameterSpec(
            name="trail_atr",
            default=2.0,
            low=0.5,
            high=6.0,
            step=0.5,
            description="Trailing stop distance as a multiple of ATR(14).",
        ),
        ParameterSpec(name="max_bars", default=180, low=30, high=600, step=30),
    ),
    provenance=_provenance("Trend continuation with a ratcheting ATR trail."),
)


BLUEPRINTS: dict[str, StrategyDefinition] = {
    "london_breakout": LONDON_BREAKOUT,
    "vwap_reversion": VWAP_REVERSION,
    "trend_pullback": TREND_PULLBACK,
}


def blueprint(key: str) -> StrategyDefinition:
    definition = BLUEPRINTS.get(key)
    if definition is None:
        raise KeyError(f"unknown blueprint '{key}'. Known: {', '.join(sorted(BLUEPRINTS))}")
    return definition


def catalogue() -> list[dict[str, object]]:
    """What the interface and the agent both list. Includes the identity, so a
    caller can tell two blueprints apart after one has been edited."""
    return [
        {
            "key": key,
            "name": definition.name,
            "family": definition.family,
            "symbol": definition.symbol,
            "timeframe": definition.timeframe,
            "hypothesis": definition.hypothesis,
            "falsifiable_prediction": definition.falsifiable_prediction,
            "definition_id": definition.definition_id,
            "definition_hash": definition.definition_hash,
            "parameters": [p.model_dump(mode="json") for p in definition.parameters],
            "features": [f.name for f in definition.features],
            "session": (
                definition.entry.session.label if definition.entry.session else ""
            ),
            "has_stop": definition.exit.stop is not None,
            "has_target": definition.exit.target is not None,
            "has_trailing": definition.exit.trailing is not None,
            "warmup_bars": definition.required_warmup(),
        }
        for key, definition in sorted(BLUEPRINTS.items())
    ]
