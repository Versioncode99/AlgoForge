"""Turning one backtest into a distribution of outcomes.

A backtest is a single draw. The equity curve you get is one path the strategy
could have taken through one ordering of its trades, and reading a maximum
drawdown off it is reading one sample from a distribution as though it were the
distribution.

Two resamplers live here, and the difference between them is the point.

**IID** shuffles trades independently. It is the standard baseline and it is
wrong in a specific, knowable direction: real losses cluster, because the
conditions that cause them persist. A strategy that loses in high volatility
does not lose one trade in ten scattered evenly — it loses eight in a row while
volatility is high, and that run is what actually breaches a drawdown limit. IID
resampling breaks exactly that structure, so it reports a drawdown distribution
that is too kind, and it is too kind precisely for the strategies that most need
the warning.

**Regime-aware** resampling keeps the clustering. It walks a Markov chain fitted
to the observed regime transitions and draws each trade from the pool of trades
that actually happened in the regime it lands in. Runs of bad conditions survive
resampling, because the chain produces runs of bad conditions.

Reporting both is deliberate. The *gap* between them is the interesting number:
if regime-aware drawdown is much worse than IID, the strategy's risk lives in
its clustering, and no amount of IID Monte Carlo would ever have shown it.

Nothing here invents a trade. Every resampled path is built from the strategy's
own realised trades; resampling changes the order, never the values.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from pydantic import Field

from forge.analytics.regime import MEASURED, Regime, RegimeSeries, TradeRegime, transition_matrix
from forge.contracts.models import FrozenModel


class PathStatistics(FrozenModel):
    """The distribution one resampler produced."""

    method: str
    paths: int
    trades_per_path: int
    seed: int
    median_final: float
    p05_final: float
    p95_final: float
    median_max_drawdown: float
    p95_max_drawdown: float
    #: Share of paths whose final equity is below zero.
    loss_probability: float
    #: Share of paths that spent any time below their starting equity.
    underwater_probability: float
    var_95: float
    cvar_95: float
    #: A handful of paths for drawing, not the whole set.
    sample_paths: tuple[tuple[float, ...], ...] = ()
    median_path: tuple[float, ...] = ()
    p05_path: tuple[float, ...] = ()
    p95_path: tuple[float, ...] = ()


class ResampleComparison(FrozenModel):
    regime_aware: PathStatistics | None
    iid: PathStatistics
    #: regime-aware p95 drawdown minus IID p95 drawdown. Positive means the
    #: clustering makes things worse than an IID study would have suggested.
    drawdown_gap: float | None = None
    pooled: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    trade_count: int = 0
    provenance: dict[str, str] = Field(default_factory=dict)


def _drawdowns(paths: np.ndarray) -> np.ndarray:
    """Max peak-to-trough of each cumulative path, including the zero start."""
    with_start = np.concatenate([np.zeros((paths.shape[0], 1)), paths], axis=1)
    peaks = np.maximum.accumulate(with_start, axis=1)
    span: np.ndarray = (peaks - with_start).max(axis=1)
    return span


def _statistics(
    method: str, draws: np.ndarray, seed: int, keep: int = 40
) -> PathStatistics:
    equity = np.cumsum(draws, axis=1)
    finals = equity[:, -1]
    drawdown = _drawdowns(equity)
    order = np.argsort(finals)
    count = equity.shape[0]

    def at(fraction: float) -> np.ndarray:
        row: np.ndarray = equity[order[min(count - 1, max(0, int(fraction * count)))]]
        return row

    return PathStatistics(
        method=method,
        paths=count,
        trades_per_path=int(equity.shape[1]),
        seed=seed,
        median_final=round(float(np.median(finals)), 4),
        p05_final=round(float(np.percentile(finals, 5)), 4),
        p95_final=round(float(np.percentile(finals, 95)), 4),
        median_max_drawdown=round(float(np.median(drawdown)), 4),
        p95_max_drawdown=round(float(np.percentile(drawdown, 95)), 4),
        loss_probability=round(float((finals < 0).mean()), 4),
        underwater_probability=round(float((equity.min(axis=1) < 0).mean()), 4),
        var_95=round(float(-np.percentile(finals, 5)), 4),
        cvar_95=round(float(-finals[finals <= np.percentile(finals, 5)].mean()), 4),
        sample_paths=tuple(
            tuple(round(float(v), 4) for v in equity[index])
            for index in order[:: max(1, count // keep)][:keep]
        ),
        median_path=tuple(round(float(v), 4) for v in at(0.5)),
        p05_path=tuple(round(float(v), 4) for v in at(0.05)),
        p95_path=tuple(round(float(v), 4) for v in at(0.95)),
    )


def _iid(pnl: np.ndarray, paths: int, length: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return pnl[rng.integers(0, pnl.size, size=(paths, length))]


def _chain(series: RegimeSeries) -> tuple[np.ndarray, np.ndarray]:
    """Row-normalised transition probabilities, and the stationary start.

    A regime never observed leaving itself gets a self-loop rather than a row of
    zeros: a zero row would make the chain stall, and stalling would silently
    turn a four-regime simulation into a one-regime one.
    """
    counts = np.array(transition_matrix(series), dtype=np.float64)
    totals = counts.sum(axis=1)
    probabilities = np.zeros_like(counts)
    for row in range(counts.shape[0]):
        if totals[row] > 0:
            probabilities[row] = counts[row] / totals[row]
        else:
            probabilities[row, row] = 1.0
    occupancy = counts.sum(axis=1)
    start = occupancy / occupancy.sum() if occupancy.sum() > 0 else np.full(4, 0.25)
    return probabilities, start


def compare(
    trades: list[Any],
    attributions: list[TradeRegime],
    series: RegimeSeries,
    *,
    paths: int = 2000,
    seed: int = 20260908,
    attribution: str = "entry",
) -> ResampleComparison:
    """Resample the strategy's own trades, with and without regime structure."""
    pnl = np.array([float(t.net_pnl) for t in trades], dtype=np.float64)
    warnings: list[str] = []
    if pnl.size < 2:
        raise ValueError("resampling needs at least two trades")

    length = int(pnl.size)
    iid = _statistics("iid", _iid(pnl, paths, length, seed), seed)

    # Pool each regime's trades. A regime with too few trades cannot supply a
    # draw of its own; rather than inventing one, it borrows its sibling at the
    # same volatility level, and the substitution is reported.
    by_id = {item.trade_id: item for item in attributions}
    pools: dict[Regime, list[float]] = {regime: [] for regime in MEASURED}
    for trade, value in zip(trades, pnl, strict=True):
        item = by_id.get(str(trade.trade_id))
        regime = getattr(item, attribution, Regime.UNCLASSIFIED) if item else Regime.UNCLASSIFIED
        if regime is not Regime.UNCLASSIFIED:
            pools[regime].append(float(value))

    sibling = {
        Regime.BULL_LOW: Regime.BEAR_LOW,
        Regime.BEAR_LOW: Regime.BULL_LOW,
        Regime.BULL_HIGH: Regime.BEAR_HIGH,
        Regime.BEAR_HIGH: Regime.BULL_HIGH,
    }
    #: Below this a pool is not a sample of anything; it is a couple of trades.
    MIN_POOL = 5
    pooled: list[str] = []
    resolved: dict[Regime, np.ndarray] = {}
    for regime in MEASURED:
        own = pools[regime]
        if len(own) >= MIN_POOL:
            resolved[regime] = np.array(own, dtype=np.float64)
            continue
        borrowed = own + pools[sibling[regime]]
        if len(borrowed) >= MIN_POOL:
            resolved[regime] = np.array(borrowed, dtype=np.float64)
            pooled.append(
                f"{regime.value}: {len(own)} trade(s) of its own — pooled with "
                f"{sibling[regime].value} to reach {len(borrowed)}."
            )
        else:
            resolved[regime] = pnl
            pooled.append(
                f"{regime.value}: {len(own)} trade(s) of its own and too few in its "
                "sibling — drawn from every trade instead. Its share of the "
                "simulation carries no regime information."
            )

    if all(len(pools[regime]) < MIN_POOL for regime in MEASURED):
        warnings.append(
            "No regime held enough trades to resample from. The regime-aware "
            "simulation would be the IID one wearing a different name, so it was "
            "not run."
        )
        return ResampleComparison(
            regime_aware=None,
            iid=iid,
            pooled=tuple(pooled),
            warnings=tuple(warnings),
            trade_count=length,
            provenance={
                "series_fingerprint": series.fingerprint,
                "attribution": attribution,
                "seed": str(seed),
            },
        )

    probabilities, start = _chain(series)
    rng = np.random.default_rng(seed + 1)
    draws = np.empty((paths, length), dtype=np.float64)
    order = list(MEASURED)
    # One chain per path, walked together so the regime structure is preserved
    # within a path and independent across paths.
    state = rng.choice(len(order), size=paths, p=start)
    for step in range(length):
        for index, regime in enumerate(order):
            members = state == index
            taken = int(members.sum())
            if not taken:
                continue
            pool = resolved[regime]
            draws[members, step] = pool[rng.integers(0, pool.size, size=taken)]
        # Advance every chain one step, each by its own row's probabilities.
        uniform = rng.random(paths)
        cumulative = probabilities.cumsum(axis=1)[state]
        state = (uniform[:, None] > cumulative).sum(axis=1).clip(0, len(order) - 1)

    regime_aware = _statistics("regime_aware", draws, seed + 1)
    if pooled:
        warnings.append(
            "Some regimes were too thin to resample from on their own; see `pooled` "
            "for exactly which, and what stood in for them."
        )
    gap = round(regime_aware.p95_max_drawdown - iid.p95_max_drawdown, 4)
    if gap > 0:
        warnings.append(
            f"Preserving regime clustering worsens the 95th-percentile drawdown by "
            f"{gap:,.0f}. An IID study of this strategy understates its drawdown."
        )

    return ResampleComparison(
        regime_aware=regime_aware,
        iid=iid,
        drawdown_gap=gap,
        pooled=tuple(pooled),
        warnings=tuple(warnings),
        trade_count=length,
        provenance={
            "series_fingerprint": series.fingerprint,
            "attribution": attribution,
            "seed": str(seed),
        },
    )
