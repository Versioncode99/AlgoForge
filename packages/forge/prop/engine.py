from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from typing import Literal

import numpy as np
import yaml

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel
from forge.prop.models import BoundaryEvent, PathOutcome, PropRuleSet


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
    outcomes: tuple[PathOutcome, ...]
    equity_paths: tuple[tuple[float, ...], ...]
    labels: tuple[str, ...] = ("SAMPLE_DATA", "UNVERIFIED_RULES", "RESEARCH_ONLY")


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
    rng = np.random.default_rng(seed)
    outcomes: list[PathOutcome] = []
    equities: list[tuple[float, ...]] = []
    for _ in range(paths):
        sample = rng.choice(pnl, size=rule.timeout_days, replace=True)
        outcome, equity = replay_path(rule, sample)
        outcomes.append(outcome)
        equities.append(equity)
    passes = sum(item.outcome in {"PASS", "SURVIVED"} for item in outcomes)
    failures = sum(item.outcome == "FAIL" for item in outcomes)
    timeouts = sum(item.outcome == "TIMEOUT" for item in outcomes)
    low, high = _wilson(passes, paths)
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
        outcomes=tuple(outcomes[:100]),
        equity_paths=tuple(equities[:100]),
    )
