"""The validation runner must find a real edge and refuse a manufactured one."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pytest
from forge.judge import Judge, JudgeInput
from forge.research import expand_grid, run_validation

BARS: list[int] = list(range(30_000))
GRID: dict[str, list[Any]] = {"threshold": [0.01, 0.02, 0.03, 0.04], "size": [1, 2, 3]}
EDGE = {"threshold": 0.02, "size": 1}


def _series(params: Mapping[str, Any], length: int, drift: float) -> tuple[float, ...]:
    seed = int(params["threshold"] * 100) * 10 + int(params["size"])
    return tuple(np.random.default_rng(4000 + seed).normal(drift, 1.0, length))


def edged_backtest(bars: Sequence[Any], params: Mapping[str, Any]) -> tuple[float, ...]:
    """Only one configuration in the grid carries a genuine edge."""
    drift = 0.35 if dict(params) == EDGE else 0.0
    return _series(params, len(bars), drift)


def noise_backtest(bars: Sequence[Any], params: Mapping[str, Any]) -> tuple[float, ...]:
    """No configuration has an edge; any winner is luck."""
    return _series(params, len(bars), 0.0)


def test_expand_grid_is_stable_and_complete() -> None:
    combos = expand_grid({"b": [1, 2], "a": [3, 4, 5]})
    assert len(combos) == 6
    assert list(combos[0]) == ["a", "b"]  # sorted key order
    assert expand_grid({"b": [1, 2], "a": [3, 4, 5]}) == combos
    assert expand_grid({}) == [{}]


def test_a_real_edge_survives_the_whole_stack() -> None:
    evidence = run_validation(BARS, warmup_bars=50, backtest=edged_backtest, parameter_grid=GRID)
    assert evidence.trial_count == 12
    assert evidence.best_parameters == EDGE
    assert evidence.overfitting.probability < 0.1
    assert evidence.walk_forward.survives
    assert evidence.paths.robust
    assert evidence.selection_stability == 1.0


def test_pure_noise_is_rejected_on_several_independent_grounds() -> None:
    evidence = run_validation(BARS, warmup_bars=50, backtest=noise_backtest, parameter_grid=GRID)
    assert evidence.overfitting.selection_is_unreliable
    assert not evidence.walk_forward.survives
    assert not evidence.paths.robust
    # The search lands somewhere different almost every time the window moves.
    assert evidence.selection_stability < 0.6


def test_noise_cannot_reach_a_pass_through_the_judge() -> None:
    evidence = run_validation(BARS, warmup_bars=50, backtest=noise_backtest, parameter_grid=GRID)
    winner = np.asarray(noise_backtest(BARS, evidence.best_parameters))
    verdict = Judge().evaluate(
        JudgeInput(
            run_id="noise",
            tier="HOLDOUT",
            pnl=tuple(winner[:400]),
            trial_count=evidence.trial_count,
            data_gate_passed=True,
            preregistered=True,
            implementation_tests_passed=True,
            trial_sharpes=evidence.trial_sharpes,
            overfitting=evidence.overfitting,
            walk_forward=evidence.walk_forward,
            paths=evidence.paths,
        )
    )
    assert verdict.decision == "FAIL"
    failed = {gate.gate for gate in verdict.gates if gate.status == "FAIL"}
    assert {"G11", "G12", "G13"} <= failed


def test_cpcv_paths_diverge_when_selection_is_unstable() -> None:
    """Identical paths would mean the reconstruction is not re-selecting."""
    unstable = run_validation(BARS, warmup_bars=50, backtest=noise_backtest, parameter_grid=GRID)
    assert unstable.paths.dispersion > 0.0


def test_evidence_is_deterministic_and_content_addressed() -> None:
    first = run_validation(BARS, warmup_bars=50, backtest=edged_backtest, parameter_grid=GRID)
    second = run_validation(BARS, warmup_bars=50, backtest=edged_backtest, parameter_grid=GRID)
    assert first.evidence_id == second.evidence_id
    assert first.as_metadata() == second.as_metadata()


def test_a_single_configuration_has_no_selection_to_audit() -> None:
    with pytest.raises(ValueError, match="at least 2 configurations"):
        run_validation(
            BARS, warmup_bars=50, backtest=edged_backtest, parameter_grid={"threshold": [0.02]}
        )


def test_misaligned_callback_output_is_rejected() -> None:
    def short(bars: Sequence[Any], params: Mapping[str, Any]) -> tuple[float, ...]:
        return (0.0, 1.0, 2.0)

    with pytest.raises(ValueError, match="one P&L value per bar"):
        run_validation(BARS, warmup_bars=50, backtest=short, parameter_grid=GRID)


def test_empty_bars_are_rejected() -> None:
    with pytest.raises(ValueError, match="empty bar series"):
        run_validation([], warmup_bars=50, backtest=edged_backtest, parameter_grid=GRID)
