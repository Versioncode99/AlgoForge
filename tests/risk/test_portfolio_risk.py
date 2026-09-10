"""The portfolio risk layer: what it measures, what it refuses to guess, and the switch.

The rule under test throughout is that a limit is enforceable. A risk screen
that reports a number is commentary; a risk layer that returns `within_limits`
False on the same inputs every time is a control, and the pre-trade gate reads
it as one.
"""

from __future__ import annotations

import numpy as np
import pytest
from forge.portfolio.covariance import estimate
from forge.risk import (
    MINIMUM_TAIL_OBSERVATIONS,
    BookPosition,
    PortfolioLimits,
    evaluate_portfolio,
    kill_switch_reason,
    severity,
)
from pydantic import ValidationError

SYMBOLS = ("NQ", "ES")


def covariance(rows: int = 400, seed: int = 5):
    rng = np.random.default_rng(seed)
    common = rng.normal(0, 0.01, size=(rows, 1))
    data = np.hstack([common + rng.normal(0, 0.004, size=(rows, 1)) for _ in SYMBOLS])
    return estimate(data, SYMBOLS)


def book(*weights: float) -> list[BookPosition]:
    return [
        BookPosition(symbol=symbol, weight=weight, strategy_ids=(f"s_{symbol}",))
        for symbol, weight in zip(SYMBOLS, weights, strict=True)
    ]


# ── the measures ─────────────────────────────────────────────────────────────


def test_exposure_and_leverage_are_computed_from_the_book() -> None:
    assessment = evaluate_portfolio(book(0.6, -0.2), PortfolioLimits())
    assert assessment.exposures == {"gross": 0.8, "net": 0.4, "long": 0.6, "short": 0.2}
    assert assessment.measure("leverage").value == pytest.approx(0.8)  # type: ignore[union-attr]


def test_concentration_is_one_for_a_single_position_and_falls_as_it_spreads() -> None:
    alone = evaluate_portfolio([BookPosition(symbol="NQ", weight=0.5)], PortfolioLimits())
    spread = evaluate_portfolio(book(0.25, 0.25), PortfolioLimits())
    assert alone.measure("concentration").value == pytest.approx(1.0)  # type: ignore[union-attr]
    assert spread.measure("concentration").value == pytest.approx(0.5)  # type: ignore[union-attr]


def test_a_breach_names_the_limit_and_turns_the_assessment_red() -> None:
    # Only the gross ceiling is tight; everything else is set wide, so the one
    # breach reported is the one this test is about.
    limits = PortfolioLimits(
        max_gross=0.5, max_concentration=1.0, max_position_weight=1.0,
        max_strategy_weight=1.0, max_net=1.0,
    )
    assessment = evaluate_portfolio(book(0.6, 0.3), limits)
    assert assessment.within_limits is False
    assert [breach.limit for breach in assessment.breaches] == ["gross"]
    assert assessment.breaches[0].detail
    assert severity(assessment) == "breach"


def test_every_breach_is_reported_rather_than_the_first() -> None:
    """A caller fixing one breach should not discover the next by trying again."""
    limits = PortfolioLimits(max_gross=0.5, max_net=0.1, max_position_weight=0.2)
    assessment = evaluate_portfolio(book(0.6, 0.3), limits)
    assert {breach.limit for breach in assessment.breaches} >= {"gross", "net", "position_weight"}


def test_a_strategy_holding_a_position_is_charged_its_full_exposure() -> None:
    """Turning one of two strategies off does not halve a position they both want."""
    shared = [
        BookPosition(symbol="NQ", weight=0.4, strategy_ids=("a", "b")),
    ]
    assessment = evaluate_portfolio(shared, PortfolioLimits())
    assert assessment.by_strategy == {"a": 0.4, "b": 0.4}


# ── absent is not zero ───────────────────────────────────────────────────────


def test_volatility_is_absent_without_a_covariance_estimate() -> None:
    assessment = evaluate_portfolio(book(0.4, 0.2), PortfolioLimits())
    volatility = assessment.measure("volatility")
    assert volatility is not None and volatility.value is None
    assert volatility.note
    assert any("volatility" in note for note in assessment.limitations)


def test_volatility_is_measured_when_an_estimate_is_supplied_and_names_it() -> None:
    assessment = evaluate_portfolio(book(0.4, 0.2), PortfolioLimits(), covariance=covariance())
    volatility = assessment.measure("volatility")
    assert volatility is not None and volatility.value is not None and volatility.value > 0
    assert "observations" in volatility.method


def test_a_misaligned_covariance_estimate_is_refused_rather_than_used() -> None:
    assessment = evaluate_portfolio(
        [BookPosition(symbol="NQ", weight=0.4)], PortfolioLimits(), covariance=covariance()
    )
    volatility = assessment.measure("volatility")
    assert volatility is not None and volatility.value is None
    assert "not aligned" in volatility.note or "aligned" in volatility.note


def test_turnover_with_no_rebalance_is_not_measured_rather_than_zero() -> None:
    assessment = evaluate_portfolio(book(0.4, 0.2), PortfolioLimits())
    turnover = assessment.measure("turnover")
    assert turnover is not None and turnover.value is None


def test_drawdown_without_an_equity_curve_is_not_measured() -> None:
    assessment = evaluate_portfolio(book(0.4, 0.2), PortfolioLimits())
    assert assessment.measure("drawdown").value is None  # type: ignore[union-attr]


def test_drawdown_is_measured_from_a_supplied_curve() -> None:
    curve = np.array([100.0, 120.0, 90.0, 110.0])
    assessment = evaluate_portfolio(book(0.4, 0.2), PortfolioLimits(), equity_curve=curve)
    assert assessment.measure("drawdown").value == pytest.approx(0.25)  # type: ignore[union-attr]


def test_a_curve_starting_at_zero_does_not_produce_an_infinite_drawdown() -> None:
    curve = np.array([0.0, 10.0, 5.0])
    assessment = evaluate_portfolio(book(0.4, 0.2), PortfolioLimits(), equity_curve=curve)
    value = assessment.measure("drawdown").value  # type: ignore[union-attr]
    assert value is not None and np.isfinite(value)


# ── the tail ─────────────────────────────────────────────────────────────────


def test_a_long_return_history_produces_a_historical_var() -> None:
    rng = np.random.default_rng(3)
    series = rng.normal(0.0005, 0.01, size=400)
    assessment = evaluate_portfolio(book(0.4, 0.2), PortfolioLimits(), portfolio_returns=series)
    var = assessment.measure("var_95")
    assert var is not None and var.value is not None
    assert "historical" in var.method


def test_a_short_history_falls_back_to_the_parametric_estimate_and_says_so() -> None:
    rng = np.random.default_rng(3)
    series = rng.normal(0.0005, 0.01, size=MINIMUM_TAIL_OBSERVATIONS - 5)
    assessment = evaluate_portfolio(
        book(0.4, 0.2), PortfolioLimits(), portfolio_returns=series, covariance=covariance()
    )
    var = assessment.measure("var_95")
    assert var is not None and "parametric" in var.method
    assert "fat tails" in var.note


def test_with_neither_a_history_nor_an_estimate_the_tail_is_absent() -> None:
    assessment = evaluate_portfolio(book(0.4, 0.2), PortfolioLimits())
    assert assessment.measure("var_95").value is None  # type: ignore[union-attr]
    assert assessment.measure("expected_shortfall").value is None  # type: ignore[union-attr]


def test_expected_shortfall_is_at_least_as_large_as_var() -> None:
    rng = np.random.default_rng(9)
    series = rng.normal(0.0, 0.012, size=600)
    assessment = evaluate_portfolio(book(0.4, 0.2), PortfolioLimits(), portfolio_returns=series)
    var = assessment.measure("var_95").value  # type: ignore[union-attr]
    shortfall = assessment.measure("expected_shortfall").value  # type: ignore[union-attr]
    assert var is not None and shortfall is not None and shortfall >= var


# ── correlation ──────────────────────────────────────────────────────────────


def test_pair_correlation_needs_two_held_positions() -> None:
    single = evaluate_portfolio(
        [BookPosition(symbol="NQ", weight=0.4), BookPosition(symbol="ES", weight=0.0)],
        PortfolioLimits(), covariance=covariance(),
    )
    measure = single.measure("pair_correlation")
    assert measure is not None and measure.value is None
    assert "fewer than two" in measure.note


def test_pair_correlation_names_the_pair_it_measured() -> None:
    assessment = evaluate_portfolio(book(0.4, 0.2), PortfolioLimits(), covariance=covariance())
    measure = assessment.measure("pair_correlation")
    assert measure is not None and measure.value is not None
    assert "NQ" in measure.note and "ES" in measure.note


# ── the kill switch ──────────────────────────────────────────────────────────


def test_a_disabled_limit_set_is_out_of_limits_whatever_the_numbers_say() -> None:
    """A switch that turns nothing off is not a switch."""
    # A book so small that nothing else could possibly be out of limits, so the
    # only thing making this assessment red is the switch.
    quiet = PortfolioLimits(enabled=False, max_concentration=1.0)
    assessment = evaluate_portfolio(book(0.01, 0.0), quiet)
    assert assessment.within_limits is False
    assert assessment.breaches == ()
    assert severity(assessment) == "halted"


def test_the_kill_switch_explains_why_nothing_is_running() -> None:
    assert kill_switch_reason(PortfolioLimits()) is None
    reason = kill_switch_reason(PortfolioLimits(name="fund", enabled=False))
    assert reason is not None and "kill switch" in reason and "fund" in reason


# ── the limits themselves ────────────────────────────────────────────────────


def test_a_limit_set_with_no_ceiling_is_not_a_limit_set() -> None:
    with pytest.raises(ValidationError):
        PortfolioLimits(max_gross=0)
    with pytest.raises(ValidationError):
        PortfolioLimits(max_leverage=-1)


def test_limits_are_frozen_so_nothing_can_edit_them_in_place() -> None:
    limits = PortfolioLimits()
    with pytest.raises(ValidationError):
        limits.max_gross = 99.0  # type: ignore[misc]
