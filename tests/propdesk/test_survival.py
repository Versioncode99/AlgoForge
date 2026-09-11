"""The appetite meter: what it selects, and what it refuses to guess."""

from __future__ import annotations

import numpy as np
import pytest
from forge.propdesk.risk import RiskBoundaries
from forge.propdesk.survival import (
    AGGRESSIVE_QUANTILE,
    CONSERVATIVE_QUANTILE,
    MIN_SERIES_DAYS,
    DrawdownDistribution,
    Unmeasurable,
    appetite_quantile,
    derive_band,
    drawdown_distribution,
)


def normal(days: int = 200, scale: float = 100.0, seed: int = 3) -> tuple[float, ...]:
    rng = np.random.default_rng(seed)
    return tuple(float(x) for x in rng.normal(15.0, scale, days))


def fat_tailed(days: int = 200, seed: int = 3) -> tuple[float, ...]:
    rng = np.random.default_rng(seed)
    return tuple(float(x) for x in rng.standard_t(2.0, days) * 100.0 + 15.0)


class TestRefusals:
    def test_too_short_a_series_is_unmeasurable_and_says_by_how_much(self) -> None:
        result = drawdown_distribution(tuple(range(10)))
        assert isinstance(result, Unmeasurable)
        assert result.observed_days == 10
        assert result.shortfall == MIN_SERIES_DAYS - 10

    def test_a_flat_series_has_no_drawdown_to_bootstrap(self) -> None:
        result = drawdown_distribution(tuple([5.0] * 100))
        assert isinstance(result, Unmeasurable)
        assert "no variation" in result.reason

    def test_a_series_with_a_nan_is_refused_rather_than_dropped(self) -> None:
        result = drawdown_distribution((*normal(100), float("nan")))
        assert isinstance(result, Unmeasurable)
        assert "finite" in result.reason

    def test_an_unknown_buffer_yields_no_band(self) -> None:
        band = derive_band(
            appetite=50, boundaries=RiskBoundaries(), buffer=None, daily_pnl=normal()
        )
        assert isinstance(band, Unmeasurable)
        assert "buffer" in band.reason

    def test_a_spent_buffer_yields_no_band(self) -> None:
        band = derive_band(
            appetite=50, boundaries=RiskBoundaries(), buffer=-50.0, daily_pnl=normal()
        )
        assert isinstance(band, Unmeasurable)
        assert "no buffer left" in band.reason

    def test_nothing_falls_back_to_a_default_distribution(self) -> None:
        # The failure mode this guards: a plausible, unlabelled number
        # multiplied directly by the account's buffer.
        band = derive_band(
            appetite=50, boundaries=RiskBoundaries(), buffer=10_000.0, daily_pnl=()
        )
        assert isinstance(band, Unmeasurable)


class TestTheDistribution:
    def test_quantiles_are_ordered(self) -> None:
        distribution = drawdown_distribution(normal())
        assert isinstance(distribution, DrawdownDistribution)
        assert (
            distribution.p50
            <= distribution.p75
            <= distribution.p90
            <= distribution.p95
            <= distribution.p99
            <= distribution.worst
        )

    def test_it_is_deterministic_for_a_given_seed(self) -> None:
        first = drawdown_distribution(normal(), seed=11)
        second = drawdown_distribution(normal(), seed=11)
        assert first == second

    def test_a_fat_tail_reports_a_larger_tail_ratio(self) -> None:
        thin = drawdown_distribution(normal())
        fat = drawdown_distribution(fat_tailed())
        assert isinstance(thin, DrawdownDistribution)
        assert isinstance(fat, DrawdownDistribution)
        assert fat.tail_ratio > thin.tail_ratio

    def test_quantile_returns_the_nearest_estimated_point_not_an_interpolation(self) -> None:
        # Interpolating would invent precision the bootstrap does not have.
        distribution = drawdown_distribution(normal())
        assert isinstance(distribution, DrawdownDistribution)
        assert distribution.quantile(0.87) in {distribution.p90, distribution.p95}
        assert distribution.quantile(0.99) == distribution.p99


class TestTheMeter:
    def test_conservative_sizes_against_a_deeper_quantile_than_aggressive(self) -> None:
        assert appetite_quantile(0) == CONSERVATIVE_QUANTILE
        assert appetite_quantile(100) == AGGRESSIVE_QUANTILE
        assert appetite_quantile(0) > appetite_quantile(100)

    def test_turning_the_meter_up_never_reduces_the_contract_count(self) -> None:
        boundaries = RiskBoundaries(minimum_fraction=0.05, maximum_fraction=0.3, max_contracts=50)
        counts = [
            derive_band(
                appetite=a, boundaries=boundaries, buffer=100_000.0, daily_pnl=normal()
            ).contracts
            for a in (0, 25, 50, 75, 100)
        ]
        assert counts == sorted(counts)

    def test_the_same_appetite_buys_fewer_contracts_on_a_fat_tailed_strategy(self) -> None:
        # The property that makes the meter statistical rather than cosmetic:
        # what it costs depends on the strategy's measured tail, not on the
        # meter's position.
        boundaries = RiskBoundaries(minimum_fraction=0.05, maximum_fraction=0.3, max_contracts=50)
        thin = derive_band(
            appetite=50, boundaries=boundaries, buffer=100_000.0, daily_pnl=normal()
        )
        fat = derive_band(
            appetite=50, boundaries=boundaries, buffer=100_000.0, daily_pnl=fat_tailed()
        )
        assert fat.contracts < thin.contracts

    def test_the_band_reports_both_ends_so_the_meter_can_be_drawn(self) -> None:
        boundaries = RiskBoundaries(minimum_fraction=0.05, maximum_fraction=0.3)
        band = derive_band(
            appetite=40, boundaries=boundaries, buffer=50_000.0, daily_pnl=normal()
        )
        assert band.conservative_fraction == boundaries.minimum_fraction
        assert band.aggressive_fraction == boundaries.maximum_fraction
        assert band.conservative_fraction <= band.target_fraction <= band.aggressive_fraction

    def test_the_contract_count_respects_the_operators_blunt_ceiling(self) -> None:
        boundaries = RiskBoundaries(max_contracts=2, maximum_fraction=1.0, max_daily_change=1.0)
        band = derive_band(
            appetite=100, boundaries=boundaries, buffer=10_000_000.0, daily_pnl=normal()
        )
        assert band.contracts == 2

    def test_the_band_carries_the_distribution_it_was_derived_from(self) -> None:
        band = derive_band(
            appetite=50, boundaries=RiskBoundaries(), buffer=50_000.0, daily_pnl=normal()
        )
        assert band.distribution.observed_days == 200
        assert band.drawdown_per_contract == pytest.approx(
            band.distribution.quantile(band.quantile)
        )
