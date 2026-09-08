"""A parameter landscape, and the warning that has to travel with it.

A surface over two of a strategy's own parameters is the most attractive
picture this application can draw and the most dangerous one. A single tall
peak surrounded by loss is what overfitting looks like from above, and it is
also what a good result looks like to somebody who has not been told the
difference.

So the tests here are about the caveats as much as the arithmetic: that a
configuration which never traded is a gap rather than a zero, that the trial
count is stated because the deflated-Sharpe gate needs it, and that a peak
standing clear of its own neighbours is called out before the number is read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from forge.research.analyses import AnalysisError, AnalysisProvenance
from forge.research.parameter_surface import (
    METRICS,
    MIN_TRADES_PER_CELL,
    build_surface,
)


def provenance() -> AnalysisProvenance:
    return AnalysisProvenance(
        analysis="parameter_surface",
        strategy_id="strat_1",
        backtest_id="bt_1",
        dataset_key="nq_1m",
        evidence_tier="DEVELOPMENT_IN_SAMPLE",
        partition_name="DEVELOPMENT",
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


def point(x: float, y: float, net: float, trades: int = 60, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "x": x,
        "y": y,
        "net_pnl": net,
        "trade_count": trades,
        "win_rate": 0.5,
        "max_drawdown": 500.0,
    }
    body.update(extra)
    return body


def grid(values: dict[tuple[float, float], float], trades: int = 60) -> list[dict[str, Any]]:
    return [point(x, y, net, trades) for (x, y), net in values.items()]


def flat_grid(net: float = 100.0) -> list[dict[str, Any]]:
    return grid({(x, y): net for x in (10.0, 20.0, 30.0) for y in (1.0, 2.0, 3.0)})


# ── shape ────────────────────────────────────────────────────────────────────


def test_the_grid_becomes_a_surface_the_existing_renderer_can_draw() -> None:
    """Reuse is the point: no second surface format to keep in step."""
    result = build_surface(flat_grid(), provenance(), x_name="lookback", y_name="atr_mult")
    assert result.shape == "surface"
    assert len(result.axes) == 2
    assert result.axes[0].name == "lookback"
    assert result.axes[1].name == "atr_mult"
    assert result.axes[0].categories == ("10", "20", "30")
    assert result.axes[1].categories == ("1", "2", "3")
    assert len(result.cells) == 9
    assert {cell.coords for cell in result.cells} == {
        (x, y) for x in range(3) for y in range(3)
    }


def test_axis_values_are_sorted_however_the_sweep_produced_them() -> None:
    scrambled = grid({(30.0, 3.0): 1.0, (10.0, 1.0): 2.0, (20.0, 2.0): 3.0})
    scrambled += grid({(10.0, 3.0): 4.0, (30.0, 1.0): 5.0})
    result = build_surface(scrambled, provenance(), x_name="a", y_name="b")
    assert result.axes[0].categories == ("10", "20", "30")
    assert result.axes[1].categories == ("1", "2", "3")


def test_a_single_valued_axis_is_refused_as_a_line() -> None:
    one_row = grid({(10.0, 1.0): 5.0, (20.0, 1.0): 7.0})
    with pytest.raises(AnalysisError, match="at least two values on each axis"):
        build_surface(one_row, provenance(), x_name="a", y_name="b")


def test_an_empty_sweep_is_refused() -> None:
    with pytest.raises(AnalysisError, match="no configurations"):
        build_surface([], provenance(), x_name="a", y_name="b")


def test_an_unknown_metric_names_the_ones_that_exist() -> None:
    with pytest.raises(AnalysisError, match="unknown metric"):
        build_surface(flat_grid(), provenance(), x_name="a", y_name="b", metric="sharpe_ish")


# ── absence is never zero ────────────────────────────────────────────────────


def test_a_configuration_that_never_traded_is_a_gap_not_a_zero() -> None:
    """A flat plateau at the origin, where there is actually no data at all."""
    points = flat_grid()
    points[4] = point(20.0, 2.0, 0.0, trades=0)
    result = build_surface(points, provenance(), x_name="a", y_name="b")
    silent = next(cell for cell in result.cells if cell.coords == (1, 1))
    assert silent.trade_count == 0
    assert silent.value is None
    assert silent.average_trade is None
    assert silent.win_rate is None
    assert any("gaps, not as zeroes" in warning for warning in result.warnings)


def test_a_combination_the_sweep_never_reached_is_absent() -> None:
    points = [p for p in flat_grid() if not (p["x"] == 20.0 and p["y"] == 2.0)]
    result = build_surface(points, provenance(), x_name="a", y_name="b")
    assert len(result.cells) == 9, "the grid is still complete"
    missing = next(cell for cell in result.cells if cell.coords == (1, 1))
    assert missing.value is None
    assert missing.trade_count == 0


def test_return_over_drawdown_is_absent_rather_than_enormous_without_a_drawdown() -> None:
    points = flat_grid()
    points[0] = point(10.0, 1.0, 900.0, max_drawdown=0.0)
    result = build_surface(
        points, provenance(), x_name="a", y_name="b", metric="return_over_drawdown"
    )
    assert next(cell for cell in result.cells if cell.coords == (0, 0)).value is None


@pytest.mark.parametrize("metric", sorted(METRICS))
def test_every_metric_is_undefined_where_nothing_traded(metric: str) -> None:
    points = flat_grid()
    points[4] = point(20.0, 2.0, 0.0, trades=0)
    result = build_surface(points, provenance(), x_name="a", y_name="b", metric=metric)
    silent = next(cell for cell in result.cells if cell.coords == (1, 1))
    if metric == "trade_count":
        # The one metric for which zero is a measurement rather than an absence.
        assert silent.value == 0.0
    else:
        assert silent.value is None


# ── the caveats that have to travel with it ──────────────────────────────────


def test_the_result_says_every_cell_is_in_sample() -> None:
    result = build_surface(flat_grid(), provenance(), x_name="a", y_name="b")
    text = " ".join(result.warnings)
    assert "in-sample" in text
    assert "can never promote" in text


def test_the_trial_count_is_stated_because_the_gate_needs_it() -> None:
    """Nine configurations tried is nine trials, and G5 has to be told."""
    result = build_surface(flat_grid(), provenance(), x_name="a", y_name="b")
    assert any("9 configurations were tried" in warning for warning in result.warnings)
    assert any("deflated-Sharpe" in warning for warning in result.warnings)


def test_a_lone_peak_is_called_out_as_the_shape_of_a_fit() -> None:
    values = {(x, y): 10.0 for x in (10.0, 20.0, 30.0) for y in (1.0, 2.0, 3.0)}
    values[(20.0, 2.0)] = 5_000.0
    result = build_surface(grid(values), provenance(), x_name="a", y_name="b")
    text = " ".join(result.findings)
    assert "stands well clear of its own neighbours" in text
    assert "the shape of a fit" in text


def test_a_broad_plateau_is_described_as_one() -> None:
    values = {(x, y): 100.0 for x in (10.0, 20.0, 30.0) for y in (1.0, 2.0, 3.0)}
    values[(20.0, 2.0)] = 120.0
    result = build_surface(grid(values), provenance(), x_name="a", y_name="b")
    text = " ".join(result.findings)
    assert "sits on a plateau" in text
    assert "shape of a fit" not in text


def test_a_peak_that_is_the_only_profitable_cell_is_called_out() -> None:
    """The most dangerous case: everything loses except one configuration."""
    values = {(x, y): -200.0 for x in (10.0, 20.0, 30.0) for y in (1.0, 2.0, 3.0)}
    values[(20.0, 2.0)] = 900.0
    result = build_surface(grid(values), provenance(), x_name="a", y_name="b")
    assert "the shape of a fit" in " ".join(result.findings)


def test_the_best_and_worst_are_named_with_their_coordinates() -> None:
    values = {(x, y): float(x + y) for x in (10.0, 20.0, 30.0) for y in (1.0, 2.0, 3.0)}
    result = build_surface(grid(values), provenance(), x_name="lookback", y_name="atr")
    finding = result.findings[0]
    assert "lookback=30" in finding and "atr=3" in finding
    assert "lookback=10" in finding and "atr=1" in finding


def test_a_thin_cell_is_marked_rather_than_hidden() -> None:
    points = flat_grid()
    points[0] = point(10.0, 1.0, 50.0, trades=MIN_TRADES_PER_CELL - 1)
    result = build_surface(points, provenance(), x_name="a", y_name="b")
    thin = next(cell for cell in result.cells if cell.coords == (0, 0))
    assert thin.insufficient is True
    assert thin.value is not None, "its P&L is what happened; only its rates estimate nothing"


# ── provenance ───────────────────────────────────────────────────────────────


def test_the_provenance_travels_and_the_artifact_has_an_id() -> None:
    result = build_surface(flat_grid(), provenance(), x_name="a", y_name="b")
    assert result.provenance.strategy_id == "strat_1"
    assert result.provenance.evidence_tier == "DEVELOPMENT_IN_SAMPLE"
    assert result.provenance.artifact_id
    assert result.content_hash


def test_the_same_grid_produces_the_same_surface() -> None:
    first = build_surface(flat_grid(), provenance(), x_name="a", y_name="b")
    second = build_surface(flat_grid(), provenance(), x_name="a", y_name="b")
    assert first.content_hash == second.content_hash


def test_a_different_metric_is_a_different_artifact() -> None:
    net = build_surface(flat_grid(), provenance(), x_name="a", y_name="b", metric="net_pnl")
    wins = build_surface(flat_grid(), provenance(), x_name="a", y_name="b", metric="win_rate")
    assert net.content_hash != wins.content_hash
    assert net.measure != wins.measure
