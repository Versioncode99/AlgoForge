"""Runs the validation stack and returns the evidence the judge demands.

The judge refuses to pass a strategy whose walk-forward, selection integrity and
path robustness were never measured. This module is what measures them.

It takes a **callback** rather than importing the backtest runtime, for two
reasons: ``forge.strategy.models`` already imports ``forge.research.models``, so
the reverse import would be a cycle; and a plain callable keeps every function
here testable against a synthetic stand-in instead of a real strategy.

The callback runs one configuration over one slice of bars and returns a P&L
value **per bar**, aligned to the slice it was given — zero on bars where the
strategy held no position. Per-bar alignment is what makes the configurations
comparable: CSCV needs a ``T x N`` matrix on one shared time grid, and per-trade
series have neither a common length nor a common clock.

The parameter search is re-run inside every fold and every split. That is the
whole point of the exercise: a strategy is only out-of-sample if the choice of
parameters was also made without seeing the test window. Selecting once on the
full series and then measuring folds of that winner reports the stability of a
number that already saw everything.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import product
from typing import Any

import numpy as np

from forge.contracts.hashing import stable_id
from forge.judge.statistics import (
    BacktestOverfitting,
    per_period_sharpe,
    probability_of_backtest_overfitting,
)
from forge.research.cpcv import (
    CombinatorialPlan,
    PathDistribution,
    combinatorial_purged_plan,
    path_distribution,
)
from forge.research.walkforward import (
    WalkForwardPlan,
    WalkForwardResult,
    walk_forward_efficiency,
)
from forge.research.walkforward import (
    walk_forward_plan as build_walk_forward_plan,
)

# Runs one parameter set over one slice; returns per-bar P&L of that slice.
BarPnlFn = Callable[[Sequence[Any], Mapping[str, Any]], tuple[float, ...]]


@dataclass(frozen=True)
class ValidationEvidence:
    """Everything gates G5 and G11-G13 need, produced in one pass."""

    evidence_id: str
    trial_count: int
    trial_sharpes: tuple[float, ...]
    overfitting: BacktestOverfitting
    walk_forward: WalkForwardResult
    paths: PathDistribution
    best_parameters: dict[str, Any]
    selection_stability: float
    walk_forward_plan_id: str
    cpcv_plan_id: str

    def as_metadata(self) -> dict[str, Any]:
        """Flat summary for API responses and activity logging."""
        return {
            "evidence_id": self.evidence_id,
            "trial_count": self.trial_count,
            "probability_of_overfitting": round(self.overfitting.probability, 6),
            "cscv_splits": self.overfitting.splits,
            "walk_forward_efficiency": self.walk_forward.efficiency,
            "walk_forward_folds": self.walk_forward.fold_count,
            "walk_forward_consistency": self.walk_forward.consistency,
            "cpcv_paths": self.paths.paths,
            "path_sharpe_p05": self.paths.sharpe_p05,
            "path_positive_share": self.paths.positive_share,
            "selection_stability": self.selection_stability,
            "best_parameters": self.best_parameters,
            "walk_forward_plan_id": self.walk_forward_plan_id,
            "cpcv_plan_id": self.cpcv_plan_id,
        }


def expand_grid(grid: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    """Every combination in the grid, in a stable order.

    Sorted by name so the same grid always produces the same trial ordering,
    which is what lets a trial count be compared across runs.
    """
    if not grid:
        return [{}]
    names = sorted(grid)
    return [
        dict(zip(names, values, strict=True))
        for values in product(*(list(grid[name]) for name in names))
    ]


def _select(matrix: np.ndarray, rows: np.ndarray) -> int:
    """Index of the configuration with the best Sharpe over ``rows``."""
    block = matrix[rows]
    means = block.mean(axis=0)
    deviations = block.std(axis=0, ddof=1) if block.shape[0] > 1 else np.zeros(block.shape[1])
    scores = np.divide(means, deviations, out=np.zeros_like(means), where=deviations > 0)
    return int(np.argmax(scores))


def run_validation(
    bars: Sequence[Any],
    *,
    warmup_bars: int,
    backtest: BarPnlFn,
    parameter_grid: Mapping[str, Sequence[Any]],
    folds: int = 6,
    groups: int = 6,
    test_groups: int = 2,
    blocks: int = 8,
) -> ValidationEvidence:
    """Run the full stack: sweep, CSCV, walk-forward and CPCV.

    The sweep runs once and its ``T x N`` matrix is reused everywhere, so
    re-selecting parameters inside each fold and split costs no extra backtests.
    """
    total = len(bars)
    if total == 0:
        raise ValueError("cannot validate an empty bar series")
    trials = expand_grid(parameter_grid)
    if len(trials) < 2:
        raise ValueError(
            "validation needs at least 2 configurations: with one trial there is no "
            "selection to audit and PBO is undefined"
        )

    columns: list[np.ndarray] = []
    for parameters in trials:
        series = np.asarray(backtest(bars, parameters), dtype=np.float64)
        if series.size != total:
            raise ValueError(
                f"backtest returned {series.size} values for {total} bars; the callback must "
                "return one P&L value per bar so configurations share a time grid"
            )
        columns.append(series)
    matrix = np.column_stack(columns)
    trial_sharpes = tuple(round(per_period_sharpe(column), 8) for column in columns)

    overfitting = probability_of_backtest_overfitting(matrix, blocks=blocks)

    wf_plan = build_walk_forward_plan(total, warmup_bars=warmup_bars, folds=folds)
    walk_forward, fold_choices = _walk_forward_evidence(wf_plan, matrix)

    cpcv_plan = combinatorial_purged_plan(
        total, warmup_bars=warmup_bars, groups=groups, test_groups=test_groups
    )
    path_returns, split_choices = reconstruct_paths(cpcv_plan, matrix)
    paths = path_distribution(path_returns)

    # How often the search lands on the same configuration when the window
    # moves. A strategy whose winner changes every fold has no stable parameter
    # to deploy, whatever its aggregate numbers say.
    choices = fold_choices + split_choices
    modal = max(set(choices), key=choices.count)
    stability = round(choices.count(modal) / len(choices), 6)

    payload = {
        "bars": total,
        "trials": len(trials),
        "walk_forward": wf_plan.plan_id,
        "cpcv": cpcv_plan.plan_id,
        "modal_trial": modal,
    }
    return ValidationEvidence(
        evidence_id=stable_id("validation", payload),
        trial_count=len(trials),
        trial_sharpes=trial_sharpes,
        overfitting=overfitting,
        walk_forward=walk_forward,
        paths=paths,
        best_parameters=trials[modal],
        selection_stability=stability,
        walk_forward_plan_id=wf_plan.plan_id,
        cpcv_plan_id=cpcv_plan.plan_id,
    )


def _walk_forward_evidence(
    plan: WalkForwardPlan, matrix: np.ndarray
) -> tuple[WalkForwardResult, list[int]]:
    """Re-select on each fold's training window, then measure its test window."""
    in_sample: list[tuple[float, ...]] = []
    out_sample: list[tuple[float, ...]] = []
    chosen: list[int] = []
    for fold in plan.folds:
        train_rows = np.arange(fold.train_start, fold.train_end)
        best = _select(matrix, train_rows)
        chosen.append(best)
        in_sample.append(tuple(matrix[train_rows, best]))
        out_sample.append(tuple(matrix[fold.test_start : fold.test_end, best]))
    return walk_forward_efficiency(tuple(in_sample), tuple(out_sample)), chosen


def reconstruct_paths(
    plan: CombinatorialPlan, matrix: np.ndarray
) -> tuple[tuple[tuple[float, ...], ...], list[int]]:
    """Assemble CPCV splits into distinct out-of-sample histories.

    For every split the winner is chosen on that split's purged training data
    and evaluated on its test blocks, so each group accumulates one prediction
    per split that held it out. Each group is held out by exactly
    ``C(groups - 1, test_groups - 1)`` splits — the path count — so path ``p``
    takes each group's ``p``-th prediction.

    The paths differ precisely to the extent that the parameter search is
    unstable. If selection were perfectly stable every path would coincide, and
    that degenerate case is itself the finding.
    """
    predictions: dict[int, list[np.ndarray]] = {group: [] for group in range(plan.groups)}
    spans: dict[int, tuple[int, int]] = {}
    chosen: list[int] = []
    for split in plan.splits:
        best = _select(matrix, split.train_indices())
        chosen.append(best)
        for group, (start, end) in zip(split.test_groups, split.test_ranges, strict=True):
            predictions[group].append(matrix[start:end, best])
            spans[group] = (start, end)

    built: list[tuple[float, ...]] = []
    for path in range(plan.path_count):
        segments = [
            predictions[group][path]
            for group in range(plan.groups)
            if path < len(predictions[group])
        ]
        if segments:
            built.append(tuple(float(value) for value in np.concatenate(segments)))
    return tuple(built), chosen
