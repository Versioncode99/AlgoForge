"""Regime classification, and the lookahead it must not commit.

The test that matters most here is
`test_a_trailing_label_never_changes_when_later_bars_change`. Classifying a 2019
bar with 2024's volatility distribution is a use of the future that is one line
of numpy away at all times, and a strategy tuned on labels built that way has
been tuned on information it could not have had. The property that catches it is
simple: append more history, and no earlier label may move.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from forge.analytics.regime import (
    MEASURED,
    MIN_TRADES_FOR_ESTIMATE,
    Basis,
    Regime,
    RegimeSettings,
    attribute,
    classify,
    summarise,
    transition_matrix,
)
from forge.analytics.resample import compare
from forge.strategy import generate_bars, run_backtest
from forge.strategy.ir import (
    Compare,
    Constant,
    DefinitionProvenance,
    EntryRules,
    ExitRules,
    Feature,
    FeatureRef,
    Level,
    ParamRef,
    StrategyDefinition,
    compile_definition,
)
from forge.strategy.models import ParameterSpec, StrategySpec

NOW = datetime(2026, 9, 8, tzinfo=UTC)
SETTINGS = RegimeSettings(trend_length=60, vol_length=14, min_history=120)


def arrays(count: int = 4000, seed: int = 20260908) -> tuple[np.ndarray, ...]:
    bars = generate_bars(count=count, seed=seed)
    return (
        np.array([b.high for b in bars], dtype=np.float64),
        np.array([b.low for b in bars], dtype=np.float64),
        np.array([b.close for b in bars], dtype=np.float64),
    )


# ── lookahead ────────────────────────────────────────────────────────────────


def test_a_trailing_label_never_changes_when_later_bars_change() -> None:
    """The property that distinguishes a trailing classifier from a leaking one.

    A classifier that took its threshold from the whole series would relabel
    2019 the moment 2024 arrived. Nothing that happens after bar i may move bar
    i's label.
    """
    high, low, close = arrays(count=4000)
    # A fixed refresh interval, so the two runs recompute at the same bars and
    # the comparison is of the threshold's *inputs*, not of its schedule.
    settings = SETTINGS.model_copy(update={"refresh_bars": 100})
    prefix = classify(high[:2000], low[:2000], close[:2000], settings)
    whole = classify(high, low, close, settings)
    assert prefix.labels == whole.labels[:2000]
    # NaN before warmup, so compared with equal_nan rather than by identity.
    assert np.array_equal(
        np.array(prefix.vol_threshold),
        np.array(whole.vol_threshold[:2000]),
        equal_nan=True,
    )


def test_the_full_sample_basis_does_leak_and_says_so() -> None:
    """Available, named for what it is, and never the default."""
    high, low, close = arrays(count=4000)
    descriptive = SETTINGS.model_copy(update={"basis": Basis.FULL_SAMPLE})
    prefix = classify(high[:2000], low[:2000], close[:2000], descriptive)
    whole = classify(high, low, close, descriptive)
    # The later bars moved the threshold, so earlier labels moved with it. That
    # is the leak, demonstrated rather than asserted away.
    assert prefix.labels != whole.labels[:2000]
    assert RegimeSettings().basis is Basis.TRAILING


def test_bars_without_enough_history_are_unclassified_not_calm() -> None:
    high, low, close = arrays(count=2000)
    series = classify(high, low, close, SETTINGS)
    assert all(label is Regime.UNCLASSIFIED for label in series.labels[: SETTINGS.min_history])
    assert series.classified_bars < series.bar_count
    assert 0.0 < series.coverage < 1.0


# ── the classification says something about the market ───────────────────────


def test_high_volatility_bars_really_are_more_volatile() -> None:
    high, low, close = arrays(count=6000)
    series = classify(high, low, close, SETTINGS)
    vol = np.array(series.volatility)
    hot = np.array([label in (Regime.BULL_HIGH, Regime.BEAR_HIGH) for label in series.labels])
    cool = np.array([label in (Regime.BULL_LOW, Regime.BEAR_LOW) for label in series.labels])
    assert hot.any() and cool.any()
    assert np.nanmean(vol[hot]) > np.nanmean(vol[cool])


def test_bull_bars_really_are_above_their_trailing_average() -> None:
    high, low, close = arrays(count=6000)
    series = classify(high, low, close, SETTINGS)
    strength = np.array(series.trend_strength)
    bull = np.array([label in (Regime.BULL_LOW, Regime.BULL_HIGH) for label in series.labels])
    bear = np.array([label in (Regime.BEAR_LOW, Regime.BEAR_HIGH) for label in series.labels])
    assert bull.any() and bear.any()
    # Trend strength is (close - trailing mean) / volatility, so the sign is the
    # classification itself. Every classified bar must agree with its label.
    assert (strength[bull] > 0).all()
    assert (strength[bear] <= 0).all()


def test_settings_are_recorded_so_a_label_can_be_reproduced() -> None:
    high, low, close = arrays(count=3000)
    series = classify(high, low, close, SETTINGS)
    again = classify(high, low, close, series.settings)
    assert series.fingerprint == again.fingerprint
    different = classify(high, low, close, SETTINGS.model_copy(update={"vol_percentile": 80.0}))
    assert different.fingerprint != series.fingerprint


# ── transitions ──────────────────────────────────────────────────────────────


def test_transitions_are_counts_and_skip_unclassified_gaps() -> None:
    """A pair either side of a gap did not follow each other."""
    high, low, close = arrays(count=4000)
    series = classify(high, low, close, SETTINGS)
    matrix = transition_matrix(series)
    assert len(matrix) == len(MEASURED)
    total = sum(sum(row) for row in matrix)
    classified = series.classified_bars
    # Every counted transition joins two classified bars, so there can be at
    # most one per classified bar, and strictly fewer once a gap exists.
    assert 0 < total < classified
    # Regimes are sticky: the diagonal must dominate on real price data.
    for index in range(len(MEASURED)):
        row = matrix[index]
        if sum(row) > 50:
            assert row[index] == max(row)


# ── attribution over a real backtest ─────────────────────────────────────────


def strategy() -> tuple[StrategyDefinition, StrategySpec]:
    defn = StrategyDefinition(
        name="Regime Test Breakout",
        family="breakout",
        symbol="MNQ",
        hypothesis=(
            "A close above the prior N-bar high reflects liquidity-taking flow that "
            "must be absorbed, so short-horizon continuation should follow the break."
        ),
        falsifiable_prediction=(
            "Continuation must be stronger on above-median volume; equal behaviour "
            "disproves the mechanism."
        ),
        features=(
            Feature(name="prior_high", kind="highest", args=(ParamRef(name="lookback"),), shift=1),
            Feature(name="atr14", kind="atr", args=(Constant(value=14),)),
            Feature(name="last_close", kind="close"),
        ),
        entry=EntryRules(
            long=Compare(
                op="gt", left=FeatureRef(name="last_close"), right=FeatureRef(name="prior_high")
            )
        ),
        exit=ExitRules(
            stop=Level(kind="feature", multiple=Constant(value=2.0), feature="atr14"),
            max_bars=Constant(value=15),
        ),
        parameters=(ParameterSpec(name="lookback", default=20, low=5, high=40, step=5),),
        provenance=DefinitionProvenance(created_at=NOW),
    )
    spec = StrategySpec(
        strategy_id="regime_test",
        name=defn.name,
        lineage="test",
        family=defn.family,
        market="futures",
        symbol="MNQ.SYNTH",
        template="ir",
        hypothesis=defn.hypothesis,
        falsifiable_prediction=defn.falsifiable_prediction,
        parameters=defn.parameters,
        warmup_bars=defn.required_warmup(),
        created_at=NOW,
    )
    return defn, spec


@pytest.fixture(scope="module")
def backtest() -> tuple[object, object]:
    defn, spec = strategy()
    bars = generate_bars(count=12000, seed=515)
    result = run_backtest(compile_definition(defn), spec, bars, code_hash="regime")
    series = classify(
        [b.high for b in bars], [b.low for b in bars], [b.close for b in bars], SETTINGS
    )
    return result, series


def test_every_trade_gets_a_regime_on_three_bases(backtest: tuple) -> None:
    result, series = backtest
    assert result.trades
    marks = attribute(list(result.trades), series)
    assert len(marks) == len(result.trades)
    for mark in marks:
        assert mark.entry in set(Regime)
        assert mark.exit in set(Regime)
        assert 0.0 <= mark.dominant_share <= 1.0


def test_the_entry_regime_is_read_at_the_decision_bar_not_the_fill(backtest: tuple) -> None:
    """The decision is what the regime is supposed to explain."""
    result, series = backtest
    marks = {m.trade_id: m for m in attribute(list(result.trades), series)}
    for trade in result.trades:
        assert marks[trade.trade_id].entry == series.at(trade.entry_decision_index)
        # The fill bar is one later, and its label may differ; that difference is
        # exactly what would be wrong to report as the entry regime.
        assert trade.entry_decision_index == trade.entry_index - 1


def test_the_summary_adds_up_and_names_what_it_could_not_place(backtest: tuple) -> None:
    result, series = backtest
    trades = list(result.trades)
    report = summarise(trades, attribute(trades, series), series)
    counted = sum(cell.trade_count for cell in report.cells)
    assert counted + report.unclassified_trades == len(trades)
    assert report.total_trades == len(trades)
    assert round(sum(cell.net_pnl for cell in report.cells), 2) == pytest.approx(
        round(sum(t.net_pnl for t in trades if _placed(t, report, series)), 2), abs=0.5
    )


def _placed(trade: object, report: object, series: object) -> bool:
    return series.at(trade.entry_decision_index) is not Regime.UNCLASSIFIED  # type: ignore[attr-defined]


def test_a_thin_cell_is_marked_rather_than_estimated(backtest: tuple) -> None:
    """Its P&L is what happened; its rates are not an estimate of anything."""
    result, series = backtest
    trades = list(result.trades)
    report = summarise(trades, attribute(trades, series), series)
    for cell in report.cells:
        assert cell.insufficient == (cell.trade_count < MIN_TRADES_FOR_ESTIMATE)
        if cell.insufficient:
            assert cell.note
        if cell.trade_count == 0:
            assert cell.win_rate is None
            assert cell.average_trade is None


def test_exposure_is_measured_in_bars_not_trades(backtest: tuple) -> None:
    """A regime holding 5% of the trades but 40% of the time is a different fact."""
    result, series = backtest
    trades = list(result.trades)
    report = summarise(trades, attribute(trades, series), series)
    assert round(sum(cell.bar_exposure for cell in report.cells), 3) == pytest.approx(1.0, abs=0.01)
    assert any(
        abs(cell.bar_exposure - cell.trade_share) > 0.01 for cell in report.cells
    ), "exposure and trade share must be computed differently, not aliased"


def test_concentration_is_reported_when_one_regime_carries_the_edge(backtest: tuple) -> None:
    result, series = backtest
    trades = list(result.trades)
    report = summarise(trades, attribute(trades, series), series)
    if report.concentration is not None:
        assert 0.0 < report.concentration <= 1.0
        assert report.concentration_regime in MEASURED
        if report.concentration > 0.6:
            assert any("bet that the regime persists" in w for w in report.warnings)


def test_a_descriptive_percentile_is_off_by_default(backtest: tuple) -> None:
    result, series = backtest
    trades = list(result.trades)
    assert all(m.entry_vol_percentile is None for m in attribute(trades, series))
    asked = attribute(trades, series, with_percentile=True)
    placed = [m for m in asked if m.entry is not Regime.UNCLASSIFIED]
    assert placed
    assert all(0.0 <= m.entry_vol_percentile <= 100.0 for m in placed)  # type: ignore[operator]


# ── resampling ───────────────────────────────────────────────────────────────


def test_resampling_never_invents_a_trade(backtest: tuple) -> None:
    result, series = backtest
    trades = list(result.trades)
    comparison = compare(trades, attribute(trades, series), series, paths=200, seed=7)
    observed = {round(float(t.net_pnl), 6) for t in trades}
    for path in comparison.iid.sample_paths:
        steps = np.diff(np.concatenate([[0.0], np.array(path)]))
        for value in steps:
            assert round(float(value), 6) in observed


def test_both_resamplers_run_and_the_gap_is_reported(backtest: tuple) -> None:
    result, series = backtest
    trades = list(result.trades)
    comparison = compare(trades, attribute(trades, series), series, paths=400, seed=11)
    assert comparison.iid.paths == 400
    if comparison.regime_aware is not None:
        assert comparison.regime_aware.trades_per_path == len(trades)
        assert comparison.drawdown_gap is not None
        assert comparison.provenance["series_fingerprint"] == series.fingerprint


def test_resampling_is_reproducible_from_its_seed(backtest: tuple) -> None:
    result, series = backtest
    trades = list(result.trades)
    marks = attribute(trades, series)
    first = compare(trades, marks, series, paths=200, seed=99)
    second = compare(trades, marks, series, paths=200, seed=99)
    assert first.iid.median_final == second.iid.median_final
    assert first.iid.p95_max_drawdown == second.iid.p95_max_drawdown
    if first.regime_aware and second.regime_aware:
        assert first.regime_aware.p95_max_drawdown == second.regime_aware.p95_max_drawdown


def test_a_thin_regime_is_pooled_and_the_substitution_is_named(backtest: tuple) -> None:
    """Rather than inventing a draw for a regime that has almost no trades."""
    result, series = backtest
    trades = list(result.trades)
    comparison = compare(trades, attribute(trades, series), series, paths=200, seed=3)
    for note in comparison.pooled:
        assert "trade(s) of its own" in note


def test_resampling_refuses_a_sample_it_cannot_resample() -> None:
    high, low, close = arrays(count=1000)
    series = classify(high, low, close, SETTINGS)
    with pytest.raises(ValueError, match="at least two trades"):
        compare([], [], series, paths=10)


# ── the fabricated labels are gone ───────────────────────────────────────────


def test_the_pnl_only_summary_no_longer_claims_to_know_the_market() -> None:
    """It split the trades into four chronological chunks and called the first
    one TREND. Regression guard for that whole class of defect."""
    from forge.analytics import build_normal_analysis
    from forge.judge import Judge, JudgeInput

    verdict = Judge().evaluate(
        JudgeInput(
            run_id="r",
            tier="SWEEP",
            pnl=(80, -25, 95, -30) * 8,
            trial_count=4,
            data_gate_passed=True,
            preregistered=True,
        )
    )
    analysis = build_normal_analysis("r", verdict, (80.0, -25.0, 95.0, -30.0) * 8)
    names = {regime.name for regime in analysis.regimes}
    assert not names & {"TREND", "HIGH_VOL", "RANGE", "LOW_VOL"}
    assert all(regime.basis == "chronological_quarter" for regime in analysis.regimes)
