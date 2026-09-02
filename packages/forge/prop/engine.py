from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np
import yaml

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel
from forge.prop.models import (
    BoundaryEvent,
    BoundaryRaceSummary,
    DistributionBin,
    PathOutcome,
    PropRuleSet,
    ReturnDrawdownPoint,
    TailRiskSummary,
    TargetReachPoint,
)


class PropSimulation(FrozenModel):
    simulation_id: str
    run_id: str
    rule: PropRuleSet
    seed: int
    path_count: int
    pass_count: int
    fail_count: int
    timeout_count: int
    pass_rate: float
    interval_low: float
    interval_high: float
    mean_payout: float
    risk_of_ruin: float
    boundary_race: BoundaryRaceSummary
    target_reach_curve: tuple[TargetReachPoint, ...]
    terminal_histogram: tuple[DistributionBin, ...]
    return_drawdown_map: tuple[ReturnDrawdownPoint, ...]
    tail_risk: TailRiskSummary
    outcomes: tuple[PathOutcome, ...]
    equity_paths: tuple[tuple[float, ...], ...]
    labels: tuple[str, ...] = (
        "SAMPLE_DATA",
        "UNVERIFIED_RULES",
        "RESEARCH_ONLY",
        "BLOCK_BOOTSTRAP",
        "DAILY_SETTLEMENT_APPROXIMATION",
    )


def load_rules(directory: Path) -> list[PropRuleSet]:
    rules = []
    for path in sorted(directory.glob("*.yaml")):
        rules.append(PropRuleSet.model_validate(yaml.safe_load(path.read_text("utf-8"))))
    return rules


def replay_path(rule: PropRuleSet, daily_pnl: np.ndarray) -> tuple[PathOutcome, tuple[float, ...]]:
    balance = rule.starting_balance
    high_water = balance
    floor = balance - rule.maximum_loss
    daily_profits: list[float] = []
    events: list[BoundaryEvent] = []
    equity = [balance]
    payouts = 0.0
    for day, pnl in enumerate(daily_pnl[: rule.timeout_days], start=1):
        if rule.daily_loss_limit is not None and pnl <= -rule.daily_loss_limit:
            events.append(
                BoundaryEvent(
                    day=day,
                    event="DAILY_STOP",
                    balance=balance,
                    loss_floor=floor,
                    target=rule.profit_target,
                )
            )
            pnl = -rule.daily_loss_limit
        balance += float(pnl)
        daily_profits.append(max(float(pnl), 0.0))
        equity.append(round(balance, 2))
        intraday_high = max(high_water, balance)
        candidate_floor = min(rule.floor_cap, intraday_high - rule.maximum_loss)
        if rule.trail_mode == "INTRADAY":
            floor = max(floor, candidate_floor)
        if balance <= floor:
            events.append(
                BoundaryEvent(
                    day=day,
                    event="MAXIMUM_LOSS",
                    balance=balance,
                    loss_floor=floor,
                    target=rule.profit_target,
                )
            )
            return PathOutcome(
                outcome="FAIL",
                days=day,
                terminal_balance=round(balance, 2),
                failure_reason="MAXIMUM_LOSS",
                payouts=payouts,
                events=tuple(events),
            ), tuple(equity)
        high_water = max(high_water, balance)
        if rule.trail_mode == "EOD":
            floor = max(floor, min(rule.floor_cap, high_water - rule.maximum_loss))
        profit = balance - rule.starting_balance
        if rule.phase == "CHALLENGE":
            target = rule.profit_target
            if rule.consistency_percent and profit > 0:
                target = max(target, max(daily_profits) / (rule.consistency_percent / 100))
            if day >= rule.minimum_days and profit >= target:
                events.append(
                    BoundaryEvent(
                        day=day, event="TARGET", balance=balance, loss_floor=floor, target=target
                    )
                )
                return PathOutcome(
                    outcome="PASS",
                    days=day,
                    terminal_balance=round(balance, 2),
                    failure_reason=None,
                    payouts=0.0,
                    events=tuple(events),
                ), tuple(equity)
        elif rule.payout_threshold and rule.payout_amount and profit >= rule.payout_threshold:
            balance -= rule.payout_amount
            payouts += rule.payout_amount
            equity[-1] = round(balance, 2)
            events.append(
                BoundaryEvent(
                    day=day,
                    event="PAYOUT",
                    balance=balance,
                    loss_floor=floor,
                    target=rule.payout_threshold,
                )
            )
    outcome: Literal["SURVIVED", "TIMEOUT"] = "SURVIVED" if rule.phase == "FUNDED" else "TIMEOUT"
    return PathOutcome(
        outcome=outcome,
        days=min(len(daily_pnl), rule.timeout_days),
        terminal_balance=round(balance, 2),
        failure_reason=None,
        payouts=payouts,
        events=tuple(events),
    ), tuple(equity)


# A prop evaluation runs for weeks. Estimating it from fewer days than this is
# resampling a handful of numbers into a shape they cannot support.
MIN_TRADING_DAYS = 30

# Daily P&L is streaky: losing days cluster, and clusters are what breach a
# trailing drawdown. Independent day-by-day resampling erases that clustering and
# systematically understates the chance of ruin.
DEFAULT_BLOCK_DAYS = 5


def _block_bootstrap(
    pnl: np.ndarray, length: int, rng: np.random.Generator, block: int = DEFAULT_BLOCK_DAYS
) -> np.ndarray:
    """Stationary block bootstrap: draw contiguous runs, so streaks survive."""
    if pnl.size <= 1:
        return np.repeat(pnl, length)[:length]
    block = max(1, min(block, pnl.size))
    out = np.empty(length, dtype=float)
    filled = 0
    while filled < length:
        start = int(rng.integers(0, pnl.size))
        take = min(block, length - filled)
        # Wrap around the sample so every day can begin a block.
        idx = (np.arange(start, start + take)) % pnl.size
        out[filled : filled + take] = pnl[idx]
        filled += take
    return out


def _sample_aware_interval(
    pnl: np.ndarray,
    rule: PropRuleSet,
    paths: int,
    seed: int,
    replicates: int = 40,
) -> tuple[float, float]:
    """Confidence interval that widens when few trading days were observed.

    Outer loop resamples the observed days themselves; inner loop simulates
    accounts from that resampled history. The spread across outer replicates is
    the uncertainty that comes from having seen a short track record, which a
    path-count interval cannot express.
    """
    rng = np.random.default_rng(seed + 977)
    inner = max(20, paths // replicates)
    rates: list[float] = []
    for _ in range(replicates):
        days = _block_bootstrap(pnl, pnl.size, rng)
        wins = 0
        for _ in range(inner):
            outcome, _ = replay_path(rule, _block_bootstrap(days, rule.timeout_days, rng))
            wins += outcome.outcome in {"PASS", "SURVIVED"}
        rates.append(wins / inner)
    return float(np.percentile(rates, 2.5)), float(np.percentile(rates, 97.5))


def _wilson(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    probability = successes / total
    denominator = 1 + z * z / total
    centre = (probability + z * z / (2 * total)) / denominator
    margin = (
        z
        / denominator
        * math.sqrt(probability * (1 - probability) / total + z * z / (4 * total * total))
    )
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _optional_quantiles(values: list[int]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return None, None, None
    data = np.asarray(values, dtype=float)
    return tuple(float(value) for value in np.percentile(data, (10, 50, 90)))  # type: ignore[return-value]


def _max_drawdown(equity: tuple[float, ...]) -> float:
    values = np.asarray(equity, dtype=float)
    if values.size == 0:
        return 0.0
    return float(np.max(np.maximum.accumulate(values) - values))


def _tail_summary(terminal_pnl: np.ndarray) -> TailRiskSummary:
    p05, median, p95 = (float(value) for value in np.percentile(terminal_pnl, (5, 50, 95)))
    tail = terminal_pnl[terminal_pnl <= p05]
    mean = float(np.mean(terminal_pnl))
    centred = terminal_pnl - mean
    std = float(np.std(terminal_pnl, ddof=0))
    if std <= 0:
        skewness = 0.0
        excess_kurtosis = 0.0
    else:
        skewness = float(np.mean((centred / std) ** 3))
        excess_kurtosis = float(np.mean((centred / std) ** 4) - 3.0)
    return TailRiskSummary(
        var_95=round(max(0.0, -p05), 2),
        cvar_95=round(max(0.0, -float(np.mean(tail))), 2),
        skewness=round(skewness, 4),
        excess_kurtosis=round(excess_kurtosis, 4),
        terminal_p05=round(p05, 2),
        terminal_median=round(median, 2),
        terminal_p95=round(p95, 2),
    )


def _histogram(values: np.ndarray, bins: int = 20) -> tuple[DistributionBin, ...]:
    if np.all(values == values[0]):
        return (
            DistributionBin(
                lower=round(float(values[0]) - 0.5, 2),
                upper=round(float(values[0]) + 0.5, 2),
                count=int(values.size),
            ),
        )
    counts, edges = np.histogram(values, bins=bins)
    return tuple(
        DistributionBin(
            lower=round(float(edges[index]), 2),
            upper=round(float(edges[index + 1]), 2),
            count=int(count),
        )
        for index, count in enumerate(counts)
    )


def _target_curve(outcomes: list[PathOutcome], timeout_days: int) -> tuple[TargetReachPoint, ...]:
    target_days = sorted(
        outcome.days
        for outcome in outcomes
        if outcome.outcome == "PASS" or any(event.event == "PAYOUT" for event in outcome.events)
    )
    points: list[TargetReachPoint] = []
    reached = 0
    for day in range(1, timeout_days + 1):
        while reached < len(target_days) and target_days[reached] <= day:
            reached += 1
        points.append(TargetReachPoint(day=day, probability=round(reached / len(outcomes), 6)))
    return tuple(points)


def simulate_prop_paths(
    run_id: str,
    rule: PropRuleSet,
    pnl_values: tuple[float, ...],
    *,
    seed: int = 20260901,
    paths: int = 300,
    allow_unverified: bool = False,
) -> PropSimulation:
    if not allow_unverified and not rule.runnable(date.today()):
        raise ValueError("RULE_LOCKED_UNVERIFIED_OR_EXPIRED")
    if paths < 20:
        raise ValueError("at least 20 paths required")
    pnl = np.asarray(pnl_values, dtype=float)
    if pnl.size < MIN_TRADING_DAYS:
        raise ValueError(
            f"INSUFFICIENT_DAYS: {pnl.size} trading days observed, {MIN_TRADING_DAYS} required. "
            "Resampling a long evaluation from a handful of days measures the sample, "
            "not the strategy."
        )
    rng = np.random.default_rng(seed)
    outcomes: list[PathOutcome] = []
    equities: list[tuple[float, ...]] = []
    for _ in range(paths):
        sample = _block_bootstrap(pnl, rule.timeout_days, rng)
        outcome, equity = replay_path(rule, sample)
        outcomes.append(outcome)
        equities.append(equity)
    passes = sum(item.outcome in {"PASS", "SURVIVED"} for item in outcomes)
    failures = sum(item.outcome == "FAIL" for item in outcomes)
    timeouts = sum(item.outcome == "TIMEOUT" for item in outcomes)
    terminal_pnl = np.asarray(
        [item.terminal_balance - rule.starting_balance + item.payouts for item in outcomes],
        dtype=float,
    )
    drawdowns = np.asarray([_max_drawdown(path) for path in equities], dtype=float)
    target_days = [item.days for item in outcomes if item.outcome in {"PASS", "SURVIVED"}]
    loss_days = [item.days for item in outcomes if item.outcome == "FAIL"]
    target_q10, target_median, target_q90 = _optional_quantiles(target_days)
    loss_q10, loss_median, loss_q90 = _optional_quantiles(loss_days)
    boundary_race = BoundaryRaceSummary(
        target_first_probability=round(passes / paths, 6),
        loss_first_probability=round(failures / paths, 6),
        timeout_probability=round(timeouts / paths, 6),
        target_days_p10=None if target_q10 is None else round(target_q10, 2),
        target_days_median=None if target_median is None else round(target_median, 2),
        target_days_p90=None if target_q90 is None else round(target_q90, 2),
        loss_days_p10=None if loss_q10 is None else round(loss_q10, 2),
        loss_days_median=None if loss_median is None else round(loss_median, 2),
        loss_days_p90=None if loss_q90 is None else round(loss_q90, 2),
    )
    return_drawdown = tuple(
        ReturnDrawdownPoint(
            terminal_pnl=round(float(net), 2),
            max_drawdown=round(float(drawdown), 2),
            outcome=outcome.outcome,
        )
        for net, drawdown, outcome in zip(
            terminal_pnl[:500], drawdowns[:500], outcomes[:500], strict=True
        )
    )
    # The interval must reflect how few days were observed, not just how many
    # paths were drawn. A Wilson interval over paths alone reports near-certainty
    # from a five-day sample, which is exactly the false confidence to avoid.
    low, high = _sample_aware_interval(pnl, rule, paths, seed)
    payload = {"run": run_id, "rule": rule.rule_id, "seed": seed, "paths": paths}
    return PropSimulation(
        simulation_id=stable_id("prop", payload),
        run_id=run_id,
        rule=rule,
        seed=seed,
        path_count=paths,
        pass_count=passes,
        fail_count=failures,
        timeout_count=timeouts,
        pass_rate=round(passes / paths, 6),
        interval_low=round(low, 6),
        interval_high=round(high, 6),
        mean_payout=round(sum(item.payouts for item in outcomes) / paths, 2),
        risk_of_ruin=round(failures / paths, 6),
        boundary_race=boundary_race,
        target_reach_curve=_target_curve(outcomes, rule.timeout_days),
        terminal_histogram=_histogram(terminal_pnl),
        return_drawdown_map=return_drawdown,
        tail_risk=_tail_summary(terminal_pnl),
        outcomes=tuple(outcomes[:100]),
        equity_paths=tuple(equities[:100]),
    )
