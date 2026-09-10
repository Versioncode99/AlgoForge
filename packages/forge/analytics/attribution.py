"""Where did the return come from, and what risk generated it?

Two questions, and a performance screen that answers only the first is a
scoreboard. This module produces both: a decomposition of P&L by strategy,
instrument and sector, and the risk statistics that put the number in context.

**It reuses the judge's statistics rather than growing its own.** Sharpe,
Sortino and drawdown already exist in `forge.judge`, they are already tested, and
a second implementation here would eventually disagree with the first — at which
point a strategy's Sharpe would depend on which screen you were looking at.

**Attribution that cannot be computed is absent, not zero.** No factor model is
installed, so factor attribution appears only when the caller supplies loadings,
and otherwise reports that no model is configured. Benchmark comparison appears
only when a benchmark series is supplied and long enough to regress against. The
alternative — a factor panel of zeros — reads as "no factor exposure", which is a
finding, and it would be one nobody measured.

**Execution attribution measures against arrival price.** That is the honest
comparison for a fill: what the price was when the decision was made, against
what was paid. On simulated fills it measures the simulator's own slippage model,
which is stated rather than presented as a market observation.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from forge.contracts.models import FrozenModel
from forge.judge.metrics import max_drawdown
from forge.judge.statistics import annualised_sharpe, per_period_sharpe, sortino_ratio

#: Below this, a regression against a benchmark is not an estimate of beta. The
#: same reasoning as the judge's trial-count floors: a slope through eleven
#: points has a standard error nobody would act on.
MINIMUM_REGRESSION_OBSERVATIONS = 24


class Contribution(FrozenModel):
    """One line of the decomposition."""

    key: str
    label: str
    pnl: float
    share: float
    trades: int = 0
    costs: float = 0.0


class RiskStatistics(FrozenModel):
    periods: int
    total_pnl: float
    mean: float
    volatility: float
    sharpe: float | None
    annualised_sharpe: float | None
    sortino: float | None
    max_drawdown: float
    hit_rate: float | None
    #: `None` when there is no losing period at all — which is a fact about a
    #: short sample, not an infinite profit factor.
    profit_factor: float | None
    note: str = ""


class BenchmarkComparison(FrozenModel):
    benchmark: str
    observations: int
    beta: float | None
    alpha: float | None
    correlation: float | None
    tracking_error: float | None
    information_ratio: float | None
    note: str = ""


class ExecutionAttribution(FrozenModel):
    fills: int
    #: Signed cost against arrival price, in currency. Positive means the fills
    #: were worse than the arrival price.
    implementation_shortfall: float
    commission: float
    modelled_slippage: float
    #: Shortfall per contract, which is the number that compares across sizes.
    shortfall_per_contract: float | None
    simulated: bool
    note: str = ""


class PerformanceReport(FrozenModel):
    statistics: RiskStatistics
    by_strategy: tuple[Contribution, ...]
    by_symbol: tuple[Contribution, ...]
    by_sector: tuple[Contribution, ...]
    factor: tuple[Contribution, ...] = ()
    benchmark: BenchmarkComparison | None = None
    execution: ExecutionAttribution | None = None
    exposure_history: tuple[dict[str, float], ...] = ()
    limitations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class PnlRecord(FrozenModel):
    """One realised P&L event, tagged with everything it can be attributed to."""

    pnl: float
    strategy_id: str = ""
    symbol: str = ""
    sector: str = ""
    contracts: int = 0
    costs: float = 0.0


def statistics(
    returns: np.ndarray | tuple[float, ...] | list[float],
    *,
    periods_per_year: float = 252.0,
) -> RiskStatistics:
    """Risk statistics over a P&L or return series.

    A series of fewer than two observations produces zeros with a note rather
    than a division by zero dressed up as a Sharpe of infinity.
    """
    series = np.asarray(list(returns), dtype=float)
    series = series[np.isfinite(series)]
    if len(series) < 2:
        return RiskStatistics(
            periods=len(series),
            total_pnl=round(float(series.sum()), 6),
            mean=round(float(series.mean()), 6) if len(series) else 0.0,
            volatility=0.0,
            sharpe=None,
            annualised_sharpe=None,
            sortino=None,
            max_drawdown=0.0,
            hit_rate=None,
            profit_factor=None,
            note="fewer than two observations: no dispersion statistic is estimable",
        )

    wins = series[series > 0]
    losses = series[series < 0]
    gross_loss = float(-losses.sum())
    volatility = float(series.std(ddof=1))
    return RiskStatistics(
        periods=len(series),
        total_pnl=round(float(series.sum()), 6),
        mean=round(float(series.mean()), 6),
        volatility=round(volatility, 6),
        sharpe=round(per_period_sharpe(series), 6) if volatility > 0 else None,
        annualised_sharpe=(
            round(annualised_sharpe(series, periods_per_year), 6) if volatility > 0 else None
        ),
        sortino=round(sortino_ratio(series), 6),
        max_drawdown=round(max_drawdown(series), 6),
        hit_rate=round(len(wins) / len(series), 6),
        profit_factor=(
            round(float(wins.sum()) / gross_loss, 6) if gross_loss > 0 else None
        ),
        note="" if gross_loss > 0 else "no losing period in this sample, so profit factor "
                                       "is undefined rather than infinite",
    )


def attribute(
    records: tuple[PnlRecord, ...] | list[PnlRecord],
) -> tuple[tuple[Contribution, ...], tuple[Contribution, ...], tuple[Contribution, ...]]:
    """Decompose P&L by strategy, symbol and sector.

    Shares are against *gross* contribution, not net. A book that made 100 from
    one strategy and lost 90 from another has a net of 10, and expressing the
    winner as 1000% of the total is arithmetically true and useless.
    """
    return (
        _bucket(records, "strategy_id", "unattributed"),
        _bucket(records, "symbol", "unattributed"),
        _bucket(records, "sector", "unclassified"),
    )


def _bucket(
    records: tuple[PnlRecord, ...] | list[PnlRecord], attribute_name: str, fallback: str
) -> tuple[Contribution, ...]:
    totals: dict[str, dict[str, float]] = {}
    for record in records:
        key = getattr(record, attribute_name) or fallback
        entry = totals.setdefault(key, {"pnl": 0.0, "trades": 0.0, "costs": 0.0})
        entry["pnl"] += record.pnl
        entry["trades"] += 1
        entry["costs"] += record.costs
    gross = sum(abs(entry["pnl"]) for entry in totals.values())
    return tuple(
        sorted(
            (
                Contribution(
                    key=key,
                    label=key,
                    pnl=round(entry["pnl"], 6),
                    share=round(abs(entry["pnl"]) / gross, 6) if gross > 0 else 0.0,
                    trades=int(entry["trades"]),
                    costs=round(entry["costs"], 6),
                )
                for key, entry in totals.items()
            ),
            key=lambda contribution: -abs(contribution.pnl),
        )
    )


def compare_benchmark(
    portfolio: np.ndarray | tuple[float, ...] | list[float],
    benchmark: np.ndarray | tuple[float, ...] | list[float],
    *,
    name: str = "benchmark",
    periods_per_year: float = 252.0,
) -> BenchmarkComparison:
    """Beta, alpha, tracking error and information ratio against a supplied series.

    Ordinary least squares, computed directly. There is no risk-free rate
    subtracted: this application has no rate series, and quietly assuming zero
    while calling the result an excess return would misstate alpha by whatever
    cash was paying.
    """
    p = np.asarray(list(portfolio), dtype=float)
    b = np.asarray(list(benchmark), dtype=float)
    if len(p) != len(b):
        return BenchmarkComparison(
            benchmark=name, observations=0, beta=None, alpha=None, correlation=None,
            tracking_error=None, information_ratio=None,
            note=f"the two series have different lengths ({len(p)} and {len(b)})",
        )
    mask = np.isfinite(p) & np.isfinite(b)
    p, b = p[mask], b[mask]
    if len(p) < MINIMUM_REGRESSION_OBSERVATIONS:
        return BenchmarkComparison(
            benchmark=name, observations=len(p), beta=None, alpha=None,
            correlation=None, tracking_error=None, information_ratio=None,
            note=(
                f"{len(p)} observations is below the {MINIMUM_REGRESSION_OBSERVATIONS} "
                "needed for a beta anyone should act on"
            ),
        )
    variance = float(b.var(ddof=1))
    if variance <= 0:
        return BenchmarkComparison(
            benchmark=name, observations=len(p), beta=None, alpha=None,
            correlation=None, tracking_error=None, information_ratio=None,
            note="the benchmark series does not move, so beta is undefined",
        )
    beta = float(np.cov(p, b, ddof=1)[0, 1] / variance)
    alpha = float(p.mean() - beta * b.mean())
    active = p - b
    tracking = float(active.std(ddof=1))
    return BenchmarkComparison(
        benchmark=name,
        observations=len(p),
        beta=round(beta, 6),
        alpha=round(alpha * periods_per_year, 6),
        correlation=round(float(np.corrcoef(p, b)[0, 1]), 6),
        tracking_error=round(tracking * math.sqrt(periods_per_year), 6),
        information_ratio=(
            round(float(active.mean()) / tracking * math.sqrt(periods_per_year), 6)
            if tracking > 0
            else None
        ),
        note="alpha is annualised and gross of any risk-free rate, which this "
             "application does not hold a series for",
    )


def attribute_factors(
    returns: np.ndarray | tuple[float, ...] | list[float],
    loadings: dict[str, np.ndarray | tuple[float, ...] | list[float]],
) -> tuple[tuple[Contribution, ...], str]:
    """Attribute return to factors the caller supplies loadings for.

    Returns an empty decomposition and a stated reason when no loadings are
    given. This is the point at which a commercial factor model would attach:
    the interface is here, and nothing pretends one is installed.
    """
    if not loadings:
        return (), (
            "no factor model is configured. Factor attribution requires loadings, and "
            "no commercial risk model is installed in this build — the integration "
            "point is `attribute_factors`."
        )
    y = np.asarray(list(returns), dtype=float)
    names = sorted(loadings)
    matrix = np.column_stack([np.asarray(list(loadings[name]), dtype=float) for name in names])
    if matrix.shape[0] != len(y):
        return (), (
            f"the loadings cover {matrix.shape[0]} periods but the return series has "
            f"{len(y)}; they were not aligned"
        )
    if len(y) <= matrix.shape[1] + 1:
        return (), (
            f"{len(y)} observations against {matrix.shape[1]} factors leaves too few "
            "degrees of freedom to attribute anything"
        )
    design = np.column_stack([np.ones(len(y)), matrix])
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    contributions = coefficients[1:] * matrix.mean(axis=0)
    residual = float(y.mean() - float(coefficients[0]) - contributions.sum())
    gross = float(np.abs(contributions).sum()) + abs(float(coefficients[0])) + abs(residual)

    rows = [
        Contribution(
            key=name, label=name, pnl=round(float(value), 8),
            share=round(abs(float(value)) / gross, 6) if gross > 0 else 0.0,
        )
        for name, value in zip(names, contributions, strict=True)
    ]
    rows.append(
        Contribution(
            key="specific", label="Specific (intercept)",
            pnl=round(float(coefficients[0]), 8),
            share=round(abs(float(coefficients[0])) / gross, 6) if gross > 0 else 0.0,
        )
    )
    return tuple(rows), ""


def attribute_execution(
    fills: tuple[Any, ...] | list[Any],
    arrival_prices: dict[str, float],
) -> ExecutionAttribution:
    """Measure fills against the price when the decision was made.

    `fills` are `forge.execution.oms.Fill` objects, typed loosely so this module
    does not depend on the execution package. `arrival_prices` is per symbol,
    which is the granularity a rebalance actually has: one decision, one arrival
    reference, many fills.
    """
    if not fills:
        return ExecutionAttribution(
            fills=0, implementation_shortfall=0.0, commission=0.0, modelled_slippage=0.0,
            shortfall_per_contract=None, simulated=True,
            note="no fills to measure",
        )
    shortfall = commission = slippage = 0.0
    contracts = 0
    measured = 0
    simulated = True
    for fill in fills:
        commission += float(getattr(fill, "commission", 0.0))
        slippage += float(getattr(fill, "slippage", 0.0))
        contracts += int(getattr(fill, "quantity", 0))
        simulated = simulated and bool(getattr(fill, "simulated", True))
        arrival = arrival_prices.get(str(getattr(fill, "symbol", "")))
        if arrival is None:
            continue
        measured += 1
        direction = 1.0 if str(getattr(fill, "side", "")) == "buy" else -1.0
        multiplier = float(getattr(fill, "multiplier", 1.0))
        shortfall += (
            (float(fill.price) - arrival) * direction * int(fill.quantity) * multiplier
        )

    note = ""
    if measured < len(fills):
        note = (
            f"{len(fills) - measured} of {len(fills)} fills had no arrival price and were "
            "excluded from the shortfall"
        )
    if simulated:
        note = (
            (note + "; " if note else "")
            + "every fill is simulated, so the shortfall measures this build's slippage "
            "model rather than a venue"
        )
    return ExecutionAttribution(
        fills=len(fills),
        implementation_shortfall=round(shortfall, 4),
        commission=round(commission, 4),
        modelled_slippage=round(slippage, 4),
        shortfall_per_contract=round(shortfall / contracts, 6) if contracts else None,
        simulated=simulated,
        note=note,
    )


def report(
    records: tuple[PnlRecord, ...] | list[PnlRecord],
    *,
    returns: np.ndarray | tuple[float, ...] | list[float] | None = None,
    benchmark: np.ndarray | tuple[float, ...] | list[float] | None = None,
    benchmark_name: str = "benchmark",
    loadings: dict[str, Any] | None = None,
    fills: tuple[Any, ...] | list[Any] = (),
    arrival_prices: dict[str, float] | None = None,
    exposure_history: tuple[dict[str, float], ...] = (),
    periods_per_year: float = 252.0,
) -> PerformanceReport:
    """Assemble the whole answer from the parts above."""
    series = list(returns) if returns is not None else [record.pnl for record in records]
    limitations: list[str] = []
    by_strategy, by_symbol, by_sector = attribute(records)

    factor, factor_note = attribute_factors(series, dict(loadings or {}))
    if factor_note:
        limitations.append(factor_note)

    comparison = None
    if benchmark is not None:
        comparison = compare_benchmark(
            series, benchmark, name=benchmark_name, periods_per_year=periods_per_year
        )
        if comparison.beta is None and comparison.note:
            limitations.append(comparison.note)

    execution = attribute_execution(fills, dict(arrival_prices or {})) if fills else None
    if execution is not None and execution.note:
        limitations.append(execution.note)

    return PerformanceReport(
        statistics=statistics(series, periods_per_year=periods_per_year),
        by_strategy=by_strategy,
        by_symbol=by_symbol,
        by_sector=by_sector,
        factor=factor,
        benchmark=comparison,
        execution=execution,
        exposure_history=exposure_history,
        limitations=tuple(limitations),
    )
