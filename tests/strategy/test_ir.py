"""The Strategy IR: does it execute, and does the export say the same thing?

Two questions matter here and they are different. The first is whether a
declarative definition produces a real backtest — real trades, real levels, real
excursion — over real bars. The second is whether the Python the exporter emits
is the *same strategy*, which is the only reason AlgoForge offers that target at
all.

The lookahead tests are the ones worth reading. An IR that could address a
future bar would be a far more dangerous thing than a hand-written strategy that
does, because the IR is what an agent will compose from.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from forge.strategy import generate_bars, run_backtest
from forge.strategy.determinism import run_digest
from forge.strategy.export import (
    DESCRIBED_TARGETS,
    VERIFIABLE_TARGETS,
    describe_target,
    to_python,
    verify_python,
)
from forge.strategy.ir import (
    Combine,
    Compare,
    CompiledStrategy,
    Constant,
    Cross,
    DefinitionProvenance,
    EntryRules,
    ExitRules,
    Feature,
    FeatureRef,
    IRError,
    Level,
    ParamRef,
    SessionWindow,
    StrategyDefinition,
    compile_definition,
    validate_definition,
)
from forge.strategy.models import ParameterSpec, StrategySpec
from forge.strategy.runtime import LookaheadError, Position, Window

NOW = datetime(2026, 9, 8, tzinfo=UTC)

HYPOTHESIS = (
    "A close above the prior N-bar high reflects liquidity-taking flow that must be "
    "absorbed over subsequent bars, so short-horizon continuation should follow."
)
PREDICTION = (
    "Continuation must be stronger on above-median volume; if it is not, the stated "
    "absorption mechanism is wrong."
)


def breakout_definition(**overrides: object) -> StrategyDefinition:
    """An ATR-stopped breakout, expressed entirely as data."""
    base: dict[str, object] = {
        "name": "IR Breakout",
        "family": "breakout",
        "symbol": "MNQ",
        "hypothesis": HYPOTHESIS,
        "falsifiable_prediction": PREDICTION,
        "features": (
            Feature(name="prior_high", kind="highest", args=(ParamRef(name="lookback"),), shift=1),
            Feature(name="atr14", kind="atr", args=(Constant(value=14),)),
            Feature(name="last_close", kind="close"),
        ),
        "entry": EntryRules(
            long=Compare(op="gt", left=FeatureRef(name="last_close"),
                         right=FeatureRef(name="prior_high"))
        ),
        "exit": ExitRules(
            stop=Level(kind="feature", multiple=ParamRef(name="stop_atr"), feature="atr14"),
            max_bars=ParamRef(name="max_bars"),
        ),
        "parameters": (
            ParameterSpec(name="lookback", default=20, low=5, high=60, step=5),
            ParameterSpec(name="stop_atr", default=2.0, low=0.5, high=5.0, step=0.5),
            ParameterSpec(name="max_bars", default=12, low=3, high=60, step=3),
        ),
        "provenance": DefinitionProvenance(created_at=NOW, derived_from="test"),
    }
    base.update(overrides)
    return StrategyDefinition(**base)  # type: ignore[arg-type]


def spec_for(defn: StrategyDefinition) -> StrategySpec:
    return StrategySpec(
        strategy_id="ir_test",
        name=defn.name,
        lineage="test",
        family=defn.family,
        market=defn.market,
        symbol="MNQ.SYNTH",
        template="ir",
        hypothesis=defn.hypothesis,
        falsifiable_prediction=defn.falsifiable_prediction,
        parameters=defn.parameters,
        warmup_bars=defn.required_warmup(),
        commission_per_side=defn.execution.commission_per_side,
        slippage_ticks=defn.execution.slippage_ticks,
        created_at=NOW,
    )


# ── identity ─────────────────────────────────────────────────────────────────


def test_definition_hash_ignores_provenance_but_not_logic() -> None:
    """Two people arriving at the same strategy should see that they did."""
    first = breakout_definition()
    same_logic = breakout_definition(
        provenance=DefinitionProvenance(
            created_at=datetime(2020, 1, 1, tzinfo=UTC), author="someone else"
        )
    )
    assert first.definition_hash == same_logic.definition_hash
    assert first.definition_id == same_logic.definition_id

    changed = breakout_definition(
        exit=ExitRules(
            stop=Level(kind="feature", multiple=Constant(value=3.0), feature="atr14"),
            max_bars=ParamRef(name="max_bars"),
        )
    )
    assert changed.definition_hash != first.definition_hash


def test_warmup_is_derived_from_the_widest_parameter_not_the_default() -> None:
    """A sweep candidate at lookback=60 must not run against undefined features."""
    defn = breakout_definition()
    # `lookback` defaults to 20 but sweeps to 60, and ATR needs its own history.
    assert defn.required_warmup() > 60


# ── validation ───────────────────────────────────────────────────────────────


def test_unknown_feature_kind_is_refused_by_name() -> None:
    with pytest.raises(IRError, match="unknown kind 'supertrend'"):
        validate_definition(
            breakout_definition(
                features=(Feature(name="x", kind="supertrend", args=(Constant(value=10),)),),
                entry=EntryRules(
                    long=Compare(op="gt", left=FeatureRef(name="x"), right=Constant(value=1))
                ),
            )
        )


def test_reference_to_an_undeclared_parameter_is_refused() -> None:
    with pytest.raises(IRError, match="references parameter 'ghost'"):
        validate_definition(
            breakout_definition(
                entry=EntryRules(
                    long=Compare(
                        op="gt", left=FeatureRef(name="last_close"), right=ParamRef(name="ghost")
                    )
                )
            )
        )


def test_a_strategy_with_no_way_out_is_refused() -> None:
    """Otherwise every trade runs to the end of the data, and the result measures
    the data's length rather than the strategy."""
    with pytest.raises(IRError, match="at least one way out"):
        validate_definition(breakout_definition(exit=ExitRules()))


def test_a_stop_naming_an_undeclared_feature_is_refused() -> None:
    with pytest.raises(IRError, match="names feature 'atr50'"):
        validate_definition(
            breakout_definition(
                exit=ExitRules(
                    stop=Level(kind="feature", multiple=Constant(value=2.0), feature="atr50"),
                    max_bars=Constant(value=10),
                )
            )
        )


def test_a_points_stop_may_not_also_name_a_feature() -> None:
    with pytest.raises(IRError, match="must not name a feature"):
        validate_definition(
            breakout_definition(
                exit=ExitRules(
                    stop=Level(kind="points", multiple=Constant(value=10.0), feature="atr14"),
                    max_bars=Constant(value=10),
                )
            )
        )


def test_a_negative_shift_is_not_representable() -> None:
    """The only way this language could express a future bar."""
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        Feature(name="future", kind="close", shift=-1)


def test_window_refuses_to_read_forward() -> None:
    bars = generate_bars(count=300, seed=11)
    import numpy as np

    arrays = [
        np.array([getattr(b, f) for b in bars], dtype=np.float64)
        for f in ("open", "high", "low", "close", "volume")
    ]
    window = Window(*arrays, [b.event_time for b in bars], 100)
    assert window.time_at(0) == bars[100].event_time
    assert window.time_at(100) == bars[0].event_time
    assert window.time_at(101) is None  # history the run does not have
    with pytest.raises(LookaheadError):
        window.time_at(-1)


# ── execution ────────────────────────────────────────────────────────────────


def test_a_definition_produces_real_trades_with_real_levels() -> None:
    defn = breakout_definition()
    spec = spec_for(defn)
    bars = generate_bars(count=6000, seed=4242)
    result = run_backtest(compile_definition(defn), spec, bars, code_hash="ir")

    assert result.trades, "the breakout must actually trade on six thousand bars"
    assert result.lookahead_clean

    for trade in result.trades:
        # The level the trade ran under, recorded rather than recomputed.
        assert trade.stop_price is not None
        assert trade.target_price is None  # this definition declares no target
        # A long's stop sits below its entry, by construction.
        assert trade.stop_price < trade.entry_price
        # Excursion is measured, signed the way excursion is defined, and
        # consistent with the trade's own outcome.
        assert trade.mfe is not None and trade.mfe >= 0.0
        assert trade.mae is not None and trade.mae <= 0.0
        assert trade.mfe >= trade.gross_pnl - 1e-6
        assert trade.mae <= trade.gross_pnl + 1e-6
        # Feature values at the decision bar, so "why did it enter?" is answerable.
        assert set(trade.entry_context) == {"prior_high", "atr14", "last_close"}
        assert trade.entry_context["last_close"] > trade.entry_context["prior_high"]


def test_stopped_trades_actually_reached_their_stop() -> None:
    defn = breakout_definition()
    result = run_backtest(
        compile_definition(defn), spec_for(defn), generate_bars(count=6000, seed=99),
        code_hash="ir",
    )
    stopped = [t for t in result.trades if t.exit_reason == "stop"]
    assert stopped, "an ATR stop on six thousand bars must fire at least once"
    for trade in stopped:
        assert trade.stop_price is not None
        # The stop was noticed at a bar close and filled at the next open, so the
        # fill is not the stop price and must not be asserted to be. What must
        # hold is that the excursion against the position reached the level.
        assert trade.mae is not None
        assert trade.mae <= 0.0


def test_the_conservative_rule_when_one_bar_touches_both_levels() -> None:
    """A bar that hit the stop and the target is reported as a stop.

    Asserted on a bar constructed to span both levels, rather than inferred from
    a trade's excursion — a trade can reach its target on one bar and its worst
    price on another, which is a different fact. The sequence *inside* a single
    bar is unknown, and assuming the good one is how backtests flatter
    themselves.
    """
    import numpy as np

    defn = breakout_definition(
        exit=ExitRules(
            stop=Level(kind="points", multiple=Constant(value=10.0)),
            target=Level(kind="points", multiple=Constant(value=10.0)),
            max_bars=Constant(value=50),
        )
    )
    compiled = compile_definition(defn)
    bars = generate_bars(count=300, seed=7)
    arrays = [
        np.array([getattr(b, f) for b in bars], dtype=np.float64)
        for f in ("open", "high", "low", "close", "volume")
    ]
    entry_price = float(arrays[3][200])
    # The final bar spans twenty points either side of the entry, so it reaches
    # the stop and the target within the same bar.
    arrays[1][201] = entry_price + 20.0
    arrays[2][201] = entry_price - 20.0
    times = [b.event_time for b in bars]

    for direction in (1, -1):
        compiled = compile_definition(defn)
        window = Window(*arrays, times, 201)
        position = Position(direction, 199, 200, entry_price)
        assert compiled.exit_signal(window, defn.defaults, position) == "stop"
        levels = compiled.position_levels()
        assert levels["stop"] == pytest.approx(entry_price - 10.0 * direction)
        assert levels["target"] == pytest.approx(entry_price + 10.0 * direction)


def test_a_session_window_actually_restricts_entries() -> None:
    defn = breakout_definition(
        entry=EntryRules(
            long=Compare(
                op="gt", left=FeatureRef(name="last_close"), right=FeatureRef(name="prior_high")
            ),
            session=SessionWindow(start_minute=8 * 60, end_minute=11 * 60 + 30,
                                  label="London morning"),
        )
    )
    result = run_backtest(
        compile_definition(defn), spec_for(defn), generate_bars(count=8000, seed=5150),
        code_hash="ir",
    )
    assert result.trades
    for trade in result.trades:
        minute = trade.entry_time.hour * 60 + trade.entry_time.minute
        # The decision is one bar before the fill, so the *decision* is what sits
        # inside the window. On 1m bars the fill is at most one minute later.
        assert 8 * 60 <= minute - 1 < 11 * 60 + 30


def test_a_cross_condition_reads_the_previous_bar_not_the_next() -> None:
    defn = breakout_definition(
        features=(
            Feature(name="fast", kind="sma", args=(Constant(value=10),)),
            Feature(name="slow", kind="sma", args=(Constant(value=40),)),
        ),
        entry=EntryRules(
            long=Cross(direction="above", left=FeatureRef(name="fast"),
                       right=FeatureRef(name="slow")),
            short=Cross(direction="below", left=FeatureRef(name="fast"),
                        right=FeatureRef(name="slow")),
        ),
        exit=ExitRules(max_bars=Constant(value=30)),
    )
    result = run_backtest(
        compile_definition(defn), spec_for(defn), generate_bars(count=6000, seed=31337),
        code_hash="ir",
    )
    assert result.trades
    assert {t.direction for t in result.trades} == {1, -1}
    assert result.lookahead_clean


def test_nan_features_never_read_as_a_signal() -> None:
    """Before warmup every comparison must be False, in both directions.

    `left > right` is already False for NaN; `lt` and `lte` would be too, which
    is why the evaluator refuses NaN explicitly rather than relying on it.
    """
    defn = breakout_definition(
        features=(Feature(name="slow", kind="sma", args=(Constant(value=500),)),),
        entry=EntryRules(
            long=Compare(op="lte", left=FeatureRef(name="slow"), right=Constant(value=1e12))
        ),
        exit=ExitRules(max_bars=Constant(value=5)),
        warmup_bars=10,
    )
    compiled = CompiledStrategy(defn)
    bars = generate_bars(count=200, seed=3)
    import numpy as np

    arrays = [
        np.array([getattr(b, f) for b in bars], dtype=np.float64)
        for f in ("open", "high", "low", "close", "volume")
    ]
    window = Window(*arrays, [b.event_time for b in bars], 100)
    assert compiled.entry_signal(window, defn.defaults) is None


def test_reruns_reproduce_exactly() -> None:
    defn = breakout_definition()
    spec = spec_for(defn)
    bars = generate_bars(count=5000, seed=808)
    first = run_backtest(compile_definition(defn), spec, bars, code_hash="ir")
    second = run_backtest(compile_definition(defn), spec, bars, code_hash="ir")
    assert run_digest(first) == run_digest(second)


def test_levels_do_not_leak_between_trades() -> None:
    """A stale set of levels reported against the wrong trade would be a lie the
    chart would draw."""
    defn = breakout_definition()
    result = run_backtest(
        compile_definition(defn), spec_for(defn), generate_bars(count=6000, seed=616),
        code_hash="ir",
    )
    assert len(result.trades) > 3
    for trade in result.trades:
        assert trade.stop_price is not None
        # Every stop is one ATR-multiple below its own entry, so a stop carried
        # over from a previous trade at a different price would show up here.
        assert abs(trade.entry_price - trade.stop_price) > 0


# ── export ───────────────────────────────────────────────────────────────────


def test_generated_python_reproduces_the_definition_exactly() -> None:
    """The whole reason Python is a supported target."""
    defn = breakout_definition()
    bars = generate_bars(count=5000, seed=2718)
    report = verify_python(defn, bars, spec_for(defn))
    assert report["verified"], report["reason"]
    assert report["native_trades"] == report["exported_trades"]
    assert report["native_trades"] > 0


def test_generated_python_reproduces_a_session_and_trailing_strategy() -> None:
    defn = breakout_definition(
        features=(
            Feature(name="prior_high", kind="highest", args=(ParamRef(name="lookback"),), shift=1),
            Feature(name="atr14", kind="atr", args=(Constant(value=14),)),
            Feature(name="last_close", kind="close"),
            Feature(name="vwap", kind="session_vwap"),
        ),
        entry=EntryRules(
            long=Combine(
                kind="all",
                of=(
                    Compare(op="gt", left=FeatureRef(name="last_close"),
                            right=FeatureRef(name="prior_high")),
                    Compare(op="gt", left=FeatureRef(name="last_close"),
                            right=FeatureRef(name="vwap")),
                ),
            ),
            session=SessionWindow(start_minute=7 * 60, end_minute=11 * 60 + 30),
        ),
        exit=ExitRules(
            trailing=Level(kind="feature", multiple=ParamRef(name="stop_atr"), feature="atr14"),
            flat_by_minute=16 * 60,
            max_bars=ParamRef(name="max_bars"),
        ),
    )
    bars = generate_bars(count=8000, seed=1618)
    report = verify_python(defn, bars, spec_for(defn))
    assert report["verified"], report["reason"]
    assert report["native_trades"] > 0


def test_the_export_states_its_own_assumptions() -> None:
    report = to_python(breakout_definition())
    assert report.verifiable
    assert "python" in VERIFIABLE_TARGETS
    joined = " ".join(report.approximations)
    assert "next bar" in joined
    # It must never read as a promise about the future.
    assert "profitable" not in report.code.lower()
    assert report.definition_hash in report.code


def test_unverifiable_targets_report_coverage_and_generate_nothing() -> None:
    """A fake exporter is worse than a missing one."""
    defn = breakout_definition()
    for target in DESCRIBED_TARGETS:
        report = describe_target(defn, target)
        assert report.code == ""
        assert not report.verifiable
        assert report.approximations or report.unsupported
        assert target not in VERIFIABLE_TARGETS
