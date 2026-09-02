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


class TailRiskSummary(FrozenModel):
    var_95: float
    cvar_95: float
    skewness: float
    excess_kurtosis: float
    terminal_p05: float
    terminal_median: float
    terminal_p95: float
