"""Tests for the selection-bias statistics.

Where a published reference value exists it is asserted directly, because these
estimators are easy to implement plausibly and wrongly.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from forge.judge.statistics import (
    annualised_sharpe,
    calmar_ratio,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    kurtosis,
    minimum_track_record_length,
    per_period_sharpe,
    permutation_pvalue,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
    skewness,
    sortino_ratio,
    t_statistic,
)


def test_sharpe_does_not_grow_with_sample_size() -> None:
    """The old bug: mean/sd*sqrt(n) rewards trading more, not trading better."""
    rng = np.random.default_rng(2)
    short = rng.normal(1.0, 10.0, 100)
    long = np.concatenate([short] * 8)
    assert per_period_sharpe(short) == pytest.approx(per_period_sharpe(long), rel=0.02)
    # The t-statistic is the thing that scales, and it is named accordingly.
    assert t_statistic(long) > t_statistic(short) * 2


def test_annualisation_is_explicit_and_multiplicative() -> None:
    rng = np.random.default_rng(4)
    series = rng.normal(0.5, 5.0, 500)
    assert annualised_sharpe(series, 252.0) == pytest.approx(
        per_period_sharpe(series) * math.sqrt(252.0)
    )


def test_expected_max_sharpe_matches_published_value() -> None:
    """Bailey & Lopez de Prado report ~3.26 for 1,000 trials at V[SR] = 1."""
    assert expected_max_sharpe(1000, 1.0) == pytest.approx(3.26, abs=0.02)
    # One trial is no selection at all, so there is no hurdle to clear.
    assert expected_max_sharpe(1, 1.0) == 0.0
    # More trials can only raise the bar.
    assert expected_max_sharpe(10_000, 1.0) > expected_max_sharpe(100, 1.0)


def test_probabilistic_sharpe_falls_with_negative_skew_and_fat_tails() -> None:
    rng = np.random.default_rng(6)
    symmetric = rng.normal(1.0, 10.0, 400)
    skewed = symmetric.copy()
    skewed[:20] -= 90.0  # a handful of severe losses, same broad shape otherwise
    assert probabilistic_sharpe_ratio(skewed) < probabilistic_sharpe_ratio(symmetric)
    assert skewness(skewed) < skewness(symmetric)


def test_deflated_sharpe_collapses_as_the_search_widens() -> None:
    rng = np.random.default_rng(8)
    series = rng.normal(0.15, 1.0, 600)
    trials = tuple(rng.normal(0.0, 0.1, 500))
    narrow = deflated_sharpe_ratio(series, 5, trials[:5])
    wide = deflated_sharpe_ratio(series, 500, trials)
    assert narrow.deflated_sharpe_ratio > wide.deflated_sharpe_ratio
    assert wide.benchmark_sharpe > narrow.benchmark_sharpe
    # The undeflated PSR is unmoved by how many things were tried, which is
    # exactly why it is the wrong number to gate on.
    assert narrow.probabilistic_sharpe_ratio == pytest.approx(wide.probabilistic_sharpe_ratio)


def test_deflated_sharpe_measures_trial_spread_when_given_it() -> None:
    rng = np.random.default_rng(9)
    series = rng.normal(0.2, 1.0, 400)
    assumed = deflated_sharpe_ratio(series, 50)
    measured = deflated_sharpe_ratio(series, 50, tuple(rng.normal(0.0, 0.05, 50)))
    assert assumed.sharpe_variance == 1.0
    assert measured.sharpe_variance < 0.02
    assert measured.benchmark_sharpe < assumed.benchmark_sharpe


def test_kurtosis_is_non_excess() -> None:
    rng = np.random.default_rng(12)
    normal = rng.normal(0.0, 1.0, 20_000)
    assert kurtosis(normal) == pytest.approx(3.0, abs=0.15)


def test_pbo_is_near_chance_on_noise_and_low_with_a_real_edge() -> None:
    rng = np.random.default_rng(13)
    noise = rng.normal(0.0, 1.0, (400, 30))
    assert probability_of_backtest_overfitting(noise, blocks=8).probability == pytest.approx(
        0.5, abs=0.15
    )
    edged = noise.copy()
    edged[:, 0] += 0.4
    assert probability_of_backtest_overfitting(edged, blocks=8).probability < 0.1


def test_pbo_is_near_chance_on_noise_for_an_odd_number_of_configurations() -> None:
    """The case the 30-column test above cannot see.

    `rank / (N + 1)` is exactly 0.5 when the winner takes the middle rank, and
    the middle rank is an integer only when N is odd. Counting those ties as
    overfit inflated PBO for odd N alone, while every even count stayed
    correct.

    Averaged over independent draws rather than asserted on one: with three
    configurations a single 400-row matrix swings between roughly 0.15 and
    0.85, so one draw measures the draw and not the estimator. Over 24 the mean
    is stable, and the old behaviour misses it by a wide margin -- 0.70 against
    0.53 at three columns, 0.66 against 0.54 at five.
    """
    for columns in (3, 5, 9, 15):
        rng = np.random.default_rng(13 + columns)
        measured = float(
            np.mean(
                [
                    probability_of_backtest_overfitting(
                        rng.normal(0.0, 1.0, (400, columns)), blocks=8
                    ).probability
                    for _ in range(24)
                ]
            )
        )
        assert measured == pytest.approx(0.5, abs=0.08), (
            f"{columns} configurations of pure noise averaged PBO {measured}; "
            "the selection rule is neither better nor worse than chance here"
        )


def test_pbo_calls_indistinguishable_configurations_a_coin_flip() -> None:
    """Selection among identical things is exactly neutral, not damning.

    Every column the same means every split ties on the median, so every logit
    is exactly 0.0. Counting a tie as overfit scored this 1.0 -- the most
    damning value available -- for the one case that is unambiguously a coin
    flip. PBO gates promotion, so that error withheld verdicts.
    """
    rng = np.random.default_rng(29)
    identical = np.tile(rng.normal(0.0, 1.0, (300, 1)), (1, 6))
    result = probability_of_backtest_overfitting(identical, blocks=8)
    assert all(logit == 0.0 for logit in result.logits)
    assert result.probability == pytest.approx(0.5)


def test_pbo_still_condemns_a_search_that_never_generalises() -> None:
    """The fix must not soften the finding PBO exists to make.

    Columns that are best in-sample precisely when they are worst out of
    sample: the selection rule is actively misleading, and no tie-handling
    convention should rescue it.
    """
    rng = np.random.default_rng(5)
    periods, columns = 400, 6
    matrix = rng.normal(0.0, 0.5, (periods, columns))
    half = periods // 2
    for column in range(columns):
        # A ramp in the first half, reversed in the second: whoever wins on one
        # side is the loser on the other, whichever way the blocks are dealt.
        matrix[:half, column] += 0.4 * column
        matrix[half:, column] -= 0.4 * column
    assert probability_of_backtest_overfitting(matrix, blocks=8).probability > 0.5


def test_pbo_split_count_is_the_symmetric_combination_count() -> None:
    rng = np.random.default_rng(14)
    result = probability_of_backtest_overfitting(rng.normal(0.0, 1.0, (200, 8)), blocks=6)
    assert result.splits == 20  # C(6, 3)
    assert result.trials == 8


def test_pbo_rejects_odd_blocks_and_thin_matrices() -> None:
    rng = np.random.default_rng(15)
    with pytest.raises(ValueError, match="even"):
        probability_of_backtest_overfitting(rng.normal(0.0, 1.0, (200, 6)), blocks=7)
    with pytest.raises(ValueError, match="INSUFFICIENT_PERIODS"):
        probability_of_backtest_overfitting(rng.normal(0.0, 1.0, (10, 6)), blocks=8)
    with pytest.raises(ValueError, match="at least 2 configurations"):
        probability_of_backtest_overfitting(rng.normal(0.0, 1.0, (200, 1)), blocks=4)


def test_minimum_track_record_is_infinite_without_an_edge() -> None:
    rng = np.random.default_rng(16)
    assert minimum_track_record_length(rng.normal(-1.0, 10.0, 300)) == math.inf
    needed = minimum_track_record_length(rng.normal(1.0, 10.0, 300))
    assert 1.0 < needed < math.inf


def test_permutation_pvalue_separates_signal_from_noise() -> None:
    rng = np.random.default_rng(17)
    assert permutation_pvalue(rng.normal(0.0, 1.0, 300), samples=800) > 0.1
    assert permutation_pvalue(rng.normal(0.6, 1.0, 300), samples=800) < 0.01


def test_sortino_ignores_upside_volatility_and_calmar_uses_drawdown() -> None:
    steady = np.array([10.0, 10.0, 10.0, -5.0, 10.0, 10.0])
    spiky = np.array([10.0, 90.0, 10.0, -5.0, 10.0, 10.0])
    # Adding a large winner must not reduce the Sortino ratio.
    assert sortino_ratio(spiky) > sortino_ratio(steady)
    assert calmar_ratio(steady) == pytest.approx(float(steady.sum()) / 5.0)


def test_non_finite_input_is_rejected_everywhere() -> None:
    bad = np.array([1.0, float("nan"), 2.0])
    for function in (per_period_sharpe, probabilistic_sharpe_ratio, sortino_ratio, calmar_ratio):
        with pytest.raises(ValueError):
            function(bad)  # type: ignore[operator]
