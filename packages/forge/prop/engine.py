from __future__ import annotations

import math
from dataclasses import dataclass
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
    EquityFanPoint,
    JourneyStage,
    PathOutcome,
    PayoutSummary,
    PropJourney,
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
    #: The percentile band per day, over every path. `equity_paths` below is a
    #: sample of individual accounts; this is the population, and the two answer
    #: different questions.
    equity_fan: tuple[EquityFanPoint, ...]
    payout: PayoutSummary
    #: Why the failing accounts failed, counted over **every** path. This has to
    #: be summarised here rather than by a caller, because `outcomes` below is a
    #: sample: a consumer tallying reasons from it reported 100 failures out of a
    #: thousand and displayed the tally under the full count.
    failure_reasons: dict[str, int]
    #: A sample of the paths, kept so a reader can look at individual accounts.
    #: `outcome_sample_size` says how many, so nothing has to infer it from the
    #: length and nothing can mistake the sample for the population.
    outcomes: tuple[PathOutcome, ...]
    outcome_sample_size: int
    equity_paths: tuple[tuple[float, ...], ...]
    #: What this simulation actually rests on. Three of these always hold and
    #: describe the method; the rest are supplied by the caller and describe the
    #: input, so a label here means something rather than being decoration.
    labels: tuple[str, ...]


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

# Largest window a single backtest request may ask for. The sixteen-year NQ
# archive is 4.8M bars, so the ceiling has to clear it or the longest range on
# offer would be rejected by the request model before anything ran. Runs this
# size go through the job queue rather than blocking a request.
MAX_BACKTEST_BARS = 5_000_000


@dataclass(frozen=True)
class DayCoverage:
    """Whether a backtest saw enough distinct days to simulate an evaluation.

    The blocker is almost never that the *strategy* is wrong — it is that the
    backtest window was too short to contain a month of trading. A bare refusal
    leaves the user guessing, so this carries the arithmetic that tells them
    exactly how much more data to ask for.
    """

    trading_days: int
    days_required: int
    bars_used: int
    span_days: int
    bars_per_trading_day: float
    suggested_bar_count: int | None
    max_bar_count: int
    suggestion_exceeds_limit: bool
    # Share of the requested window that reached this artifact. The prop
    # simulator reads the validation partition only, so a suggestion phrased in
    # partition bars would understate the request by five times.
    partition_fraction: float

    @property
    def sufficient(self) -> bool:
        return self.trading_days >= self.days_required

    @property
    def suggested_request_bars(self) -> int | None:
        """The suggestion in request units, not partition units."""
        if self.suggested_bar_count is None:
            return None
        fraction = self.partition_fraction if self.partition_fraction > 0 else 1.0
        return math.ceil(self.suggested_bar_count / fraction)

    def explain(self) -> str:
        if self.sufficient:
            return f"{self.trading_days} trading days observed; {self.days_required} required."
        parts = [
            f"Only {self.trading_days} trading day"
            f"{'' if self.trading_days == 1 else 's'} produced trades, "
            f"but {self.days_required} are required. Estimating a "
            f"{self.days_required}+ day evaluation from fewer measures the sample, "
            "not the strategy."
        ]
        if self.bars_used:
            parts.append(
                f"The backtest ran on {self.bars_used:,} bars spanning {self.span_days} "
                f"calendar day{'' if self.span_days == 1 else 's'}."
            )
        if self.suggested_bar_count and not self.suggestion_exceeds_limit:
            parts.append(
                f"At the observed rate of {self.bars_per_trading_day:,.0f} bars per trading "
                f"day, re-run the backtest with bar_count of about "
                f"{self.suggested_request_bars:,}."
            )
        elif self.suggested_bar_count:
            parts.append(
                f"Reaching {self.days_required} trading days would take a bar_count of "
                f"roughly {self.suggested_request_bars:,}, beyond the {self.max_bar_count:,}-bar "
                "limit for a single run. This strategy trades too rarely to be evaluated "
                "against a prop account on the available window."
            )
        else:
            parts.append(
                "This strategy took trades on too few days to extrapolate a window from; "
                "it may simply trade too rarely for a prop evaluation."
            )
        return " ".join(parts)


def assess_day_coverage(
    trading_days: int,
    bars_used: int,
    span_days: int,
    required: int = MIN_TRADING_DAYS,
    max_bar_count: int = MAX_BACKTEST_BARS,
    partition_fraction: float = 1.0,
) -> DayCoverage:
    """Diagnose a short backtest and size the window that would fix it.

    The suggestion scales the bar count by the ratio of required to observed
    *trading* days rather than calendar days, because a selective strategy may
    trade on only a fraction of the sessions its window covers. Extrapolating
    from calendar span would under-shoot exactly those strategies.
    """
    density = bars_used / trading_days if trading_days > 0 and bars_used > 0 else 0.0
    suggested: int | None = None
    if 0 < trading_days < required and density > 0:
        # A 20% margin, because trade frequency is not perfectly uniform and a
        # window that lands one day short wastes the whole re-run.
        suggested = math.ceil(density * required * 1.2)
    return DayCoverage(
        trading_days=trading_days,
        days_required=required,
        bars_used=bars_used,
        span_days=span_days,
        bars_per_trading_day=round(density, 2),
        suggested_bar_count=suggested,
        max_bar_count=max_bar_count,
        suggestion_exceeds_limit=(
            suggested is not None
            and math.ceil(suggested / (partition_fraction or 1.0)) > max_bar_count
        ),
        partition_fraction=partition_fraction,
    )


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


def _equity_fan(equities: list[tuple[float, ...]], timeout_days: int) -> tuple[EquityFanPoint, ...]:
    """Per-day percentile bands over every path, closed accounts carried forward.

    Paths have different lengths: one that hits the maximum loss on day 12 has
    no day 40. Taking the percentile over whatever is present on day 40 would
    compute it over survivors, which is the standard way to make a simulation
    look better than it was -- the band tightens and drifts upward exactly
    because the bad paths stopped being counted.

    A closed account keeps its final balance instead, so every day is a
    percentile over the same `len(equities)` accounts and the days are
    comparable to each other. `live` carries the honest half of that: it is the
    count still trading, and it is what tells a reader whether a narrowing band
    means agreement or attrition.
    """
    if not equities:
        return ()
    span = min(timeout_days, max(len(path) for path in equities) - 1)
    counted = len(equities)
    points: list[EquityFanPoint] = []
    for day in range(span + 1):
        values = np.asarray(
            [path[day] if day < len(path) else path[-1] for path in equities], dtype=float
        )
        live = sum(1 for path in equities if day < len(path))
        p05, p25, median, p75, p95 = (
            float(value) for value in np.percentile(values, (5, 25, 50, 75, 95))
        )
        points.append(
            EquityFanPoint(
                day=day,
                p05=round(p05, 2),
                p25=round(p25, 2),
                median=round(median, 2),
                p75=round(p75, 2),
                p95=round(p95, 2),
                live=live,
                resolved=counted - live,
            )
        )
    return tuple(points)


def _payout_summary(payouts: list[float]) -> PayoutSummary:
    values = np.asarray(payouts, dtype=float)
    p05, median, p95 = (float(value) for value in np.percentile(values, (5, 50, 95)))
    return PayoutSummary(
        mean=round(float(np.mean(values)), 2),
        median=round(median, 2),
        p05=round(p05, 2),
        p95=round(p95, 2),
        best=round(float(np.max(values)), 2),
        any_probability=round(float(np.count_nonzero(values > 0) / values.size), 6),
    )


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


#: How many individual accounts are kept for inspection. Every *statistic* is
#: computed over all paths; this only bounds what is carried back for a reader
#: to look at one at a time.
OUTCOME_SAMPLE = 100

#: Assumptions of the method itself. They hold for every simulation this
#: function produces, whatever it is fed.
METHOD_LABELS: tuple[str, ...] = (
    "RESEARCH_ONLY",
    "BLOCK_BOOTSTRAP",
    "DAILY_SETTLEMENT_APPROXIMATION",
)


def simulate_prop_paths(
    run_id: str,
    rule: PropRuleSet,
    pnl_values: tuple[float, ...],
    *,
    seed: int = 20260901,
    paths: int = 300,
    allow_unverified: bool = False,
    source_labels: tuple[str, ...] = ("UNDECLARED_SOURCE",),
) -> PropSimulation:
    """Simulate `paths` accounts against `rule`, resampling the observed days.

    `source_labels` says what the daily series actually is — the caller knows
    and this function cannot. It used to assert `SAMPLE_DATA` and
    `UNVERIFIED_RULES` unconditionally, which meant a simulation over a real
    strategy's real trade ledger against a verified rule set carried both. A
    label that is always present carries no information, and the cost of one is
    not zero: it teaches the reader to skip the whole row, including the labels
    that do mean something.
    """
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
    reasons: dict[str, int] = {}
    for item in outcomes:
        if item.failure_reason:
            reasons[item.failure_reason] = reasons.get(item.failure_reason, 0) + 1
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
        equity_fan=_equity_fan(equities, rule.timeout_days),
        payout=_payout_summary([item.payouts for item in outcomes]),
        failure_reasons=reasons,
        outcomes=tuple(outcomes[:OUTCOME_SAMPLE]),
        outcome_sample_size=min(len(outcomes), OUTCOME_SAMPLE),
        equity_paths=tuple(equities[:OUTCOME_SAMPLE]),
        labels=(
            *METHOD_LABELS,
            *(() if rule.verified else ("UNVERIFIED_RULES",)),
            *source_labels,
        ),
    )


#: What the two-stage simulation itself assumes, on top of the per-leg method
#: labels. Stated separately because it is the assumption a reader is most
#: likely to carry away wrongly.
JOURNEY_LABELS: tuple[str, ...] = (
    "TWO_STAGE_RESAMPLE",
    "FUNDED_LEG_RESAMPLES_THE_SAME_DAYS",
)


def _stage(
    rule: PropRuleSet,
    outcomes: list[PathOutcome],
    reached: int,
    cleared: list[PathOutcome],
) -> JourneyStage:
    days = _optional_quantiles([item.days for item in cleared])
    return JourneyStage(
        rule_id=rule.rule_id,
        display_name=rule.display_name,
        phase=rule.phase,
        reached=reached,
        cleared=len(cleared),
        failed=sum(1 for item in outcomes if item.outcome == "FAIL"),
        timed_out=sum(1 for item in outcomes if item.outcome == "TIMEOUT"),
        days_p10=None if days[0] is None else round(days[0], 2),
        days_median=None if days[1] is None else round(days[1], 2),
        days_p90=None if days[2] is None else round(days[2], 2),
    )


def _first_payout_day(outcome: PathOutcome) -> int | None:
    for event in outcome.events:
        if event.event == "PAYOUT":
            return event.day
    return None


def _journey_interval(
    pnl: np.ndarray,
    challenge: PropRuleSet,
    funded: PropRuleSet,
    paths: int,
    seed: int,
    replicates: int = 40,
) -> tuple[float, float]:
    """The payout-probability interval, widened by how few days were observed.

    Deliberately the same double bootstrap as `_sample_aware_interval` rather
    than a Wilson interval over paths. A Wilson interval would treat the path
    count as the sample size and report near-certainty from thirty observed
    days, which is the precise false confidence the single-leg simulation
    already refuses to produce. Drawing more paths from the same thirty days
    cannot learn anything more about the strategy, and the interval has to say
    so.
    """
    rng = np.random.default_rng(seed + 1451)
    inner = max(20, paths // replicates)
    rates: list[float] = []
    for _ in range(replicates):
        days = _block_bootstrap(pnl, pnl.size, rng)
        paid = 0
        for _ in range(inner):
            outcome, _ = replay_path(
                challenge, _block_bootstrap(days, challenge.timeout_days, rng)
            )
            if outcome.outcome not in {"PASS", "SURVIVED"}:
                continue
            after, _ = replay_path(funded, _block_bootstrap(days, funded.timeout_days, rng))
            paid += _first_payout_day(after) is not None
        rates.append(paid / inner)
    return float(np.percentile(rates, 2.5)), float(np.percentile(rates, 97.5))


def simulate_prop_journey(
    run_id: str,
    challenge: PropRuleSet,
    funded: PropRuleSet,
    pnl_values: tuple[float, ...],
    *,
    seed: int = 20260901,
    paths: int = 300,
    allow_unverified: bool = False,
) -> PropJourney:
    """Simulate challenge then funded as one run, per account.

    The question a funded-account trader actually has is not "what is my pass
    rate" and not "what is my funded survival rate" -- it is "what are the odds
    I ever get paid, and how long does that take". Those two numbers cannot be
    multiplied into that answer, because the accounts that reach the funded leg
    are exactly the ones that passed, and under resampling they are the luckier
    draws rather than a fair sample. Playing the journey through keeps the
    conditioning where it belongs.

    What this does assume, and what `FUNDED_LEG_RESAMPLES_THE_SAME_DAYS` says:
    the funded leg draws fresh blocks from the same observed days. So it takes
    the strategy to behave after the challenge the way it behaved during it. It
    introduces no assumption the single-leg simulation does not already make,
    but it applies it twice, over a longer horizon.
    """
    if challenge.phase != "CHALLENGE":
        raise ValueError(f"NOT_A_CHALLENGE: {challenge.rule_id} is {challenge.phase}")
    if funded.phase != "FUNDED":
        raise ValueError(f"NOT_A_FUNDED_RULE: {funded.rule_id} is {funded.phase}")
    if challenge.provider != funded.provider:
        raise ValueError(
            f"PROVIDER_MISMATCH: {challenge.provider} challenge against {funded.provider} funded. "
            "A journey crosses one provider's two phases; joining two providers would describe "
            "an account nobody can open."
        )
    if not allow_unverified and not (
        challenge.runnable(date.today()) and funded.runnable(date.today())
    ):
        raise ValueError("RULE_LOCKED_UNVERIFIED_OR_EXPIRED")
    if paths < 20:
        raise ValueError("at least 20 paths required")
    pnl = np.asarray(pnl_values, dtype=float)
    if pnl.size < MIN_TRADING_DAYS:
        raise ValueError(
            f"INSUFFICIENT_DAYS: {pnl.size} trading days observed, {MIN_TRADING_DAYS} required."
        )

    rng = np.random.default_rng(seed)
    challenge_outcomes: list[PathOutcome] = []
    funded_outcomes: list[PathOutcome] = []
    payouts: list[float] = []
    days_to_payout: list[int] = []
    for _ in range(paths):
        outcome, _ = replay_path(challenge, _block_bootstrap(pnl, challenge.timeout_days, rng))
        challenge_outcomes.append(outcome)
        if outcome.outcome not in {"PASS", "SURVIVED"}:
            payouts.append(0.0)
            continue
        after, _ = replay_path(funded, _block_bootstrap(pnl, funded.timeout_days, rng))
        funded_outcomes.append(after)
        payouts.append(after.payouts)
        first = _first_payout_day(after)
        if first is not None:
            days_to_payout.append(outcome.days + first)

    passed = [item for item in challenge_outcomes if item.outcome in {"PASS", "SURVIVED"}]
    paid = [item for item in funded_outcomes if item.payouts > 0]
    payout_days = _optional_quantiles(days_to_payout)
    reached_payout = len(days_to_payout)
    low, high = _journey_interval(pnl, challenge, funded, paths, seed)
    return PropJourney(
        journey_id=stable_id(
            "journey",
            {
                "run": run_id,
                "challenge": challenge.rule_id,
                "funded": funded.rule_id,
                "seed": seed,
                "paths": paths,
            },
        ),
        challenge=_stage(challenge, challenge_outcomes, paths, passed),
        funded=_stage(funded, funded_outcomes, len(passed), paid),
        path_count=paths,
        seed=seed,
        payout_probability=round(reached_payout / paths, 6),
        payout_interval_low=round(low, 6),
        payout_interval_high=round(high, 6),
        payout=_payout_summary(payouts),
        days_to_payout_p10=None if payout_days[0] is None else round(payout_days[0], 2),
        days_to_payout_median=None if payout_days[1] is None else round(payout_days[1], 2),
        days_to_payout_p90=None if payout_days[2] is None else round(payout_days[2], 2),
        labels=(
            *METHOD_LABELS,
            *JOURNEY_LABELS,
            *(
                ()
                if challenge.verified and funded.verified
                else ("UNVERIFIED_RULES",)
            ),
        ),
    )
