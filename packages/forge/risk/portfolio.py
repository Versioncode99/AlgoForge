"""The deterministic risk layer for a portfolio, and the limits it enforces.

`forge.risk.limits` governs one strategy's intent to trade one instrument.
This module governs the book: what it is exposed to in aggregate, how levered it
is, how concentrated, how volatile, and how much of it is at risk in a bad tail.

Three properties are the point.

**It is deterministic.** Every number here is a closed-form function of the
holdings, the covariance estimate and the return history. Nothing consults a
model, and nothing is a judgement call. That is what makes it usable as a
control rather than as commentary: the same inputs give the same breaches, and
a breach cannot be argued out of.

**Limits are data an AI actor cannot reach.** `PortfolioLimits` is frozen, it is
supplied by the operator, and the actions that change it are marked protected —
`forge.modes.permissions` denies them to AI in every mode and every stance. An
agent that would rather have a higher leverage ceiling has exactly one route to
one: ask a person.

**A statistic that cannot be estimated says so.** VaR from twelve observations
is not a tail estimate, and a `None` with a reason is worth more than a number
that will be read as one.
"""

from __future__ import annotations

import math
from typing import Any, Literal

import numpy as np
from pydantic import Field

from forge.contracts.models import FrozenModel

#: Minimum observations before a historical tail statistic is reported. At the
#: 95th percentile, forty observations put two points in the tail; below that the
#: "estimate" is one unlucky day with a decimal place.
MINIMUM_TAIL_OBSERVATIONS = 40

#: Trading days per year, for annualising a per-period volatility.
PERIODS_PER_YEAR = 252.0


class PortfolioLimits(FrozenModel):
    """What the book is permitted to look like.

    Every ceiling is finite and required to be positive, for the same reason
    `RiskProfile` refuses an absent one: a limit set with an unlimited entry is
    not a limit set, and the panel showing it would be reassuring and empty.
    """

    name: str = Field(default="default", min_length=1, max_length=80)
    max_gross: float = Field(default=2.0, gt=0)
    max_net: float = Field(default=1.0, ge=0)
    max_leverage: float = Field(default=2.0, gt=0)
    #: Largest absolute weight in one name.
    max_position_weight: float = Field(default=0.25, gt=0, le=1.0)
    #: Herfindahl concentration ceiling over absolute weights.
    max_concentration: float = Field(default=0.4, gt=0, le=1.0)
    #: Annualised portfolio volatility ceiling, as a decimal fraction.
    max_volatility: float = Field(default=0.25, gt=0)
    #: One-day 95% VaR ceiling, as a fraction of capital.
    max_var_95: float = Field(default=0.03, gt=0)
    max_drawdown: float = Field(default=0.15, gt=0)
    max_turnover: float = Field(default=1.0, gt=0)
    #: Largest gross exposure to any one strategy.
    max_strategy_weight: float = Field(default=0.5, gt=0, le=1.0)
    #: Largest absolute pairwise correlation permitted between two held names.
    max_pair_correlation: float = Field(default=0.95, gt=0, le=1.0)
    #: The kill switch. `False` refuses everything, and the refusal names this
    #: limit set so "why is nothing running" has an answer.
    enabled: bool = True


class Breach(FrozenModel):
    limit: str
    observed: float
    ceiling: float
    detail: str


class RiskMeasure(FrozenModel):
    """One measured quantity, with its provenance attached.

    `value` is `None` when the measure could not be estimated, and `method` and
    `note` say why. A reader must be able to tell "zero risk" from "we did not
    measure risk", and a bare float cannot express the difference.
    """

    key: str
    label: str
    value: float | None
    ceiling: float | None = None
    method: str = ""
    note: str = ""

    @property
    def breached(self) -> bool:
        return self.value is not None and self.ceiling is not None and self.value > self.ceiling


class RiskAssessment(FrozenModel):
    limits_name: str
    within_limits: bool
    enabled: bool
    measures: tuple[RiskMeasure, ...]
    breaches: tuple[Breach, ...]
    exposures: dict[str, float] = Field(default_factory=dict)
    by_strategy: dict[str, float] = Field(default_factory=dict)
    limitations: tuple[str, ...] = ()

    def measure(self, key: str) -> RiskMeasure | None:
        return next((m for m in self.measures if m.key == key), None)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class BookPosition(FrozenModel):
    """One line of the book being assessed.

    Weight is against capital, signed. `strategy_ids` is what makes per-strategy
    limits possible, and it is a tuple because two strategies can hold the same
    instrument and the exposure belongs to both.
    """

    symbol: str
    weight: float
    strategy_ids: tuple[str, ...] = ()


def evaluate_portfolio(
    positions: tuple[BookPosition, ...] | list[BookPosition],
    limits: PortfolioLimits,
    *,
    covariance: Any | None = None,
    portfolio_returns: np.ndarray | None = None,
    equity_curve: np.ndarray | None = None,
    turnover: float | None = None,
    periods_per_year: float = PERIODS_PER_YEAR,
) -> RiskAssessment:
    """Measure the book and compare it against the limits.

    `covariance` is a `forge.portfolio.CovarianceEstimate` when one is available.
    It is typed loosely on purpose: this module must not import the portfolio
    package, or the risk layer would depend on the thing it is constraining.
    """
    measures: list[RiskMeasure] = []
    limitations: list[str] = []
    weights = np.array([position.weight for position in positions], dtype=float)
    symbols = tuple(position.symbol for position in positions)
    absolute = np.abs(weights)
    gross = float(absolute.sum())
    net = float(weights.sum())

    measures.append(RiskMeasure(
        key="gross", label="Gross exposure", value=round(gross, 6),
        ceiling=limits.max_gross, method="sum of absolute weights",
    ))
    measures.append(RiskMeasure(
        key="net", label="Net exposure", value=round(abs(net), 6),
        ceiling=limits.max_net, method="absolute signed sum of weights",
    ))
    measures.append(RiskMeasure(
        key="leverage", label="Leverage", value=round(gross, 6),
        ceiling=limits.max_leverage, method="gross exposure against capital",
    ))
    largest = float(absolute.max()) if len(absolute) else 0.0
    measures.append(RiskMeasure(
        key="position_weight", label="Largest position", value=round(largest, 6),
        ceiling=limits.max_position_weight, method="largest absolute weight",
    ))
    concentration = float((absolute**2).sum() / gross**2) if gross > 0 else 0.0
    measures.append(RiskMeasure(
        key="concentration", label="Concentration", value=round(concentration, 6),
        ceiling=limits.max_concentration,
        method="Herfindahl index over absolute weights",
        note="1.0 is a single position; 1/n is even",
    ))

    measures.append(_turnover_measure(turnover, limits))
    volatility = _volatility_measure(weights, covariance, periods_per_year, limits, limitations)
    measures.append(volatility)
    measures.extend(_tail_measures(portfolio_returns, volatility.value, limits, periods_per_year))
    measures.append(_drawdown_measure(equity_curve, limits))
    measures.append(_correlation_measure(symbols, weights, covariance, limits, limitations))

    by_strategy = _strategy_exposure(positions)
    worst_strategy = max(by_strategy.values(), default=0.0)
    measures.append(RiskMeasure(
        key="strategy_weight", label="Largest strategy", value=round(worst_strategy, 6),
        ceiling=limits.max_strategy_weight,
        method="largest gross exposure attributed to one strategy",
        note="" if by_strategy else "no position names a strategy, so nothing was attributed",
    ))

    breaches = tuple(
        Breach(
            limit=measure.key,
            observed=float(measure.value or 0.0),
            ceiling=float(measure.ceiling or 0.0),
            detail=f"{measure.label} {measure.value} exceeds the ceiling {measure.ceiling}",
        )
        for measure in measures
        if measure.breached
    )

    return RiskAssessment(
        limits_name=limits.name,
        # The kill switch makes the book out of limits regardless of its numbers.
        # A disabled limit set that reported "within limits" would be a switch
        # that turns nothing off.
        within_limits=limits.enabled and not breaches,
        enabled=limits.enabled,
        measures=tuple(measures),
        breaches=breaches,
        exposures={
            "gross": round(gross, 6),
            "net": round(net, 6),
            "long": round(float(weights[weights > 0].sum()), 6),
            "short": round(float(-weights[weights < 0].sum()), 6),
        },
        by_strategy={key: round(value, 6) for key, value in sorted(by_strategy.items())},
        limitations=tuple(limitations),
    )


def _turnover_measure(turnover: float | None, limits: PortfolioLimits) -> RiskMeasure:
    if turnover is None:
        return RiskMeasure(
            key="turnover", label="Turnover", value=None, ceiling=limits.max_turnover,
            note="no rebalance was supplied, so turnover was not measured",
        )
    return RiskMeasure(
        key="turnover", label="Turnover", value=round(float(turnover), 6),
        ceiling=limits.max_turnover, method="one-way turnover of the proposed rebalance",
    )


def _volatility_measure(
    weights: np.ndarray,
    covariance: Any | None,
    periods_per_year: float,
    limits: PortfolioLimits,
    limitations: list[str],
) -> RiskMeasure:
    if covariance is None or not getattr(covariance, "usable", False):
        note = getattr(covariance, "note", "") if covariance is not None else ""
        limitations.append(
            "portfolio volatility could not be estimated: " + (note or "no covariance estimate")
        )
        return RiskMeasure(
            key="volatility", label="Volatility (annualised)", value=None,
            ceiling=limits.max_volatility,
            note=note or "no usable covariance estimate was supplied",
        )
    matrix = np.asarray(covariance.matrix, dtype=float)
    if matrix.shape[0] != len(weights):
        return RiskMeasure(
            key="volatility", label="Volatility (annualised)", value=None,
            ceiling=limits.max_volatility,
            note=(
                f"the covariance estimate covers {matrix.shape[0]} assets but the book "
                f"holds {len(weights)}; they were not aligned"
            ),
        )
    per_period = math.sqrt(max(float(weights @ matrix @ weights), 0.0))
    return RiskMeasure(
        key="volatility",
        label="Volatility (annualised)",
        value=round(per_period * math.sqrt(periods_per_year), 6),
        ceiling=limits.max_volatility,
        method=f"{covariance.method} covariance, {covariance.observations} observations",
    )


def _tail_measures(
    portfolio_returns: np.ndarray | None,
    annual_volatility: float | None,
    limits: PortfolioLimits,
    periods_per_year: float,
) -> list[RiskMeasure]:
    """One-day 95% VaR and expected shortfall.

    Historical when there is enough history to have a tail, parametric otherwise,
    and absent when there is neither. The method is always reported: a parametric
    VaR on a portfolio of trend strategies is an underestimate and the reader has
    to be told which one they are looking at.
    """
    if portfolio_returns is not None:
        series = np.asarray(portfolio_returns, dtype=float)
        series = series[np.isfinite(series)]
        if len(series) >= MINIMUM_TAIL_OBSERVATIONS:
            quantile = float(np.quantile(series, 0.05))
            var = max(0.0, -quantile)
            tail = series[series <= quantile]
            shortfall = max(0.0, -float(tail.mean())) if len(tail) else var
            return [
                RiskMeasure(
                    key="var_95", label="VaR 95% (1 day)", value=round(var, 6),
                    ceiling=limits.max_var_95,
                    method=f"historical, {len(series)} observations",
                ),
                RiskMeasure(
                    key="expected_shortfall", label="Expected shortfall 95%",
                    value=round(shortfall, 6), ceiling=None,
                    method=f"mean of the worst {len(tail)} observations",
                ),
            ]

    if annual_volatility is None:
        return [
            RiskMeasure(
                key="var_95", label="VaR 95% (1 day)", value=None, ceiling=limits.max_var_95,
                note="neither a return history nor a covariance estimate was available",
            ),
            RiskMeasure(
                key="expected_shortfall", label="Expected shortfall 95%", value=None,
                note="requires a return history or a covariance estimate",
            ),
        ]

    daily = annual_volatility / math.sqrt(periods_per_year)
    # 1.645 and 2.063 are the 95% normal quantile and the corresponding
    # expected-shortfall multiplier phi(z)/0.05.
    return [
        RiskMeasure(
            key="var_95", label="VaR 95% (1 day)", value=round(1.645 * daily, 6),
            ceiling=limits.max_var_95,
            method="parametric normal from the covariance estimate",
            note="a normal assumption understates fat tails; supply a return history "
                 "for the historical estimate",
        ),
        RiskMeasure(
            key="expected_shortfall", label="Expected shortfall 95%",
            value=round(2.063 * daily, 6), ceiling=None,
            method="parametric normal from the covariance estimate",
        ),
    ]


def _drawdown_measure(equity_curve: np.ndarray | None, limits: PortfolioLimits) -> RiskMeasure:
    if equity_curve is None or len(np.asarray(equity_curve)) < 2:
        return RiskMeasure(
            key="drawdown", label="Maximum drawdown", value=None,
            ceiling=limits.max_drawdown,
            note="no equity curve was supplied, so drawdown was not measured",
        )
    curve = np.asarray(equity_curve, dtype=float)
    peak = np.maximum.accumulate(curve)
    # Guarded because a curve that starts at zero would divide by it, and the
    # resulting inf renders as an enormous drawdown on a book that has none.
    safe = np.where(np.abs(peak) < 1e-12, np.nan, peak)
    drawdown = np.nanmax((peak - curve) / np.abs(safe)) if len(curve) else 0.0
    return RiskMeasure(
        key="drawdown", label="Maximum drawdown",
        value=round(float(np.nan_to_num(drawdown)), 6), ceiling=limits.max_drawdown,
        method="peak-to-trough on the supplied equity curve",
    )


def _correlation_measure(
    symbols: tuple[str, ...],
    weights: np.ndarray,
    covariance: Any | None,
    limits: PortfolioLimits,
    limitations: list[str],
) -> RiskMeasure:
    held = np.abs(weights) > 1e-9
    if covariance is None or not getattr(covariance, "usable", False) or held.sum() < 2:
        return RiskMeasure(
            key="pair_correlation", label="Largest pair correlation", value=None,
            ceiling=limits.max_pair_correlation,
            note=(
                "fewer than two positions are held"
                if held.sum() < 2
                else "no usable covariance estimate was supplied"
            ),
        )
    matrix = np.asarray(covariance.matrix, dtype=float)
    if matrix.shape[0] != len(weights):
        limitations.append("correlation was not measured: the covariance estimate is not aligned")
        return RiskMeasure(
            key="pair_correlation", label="Largest pair correlation", value=None,
            ceiling=limits.max_pair_correlation,
            note="the covariance estimate does not cover the held names",
        )
    deviation = np.sqrt(np.clip(np.diag(matrix), 1e-18, None))
    correlation = matrix / np.outer(deviation, deviation)
    index = np.where(held)[0]
    pairs = [
        (abs(float(correlation[i, j])), symbols[i], symbols[j])
        for position, i in enumerate(index)
        for j in index[position + 1 :]
    ]
    worst, left, right = max(pairs)
    return RiskMeasure(
        key="pair_correlation", label="Largest pair correlation", value=round(worst, 6),
        ceiling=limits.max_pair_correlation,
        method=f"{covariance.method} covariance",
        note=f"{left}/{right}",
    )


def _strategy_exposure(
    positions: tuple[BookPosition, ...] | list[BookPosition],
) -> dict[str, float]:
    """Gross exposure attributed to each strategy.

    A position held by two strategies is attributed in full to each rather than
    split between them. That is the conservative reading and the one a limit
    should use: if two strategies each want the whole position, turning one off
    does not halve the exposure.
    """
    totals: dict[str, float] = {}
    for position in positions:
        for strategy_id in position.strategy_ids:
            totals[strategy_id] = totals.get(strategy_id, 0.0) + abs(position.weight)
    return totals


def kill_switch_reason(limits: PortfolioLimits) -> str | None:
    """Why everything is refused, when it is."""
    if limits.enabled:
        return None
    return (
        f"the '{limits.name}' limit set is disabled. This is a kill switch: every "
        "proposed order is refused until a person re-enables it."
    )


Severity = Literal["ok", "breach", "halted"]


def severity(assessment: RiskAssessment) -> Severity:
    if not assessment.enabled:
        return "halted"
    return "ok" if assessment.within_limits else "breach"
