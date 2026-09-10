"""Turning forecasts into sizes, inside a box, at a stated cost.

The optimiser is a mean-variance problem with linear constraints, solved locally
with SLSQP. Three choices in it are worth defending.

**Costs are in the objective, not in the report.** A turnover term charged in
basis points is what stops the solver proposing a portfolio that is optimal
before trading and negative after it. Reporting cost afterwards would let the
same portfolio through and simply describe the damage.

**Absolute values are split, not smoothed.** Gross exposure and turnover are
`sum |w|` quantities, which are not differentiable. Approximating them with a
smooth surrogate moves the constraint; splitting each weight into a long and a
short part keeps the problem exactly linear in its constraints and exactly
quadratic in its objective, so the box the solver respects is the box the
operator configured.

**An unusable covariance estimate stops construction.** It does not fall back to
equal weight. A portfolio built without a variance term is not a worse portfolio
— it is a different exercise, and presenting one as the other is how a book ends
up concentrated in whatever had the highest backtested return. The proposal comes
back infeasible with the reason attached.

The whole thing runs on numpy and scipy against return series the operator
already has. No factor vendor, no optimiser licence, no data subscription.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from forge.contracts.hashing import stable_id
from forge.portfolio.covariance import CovarianceEstimate, estimate
from forge.portfolio.models import (
    AlphaSignal,
    ConstraintReport,
    Constraints,
    Exposures,
    Holding,
    Instrument,
    PortfolioProposal,
)

#: A weight below this is treated as flat. Otherwise a solver's 3e-17 becomes a
#: holding, an order, and a line in the pre-trade gate.
FLAT = 1e-6


class ConstructionError(Exception):
    """Construction could not run at all. Distinct from producing an infeasible
    proposal, which is an answer."""


def construct(
    signals: tuple[AlphaSignal, ...] | list[AlphaSignal],
    instruments: dict[str, Instrument],
    *,
    capital: float,
    constraints: Constraints | None = None,
    returns: np.ndarray | None = None,
    return_symbols: tuple[str, ...] = (),
    current_weights: dict[str, float] | None = None,
    covariance: CovarianceEstimate | None = None,
) -> PortfolioProposal:
    """Size a set of signals into a portfolio.

    `returns` is the history the covariance estimate is built from, with columns
    named by `return_symbols`. A caller that already holds an estimate can pass
    it directly instead.
    """
    if capital <= 0:
        raise ConstructionError("capital must be positive")
    limits = constraints or Constraints()
    previous = dict(current_weights or {})
    excluded: list[dict[str, str]] = []
    limitations: list[str] = []

    eligible = _eligible(signals, instruments, limits, excluded)
    if not eligible:
        return _empty(
            capital, limits, excluded, limitations,
            reason="no signal survived eligibility screening",
        )

    symbols = tuple(sorted(eligible))
    estimated = covariance or _covariance_for(
        symbols, returns, return_symbols, limitations
    )
    if not estimated.usable:
        # `_covariance_for` has already recorded the note when it built the
        # estimate here; only a caller-supplied estimate still needs it added,
        # and adding it twice printed the same sentence to the operator twice.
        if estimated.note and estimated.note not in limitations:
            limitations.append(estimated.note)
        return _empty(
            capital, limits, excluded, limitations,
            reason=(
                "no usable covariance estimate: sizing without one is a different "
                "exercise and is not performed silently"
            ),
        )

    mu = np.array([eligible[symbol]["expected_return"] for symbol in symbols], dtype=float)
    sigma = _aligned(estimated, symbols)
    prior = np.array([previous.get(symbol, 0.0) for symbol in symbols], dtype=float)
    caps = _per_name_caps(symbols, instruments, limits, capital, limitations)

    weights, optimiser, feasible = _solve(mu, sigma, prior, caps, symbols, instruments, limits)
    weights = np.where(np.abs(weights) < FLAT, 0.0, weights)

    holdings = tuple(
        Holding(
            symbol=symbol,
            weight=round(float(weight), 6),
            target_notional=round(float(weight) * capital, 2),
            contracts=_contracts(instruments.get(symbol), float(weight) * capital),
            strategy_ids=tuple(eligible[symbol]["strategy_ids"]),
            expected_return=round(float(eligible[symbol]["expected_return"]), 6),
        )
        for symbol, weight in zip(symbols, weights, strict=True)
        if abs(weight) > 0
    )

    turnover = float(np.abs(weights - prior).sum())
    volatility = estimated.volatility(weights)
    expected = float(mu @ weights)
    cost = turnover * capital * limits.cost_bps / 10_000.0

    return PortfolioProposal(
        portfolio_id=stable_id(
            "portfolio",
            {
                "symbols": list(symbols),
                "weights": [round(float(w), 8) for w in weights],
                "capital": capital,
            },
        ),
        capital=round(capital, 2),
        holdings=holdings,
        exposures=_exposures(symbols, weights, instruments),
        expected_return=round(expected, 6),
        expected_volatility=round(volatility, 6),
        expected_sharpe=round(expected / volatility, 4) if volatility > 1e-12 else None,
        turnover=round(turnover, 6),
        estimated_cost=round(cost, 2),
        constraints=_report(symbols, weights, prior, instruments, limits, capital, caps),
        excluded=tuple(excluded),
        covariance_method=estimated.method,
        observations=estimated.observations,
        limitations=tuple(limitations),
        optimiser=optimiser,
        feasible=feasible,
    )


# ── eligibility ──────────────────────────────────────────────────────────────
def _eligible(
    signals: tuple[AlphaSignal, ...] | list[AlphaSignal],
    instruments: dict[str, Instrument],
    limits: Constraints,
    excluded: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    """Screen signals, and name every rejection.

    Signals for the same symbol are combined by summing their scaled forecasts.
    Averaging would understate two independent strategies agreeing, and taking
    the maximum would ignore the second one entirely; the sum is the only one of
    the three that treats a second confirming signal as information.
    """
    merged: dict[str, dict[str, Any]] = {}
    for signal in signals:
        instrument = instruments.get(signal.symbol)
        if instrument is None:
            excluded.append(
                {"symbol": signal.symbol, "strategy_id": signal.strategy_id,
                 "reason": "no instrument definition supplied for this symbol"}
            )
            continue
        if signal.verdict is None:
            excluded.append(
                {"symbol": signal.symbol, "strategy_id": signal.strategy_id,
                 "reason": "the strategy behind this signal has never been judged"}
            )
            continue
        if signal.verdict not in limits.eligible_verdicts:
            excluded.append(
                {"symbol": signal.symbol, "strategy_id": signal.strategy_id,
                 "reason": f"verdict {signal.verdict} is not in the eligible set "
                           f"({', '.join(limits.eligible_verdicts)})"}
            )
            continue
        if signal.scaled_return < 0 and (limits.long_only or instrument.shortable is not True):
            excluded.append(
                {"symbol": signal.symbol, "strategy_id": signal.strategy_id,
                 "reason": "short forecast on an instrument that is not established "
                           "as shortable" if not limits.long_only
                           else "short forecast in a long-only mandate"}
            )
            continue
        entry = merged.setdefault(
            signal.symbol, {"expected_return": 0.0, "strategy_ids": []}
        )
        entry["expected_return"] += signal.scaled_return
        entry["strategy_ids"].append(signal.strategy_id)
    return merged


def _covariance_for(
    symbols: tuple[str, ...],
    returns: np.ndarray | None,
    return_symbols: tuple[str, ...],
    limitations: list[str],
) -> CovarianceEstimate:
    if returns is None or not len(return_symbols):
        return CovarianceEstimate(
            matrix=np.zeros((len(symbols), len(symbols))),
            symbols=symbols,
            observations=0,
            method="none",
            shrinkage=0.0,
            usable=False,
            note="no return history was supplied, so covariance could not be estimated",
        )
    missing = [symbol for symbol in symbols if symbol not in return_symbols]
    if missing:
        return CovarianceEstimate(
            matrix=np.zeros((len(symbols), len(symbols))),
            symbols=symbols,
            observations=int(np.asarray(returns).shape[0]),
            method="none",
            shrinkage=0.0,
            usable=False,
            note=f"no return history for {', '.join(sorted(missing))}",
        )
    columns = [return_symbols.index(symbol) for symbol in symbols]
    estimated = estimate(np.asarray(returns, dtype=float)[:, columns], symbols)
    if estimated.note:
        limitations.append(estimated.note)
    return estimated


def _aligned(estimated: CovarianceEstimate, symbols: tuple[str, ...]) -> np.ndarray:
    if estimated.symbols == symbols:
        return estimated.matrix
    index = [estimated.symbols.index(symbol) for symbol in symbols]
    return estimated.matrix[np.ix_(index, index)]


def _per_name_caps(
    symbols: tuple[str, ...],
    instruments: dict[str, Instrument],
    limits: Constraints,
    capital: float,
    limitations: list[str],
) -> np.ndarray:
    """The tightest of the single-name cap and the liquidity cap, per symbol."""
    caps = np.full(len(symbols), limits.max_weight, dtype=float)
    unknown: list[str] = []
    for index, symbol in enumerate(symbols):
        instrument = instruments.get(symbol)
        adv = instrument.adv_notional if instrument else None
        if adv is None:
            unknown.append(symbol)
            continue
        caps[index] = min(caps[index], adv * limits.max_participation / capital)
    if unknown:
        limitations.append(
            "average daily notional is unknown for "
            f"{', '.join(sorted(unknown))}; the participation cap was not applied to them"
        )
    return caps


# ── the solve ────────────────────────────────────────────────────────────────
def _solve(
    mu: np.ndarray,
    sigma: np.ndarray,
    prior: np.ndarray,
    caps: np.ndarray,
    symbols: tuple[str, ...],
    instruments: dict[str, Instrument],
    limits: Constraints,
) -> tuple[np.ndarray, str, bool]:
    """Solve for weights over the split variables `[long, short, turnover]`.

    scipy is imported here rather than at module scope. `forge_api.main` imports
    this package on the startup path and `tests/test_import_cost.py` holds the
    line that startup does not pay a second of import time for an optimiser no
    request has asked for yet.
    """
    from scipy.optimize import LinearConstraint, minimize

    n = len(symbols)
    # x = [p (n), m (n), t (n)]; w = p - m, both non-negative; t >= |w - prior|.
    def unpack(x: np.ndarray) -> np.ndarray:
        return np.asarray(x[:n] - x[n : 2 * n], dtype=float)

    def objective(x: np.ndarray) -> float:
        w = unpack(x)
        turnover = float(x[2 * n :].sum())
        return float(
            -mu @ w
            + 0.5 * limits.risk_aversion * (w @ sigma @ w)
            + turnover * limits.cost_bps / 10_000.0
        )

    def gradient(x: np.ndarray) -> np.ndarray:
        w = unpack(x)
        dw = -mu + limits.risk_aversion * (sigma @ w)
        return np.concatenate([dw, -dw, np.full(n, limits.cost_bps / 10_000.0)])

    rows: list[np.ndarray] = []
    lower: list[float] = []
    upper: list[float] = []

    def add(block: np.ndarray, low: float, high: float) -> None:
        rows.append(block)
        lower.append(low)
        upper.append(high)

    ones = np.ones(n)
    # Gross: sum(p + m) <= max_gross. Conservative where p and m are both
    # positive for the same name, which the objective never rewards.
    add(np.concatenate([ones, ones, np.zeros(n)]), 0.0, limits.max_gross)
    # Net: -max_net <= sum(p - m) <= max_net.
    add(np.concatenate([ones, -ones, np.zeros(n)]), -limits.max_net, limits.max_net)
    # Per name: p_i + m_i <= cap_i. One row each so the binding name is
    # identifiable afterwards rather than only the fact that something bound.
    for index in range(n):
        row = np.zeros(3 * n)
        row[index] = 1.0
        row[n + index] = 1.0
        add(row, 0.0, float(caps[index]))
    # Turnover: t_i >= (w_i - prior_i) and t_i >= (prior_i - w_i), sum(t) <= cap.
    for index in range(n):
        plus = np.zeros(3 * n)
        plus[index], plus[n + index], plus[2 * n + index] = -1.0, 1.0, 1.0
        add(plus, -float(prior[index]), np.inf)
        minus = np.zeros(3 * n)
        minus[index], minus[n + index], minus[2 * n + index] = 1.0, -1.0, 1.0
        add(minus, float(prior[index]), np.inf)
    add(np.concatenate([np.zeros(2 * n), ones]), 0.0, limits.max_turnover)

    for attribute, cap in (("sector", limits.max_sector_weight),
                           ("region", limits.max_region_weight)):
        for group, mask in _groups(symbols, instruments, attribute).items():
            if not group:
                continue
            add(np.concatenate([mask, mask, np.zeros(n)]), 0.0, cap)

    constraint = LinearConstraint(np.vstack(rows), np.array(lower), np.array(upper))
    # The short half is pinned to zero for a long-only mandate. Pinning by
    # bounds rather than by dropping the variables keeps the constraint matrix
    # one shape, so every row above indexes the same way in both cases.
    bounds: list[tuple[float, float | None]] = [(0.0, None)] * n
    bounds += [(0.0, 0.0)] * n if limits.long_only else [(0.0, None)] * n
    bounds += [(0.0, None)] * n

    start = np.concatenate(
        [np.clip(prior, 0.0, None), np.clip(-prior, 0.0, None), np.zeros(n)]
    )
    result = minimize(
        objective,
        start,
        jac=gradient,
        method="SLSQP",
        bounds=bounds,
        constraints=[constraint],
        options={"maxiter": 400, "ftol": 1e-10},
    )
    weights = unpack(np.asarray(result.x, dtype=float))
    # A solver that stopped early can return a point outside the box. Reporting
    # it as feasible would put a portfolio through the risk engine that the
    # constraints were supposed to have excluded, so the check is repeated on the
    # answer rather than trusted from the flag.
    feasible = bool(result.success) and _within(weights, prior, caps, symbols, instruments, limits)
    optimiser = f"SLSQP mean-variance (λ={limits.risk_aversion}, {limits.cost_bps}bps)"
    if not feasible:
        # Zero is the only weight vector that is certainly inside every box, and
        # an empty book is a legible answer. A clipped near-miss is not: it looks
        # like a portfolio and satisfies nothing in particular.
        return np.zeros(n), optimiser, False
    return weights, optimiser, True


def _within(
    weights: np.ndarray,
    prior: np.ndarray,
    caps: np.ndarray,
    symbols: tuple[str, ...],
    instruments: dict[str, Instrument],
    limits: Constraints,
) -> bool:
    tolerance = 1e-6
    absolute = np.abs(weights)
    if absolute.sum() > limits.max_gross + tolerance:
        return False
    if abs(float(weights.sum())) > limits.max_net + tolerance:
        return False
    if np.any(absolute > caps + tolerance):
        return False
    if float(np.abs(weights - prior).sum()) > limits.max_turnover + tolerance:
        return False
    if limits.long_only and np.any(weights < -tolerance):
        return False
    for attribute, cap in (("sector", limits.max_sector_weight),
                           ("region", limits.max_region_weight)):
        for _, mask in _groups(symbols, instruments, attribute).items():
            if float(absolute @ mask) > cap + tolerance:
                return False
    return True


def _groups(
    symbols: tuple[str, ...], instruments: dict[str, Instrument], attribute: str
) -> dict[str, np.ndarray]:
    """Indicator vectors per sector or region.

    Instruments with the attribute blank are left out entirely rather than
    pooled into an "" group. Pooling would apply a sector cap across every
    instrument whose sector nobody recorded, which is a constraint the operator
    did not ask for and cannot see.
    """
    groups: dict[str, np.ndarray] = {}
    for index, symbol in enumerate(symbols):
        instrument = instruments.get(symbol)
        key = getattr(instrument, attribute, "") if instrument else ""
        if not key:
            continue
        mask = groups.setdefault(key, np.zeros(len(symbols)))
        mask[index] = 1.0
    return groups


# ── reporting ────────────────────────────────────────────────────────────────
def _exposures(
    symbols: tuple[str, ...], weights: np.ndarray, instruments: dict[str, Instrument]
) -> Exposures:
    absolute = np.abs(weights)
    gross = float(absolute.sum())
    longs = float(weights[weights > 0].sum())
    shorts = float(-weights[weights < 0].sum())

    def bucket(attribute: str) -> dict[str, float]:
        totals: dict[str, float] = {}
        for symbol, weight in zip(symbols, weights, strict=True):
            instrument = instruments.get(symbol)
            key = getattr(instrument, attribute, "") if instrument else ""
            if not key:
                continue
            totals[key] = round(totals.get(key, 0.0) + float(weight), 6)
        return totals

    return Exposures(
        gross=round(gross, 6),
        net=round(float(weights.sum()), 6),
        long=round(longs, 6),
        short=round(shorts, 6),
        # Leverage against capital, which for a fully-funded book equals gross.
        leverage=round(gross, 6),
        by_sector=bucket("sector"),
        by_region=bucket("region"),
        by_asset_class=bucket("asset_class"),
        concentration=round(float((absolute**2).sum() / gross**2), 6) if gross > 0 else 0.0,
    )


def _report(
    symbols: tuple[str, ...],
    weights: np.ndarray,
    prior: np.ndarray,
    instruments: dict[str, Instrument],
    limits: Constraints,
    capital: float,
    caps: np.ndarray,
) -> tuple[ConstraintReport, ...]:
    absolute = np.abs(weights)
    gross = float(absolute.sum())
    net = float(weights.sum())
    turnover = float(np.abs(weights - prior).sum())
    largest = float(absolute.max()) if len(absolute) else 0.0
    binding = 1e-4

    reports = [
        ConstraintReport(
            name="gross_exposure", limit=limits.max_gross, observed=round(gross, 6),
            binding=gross >= limits.max_gross - binding,
            detail="sum of absolute weights",
        ),
        ConstraintReport(
            name="net_exposure", limit=limits.max_net, observed=round(net, 6),
            binding=abs(net) >= limits.max_net - binding,
            detail="signed sum of weights",
        ),
        ConstraintReport(
            name="max_weight", limit=limits.max_weight, observed=round(largest, 6),
            binding=largest >= limits.max_weight - binding,
            detail="largest single-name weight",
        ),
        ConstraintReport(
            name="turnover", limit=limits.max_turnover, observed=round(turnover, 6),
            binding=turnover >= limits.max_turnover - binding,
            detail="one-way turnover against the current book",
        ),
    ]

    known = [
        (symbol, cap)
        for symbol, cap in zip(symbols, caps, strict=True)
        if instruments.get(symbol) and instruments[symbol].adv_notional is not None
    ]
    if known:
        tightest = min(cap for _, cap in known)
        touched = max(
            (
                float(abs(weights[symbols.index(symbol)])) / cap
                for symbol, cap in known
                if cap > 0
            ),
            default=0.0,
        )
        # Both sides are expressed as a fraction of each name's own capacity
        # cap, because the caps differ per instrument and a single weight cannot
        # be compared against them. `1.0` means some name is at its cap.
        reports.append(
            ConstraintReport(
                name="liquidity", limit=1.0,
                observed=round(touched, 4), binding=touched >= 1 - binding,
                detail=(
                    f"share of each name's capacity cap ({limits.max_participation:.0%} "
                    f"of average daily notional) that is used; tightest cap is "
                    f"{tightest:.4f} of capital"
                ),
            )
        )
    else:
        reports.append(
            ConstraintReport(
                name="liquidity", limit=limits.max_participation, observed=0.0,
                binding=False, applied=False,
                detail="no instrument reported average daily notional, so no capacity "
                       "constraint could be applied",
            )
        )

    for attribute, cap, label in (
        ("sector", limits.max_sector_weight, "sector"),
        ("region", limits.max_region_weight, "region"),
    ):
        groups = _groups(symbols, instruments, attribute)
        if not groups:
            reports.append(
                ConstraintReport(
                    name=f"{label}_exposure", limit=cap, observed=0.0, binding=False,
                    applied=False,
                    detail=f"no instrument declares a {label}, so the cap was not applied",
                )
            )
            continue
        worst = max(float(absolute @ mask) for mask in groups.values())
        reports.append(
            ConstraintReport(
                name=f"{label}_exposure", limit=cap, observed=round(worst, 6),
                binding=worst >= cap - binding,
                detail=f"largest gross exposure to any one {label}",
            )
        )
    return tuple(reports)


def _contracts(instrument: Instrument | None, notional: float) -> int | None:
    """Whole contracts for a notional, or `None` when the price is unknown.

    `None` rather than zero. Zero is a decision to hold nothing; unknown is the
    absence of a price, and the order preparation step has to be able to tell
    them apart.
    """
    if instrument is None or instrument.price is None:
        return None
    unit = instrument.price * instrument.multiplier
    if unit <= 0:
        return None
    # Rounded to nine places before truncating. The optimiser returns 0.3999999
    # where it means 0.4, and truncating that directly turned an exactly-one-
    # contract target into zero contracts — a position that silently disappeared
    # between the portfolio and the order.
    return int(round(notional / unit, 9))


def _empty(
    capital: float,
    limits: Constraints,
    excluded: list[dict[str, str]],
    limitations: list[str],
    *,
    reason: str,
) -> PortfolioProposal:
    return PortfolioProposal(
        portfolio_id=stable_id("portfolio", {"empty": reason, "capital": capital}),
        capital=round(capital, 2),
        holdings=(),
        exposures=Exposures(gross=0.0, net=0.0, long=0.0, short=0.0, leverage=0.0),
        expected_return=0.0,
        expected_volatility=0.0,
        expected_sharpe=None,
        turnover=0.0,
        estimated_cost=0.0,
        constraints=(),
        excluded=tuple(excluded),
        covariance_method="none",
        observations=0,
        limitations=(*limitations, reason),
        optimiser="none",
        feasible=False,
    )
