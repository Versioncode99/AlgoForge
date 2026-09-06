"""Walk-forward analysis: does the strategy keep working as the market moves on?

A single chronological split answers one question once. Walk-forward asks it
repeatedly: fit on a window, test on the window that follows, roll forward, and
see whether out-of-sample performance holds up across regimes rather than in
one lucky stretch.

Every fold carries a **purge** and an **embargo**:

* *Purge* removes the bars immediately before a test window whose feature
  look-back would otherwise overlap it. Without it a warm-up of ``w`` bars lets
  training data see ``w`` bars into the test period.
* *Embargo* removes bars immediately *after* a test window before training
  resumes, because serial correlation makes those bars near-duplicates of what
  was just tested.

Both are López de Prado's remedy for leakage in financial cross-validation, and
both are mandatory here rather than optional: a fold that cannot afford its
purge is an error, not a fold with a smaller gap.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from forge.contracts.hashing import stable_id


@dataclass(frozen=True)
class WalkForwardFold:
    """One train/test pair with its leakage gaps made explicit."""

    index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    purge_bars: int
    embargo_bars: int

    @property
    def train_bars(self) -> int:
        return self.train_end - self.train_start

    @property
    def test_bars(self) -> int:
        return self.test_end - self.test_start


@dataclass(frozen=True)
class WalkForwardPlan:
    """A reproducible set of folds, content-addressed by its own geometry."""

    plan_id: str
    total_bars: int
    folds: tuple[WalkForwardFold, ...]
    anchored: bool
    purge_bars: int
    embargo_bars: int

    def __len__(self) -> int:
        return len(self.folds)


def walk_forward_plan(
    total_bars: int,
    *,
    warmup_bars: int,
    folds: int = 6,
    anchored: bool = True,
    test_fraction: float = 0.2,
    embargo_fraction: float = 0.01,
) -> WalkForwardPlan:
    """Build ``folds`` train/test windows over ``total_bars``.

    ``anchored`` keeps every training window starting at bar 0 so the model
    always sees all available history — the honest default for a strategy meant
    to run indefinitely. Setting it ``False`` gives a fixed-width rolling
    window, which answers the different question of whether recent history
    alone is sufficient.
    """
    if total_bars <= 0:
        raise ValueError("total_bars must be positive")
    if folds < 2:
        raise ValueError("walk-forward needs at least 2 folds")
    if not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction must be between 0 and 1")
    if not 0.0 <= embargo_fraction < 1.0:
        raise ValueError("embargo_fraction must be between 0 and 1")

    purge = max(1, int(warmup_bars))
    embargo = max(0, int(total_bars * embargo_fraction))
    # Reserve the tail for the test windows; the first training block is what
    # remains once every test window and its two gaps are accounted for.
    test_bars = max(1, int(total_bars * test_fraction / folds))
    consumed = folds * (test_bars + purge + embargo)
    initial_train = total_bars - consumed
    minimum_train = max(purge + 10, 50)
    if initial_train < minimum_train:
        required = consumed + minimum_train
        raise ValueError(
            f"INSUFFICIENT_WALK_FORWARD_BARS: {total_bars} available, at least {required} "
            f"required for {folds} folds of {test_bars} test bars with "
            f"{purge}-bar purge and {embargo}-bar embargo"
        )

    built: list[WalkForwardFold] = []
    for fold in range(folds):
        train_end = initial_train + fold * (test_bars + purge + embargo)
        test_start = train_end + purge
        test_end = test_start + test_bars
        if test_end > total_bars:
            break
        train_start = 0 if anchored else max(0, train_end - initial_train)
        built.append(
            WalkForwardFold(
                index=fold,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                purge_bars=purge,
                embargo_bars=embargo,
            )
        )
    if len(built) < 2:
        raise ValueError("walk-forward produced fewer than 2 usable folds")

    payload = {
        "total_bars": total_bars,
        "anchored": anchored,
        "purge_bars": purge,
        "embargo_bars": embargo,
        "folds": [
            [fold.train_start, fold.train_end, fold.test_start, fold.test_end] for fold in built
        ],
    }
    return WalkForwardPlan(
        plan_id=stable_id("walkforward", payload),
        total_bars=total_bars,
        folds=tuple(built),
        anchored=anchored,
        purge_bars=purge,
        embargo_bars=embargo,
    )


@dataclass(frozen=True)
class WalkForwardResult:
    """Aggregate verdict across folds, including how consistent they were."""

    fold_count: int
    in_sample_sharpe: float
    out_of_sample_sharpe: float
    efficiency: float
    positive_folds: int
    consistency: float
    degradation: float

    @property
    def survives(self) -> bool:
        """Profitable out of sample, in a majority of folds, without collapsing.

        Efficiency below 0.5 means the strategy lost more than half its
        in-sample edge the moment it met unseen data — the signature of a
        curve fit, even when the remaining edge is still positive.
        """
        return self.out_of_sample_sharpe > 0.0 and self.consistency > 0.5 and self.efficiency >= 0.5


def walk_forward_efficiency(
    in_sample_returns: tuple[tuple[float, ...], ...],
    out_of_sample_returns: tuple[tuple[float, ...], ...],
) -> WalkForwardResult:
    """Compare per-fold in-sample and out-of-sample performance.

    Efficiency is the ratio of out-of-sample to in-sample Sharpe. At 1.0 the
    strategy performed as well on unseen data as on the data it was built from;
    well below 1.0 the backtest was measuring its own parameters.
    """
    from forge.judge.statistics import per_period_sharpe

    if len(in_sample_returns) != len(out_of_sample_returns):
        raise ValueError("in-sample and out-of-sample fold counts differ")
    if not in_sample_returns:
        raise ValueError("no folds to evaluate")

    in_sharpes = [per_period_sharpe(fold) if len(fold) > 1 else 0.0 for fold in in_sample_returns]
    out_sharpes = [
        per_period_sharpe(fold) if len(fold) > 1 else 0.0 for fold in out_of_sample_returns
    ]
    pooled_in = float(np.mean(in_sharpes))
    pooled_out = float(np.mean(out_sharpes))
    positive = sum(1 for value in out_sharpes if value > 0.0)
    # A non-positive in-sample Sharpe makes the ratio meaningless rather than
    # infinite: there was no edge to retain in the first place.
    efficiency = pooled_out / pooled_in if pooled_in > 0.0 else 0.0
    return WalkForwardResult(
        fold_count=len(in_sharpes),
        in_sample_sharpe=round(pooled_in, 6),
        out_of_sample_sharpe=round(pooled_out, 6),
        efficiency=round(efficiency, 6),
        positive_folds=positive,
        consistency=round(positive / len(out_sharpes), 6),
        degradation=round(pooled_in - pooled_out, 6),
    )
