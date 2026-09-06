"""The prop refusal must tell the user how to lift it, not just that it stands."""

from __future__ import annotations

from forge.prop import MIN_TRADING_DAYS, assess_day_coverage
from forge.prop.engine import MAX_BACKTEST_BARS


def test_sufficient_coverage_makes_no_suggestion() -> None:
    coverage = assess_day_coverage(trading_days=45, bars_used=90_000, span_days=64)
    assert coverage.sufficient
    assert coverage.suggested_bar_count is None
    assert "45 trading days observed" in coverage.explain()


def test_short_window_is_sized_from_observed_trade_density() -> None:
    """Scaling uses trading days, not calendar span, so selective strategies fit."""
    coverage = assess_day_coverage(trading_days=5, bars_used=30_000, span_days=21)
    assert not coverage.sufficient
    assert coverage.bars_per_trading_day == 6_000
    # 6,000 bars/day * 30 days * 1.2 margin
    assert coverage.suggested_bar_count == 216_000
    assert coverage.suggestion_exceeds_limit is False
    assert "bar_count of about 216,000" in coverage.explain()


def test_suggestion_is_scaled_back_to_the_full_request_window() -> None:
    """Only the validation slice reaches the simulator; advice must say so."""
    partitioned = assess_day_coverage(
        trading_days=10, bars_used=12_000, span_days=14, partition_fraction=0.2
    )
    assert partitioned.suggested_bar_count == 43_200
    # 43,200 partition bars means asking for five times that.
    assert partitioned.suggested_request_bars == 216_000
    assert "bar_count of about 216,000" in partitioned.explain()


def test_a_thin_window_still_gets_an_actionable_number() -> None:
    """One trading day in 30k bars is extrapolatable, and now fits the cap."""
    coverage = assess_day_coverage(trading_days=1, bars_used=30_000, span_days=21)
    assert coverage.suggestion_exceeds_limit is False
    message = coverage.explain()
    assert "Only 1 trading day produced trades" in message
    assert "bar_count of about 1,080,000" in message


def test_a_strategy_that_trades_too_rarely_is_told_so_plainly() -> None:
    """Past the cap the answer is a finding, not advice."""
    coverage = assess_day_coverage(trading_days=1, bars_used=250_000, span_days=90)
    assert coverage.suggested_request_bars is not None
    assert coverage.suggested_request_bars > MAX_BACKTEST_BARS
    assert coverage.suggestion_exceeds_limit is True
    message = coverage.explain()
    assert "trades too rarely" in message
    assert f"{MAX_BACKTEST_BARS:,}" in message


def test_no_trades_at_all_cannot_be_extrapolated_from() -> None:
    coverage = assess_day_coverage(trading_days=0, bars_used=30_000, span_days=21)
    assert coverage.suggested_bar_count is None
    assert "too few days to extrapolate" in coverage.explain()


def test_boundary_at_the_required_day_count() -> None:
    assert assess_day_coverage(MIN_TRADING_DAYS, 60_000, 42).sufficient
    assert not assess_day_coverage(MIN_TRADING_DAYS - 1, 60_000, 42).sufficient


def test_singular_and_plural_days_read_correctly() -> None:
    assert "1 trading day produced" in assess_day_coverage(1, 1_000, 2).explain()
    assert "2 trading days produced" in assess_day_coverage(2, 1_000, 3).explain()
