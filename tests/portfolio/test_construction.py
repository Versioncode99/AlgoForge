"""Portfolio construction: the box, the costs, and what it refuses to do.

The most important assertions here are the refusals. An optimiser that always
returns a portfolio is an optimiser that will return one built on an estimate
that does not exist, and that failure looks like a very good portfolio rather
than like a failure.
"""

from __future__ import annotations

import numpy as np
import pytest
from forge.portfolio import (
    AlphaSignal,
    Constraints,
    ConstructionError,
    Instrument,
    construct,
    estimate,
)
from forge.portfolio.covariance import MINIMUM_OBSERVATIONS
from pydantic import ValidationError

SYMBOLS = ("NQ", "ES", "GC", "CL")


def returns(rows: int = 300, seed: int = 11) -> np.ndarray:
    """Four correlated series: two index futures that move together, and two that do not."""
    rng = np.random.default_rng(seed)
    common = rng.normal(0, 0.01, size=(rows, 1))
    loadings = (1.0, 0.9, 0.2, -0.1)
    return np.hstack(
        [common * weight + rng.normal(0, 0.008, size=(rows, 1)) for weight in loadings]
    )


def universe() -> dict[str, Instrument]:
    return {
        "NQ": Instrument(symbol="NQ", sector="Equity Index", region="US", multiplier=20,
                         price=20_000, adv_notional=5e10, shortable=True),
        "ES": Instrument(symbol="ES", sector="Equity Index", region="US", multiplier=50,
                         price=5_600, adv_notional=8e10, shortable=True),
        "GC": Instrument(symbol="GC", sector="Metals", region="US", multiplier=100,
                         price=2_400, adv_notional=3e10, shortable=True),
        "CL": Instrument(symbol="CL", sector="Energy", region="US", multiplier=1_000,
                         price=78, adv_notional=2e10, shortable=True),
    }


def signals(**overrides: float) -> list[AlphaSignal]:
    base = {"NQ": 0.03, "ES": 0.01, "GC": 0.02, "CL": -0.015}
    base.update(overrides)
    return [
        AlphaSignal(strategy_id=f"s_{symbol}", symbol=symbol, expected_return=value,
                    verdict="PASS")
        for symbol, value in base.items()
    ]


def build(**kwargs: object):
    options: dict[str, object] = {
        "capital": 1_000_000.0,
        "returns": returns(),
        "return_symbols": SYMBOLS,
    }
    options.update(kwargs)
    return construct(signals(), universe(), **options)  # type: ignore[arg-type]


# ── the box is respected ─────────────────────────────────────────────────────


def test_the_solution_stays_inside_every_configured_bound() -> None:
    limits = Constraints(max_gross=1.5, max_net=0.8, max_weight=0.3,
                         max_sector_weight=0.45, max_turnover=1.2)
    proposal = build(constraints=limits)
    assert proposal.feasible
    weights = {holding.symbol: holding.weight for holding in proposal.holdings}
    assert sum(abs(w) for w in weights.values()) <= limits.max_gross + 1e-6
    assert abs(sum(weights.values())) <= limits.max_net + 1e-6
    assert all(abs(w) <= limits.max_weight + 1e-6 for w in weights.values())
    assert proposal.exposures.by_sector["Equity Index"] <= limits.max_sector_weight + 1e-6


def test_a_long_only_mandate_produces_no_short() -> None:
    proposal = build(constraints=Constraints(long_only=True, max_gross=1.0, max_net=1.0))
    assert all(holding.weight >= 0 for holding in proposal.holdings)


def test_tightening_the_gross_limit_shrinks_the_book() -> None:
    wide = build(constraints=Constraints(max_gross=1.5, max_net=1.0, max_weight=0.5))
    tight = build(constraints=Constraints(max_gross=0.4, max_net=0.4, max_weight=0.5))
    assert tight.exposures.gross < wide.exposures.gross
    assert tight.exposures.gross <= 0.4 + 1e-6


def test_turnover_is_measured_against_the_book_the_caller_already_holds() -> None:
    held = {"NQ": 0.3, "GC": 0.2}
    proposal = build(current_weights=held, constraints=Constraints(max_turnover=0.05,
                                                                  max_gross=1.5, max_net=1.0))
    assert proposal.turnover <= 0.05 + 1e-6
    # A tiny turnover budget means the proposal must look like what is already held.
    weights = {holding.symbol: holding.weight for holding in proposal.holdings}
    assert weights.get("NQ", 0.0) == pytest.approx(0.3, abs=0.05)


def test_the_liquidity_cap_binds_on_a_thin_instrument() -> None:
    thin = universe()
    thin["GC"] = thin["GC"].model_copy(update={"adv_notional": 1e6})
    proposal = construct(
        signals(), thin, capital=1_000_000.0, returns=returns(), return_symbols=SYMBOLS,
        constraints=Constraints(max_gross=2.0, max_net=1.0, max_weight=0.5,
                                max_participation=0.05),
    )
    weights = {holding.symbol: holding.weight for holding in proposal.holdings}
    # 5% of a million in average daily notional is 50,000, which is 5% of capital.
    assert abs(weights.get("GC", 0.0)) <= 0.05 + 1e-6


# ── costs are in the objective ───────────────────────────────────────────────


def test_a_punitive_cost_suppresses_trading_rather_than_being_reported_after_it() -> None:
    """An optimiser that ignores costs proposes a book nobody can afford to hold."""
    cheap = build(constraints=Constraints(cost_bps=1, max_gross=1.5, max_net=1.0))
    dear = build(constraints=Constraints(cost_bps=5_000, max_gross=1.5, max_net=1.0))
    assert dear.turnover < cheap.turnover


def test_the_estimated_cost_follows_the_turnover_it_charged() -> None:
    proposal = build(constraints=Constraints(cost_bps=10, max_gross=1.5, max_net=1.0))
    expected = proposal.turnover * proposal.capital * 10 / 10_000
    assert proposal.estimated_cost == pytest.approx(expected, rel=1e-6)


# ── eligibility, and naming every rejection ──────────────────────────────────


def test_a_failed_strategy_is_not_sized_and_the_rejection_is_named() -> None:
    """The judge is authoritative here too: research decides, construction obeys."""
    rejected = [
        AlphaSignal(strategy_id="loser", symbol="GC", expected_return=5.0, verdict="FAIL"),
        AlphaSignal(strategy_id="unjudged", symbol="ES", expected_return=5.0, verdict=None),
    ]
    proposal = construct(
        [*signals(), *rejected], universe(), capital=1_000_000.0,
        returns=returns(), return_symbols=SYMBOLS,
    )
    reasons = {row["strategy_id"]: row["reason"] for row in proposal.excluded}
    assert "not in the eligible set" in reasons["loser"]
    assert "never been judged" in reasons["unjudged"]


def test_a_signal_with_no_instrument_definition_is_named_rather_than_dropped() -> None:
    proposal = construct(
        [*signals(), AlphaSignal(strategy_id="s_zn", symbol="ZN", expected_return=0.05,
                                 verdict="PASS")],
        universe(), capital=1_000_000.0, returns=returns(), return_symbols=SYMBOLS,
    )
    assert any(row["symbol"] == "ZN" for row in proposal.excluded)


def test_a_short_forecast_on_an_instrument_of_unknown_shortability_is_excluded() -> None:
    unknown = universe()
    unknown["CL"] = unknown["CL"].model_copy(update={"shortable": None})
    proposal = construct(
        signals(), unknown, capital=1_000_000.0, returns=returns(), return_symbols=SYMBOLS,
    )
    assert any(row["symbol"] == "CL" and "shortable" in row["reason"]
               for row in proposal.excluded)


def test_two_strategies_agreeing_on_one_instrument_are_summed() -> None:
    """Averaging would understate agreement; the maximum would ignore the second."""
    doubled = [
        AlphaSignal(strategy_id="a", symbol="NQ", expected_return=0.02, verdict="PASS"),
        AlphaSignal(strategy_id="b", symbol="NQ", expected_return=0.02, verdict="PASS"),
    ]
    proposal = construct(doubled, universe(), capital=1_000_000.0, returns=returns(),
                         return_symbols=SYMBOLS)
    holding = next(h for h in proposal.holdings if h.symbol == "NQ")
    assert holding.expected_return == pytest.approx(0.04)
    assert set(holding.strategy_ids) == {"a", "b"}


# ── the refusals ─────────────────────────────────────────────────────────────


def test_no_covariance_estimate_means_no_portfolio_rather_than_equal_weight() -> None:
    """Falling back would be a different exercise presented as the same one."""
    proposal = construct(signals(), universe(), capital=1_000_000.0,
                         returns=returns(rows=MINIMUM_OBSERVATIONS - 5),
                         return_symbols=SYMBOLS)
    assert proposal.feasible is False
    assert proposal.holdings == ()
    assert any("covariance" in note for note in proposal.limitations)


def test_no_return_history_at_all_is_refused_with_a_reason() -> None:
    proposal = construct(signals(), universe(), capital=1_000_000.0)
    assert proposal.feasible is False
    assert any("no return history" in note for note in proposal.limitations)


def test_construction_refuses_non_positive_capital() -> None:
    with pytest.raises(ConstructionError, match="capital must be positive"):
        construct(signals(), universe(), capital=0.0)


def test_a_constraint_set_that_cannot_be_satisfied_is_refused_at_construction() -> None:
    with pytest.raises(ValidationError, match="max_net cannot exceed max_gross"):
        Constraints(max_gross=0.5, max_net=1.0)


# ── the report says what happened ────────────────────────────────────────────


def test_an_unapplied_constraint_says_so_rather_than_reporting_zero() -> None:
    """A liquidity row of 0.0 reads as "no capacity used", which is a measurement."""
    blind = {symbol: instrument.model_copy(update={"adv_notional": None})
             for symbol, instrument in universe().items()}
    proposal = construct(signals(), blind, capital=1_000_000.0, returns=returns(),
                         return_symbols=SYMBOLS)
    liquidity = next(c for c in proposal.constraints if c.name == "liquidity")
    assert liquidity.applied is False
    assert "no instrument reported" in liquidity.detail


def test_the_proposal_states_which_estimator_produced_it() -> None:
    proposal = build()
    assert proposal.covariance_method == "ledoit_wolf_constant_correlation"
    assert proposal.observations > MINIMUM_OBSERVATIONS
    assert "SLSQP" in proposal.optimiser


def test_a_refused_proposal_reports_no_sharpe_rather_than_a_division() -> None:
    """A ratio over zero volatility would render as an impressively large number."""
    proposal = construct(signals(), universe(), capital=1_000_000.0)
    assert proposal.feasible is False
    assert proposal.expected_volatility == 0.0
    assert proposal.expected_sharpe is None


def test_contracts_are_none_when_no_price_is_known() -> None:
    """None is the absence of a price; zero is a decision to hold nothing."""
    priceless = {symbol: instrument.model_copy(update={"price": None})
                 for symbol, instrument in universe().items()}
    proposal = construct(signals(), priceless, capital=1_000_000.0, returns=returns(),
                         return_symbols=SYMBOLS)
    assert all(holding.contracts is None for holding in proposal.holdings)


def test_construction_is_deterministic() -> None:
    """The same inputs give the same portfolio, or nothing downstream is reproducible."""
    first, second = build(), build()
    assert first.portfolio_id == second.portfolio_id
    assert [h.weight for h in first.holdings] == [h.weight for h in second.holdings]


# ── the covariance estimator itself ──────────────────────────────────────────


def test_shrinkage_is_reported_and_lies_between_zero_and_one() -> None:
    estimated = estimate(returns(), SYMBOLS)
    assert estimated.usable
    assert 0.0 <= estimated.shrinkage <= 1.0


def test_a_thin_sample_is_reported_unusable_rather_than_shrunk_into_existence() -> None:
    estimated = estimate(returns(rows=MINIMUM_OBSERVATIONS - 1), SYMBOLS)
    assert estimated.usable is False
    assert str(MINIMUM_OBSERVATIONS) in estimated.note


def test_a_poorly_conditioned_sample_carries_a_warning_even_when_usable() -> None:
    """Usable is not the same as good, and the difference has to be visible."""
    thin = estimate(returns(rows=MINIMUM_OBSERVATIONS + 2), SYMBOLS)
    assert thin.usable
    assert "poorly conditioned" in thin.note
    assert estimate(returns(rows=300), SYMBOLS).note == ""


def test_non_finite_returns_are_refused_rather_than_silently_dropped() -> None:
    dirty = returns()
    dirty[3, 1] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        estimate(dirty, SYMBOLS)


def test_the_estimate_recovers_the_correlation_structure_it_was_given() -> None:
    correlation = estimate(returns(rows=2_000), SYMBOLS).correlation()
    nq, es, gc = SYMBOLS.index("NQ"), SYMBOLS.index("ES"), SYMBOLS.index("GC")
    # NQ and ES share a factor with weights 1.0 and 0.9; GC barely loads on it.
    assert correlation[nq, es] > correlation[nq, gc]
    assert correlation[nq, es] > 0.4
