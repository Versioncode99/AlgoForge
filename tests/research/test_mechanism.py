"""G9: the entry-timing control.

The null is "the same directions and holding periods, entered at random times,
would have done as well". These tests build series where the answer is known by
construction, so a passing test means the estimator agrees with a fact rather
than with itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest
from forge.judge import Judge, JudgeInput
from forge.research import MECHANISM_ALPHA, entry_timing_control
from forge.research.mechanism import MINIMUM_TRADES_FOR_CONTROL


@dataclass
class T:
    direction: int
    bars_held: int
    net_pnl: float


def ramp(size: int = 4_000) -> np.ndarray:
    """A gently rising series. Random long entries make money here too."""
    return np.linspace(20_000.0, 20_400.0, size)


def noisy(size: int = 4_000, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return 20_000.0 + np.cumsum(rng.normal(0.0, 4.0, size))


# ── the control detects real timing skill ────────────────────────────────────


def test_entries_at_the_best_moments_beat_random_timing() -> None:
    """A strategy that entered exactly before each rise should clear the bar."""
    prices = noisy(size=4_000, seed=11)
    hold = 20
    # Pick the 40 windows with the largest forward move: perfect timing.
    forward = prices[hold:] - prices[:-hold]
    best = np.argsort(forward)[-40:]
    trades = [
        T(1, hold, float((prices[index + hold] - prices[index]) * 2.0 - 5.0)) for index in best
    ]

    result = entry_timing_control(
        trades,
        prices,
        point_value=2.0,
        round_trip_cost=5.0,
        warmup_bars=50,
        seed=7,
    )
    assert result.aligned is True
    assert result.p_value < MECHANISM_ALPHA
    assert result.observed_pnl > result.control_p95


def test_entries_at_random_moments_do_not_beat_random_timing() -> None:
    """The case the gate exists for: P&L that is entirely market drift."""
    prices = ramp()
    hold = 25
    rng = np.random.default_rng(5)
    entries = rng.integers(60, prices.size - hold - 1, size=60)
    trades = [
        T(1, hold, float((prices[index + hold] - prices[index]) * 2.0 - 5.0)) for index in entries
    ]

    result = entry_timing_control(
        trades, prices, point_value=2.0, round_trip_cost=5.0, warmup_bars=50, seed=7
    )
    assert result.aligned is False
    assert result.p_value >= MECHANISM_ALPHA
    assert result.reason.startswith("NOT_BETTER_THAN_RANDOM_ENTRY")


def test_a_profitable_strategy_riding_pure_drift_is_falsified() -> None:
    """Profit is not the question. G4 asks that; this asks whether timing did it.

    On a linear ramp every entry earns the same move, so a profitable book here
    is earning drift and nothing else. The observed P&L must land exactly on the
    control distribution, and the gate must say so.
    """
    prices = ramp()
    hold = 30
    move = float((prices[hold] - prices[0]) * 2.0 - 5.0)
    trades = [T(1, hold, move) for _ in range(50)]

    result = entry_timing_control(
        trades, prices, point_value=2.0, round_trip_cost=5.0, warmup_bars=50, seed=7
    )
    assert result.observed_pnl > 0
    assert result.observed_pnl == pytest.approx(result.control_median, rel=1e-6)
    # The point-mass case. Without a tie tolerance the last few bits of the
    # float decide the verdict, and a strategy earning exactly the drift is
    # declared to have timing skill at p = 1/(N+1).
    assert result.p_value > 0.9
    assert result.aligned is False


# ── what it holds fixed ──────────────────────────────────────────────────────


def test_direction_is_held_fixed_so_shorts_are_compared_against_shorts() -> None:
    """A short book in a rising market must not be flattered by a long control."""
    prices = ramp()
    trades = [T(-1, 20, -50.0) for _ in range(40)]
    result = entry_timing_control(
        trades, prices, point_value=2.0, round_trip_cost=5.0, warmup_bars=50, seed=7
    )
    # Random shorts in a rising market also lose, so this is not "better".
    assert result.control_median < 0
    assert result.aligned is False


def test_the_result_is_reproducible_for_a_given_seed() -> None:
    prices = noisy()
    trades = [T(1, 20, 10.0) for _ in range(40)]
    kwargs: dict[str, Any] = {
        "point_value": 2.0,
        "round_trip_cost": 5.0,
        "warmup_bars": 50,
        "seed": 99,
    }
    first = entry_timing_control(trades, prices, **kwargs)
    second = entry_timing_control(trades, prices, **kwargs)
    assert first.p_value == second.p_value
    assert first.control_median == second.control_median


def test_a_different_seed_gives_a_different_draw() -> None:
    prices = noisy()
    trades = [T(1, 20, 10.0) for _ in range(40)]
    base: dict[str, Any] = {"point_value": 2.0, "round_trip_cost": 5.0, "warmup_bars": 50}
    first = entry_timing_control(trades, prices, seed=1, **base)
    second = entry_timing_control(trades, prices, seed=2, **base)
    assert first.control_median != second.control_median


# ── refusing to answer ───────────────────────────────────────────────────────


def test_too_few_trades_is_unmeasured_not_aligned() -> None:
    prices = noisy()
    trades = [T(1, 20, 10.0) for _ in range(MINIMUM_TRADES_FOR_CONTROL - 1)]
    result = entry_timing_control(
        trades, prices, point_value=2.0, round_trip_cost=5.0, warmup_bars=50, seed=7
    )
    assert result.aligned is None
    assert result.reason.startswith("ONLY_")


def test_no_trades_is_unmeasured() -> None:
    result = entry_timing_control(
        [], noisy(), point_value=2.0, round_trip_cost=5.0, warmup_bars=50, seed=7
    )
    assert result.aligned is None


def test_a_series_too_short_to_place_the_holds_is_unmeasured() -> None:
    prices = noisy(size=120)
    trades = [T(1, 200, 10.0) for _ in range(40)]
    result = entry_timing_control(
        trades, prices, point_value=2.0, round_trip_cost=5.0, warmup_bars=100, seed=7
    )
    assert result.aligned is None
    assert result.reason == "SERIES_TOO_SHORT_FOR_CONTROL"


def test_a_point_mass_null_is_not_mistaken_for_significance() -> None:
    """Regression: float noise must not be counted as an edge.

    On a series with no dispersion every control draw earns exactly what the
    strategy earned. Before the tie tolerance, `totals >= observed` counted
    zero of 400 draws as matching — because the two sums differed in the
    eleventh significant digit — and the test reported p = 0.0025 for a
    strategy whose entries carried no information whatsoever.
    """
    prices = np.full(4_000, 20_000.0)
    trades = [T(1, 20, -5.0) for _ in range(40)]
    result = entry_timing_control(
        trades, prices, point_value=2.0, round_trip_cost=5.0, warmup_bars=50, seed=7
    )
    assert result.p_value == 1.0
    assert result.aligned is False


def test_the_p_value_can_never_be_zero() -> None:
    """The +1/+1 correction, same as G6's permutation test."""
    prices = noisy()
    trades = [T(1, 20, 1e9) for _ in range(40)]
    result = entry_timing_control(
        trades, prices, point_value=2.0, round_trip_cost=5.0, warmup_bars=50, seed=7
    )
    assert result.p_value > 0.0


def test_the_report_states_the_null_and_what_it_does_not_assert() -> None:
    prices = noisy()
    trades = [T(1, 20, 10.0) for _ in range(40)]
    payload = entry_timing_control(
        trades, prices, point_value=2.0, round_trip_cost=5.0, warmup_bars=50, seed=7
    ).as_dict()
    assert "uniformly random times" in payload["null"]
    assert "stated economic story" in payload["does_not_assert"]


# ── the gate ─────────────────────────────────────────────────────────────────


def judge_with(**overrides: object) -> Any:
    base: dict[str, Any] = {
        "run_id": "run-1",
        "tier": "TRUTH_OOS",
        "pnl": tuple(float(v) for v in (80, -25, 95, -30, 70, -20, 110, -35, 60, 45) * 4),
        "trial_count": 1,
        "data_gate_passed": True,
        "preregistered": True,
        "implementation_tests_passed": True,
        "engine_consistent": True,
    }
    return Judge().evaluate(JudgeInput(**{**base, **overrides}))


def test_a_falsified_mechanism_fails_the_ladder() -> None:
    verdict = judge_with(mechanism_aligned=False)
    gate = next(item for item in verdict.gates if item.gate == "G9")
    assert gate.status == "FAIL"
    assert gate.observed == "FALSIFIED"
    assert verdict.decision == "FAIL"


def test_an_untested_mechanism_withholds_the_pass() -> None:
    verdict = judge_with(mechanism_aligned=None)
    assert next(item for item in verdict.gates if item.gate == "G9").status == "INCONCLUSIVE"
    assert verdict.decision == "INCONCLUSIVE"
