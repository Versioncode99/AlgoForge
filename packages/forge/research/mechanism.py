"""G9: does the declared mechanism actually do the work?

Every `StrategySpec` carries a `hypothesis` and a `falsifiable_prediction`. G9
is named "Mechanism" and its rule read "mechanism not falsified" — but
`mechanism_aligned` was a dataclass default of `True` that no call site ever
overrode, so nothing was ever falsified because nothing was ever tested.

Leaving it permanently INCONCLUSIVE would be honest and useless: no candidate
could ever pass, the burn-once holdout would never open, and the engine's whole
promotion path would be dead code. So the gate needs a real test, and this is
it.

## The test

Every strategy here makes the same underlying claim, whatever its family:

    the entries are chosen by the declared signal, and *when* it chooses to
    enter carries information.

The null hypothesis is the direct negation:

    the same trades — same directions, same holding periods, same costs —
    entered at *random* times would have done just as well.

So the control resamples entry timing and nothing else. For each of `samples`
draws, the observed multiset of `(direction, bars_held)` is placed at uniformly
random entry points in the same bar series, and the P&L is recomputed with the
same point value and the same round-trip cost. The p-value is the share of
control draws that matched or beat the observed net P&L, with the conventional
`+1/+1` correction so it can never report zero.

## Why this and not something else

* **Direction is held fixed**, so this is not re-testing whether the strategy
  picked the right side — G4 and G6 already cover that. A long-only strategy in
  a rising market is compared against long-only random entries in the same
  rising market, which is the comparison that isolates timing.
* **Holding period is held fixed**, so a strategy is not rewarded for merely
  staying in the market longer.
* **Costs are applied identically**, so the control cannot look worse simply by
  being charged differently.
* It is distinct from G6's permutation test, which shuffles the *signs of
  realised returns*. That asks "could this P&L series be noise?". This asks
  "could these entry times have been chosen by a coin?" — a different question,
  and the one the word *mechanism* refers to.

## What it does not establish

Passing means the entry timing carried information over this series. It does
**not** confirm the stated economic story — that a volatility-shock filter works
*because* of delayed continuation rather than for some correlated reason. No
statistical test can confirm a mechanism narrative; it can only fail to
falsify the prediction the narrative makes, which is exactly what the gate
claims and no more.

Control draws may overlap in time where the strategy's own trades could not.
That leaves the mean of the null unbiased and its variance slightly understated,
which makes the test marginally *conservative* — harder to pass, not easier.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

# Conventional significance bar, matching G6's permutation test.
MECHANISM_ALPHA = 0.05

# Below this many trades the control distribution is too coarse to say anything:
# with five trades the smallest attainable p-value is dominated by how few ways
# there are to arrange them. Reported as unmeasured rather than as a pass.
MINIMUM_TRADES_FOR_CONTROL = 20

# Control draws. Enough that the p-value's own resolution (1/(N+1)) is well
# below alpha, and cheap because a draw is array indexing, not a backtest.
DEFAULT_CONTROL_SAMPLES = 400


class HasTrade(Protocol):
    """The part of `Trade` this module needs."""

    direction: int
    bars_held: int
    net_pnl: float


@dataclass(frozen=True)
class MechanismTest:
    """Whether random entry timing would have done as well."""

    observed_pnl: float
    control_median: float
    control_p95: float
    p_value: float
    samples: int
    trades: int
    reason: str

    @property
    def aligned(self) -> bool | None:
        """``None`` when the test could not be run — never a pass by default."""
        if self.samples == 0:
            return None
        return self.p_value < MECHANISM_ALPHA

    def as_dict(self) -> dict[str, Any]:
        return {
            "observed_pnl": round(self.observed_pnl, 4),
            "control_median": round(self.control_median, 4),
            "control_p95": round(self.control_p95, 4),
            "p_value": round(self.p_value, 6),
            "samples": self.samples,
            "trades": self.trades,
            "aligned": self.aligned,
            "reason": self.reason,
            "null": ("the same directions and holding periods entered at uniformly random times"),
            "does_not_assert": (
                "that the stated economic story is the cause; only that entry timing "
                "carried information over this series"
            ),
        }


def _unmeasured(reason: str, trades: int) -> MechanismTest:
    return MechanismTest(
        observed_pnl=0.0,
        control_median=0.0,
        control_p95=0.0,
        p_value=1.0,
        samples=0,
        trades=trades,
        reason=reason,
    )


def entry_timing_control(
    trades: Sequence[HasTrade],
    opens: Sequence[float] | np.ndarray,
    *,
    point_value: float,
    round_trip_cost: float,
    warmup_bars: int,
    seed: int,
    samples: int = DEFAULT_CONTROL_SAMPLES,
) -> MechanismTest:
    """Compare the run against random entry timing, holding everything else fixed.

    ``opens`` must be the same open-price series the run filled against, because
    the control has to be able to have taken the strategy's own trades.
    """
    count = len(trades)
    if count < MINIMUM_TRADES_FOR_CONTROL:
        return _unmeasured(f"ONLY_{count}_TRADES", count)

    prices = np.asarray(opens, dtype=np.float64).ravel()
    directions = np.array([int(trade.direction) for trade in trades], dtype=np.int64)
    holds = np.array([max(1, int(trade.bars_held)) for trade in trades], dtype=np.int64)
    observed = float(sum(float(trade.net_pnl) for trade in trades))

    # An entry must leave room for its own holding period inside the series.
    first = max(1, int(warmup_bars))
    last = prices.size - holds.max() - 1
    if last <= first:
        return _unmeasured("SERIES_TOO_SHORT_FOR_CONTROL", count)

    rng = np.random.default_rng(seed)
    totals = np.empty(samples, dtype=np.float64)
    for index in range(samples):
        entries = rng.integers(first, last, size=count)
        exits = entries + holds
        gross = (prices[exits] - prices[entries]) * directions * point_value
        totals[index] = float(np.sum(gross - round_trip_cost))

    # Ties count against significance, and "tie" has to mean *numerically*
    # equal rather than bit-equal. On a series with little dispersion — a near
    # linear drift, say — every control draw earns almost exactly what the
    # strategy earned, and the two differ only in the last few bits of the
    # float. A strict `>=` then counts zero control draws as matching and
    # reports p = 1/(N+1), declaring rounding error to be evidence of timing
    # skill. Scaling the tolerance to the magnitudes involved makes a point-mass
    # null read as p ≈ 1, which is the honest answer.
    tolerance = 1e-9 * max(1.0, abs(observed), float(np.max(np.abs(totals))))
    beaten = int(np.sum(totals >= observed - tolerance))
    # +1/+1, so a control that never beat the strategy reports 1/(N+1) rather
    # than the impossible claim of p = 0.
    p_value = (beaten + 1) / (samples + 1)
    return MechanismTest(
        observed_pnl=observed,
        control_median=float(np.median(totals)),
        control_p95=float(np.percentile(totals, 95)),
        p_value=p_value,
        samples=samples,
        trades=count,
        reason=(
            f"P_{p_value:.4f}_VS_RANDOM_ENTRY"
            if p_value < MECHANISM_ALPHA
            else f"NOT_BETTER_THAN_RANDOM_ENTRY_P_{p_value:.4f}"
        ),
    )
