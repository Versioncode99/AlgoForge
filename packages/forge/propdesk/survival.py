"""What the appetite meter actually selects, and why it is not a size dial.

A meter that maps position 52 to "two contracts" is a slider with extra steps.
For the meter to mean anything it has to change *what question is being
answered*, not scale an answer that was already computed.

It changes two things, and both are statistical:

**How far into the tail you size against.** Conservative sizes against the
99th-percentile bootstrapped drawdown; aggressive against the 75th. On a
strategy whose drawdown distribution has a thin tail those two numbers are
nearly the same, and the meter barely changes what it sizes against. On a
fat-tailed one they differ by a factor of several, so the same turn of the meter
costs far more. The meter's effect is therefore a property of the strategy's
measured behaviour rather than of the meter, and
`test_the_same_appetite_buys_fewer_contracts_on_a_fat_tailed_strategy` holds it.

**What share of the buffer is at stake**, interpolated between the operator's
own minimum and maximum.

The distribution comes from a stationary block bootstrap over out-of-sample
per-contract daily PnL: contiguous runs are drawn rather than individual days,
so a strategy that loses in streaks keeps its streaks. An i.i.d. resample would
average the streaks away and report a drawdown tail that the strategy does not
have — flattering exactly the strategies most likely to end an account.

**There is no fallback.** With no series, or one too short to have a tail,
`derive_band` returns `Unmeasurable` naming the shortfall. The caller falls to
the operator's minimum. It does not assume normality, substitute a default, or
scale something from a different strategy.
"""

from __future__ import annotations

import numpy as np
from pydantic import Field

from forge.contracts.models import FrozenModel
from forge.propdesk.risk import RiskBoundaries

#: Below this many out-of-sample days there is no tail to estimate. Thirty is
#: the same threshold `forge.propdesk.allocation.MIN_OOS_TRADES` uses, and for
#: the same reason: a quantile over fewer points is an order statistic of a
#: handful of outcomes.
MIN_SERIES_DAYS = 30

#: Contiguous days per bootstrap draw. Five is one trading week — long enough
#: that a losing run survives resampling, short enough that the resample is not
#: just the original series in a different order.
BLOCK_DAYS = 5

#: How many days a drawdown is measured over. A month of trading: the horizon
#: on which a prop account's trailing floor is actually decided.
HORIZON_DAYS = 21

#: Paths per estimate. Enough for a stable 99th percentile without making an
#: interactive recalculation slow.
PATHS = 2000

#: The tail the meter travels across. Conservative sizes against a drawdown
#: exceeded on 1% of horizons; aggressive against one exceeded on 25%.
CONSERVATIVE_QUANTILE = 0.99
AGGRESSIVE_QUANTILE = 0.75


class Unmeasurable(FrozenModel):
    """No band could be derived, and exactly why not.

    Carries what would fix it, because "insufficient data" without a number is
    not something an operator can act on.
    """

    reason: str
    observed_days: int = 0
    required_days: int = MIN_SERIES_DAYS

    @property
    def shortfall(self) -> int:
        return max(0, self.required_days - self.observed_days)


class DrawdownDistribution(FrozenModel):
    """Bootstrapped worst-drawdown-per-horizon, per contract, in currency.

    Quantiles rather than a fitted distribution: the shape is the finding, and
    fitting one would replace a measurement with an assumption at precisely the
    point where the assumption matters most.
    """

    observed_days: int
    paths: int
    horizon_days: int
    block_days: int
    p50: float
    p75: float
    p90: float
    p95: float
    p99: float
    #: Worst single horizon seen across the paths. Not used for sizing — a
    #: maximum over a sample is not a quantile — but shown, because an operator
    #: choosing an aggressive setting should see it.
    worst: float

    @property
    def tail_ratio(self) -> float:
        """p99 over p75. One means a thin tail; large means a fat one.

        This is the number that decides how much the meter can move, so it is
        named rather than left implicit in a division at the call site.
        """
        return self.p99 / self.p75 if self.p75 > 0 else 1.0

    def quantile(self, q: float) -> float:
        """The drawdown at one of the quantiles actually estimated.

        Interpolating between them would invent precision the bootstrap does not
        have, so the nearest estimated quantile is used and the caller is not
        offered a continuum that does not exist.
        """
        points = ((0.5, self.p50), (0.75, self.p75), (0.9, self.p90),
                  (0.95, self.p95), (0.99, self.p99))
        return min(points, key=lambda item: abs(item[0] - q))[1]


class RiskBand(FrozenModel):
    """The feasible range the appetite travels across, and where it landed."""

    appetite: int = Field(ge=0, le=100)
    #: The quantile this appetite sizes against.
    quantile: float
    #: The drawdown per contract at that quantile, in account currency.
    drawdown_per_contract: float
    #: Share of buffer at stake at this appetite, before any driver cuts it.
    target_fraction: float
    #: What the two ends of the operator's own range would have given, so the
    #: screen can draw the meter against something real.
    conservative_fraction: float
    aggressive_fraction: float
    #: Contracts the band pays for at the current buffer, by the same arithmetic
    #: the allocator uses. Reported so the meter can show a consequence rather
    #: than a percentage nobody can picture.
    contracts: int
    distribution: DrawdownDistribution

    @property
    def moves_with_the_meter(self) -> bool:
        """Whether the meter's two ends differ in contracts on this strategy."""
        return self.conservative_fraction != self.aggressive_fraction


def _max_drawdown(path: np.ndarray) -> float:
    """Worst peak-to-trough of a cumulative PnL path, as a positive number."""
    equity = np.cumsum(path)
    peak = np.maximum.accumulate(np.concatenate(([0.0], equity)))[1:]
    return float(np.max(peak - equity))


def _block_bootstrap(
    series: np.ndarray, length: int, rng: np.random.Generator, block: int
) -> np.ndarray:
    """Stationary block bootstrap: contiguous runs, wrapping at the end.

    Deliberately the same construction as `forge.prop.engine._block_bootstrap`.
    It is reimplemented rather than imported because that one is private to the
    prop simulator and importing a private helper across packages is how two
    modules end up sharing a constraint neither of them declared.
    """
    if series.size <= 1:
        return np.repeat(series, length)[:length]
    block = max(1, min(block, series.size))
    out = np.empty(length, dtype=float)
    filled = 0
    while filled < length:
        start = int(rng.integers(0, series.size))
        take = min(block, length - filled)
        out[filled : filled + take] = series[np.arange(start, start + take) % series.size]
        filled += take
    return out


def drawdown_distribution(
    daily_pnl: tuple[float, ...],
    *,
    seed: int = 20260911,
    paths: int = PATHS,
    horizon_days: int = HORIZON_DAYS,
    block_days: int = BLOCK_DAYS,
) -> DrawdownDistribution | Unmeasurable:
    """Bootstrap the worst-drawdown distribution for one contract.

    `daily_pnl` is out-of-sample, per contract, in account currency. In-sample
    figures must not be passed: the parameter name says so and the caller that
    supplies them is `forge.propdesk.scaling`, which reads them from
    `StrategyHealth`, whose own field is documented out-of-sample only.
    """
    series = np.asarray(daily_pnl, dtype=float)
    if series.size < MIN_SERIES_DAYS:
        return Unmeasurable(
            reason=(
                f"{series.size} out-of-sample days is not enough to estimate a drawdown "
                f"tail; {MIN_SERIES_DAYS} is the minimum"
            ),
            observed_days=int(series.size),
        )
    if not np.isfinite(series).all():
        return Unmeasurable(
            reason="the out-of-sample series contains values that are not finite",
            observed_days=int(series.size),
        )
    if float(np.ptp(series)) == 0.0:
        return Unmeasurable(
            reason=(
                "every out-of-sample day is identical, so the series has no variation "
                "to bootstrap a drawdown from"
            ),
            observed_days=int(series.size),
        )
    rng = np.random.default_rng(seed)
    draws = np.array(
        [
            _max_drawdown(_block_bootstrap(series, horizon_days, rng, block_days))
            for _ in range(paths)
        ]
    )
    return DrawdownDistribution(
        observed_days=int(series.size),
        paths=paths,
        horizon_days=horizon_days,
        block_days=block_days,
        p50=float(np.percentile(draws, 50)),
        p75=float(np.percentile(draws, 75)),
        p90=float(np.percentile(draws, 90)),
        p95=float(np.percentile(draws, 95)),
        p99=float(np.percentile(draws, 99)),
        worst=float(draws.max()),
    )


def appetite_quantile(appetite: int) -> float:
    """Which tail quantile an appetite sizes against.

    Linear from the conservative end to the aggressive one. The non-linearity
    that matters is in the *distribution*, not in this mapping: turning the
    meter is a constant walk across quantiles, and what that costs depends on
    how fat the strategy's tail is.
    """
    span = CONSERVATIVE_QUANTILE - AGGRESSIVE_QUANTILE
    return CONSERVATIVE_QUANTILE - span * (appetite / 100.0)


def derive_band(
    *,
    appetite: int,
    boundaries: RiskBoundaries,
    buffer: float | None,
    daily_pnl: tuple[float, ...],
    seed: int = 20260911,
) -> RiskBand | Unmeasurable:
    """The band this appetite selects on this strategy and this account.

    Returns `Unmeasurable` rather than a conservative guess when anything it
    needs is missing. A guess here would be the most dangerous kind: plausible,
    unlabelled, and directly multiplied by the account's buffer.
    """
    if buffer is None:
        return Unmeasurable(reason="this account's buffer to its loss floor is not known")
    if buffer <= 0:
        return Unmeasurable(reason=f"the account has no buffer left ({buffer:,.2f})")
    distribution = drawdown_distribution(daily_pnl, seed=seed)
    if isinstance(distribution, Unmeasurable):
        return distribution

    quantile = appetite_quantile(appetite)
    drawdown = distribution.quantile(quantile)
    if drawdown <= 0:
        return Unmeasurable(
            reason=(
                "the bootstrapped drawdown at this quantile is zero, so no number of "
                "contracts would be bounded by it"
            ),
            observed_days=distribution.observed_days,
        )

    fraction = boundaries.minimum_fraction + boundaries.span * (appetite / 100.0)
    conservative = _fraction_for(0, boundaries)
    aggressive = _fraction_for(100, boundaries)
    contracts = int((buffer * fraction) // drawdown)
    return RiskBand(
        appetite=appetite,
        quantile=quantile,
        drawdown_per_contract=drawdown,
        target_fraction=fraction,
        conservative_fraction=conservative,
        aggressive_fraction=aggressive,
        contracts=max(0, min(contracts, boundaries.max_contracts)),
        distribution=distribution,
    )


def _fraction_for(appetite: int, boundaries: RiskBoundaries) -> float:
    return boundaries.minimum_fraction + boundaries.span * (appetite / 100.0)
