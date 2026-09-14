from __future__ import annotations

from datetime import date
from typing import Literal

from forge.contracts.models import FrozenModel


class PropRuleSet(FrozenModel):
    rule_id: str
    display_name: str
    provider: str
    phase: Literal["CHALLENGE", "FUNDED"]
    starting_balance: float
    profit_target: float
    maximum_loss: float
    daily_loss_limit: float | None
    trail_mode: Literal["EOD", "INTRADAY"]
    floor_cap: float
    minimum_days: int
    consistency_percent: float | None
    timeout_days: int
    payout_threshold: float | None = None
    payout_amount: float | None = None
    verified: bool
    source_url: str
    source_hash: str
    effective_from: date
    review_expires_at: date
    timezone: str

    def runnable(self, today: date) -> bool:
        return self.verified and self.effective_from <= today <= self.review_expires_at


class BoundaryEvent(FrozenModel):
    day: int
    event: str
    balance: float
    loss_floor: float
    target: float


class PathOutcome(FrozenModel):
    outcome: Literal["PASS", "FAIL", "TIMEOUT", "SURVIVED"]
    days: int
    terminal_balance: float
    failure_reason: str | None
    payouts: float
    events: tuple[BoundaryEvent, ...]


class TargetReachPoint(FrozenModel):
    day: int
    probability: float


class DistributionBin(FrozenModel):
    lower: float
    upper: float
    count: int


class ReturnDrawdownPoint(FrozenModel):
    terminal_pnl: float
    max_drawdown: float
    outcome: Literal["PASS", "FAIL", "TIMEOUT", "SURVIVED"]


class BoundaryRaceSummary(FrozenModel):
    target_first_probability: float
    loss_first_probability: float
    timeout_probability: float
    target_days_p10: float | None
    target_days_median: float | None
    target_days_p90: float | None
    loss_days_p10: float | None
    loss_days_median: float | None
    loss_days_p90: float | None


class EquityFanPoint(FrozenModel):
    """The spread of account balances on one day, across **every** path.

    `live` and `resolved` are part of the reading, not metadata. A path that
    fails on day 12 has no balance on day 40, and dropping it would compute the
    band over survivors only -- a fan that narrows because the losers left,
    shown as a fan that narrows because the outcome became certain. Closed
    accounts are therefore carried forward at their final balance, which keeps
    the denominator at `path_count` for every day, and `live` says how many
    accounts were still trading so the reader can see which of the two is
    happening.
    """

    day: int
    p05: float
    p25: float
    median: float
    p75: float
    p95: float
    live: int
    resolved: int


class PayoutSummary(FrozenModel):
    """The payout distribution behind the mean.

    A mean payout on its own cannot distinguish "every account paid about this"
    from "one account in twenty paid twenty times this". `any_probability` is
    the share of accounts that received anything at all, which is the figure
    that separates the two.
    """

    mean: float
    median: float
    p05: float
    p95: float
    best: float
    any_probability: float


class JourneyStage(FrozenModel):
    """One leg of the funded-account journey, counted over the accounts that reached it.

    `reached` is the denominator and is not always `path_count`: only accounts
    that passed the challenge ever start the funded leg. Reporting a funded
    survival rate against every account simulated would understate it; reporting
    the journey's payout rate against the ones that got there would overstate
    the journey. Both denominators are therefore carried rather than inferred.
    """

    rule_id: str
    display_name: str
    phase: Literal["CHALLENGE", "FUNDED"]
    reached: int
    cleared: int
    failed: int
    timed_out: int
    days_p10: float | None
    days_median: float | None
    days_p90: float | None


class PropJourney(FrozenModel):
    """Challenge and funded as one simulated run rather than two separate ones.

    The two legs cannot be joined by multiplying their pass rates. An account
    only reaches the funded leg by having passed the challenge, and under a
    resampling model the paths that pass are not a fair sample of all paths --
    multiplying assumes an independence the journey does not have. So the
    journey is simulated straight through: each path plays the challenge, and
    the ones that clear it go on to play the funded account.
    """

    journey_id: str
    challenge: JourneyStage
    funded: JourneyStage
    path_count: int
    seed: int
    #: Share of *all* simulated accounts that reached at least one payout.
    payout_probability: float
    payout_interval_low: float
    payout_interval_high: float
    #: Over every account, including the ones that never reached the funded leg.
    payout: PayoutSummary
    days_to_payout_p10: float | None
    days_to_payout_median: float | None
    days_to_payout_p90: float | None
    labels: tuple[str, ...]


class TailRiskSummary(FrozenModel):
    var_95: float
    cvar_95: float
    skewness: float
    excess_kurtosis: float
    terminal_p05: float
    terminal_median: float
    terminal_p95: float
