"""Performance attribution: where the return came from, and what was not measured.

The absences are the point. No commercial factor model is installed, so factor
attribution appears only against loadings a caller supplies and otherwise says
so. A panel of factor zeros would read as "no factor exposure", which is a
finding — and one nobody made.
"""

from __future__ import annotations

import numpy as np
import pytest
from forge.analytics.attribution import (
    MINIMUM_REGRESSION_OBSERVATIONS,
    PnlRecord,
    attribute,
    attribute_execution,
    attribute_factors,
    compare_benchmark,
    report,
    statistics,
)


def records() -> list[PnlRecord]:
    return [
        PnlRecord(pnl=400.0, strategy_id="momentum", symbol="NQ", sector="Equity Index"),
        PnlRecord(pnl=-150.0, strategy_id="momentum", symbol="ES", sector="Equity Index"),
        PnlRecord(pnl=250.0, strategy_id="carry", symbol="GC", sector="Metals"),
    ]


# ── statistics ───────────────────────────────────────────────────────────────


def test_statistics_over_a_real_series_produce_the_usual_ratios() -> None:
    rng = np.random.default_rng(4)
    series = rng.normal(20.0, 100.0, size=250)
    measured = statistics(series)
    assert measured.periods == 250
    assert measured.sharpe is not None and measured.sortino is not None
    assert measured.max_drawdown > 0
    assert 0.0 <= (measured.hit_rate or 0.0) <= 1.0


def test_a_series_too_short_for_dispersion_reports_none_rather_than_zero() -> None:
    measured = statistics([100.0])
    assert measured.sharpe is None
    assert measured.annualised_sharpe is None
    assert "fewer than two" in measured.note


def test_a_sample_with_no_losing_period_has_no_profit_factor_rather_than_infinity() -> None:
    measured = statistics([10.0, 20.0, 30.0])
    assert measured.profit_factor is None
    assert "undefined rather than infinite" in measured.note


def test_the_judges_own_statistics_are_reused_rather_than_reimplemented() -> None:
    """Two implementations eventually disagree, and then the Sharpe depends on
    which screen you are looking at."""
    from forge.judge.statistics import per_period_sharpe

    series = [80.0, -25.0, 95.0, -30.0, 70.0, -20.0]
    assert statistics(series).sharpe == pytest.approx(round(per_period_sharpe(series), 6))


# ── decomposition ────────────────────────────────────────────────────────────


def test_pnl_is_decomposed_by_strategy_instrument_and_sector() -> None:
    by_strategy, by_symbol, by_sector = attribute(records())
    assert {row.key for row in by_strategy} == {"momentum", "carry"}
    assert {row.key for row in by_symbol} == {"NQ", "ES", "GC"}
    assert {row.key for row in by_sector} == {"Equity Index", "Metals"}


def test_shares_are_taken_against_gross_contribution_not_net() -> None:
    """A winner expressed as 1000% of a small net is true and useless."""
    offsetting = [
        PnlRecord(pnl=100.0, strategy_id="up"),
        PnlRecord(pnl=-90.0, strategy_id="down"),
    ]
    by_strategy, _, _ = attribute(offsetting)
    shares = {row.key: row.share for row in by_strategy}
    assert shares["up"] == pytest.approx(100 / 190, abs=1e-4)
    assert sum(shares.values()) == pytest.approx(1.0, abs=1e-6)


def test_rows_are_ordered_by_how_much_they_moved_the_book() -> None:
    by_strategy, _, _ = attribute(records())
    assert [row.key for row in by_strategy] == ["momentum", "carry"]


def test_unattributed_pnl_is_labelled_rather_than_dropped() -> None:
    _, _, by_sector = attribute([PnlRecord(pnl=10.0, symbol="NQ")])
    assert by_sector[0].key == "unclassified"


# ── the benchmark ────────────────────────────────────────────────────────────


def test_a_benchmark_comparison_recovers_a_known_beta() -> None:
    rng = np.random.default_rng(2)
    benchmark = rng.normal(0.0, 0.01, size=400)
    portfolio = 1.5 * benchmark + rng.normal(0.0, 0.001, size=400)
    comparison = compare_benchmark(portfolio, benchmark, name="index")
    assert comparison.beta == pytest.approx(1.5, abs=0.05)
    assert comparison.correlation is not None and comparison.correlation > 0.95


def test_too_few_observations_produce_no_beta_and_say_why() -> None:
    short = list(range(MINIMUM_REGRESSION_OBSERVATIONS - 2))
    comparison = compare_benchmark(short, short)
    assert comparison.beta is None
    assert str(MINIMUM_REGRESSION_OBSERVATIONS) in comparison.note


def test_a_motionless_benchmark_has_no_beta_rather_than_a_division() -> None:
    flat = [0.0] * 100
    rng = np.random.default_rng(1)
    comparison = compare_benchmark(rng.normal(0, 0.01, 100), flat)
    assert comparison.beta is None
    assert "does not move" in comparison.note


def test_mismatched_series_lengths_are_refused_rather_than_truncated() -> None:
    comparison = compare_benchmark([1.0, 2.0, 3.0], [1.0, 2.0])
    assert comparison.beta is None
    assert "different lengths" in comparison.note


def test_alpha_states_that_it_is_gross_of_a_rate_this_build_does_not_hold() -> None:
    rng = np.random.default_rng(6)
    benchmark = rng.normal(0.0, 0.01, size=300)
    comparison = compare_benchmark(benchmark * 1.2, benchmark)
    assert "risk-free rate" in comparison.note


# ── factors ──────────────────────────────────────────────────────────────────


def test_no_loadings_means_no_factor_attribution_and_a_named_integration_point() -> None:
    rows, note = attribute_factors([1.0, 2.0, 3.0], {})
    assert rows == ()
    assert "no factor model is configured" in note
    assert "attribute_factors" in note


def test_supplied_loadings_produce_an_attribution_with_a_specific_residual() -> None:
    rng = np.random.default_rng(8)
    momentum = rng.normal(0, 0.01, size=200)
    value = rng.normal(0, 0.01, size=200)
    portfolio = 0.8 * momentum + 0.2 * value + rng.normal(0, 0.001, size=200)
    rows, note = attribute_factors(portfolio, {"momentum": momentum, "value": value})
    assert note == ""
    assert {row.key for row in rows} == {"momentum", "value", "specific"}


def test_misaligned_loadings_are_refused_rather_than_regressed() -> None:
    rows, note = attribute_factors([1.0] * 50, {"momentum": [1.0] * 30})
    assert rows == ()
    assert "not aligned" in note


def test_too_few_degrees_of_freedom_is_refused() -> None:
    rows, note = attribute_factors([1.0, 2.0], {"a": [1.0, 2.0], "b": [2.0, 1.0]})
    assert rows == ()
    assert "degrees of freedom" in note


# ── execution ────────────────────────────────────────────────────────────────


class Fill:
    def __init__(self, symbol: str, side: str, quantity: int, price: float,
                 multiplier: float = 1.0, commission: float = 0.0,
                 slippage: float = 0.0, simulated: bool = True) -> None:
        self.symbol, self.side, self.quantity, self.price = symbol, side, quantity, price
        self.multiplier, self.commission = multiplier, commission
        self.slippage, self.simulated = slippage, simulated


def test_shortfall_is_measured_against_the_arrival_price() -> None:
    """A buy filled above arrival cost money; the sign has to say so."""
    fills = [Fill("NQ", "buy", 2, 20_010.0, multiplier=20, commission=3.0)]
    measured = attribute_execution(fills, {"NQ": 20_000.0})
    assert measured.implementation_shortfall == pytest.approx(10.0 * 2 * 20)
    assert measured.commission == pytest.approx(3.0)
    assert measured.shortfall_per_contract == pytest.approx(200.0)


def test_a_sell_above_arrival_is_a_gain_rather_than_a_cost() -> None:
    fills = [Fill("NQ", "sell", 1, 20_010.0, multiplier=20)]
    assert attribute_execution(fills, {"NQ": 20_000.0}).implementation_shortfall < 0


def test_a_fill_with_no_arrival_price_is_excluded_and_counted() -> None:
    fills = [Fill("NQ", "buy", 1, 20_010.0), Fill("GC", "buy", 1, 2_400.0)]
    measured = attribute_execution(fills, {"NQ": 20_000.0})
    assert "1 of 2 fills had no arrival price" in measured.note


def test_a_simulated_book_says_the_shortfall_measures_the_model_not_a_venue() -> None:
    measured = attribute_execution([Fill("NQ", "buy", 1, 20_010.0)], {"NQ": 20_000.0})
    assert measured.simulated is True
    assert "slippage model rather than a venue" in measured.note


def test_no_fills_is_reported_rather_than_zeroed_silently() -> None:
    measured = attribute_execution([], {})
    assert measured.fills == 0
    assert measured.shortfall_per_contract is None
    assert measured.note == "no fills to measure"


# ── the assembled report ─────────────────────────────────────────────────────


def test_the_report_carries_its_own_limitations() -> None:
    built = report(records())
    assert built.statistics.periods == 3
    assert built.by_strategy
    assert any("factor model" in note for note in built.limitations)


def test_the_report_includes_a_benchmark_only_when_one_is_supplied() -> None:
    assert report(records()).benchmark is None
    rng = np.random.default_rng(12)
    series = rng.normal(0, 0.01, size=200)
    with_benchmark = report(records(), returns=series, benchmark=series * 0.9)
    assert with_benchmark.benchmark is not None
