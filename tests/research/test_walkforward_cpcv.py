"""Walk-forward and CPCV: the leakage gaps are the point, so they are asserted."""

from __future__ import annotations

import numpy as np
import pytest
from forge.research import (
    combinatorial_purged_plan,
    path_distribution,
    walk_forward_efficiency,
    walk_forward_plan,
)


def test_walk_forward_folds_never_touch_their_test_window() -> None:
    plan = walk_forward_plan(50_000, warmup_bars=200, folds=6)
    assert len(plan) == 6
    for fold in plan.folds:
        # The purge gap is what makes a warm-up window unable to see forward.
        assert fold.test_start - fold.train_end >= fold.purge_bars
        assert fold.train_end > fold.train_start
        assert fold.test_end <= plan.total_bars


def test_walk_forward_moves_strictly_forward_in_time() -> None:
    plan = walk_forward_plan(50_000, warmup_bars=100, folds=5)
    starts = [fold.test_start for fold in plan.folds]
    assert starts == sorted(starts)
    for earlier, later in zip(plan.folds, plan.folds[1:], strict=False):
        assert later.test_start >= earlier.test_end


def test_anchored_plan_keeps_all_history_rolling_plan_does_not() -> None:
    anchored = walk_forward_plan(50_000, warmup_bars=100, folds=4, anchored=True)
    rolling = walk_forward_plan(50_000, warmup_bars=100, folds=4, anchored=False)
    assert all(fold.train_start == 0 for fold in anchored.folds)
    assert rolling.folds[-1].train_start > 0
    assert anchored.folds[-1].train_bars > rolling.folds[-1].train_bars


def test_walk_forward_refuses_a_window_it_cannot_purge() -> None:
    with pytest.raises(ValueError, match="INSUFFICIENT_WALK_FORWARD_BARS"):
        walk_forward_plan(600, warmup_bars=200, folds=6)


def test_walk_forward_plan_is_content_addressed() -> None:
    first = walk_forward_plan(20_000, warmup_bars=50, folds=4)
    second = walk_forward_plan(20_000, warmup_bars=50, folds=4)
    third = walk_forward_plan(20_000, warmup_bars=50, folds=5)
    assert first.plan_id == second.plan_id
    assert first.plan_id != third.plan_id


def test_efficiency_flags_a_curve_fit_that_is_still_profitable() -> None:
    rng = np.random.default_rng(21)
    in_sample = tuple(tuple(rng.normal(2.0, 1.0, 100)) for _ in range(5))
    out_sample = tuple(tuple(rng.normal(0.05, 1.0, 100)) for _ in range(5))
    result = walk_forward_efficiency(in_sample, out_sample)
    assert result.out_of_sample_sharpe > 0.0  # still makes money
    assert result.efficiency < 0.5  # but kept almost none of its edge
    assert result.survives is False


def test_efficiency_accepts_a_strategy_that_holds_up() -> None:
    rng = np.random.default_rng(22)
    in_sample = tuple(tuple(rng.normal(0.5, 1.0, 200)) for _ in range(5))
    out_sample = tuple(tuple(rng.normal(0.42, 1.0, 200)) for _ in range(5))
    result = walk_forward_efficiency(in_sample, out_sample)
    assert result.efficiency >= 0.5
    assert result.survives is True


def test_efficiency_is_zero_when_there_was_no_in_sample_edge() -> None:
    """A ratio against a non-positive denominator is meaningless, not infinite."""
    flat = tuple(tuple(np.full(50, 0.0) + np.linspace(-1, 1, 50)) for _ in range(3))
    result = walk_forward_efficiency(flat, flat)
    assert result.efficiency == 0.0


def test_cpcv_enumerates_every_combination_and_counts_paths() -> None:
    plan = combinatorial_purged_plan(60_000, warmup_bars=200, groups=6, test_groups=2)
    assert len(plan) == 15  # C(6, 2)
    assert plan.path_count == 5  # C(6, 2) * 2 / 6
    assert {split.test_groups for split in plan.splits}.__len__() == 15


def test_cpcv_purges_training_data_around_every_test_block() -> None:
    plan = combinatorial_purged_plan(60_000, warmup_bars=500, groups=6, test_groups=2)
    for split in plan.splits:
        train = set(split.train_indices().tolist())
        for start, end in split.test_ranges:
            # Nothing inside a test block, nor in the purge window before it,
            # may survive into training.
            assert not train & set(range(start, end))
            assert not train & set(range(max(0, start - plan.purge_bars), start))


def test_cpcv_train_and_test_never_overlap() -> None:
    plan = combinatorial_purged_plan(40_000, warmup_bars=100, groups=5, test_groups=2)
    for split in plan.splits:
        assert not set(split.train_indices().tolist()) & set(split.test_indices().tolist())
        assert split.train_bars > 0


def test_cpcv_refuses_blocks_too_small_to_absorb_the_gaps() -> None:
    with pytest.raises(ValueError, match="INSUFFICIENT_CPCV_BARS"):
        combinatorial_purged_plan(1_000, warmup_bars=400, groups=6, test_groups=2)


def test_path_distribution_reports_the_worst_path_not_just_the_average() -> None:
    rng = np.random.default_rng(23)
    # Four good paths and one bad one: the mean flatters, the 5th percentile does not.
    paths = (
        *(tuple(rng.normal(0.4, 1.0, 200)) for _ in range(4)),
        tuple(rng.normal(-0.4, 1.0, 200)),
    )
    summary = path_distribution(paths)
    assert summary.mean_sharpe > 0.0
    assert summary.sharpe_p05 < 0.0
    assert summary.robust is False


def test_path_distribution_accepts_uniformly_positive_paths() -> None:
    rng = np.random.default_rng(24)
    summary = path_distribution(tuple(tuple(rng.normal(0.45, 1.0, 300)) for _ in range(5)))
    assert summary.positive_share == 1.0
    assert summary.robust is True
