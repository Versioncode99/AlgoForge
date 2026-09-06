"""Combinatorial Purged Cross-Validation (López de Prado, *AFML* ch. 12).

Walk-forward gives one out-of-sample path: fit, test, roll, once. That single
path is itself a sample of size one, and judging a strategy by it is how a
lucky ordering of history gets mistaken for an edge.

CPCV instead cuts the series into ``groups`` contiguous blocks and tests every
combination of ``test_groups`` of them at once, training on the rest. With
``N`` groups and ``k`` held out there are ``C(N, k)`` splits, which reassemble
into ``C(N, k) · k / N`` distinct out-of-sample **paths** through history — a
distribution of outcomes rather than a point estimate. A strategy whose edge
depends on where the split happened to fall shows up immediately as spread
across those paths.

Purging and embargoing are applied around every test block, not just at the
two boundaries of a single split, which is what makes the combinatorial version
sound: each of the many train/test frontiers is a leakage opportunity.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import comb

import numpy as np

from forge.contracts.hashing import stable_id


@dataclass(frozen=True)
class CombinatorialSplit:
    """One CPCV split: several disjoint test blocks and the purged remainder."""

    index: int
    test_groups: tuple[int, ...]
    test_ranges: tuple[tuple[int, int], ...]
    train_ranges: tuple[tuple[int, int], ...]

    @property
    def test_bars(self) -> int:
        return sum(end - start for start, end in self.test_ranges)

    @property
    def train_bars(self) -> int:
        return sum(end - start for start, end in self.train_ranges)

    def test_indices(self) -> np.ndarray:
        return _concatenate(self.test_ranges)

    def train_indices(self) -> np.ndarray:
        return _concatenate(self.train_ranges)


@dataclass(frozen=True)
class CombinatorialPlan:
    """Every split, plus the number of backtest paths they reconstruct into."""

    plan_id: str
    total_bars: int
    groups: int
    test_groups: int
    purge_bars: int
    embargo_bars: int
    splits: tuple[CombinatorialSplit, ...]

    @property
    def path_count(self) -> int:
        """Distinct out-of-sample histories these splits can be assembled into."""
        return comb(self.groups, self.test_groups) * self.test_groups // self.groups

    def __len__(self) -> int:
        return len(self.splits)


def _concatenate(ranges: tuple[tuple[int, int], ...]) -> np.ndarray:
    if not ranges:
        return np.empty(0, dtype=np.int64)
    return np.concatenate([np.arange(start, end, dtype=np.int64) for start, end in ranges])


def combinatorial_purged_plan(
    total_bars: int,
    *,
    warmup_bars: int,
    groups: int = 6,
    test_groups: int = 2,
    embargo_fraction: float = 0.01,
) -> CombinatorialPlan:
    """Enumerate all ``C(groups, test_groups)`` purged splits.

    ``groups=6, test_groups=2`` gives 15 splits and 5 reconstructed paths, which
    is the usual starting point: enough paths to see a distribution without the
    combinatorial cost of a finer partition.
    """
    if total_bars <= 0:
        raise ValueError("total_bars must be positive")
    if groups < 3:
        raise ValueError("CPCV needs at least 3 groups")
    if not 0 < test_groups < groups:
        raise ValueError("test_groups must be between 1 and groups - 1")
    if not 0.0 <= embargo_fraction < 1.0:
        raise ValueError("embargo_fraction must be between 0 and 1")

    purge = max(1, int(warmup_bars))
    embargo = max(0, int(total_bars * embargo_fraction))
    boundaries = np.linspace(0, total_bars, groups + 1, dtype=np.int64)
    blocks = tuple((int(boundaries[i]), int(boundaries[i + 1])) for i in range(groups))
    smallest = min(end - start for start, end in blocks)
    if smallest <= purge + embargo:
        required = (purge + embargo + 1) * groups
        raise ValueError(
            f"INSUFFICIENT_CPCV_BARS: {total_bars} bars over {groups} groups leaves blocks of "
            f"{smallest}, which cannot absorb a {purge}-bar purge and {embargo}-bar embargo; "
            f"at least {required} bars required"
        )

    splits: list[CombinatorialSplit] = []
    for index, selected in enumerate(combinations(range(groups), test_groups)):
        chosen = set(selected)
        test_ranges = tuple(blocks[group] for group in selected)
        # Drop the purge window before each test block and the embargo after it,
        # from whichever training blocks those windows fall inside.
        forbidden = np.zeros(total_bars, dtype=bool)
        for start, end in test_ranges:
            forbidden[max(0, start - purge) : min(total_bars, end + embargo)] = True
        train_ranges: list[tuple[int, int]] = []
        for group, (start, end) in enumerate(blocks):
            if group in chosen:
                continue
            train_ranges.extend(_usable_spans(forbidden, start, end))
        if not train_ranges:
            raise ValueError(f"split {index} has no training data left after purging")
        splits.append(
            CombinatorialSplit(
                index=index,
                test_groups=selected,
                test_ranges=test_ranges,
                train_ranges=tuple(train_ranges),
            )
        )

    payload = {
        "total_bars": total_bars,
        "groups": groups,
        "test_groups": test_groups,
        "purge_bars": purge,
        "embargo_bars": embargo,
    }
    return CombinatorialPlan(
        plan_id=stable_id("cpcv", payload),
        total_bars=total_bars,
        groups=groups,
        test_groups=test_groups,
        purge_bars=purge,
        embargo_bars=embargo,
        splits=tuple(splits),
    )


def _usable_spans(forbidden: np.ndarray, start: int, end: int) -> list[tuple[int, int]]:
    """Contiguous stretches of ``[start, end)`` that survive purge and embargo."""
    spans: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        if forbidden[cursor]:
            cursor += 1
            continue
        run = cursor
        while run < end and not forbidden[run]:
            run += 1
        spans.append((cursor, run))
        cursor = run
    return spans


@dataclass(frozen=True)
class PathDistribution:
    """The spread of outcomes across reconstructed backtest paths."""

    paths: int
    mean_sharpe: float
    median_sharpe: float
    sharpe_p05: float
    sharpe_p95: float
    positive_share: float
    dispersion: float

    @property
    def robust(self) -> bool:
        """Positive on the great majority of paths, not merely on average.

        The 5th percentile carries the weight here: a strategy whose worst
        reconstructed history still makes money is a different proposition from
        one whose average does.
        """
        return self.positive_share >= 0.8 and self.sharpe_p05 > 0.0


def path_distribution(path_returns: tuple[tuple[float, ...], ...]) -> PathDistribution:
    """Summarise per-path returns produced by running a CPCV plan."""
    from forge.judge.statistics import per_period_sharpe

    if not path_returns:
        raise ValueError("no paths to summarise")
    sharpes = np.asarray(
        [per_period_sharpe(path) if len(path) > 1 else 0.0 for path in path_returns],
        dtype=np.float64,
    )
    p05, median, p95 = (float(value) for value in np.percentile(sharpes, (5, 50, 95)))
    return PathDistribution(
        paths=int(sharpes.size),
        mean_sharpe=round(float(np.mean(sharpes)), 6),
        median_sharpe=round(median, 6),
        sharpe_p05=round(p05, 6),
        sharpe_p95=round(p95, 6),
        positive_share=round(float(np.mean(sharpes > 0.0)), 6),
        dispersion=round(float(np.std(sharpes, ddof=1)) if sharpes.size > 1 else 0.0, 6),
    )
