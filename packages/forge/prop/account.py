"""How close is this account to breaching its rules?

`forge.prop.engine` answers a *distributional* question: across ten thousand
simulated paths, how often does an account like this pass. This module answers
the question the operator actually has open in front of them — where does *this*
account stand right now, against *its* contract, and what is left before
something breaks.

Three commitments shape the design.

**The rule set is configuration, not code.** Nothing here knows the name of any
prop firm, and no threshold is a literal in a branch. An `AccountRules` is data:
it can be written by hand, loaded from `rules/`, or built in the interface, and
two firms with different trailing behaviour are two values rather than two code
paths. `custom_limits` extends it further, but only over metrics the account
state actually measures — a limit naming a quantity nobody observes would be a
rule that can never be evaluated, and reporting it as satisfied would be a lie.

**Absence is not compliance.** A rule that cannot be assessed reports
`NOT_ASSESSED` and says why. This is the same three-valued discipline the judge
uses, and for the same reason: a green row that means "we never checked" is worse
than no row, because the operator stops looking.

**A breach is stated, never softened.** `can_trade` goes false on a hard breach
and the assessment names which rule did it. Nothing in this module rounds a
number toward the answer the operator would prefer.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel

#: Headroom fractions at which the assessment starts saying something. A rule
#: with a quarter of its allowance left is not yet broken and is not fine; an
#: interface that only distinguishes OK from BREACH tells the operator nothing
#: until it is too late to act on.
WARNING_HEADROOM = 0.25
CAUTION_HEADROOM = 0.50


class TrailMode(StrEnum):
    """How the loss floor follows profit.

    The three that actually occur in funded-account contracts. `STATIC` never
    moves; `END_OF_DAY` ratchets on settled balance; `INTRADAY` ratchets on the
    highest equity touched, which is materially harsher and is the one operators
    most often misjudge.
    """

    STATIC = "static"
    END_OF_DAY = "end_of_day"
    INTRADAY = "intraday"


class Metric(StrEnum):
    """The observable quantities a rule may be written against.

    A closed set on purpose. `custom_limits` is the extension point for a firm's
    own constraint, and bounding it to metrics `AccountState` reports is what
    keeps "configurable" from meaning "accepts a rule it will silently never
    check".
    """

    DAILY_LOSS = "daily_loss"
    TOTAL_DRAWDOWN = "total_drawdown"
    OPEN_CONTRACTS = "open_contracts"
    OPEN_POSITIONS = "open_positions"
    ORDER_CONTRACTS = "order_contracts"
    TRADES_TODAY = "trades_today"
    TRADING_DAYS = "trading_days"
    PROFIT = "profit"
    EXPOSURE_NOTIONAL = "exposure_notional"
    RISK_PER_TRADE = "risk_per_trade"
    LARGEST_DAY_SHARE = "largest_day_share"


class Level(StrEnum):
    """How a single rule stands.

    Ordered by severity below in `_SEVERITY`; `NOT_ASSESSED` deliberately sorts
    above `OK`, because "we could not check this" must never be summarised as a
    clean account.
    """

    OK = "ok"
    CAUTION = "caution"
    WARNING = "warning"
    BREACH = "breach"
    NOT_ASSESSED = "not_assessed"


_SEVERITY: dict[Level, int] = {
    Level.OK: 0,
    Level.CAUTION: 1,
    Level.NOT_ASSESSED: 2,
    Level.WARNING: 3,
    Level.BREACH: 4,
}


class SessionWindow(FrozenModel):
    """A period during which trading is permitted, in the rule set's timezone."""

    label: str = ""
    opens: time
    closes: time

    def contains(self, moment: time) -> bool:
        # A window that wraps midnight is expressed with closes < opens, which is
        # how overnight sessions are actually written.
        if self.closes >= self.opens:
            return self.opens <= moment <= self.closes
        return moment >= self.opens or moment <= self.closes

    def describe(self) -> str:
        span = f"{self.opens.strftime('%H:%M')}-{self.closes.strftime('%H:%M')}"
        return f"{self.label} {span}".strip()


class CustomLimit(FrozenModel):
    """A firm-specific constraint, expressed over a measured metric."""

    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    metric: Metric
    #: `max` breaches when the observed value goes above `value`; `min` when it
    #: falls below. Both directions occur: a contract cap is a maximum, a minimum
    #: trading-day count is a minimum.
    comparison: Literal["max", "min"]
    value: float
    #: A minimum that is only meaningful at the end of an evaluation — minimum
    #: trading days, for instance — is not a breach mid-run. Marking it advisory
    #: reports it without failing the account for being early.
    advisory: bool = False


class AccountRules(FrozenModel):
    """One account's contract, as data.

    Every ceiling is optional except `maximum_loss`, because an account with no
    loss limit at all is not a funded account and treating it as one would make
    the drawdown panel meaningless.
    """

    schema_version: Literal["1"] = "1"
    name: str = Field(min_length=1, max_length=120)
    #: Free text. AlgoForge asserts nothing about what any named firm's contract
    #: actually says; this is the operator's label for their own configuration.
    provider: str = ""
    phase: Literal["CHALLENGE", "FUNDED"] = "CHALLENGE"
    starting_balance: float = Field(gt=0)
    maximum_loss: float = Field(gt=0)
    trail_mode: TrailMode = TrailMode.STATIC
    #: The trailing floor stops rising once it reaches this balance. `None` means
    #: it never stops, which is the harsher reading and so is not the default
    #: silently — a caller has to leave it out deliberately.
    floor_cap: float | None = None
    daily_loss_limit: float | None = Field(default=None, gt=0)
    profit_target: float | None = Field(default=None, gt=0)
    max_position_contracts: int | None = Field(default=None, gt=0)
    max_order_contracts: int | None = Field(default=None, gt=0)
    max_open_positions: int | None = Field(default=None, gt=0)
    minimum_trading_days: int | None = Field(default=None, gt=0)
    #: No single day may account for more than this share of total profit.
    #: Expressed 0-1; 0.4 is a common shape.
    consistency_share: float | None = Field(default=None, gt=0, le=1)
    max_risk_per_trade: float | None = Field(default=None, gt=0)
    session_windows: tuple[SessionWindow, ...] = ()
    timezone: str = "UTC"
    custom_limits: tuple[CustomLimit, ...] = ()
    #: What this configuration rests on, stated by whoever entered it. Left empty
    #: rather than filled with a plausible URL.
    source_note: str = ""

    @model_validator(mode="after")
    def _coherent(self) -> AccountRules:
        initial_floor = self.starting_balance - self.maximum_loss
        if self.floor_cap is not None and self.floor_cap < initial_floor:
            raise ValueError(
                "floor_cap sits below the initial floor, so the floor could never reach it"
            )
        if self.daily_loss_limit is not None and self.daily_loss_limit > self.maximum_loss:
            raise ValueError(
                "daily_loss_limit exceeds maximum_loss: a single permitted day would end "
                "the account, which is not a rule set anyone wrote on purpose"
            )
        keys = [limit.key for limit in self.custom_limits]
        duplicates = {key for key in keys if keys.count(key) > 1}
        if duplicates:
            raise ValueError(f"duplicate custom limit keys: {', '.join(sorted(duplicates))}")
        return self

    @property
    def rules_id(self) -> str:
        return stable_id("proprules", self.model_dump(mode="json"))

    @property
    def initial_floor(self) -> float:
        return self.starting_balance - self.maximum_loss


class AccountState(FrozenModel):
    """What the account looks like right now.

    Supplied rather than invented. This build has no broker connector, so the
    state comes from one of two honest places: the operator recording it, or
    `state_from_trades` deriving it from trades that actually exist in the
    ledger. Nothing here fabricates a balance.
    """

    as_of: datetime
    #: Settled balance: realised only.
    balance: float
    #: Balance plus open profit. Equal to `balance` when flat.
    equity: float
    #: The highest settled balance the account has reached, for EOD trailing.
    high_water_balance: float
    #: The highest equity the account has touched, for intraday trailing. Never
    #: below `high_water_balance`; the validator enforces it rather than trusting
    #: the caller, because getting this wrong understates the floor.
    high_water_equity: float
    realised_today: float = 0.0
    unrealised: float = 0.0
    open_contracts: int = 0
    open_positions: int = 0
    largest_order_contracts: int = 0
    trades_today: int = 0
    trading_days: int = 0
    #: Realised profit for each completed trading day, used for consistency.
    #: Losing days are included: a consistency rule computed over winners only
    #: reports a different, easier number than the contract describes.
    daily_profits: tuple[float, ...] = ()
    exposure_notional: float = 0.0
    #: The capital at risk on the largest currently-open trade, when the caller
    #: knows it. `None` means unknown, and the rule reports NOT_ASSESSED.
    risk_per_trade: float | None = None

    @model_validator(mode="after")
    def _high_water_is_coherent(self) -> AccountState:
        if self.high_water_equity < self.high_water_balance:
            raise ValueError("high_water_equity cannot sit below high_water_balance")
        return self

    @property
    def daily_loss(self) -> float:
        """Today's loss as a positive magnitude; zero on a flat or winning day.

        Open profit counts. Most contracts assess the daily limit on equity, not
        on settled balance, and assessing it on balance alone would report an
        account as comfortable while an open position was taking it through the
        limit.
        """
        day = self.realised_today + self.unrealised
        return -day if day < 0 else 0.0


class RuleStatus(FrozenModel):
    """One rule, evaluated.

    `buffer` is what is left before a breach, in the rule's own units, and
    `headroom` is that as a fraction of the whole allowance. Both are `None` when
    the rule has no allowance to speak of — a session restriction is satisfied or
    it is not.
    """

    key: str
    label: str
    level: Level
    observed: float | int | str | None
    limit: float | int | str | None
    buffer: float | None = None
    headroom: float | None = None
    detail: str = ""

    @property
    def breached(self) -> bool:
        return self.level is Level.BREACH


class AccountAssessment(FrozenModel):
    """The whole contract, evaluated at one instant."""

    rules_id: str
    rules_name: str
    as_of: datetime
    level: Level
    can_trade: bool
    balance: float
    equity: float
    loss_floor: float
    statuses: tuple[RuleStatus, ...]
    #: Keys of the rules that are breached, so a caller does not have to filter.
    breaches: tuple[str, ...]
    limitations: tuple[str, ...] = ()

    def status(self, key: str) -> RuleStatus | None:
        return next((s for s in self.statuses if s.key == key), None)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def loss_floor(rules: AccountRules, state: AccountState) -> float:
    """The balance at which this account is finished.

    The one calculation in the module that everything else leans on, so it is a
    function rather than three lines inside the evaluator: the drawdown panel,
    the buffer, and the breach test all have to agree, and they agree by calling
    this.
    """
    if rules.trail_mode is TrailMode.STATIC:
        return rules.initial_floor
    peak = (
        state.high_water_balance
        if rules.trail_mode is TrailMode.END_OF_DAY
        else state.high_water_equity
    )
    trailed = max(rules.initial_floor, peak - rules.maximum_loss)
    if rules.floor_cap is not None:
        trailed = min(trailed, rules.floor_cap)
    return trailed


def _headroom_level(headroom: float, *, breached: bool) -> Level:
    if breached:
        return Level.BREACH
    if headroom <= WARNING_HEADROOM:
        return Level.WARNING
    if headroom <= CAUTION_HEADROOM:
        return Level.CAUTION
    return Level.OK


def _ceiling(
    key: str,
    label: str,
    observed: float | None,
    limit: float | None,
    *,
    detail_ok: str,
    detail_breach: str,
    unassessed: str = "",
) -> RuleStatus:
    """A rule of the form 'observed must stay at or below limit'.

    Shared because every allowance-shaped rule has the same three failure modes
    — not configured, within, exceeded — and writing them out per rule is how the
    daily-loss row and the drawdown row end up disagreeing about whether equal
    counts as a breach. It does: contracts are written as "must not reach".
    """
    if limit is None:
        return RuleStatus(
            key=key, label=label, level=Level.NOT_ASSESSED, observed=observed, limit=None,
            detail=unassessed or "no limit is configured for this rule",
        )
    if observed is None:
        return RuleStatus(
            key=key, label=label, level=Level.NOT_ASSESSED, observed=None, limit=limit,
            detail=unassessed or "the account state does not report this quantity",
        )
    buffer = limit - observed
    headroom = max(0.0, buffer / limit) if limit else 0.0
    breached = observed >= limit
    return RuleStatus(
        key=key,
        label=label,
        level=_headroom_level(headroom, breached=breached),
        observed=round(observed, 4),
        limit=round(limit, 4),
        buffer=round(buffer, 4),
        headroom=round(headroom, 4),
        detail=detail_breach if breached else detail_ok,
    )


def assess(
    rules: AccountRules,
    state: AccountState,
    *,
    proposed_contracts: int = 0,
) -> AccountAssessment:
    """Evaluate every rule against the account, and say what is left.

    `proposed_contracts` lets a caller ask the question one step ahead — "if I
    add this, does anything break" — which is the form the order ticket needs.
    Zero means "as things stand", and is the default because that is the panel's
    question.
    """
    floor = loss_floor(rules, state)
    statuses: list[RuleStatus] = []
    limitations: list[str] = []

    # ── the loss floor ───────────────────────────────────────────────────────
    drawdown_buffer = state.equity - floor
    allowance = max(rules.maximum_loss, 1e-9)
    statuses.append(
        RuleStatus(
            key="max_drawdown",
            label="Maximum loss",
            level=_headroom_level(
                max(0.0, drawdown_buffer / allowance), breached=state.equity <= floor
            ),
            observed=round(state.equity, 2),
            limit=round(floor, 2),
            buffer=round(drawdown_buffer, 2),
            headroom=round(max(0.0, min(1.0, drawdown_buffer / allowance)), 4),
            detail=(
                f"floor {floor:,.2f} ({rules.trail_mode.value.replace('_', ' ')})"
                if state.equity > floor
                else f"equity {state.equity:,.2f} is at or below the floor {floor:,.2f}"
            ),
        )
    )

    # ── the day ──────────────────────────────────────────────────────────────
    statuses.append(
        _ceiling(
            "daily_loss",
            "Daily loss limit",
            state.daily_loss,
            rules.daily_loss_limit,
            detail_ok=(
                f"{(rules.daily_loss_limit or 0) - state.daily_loss:,.2f} of today's "
                "allowance remains"
            ),
            detail_breach="today's loss has reached the limit",
            unassessed="this account has no daily loss limit configured",
        )
    )

    # ── the target ───────────────────────────────────────────────────────────
    profit = state.equity - rules.starting_balance
    if rules.profit_target is None:
        statuses.append(
            RuleStatus(
                key="profit_target", label="Profit target", level=Level.NOT_ASSESSED,
                observed=round(profit, 2), limit=None,
                detail="no profit target is configured",
            )
        )
    else:
        remaining = rules.profit_target - profit
        statuses.append(
            RuleStatus(
                key="profit_target",
                label="Profit target",
                # A target is not a hazard, so it never reaches WARNING: reaching
                # it is the good outcome. OK means met.
                level=Level.OK if remaining <= 0 else Level.CAUTION,
                observed=round(profit, 2),
                limit=round(rules.profit_target, 2),
                buffer=round(remaining, 2),
                headroom=round(max(0.0, min(1.0, profit / rules.profit_target)), 4),
                detail=(
                    "target met"
                    if remaining <= 0
                    else f"{remaining:,.2f} to go"
                ),
            )
        )

    # ── size ─────────────────────────────────────────────────────────────────
    projected = state.open_contracts + max(0, proposed_contracts)
    statuses.append(
        _ceiling(
            "position_limit",
            "Position limit",
            float(projected),
            None if rules.max_position_contracts is None else float(rules.max_position_contracts),
            detail_ok=(
                f"{projected} contract(s) open"
                + (f", {proposed_contracts} proposed" if proposed_contracts else "")
            ),
            detail_breach="the projected position exceeds the contract limit",
            unassessed="no position limit is configured",
        )
    )
    statuses.append(
        _ceiling(
            "order_limit",
            "Order size limit",
            float(max(state.largest_order_contracts, max(0, proposed_contracts))),
            None if rules.max_order_contracts is None else float(rules.max_order_contracts),
            detail_ok="order sizes are within the configured cap",
            detail_breach="an order exceeds the per-order contract cap",
            unassessed="no per-order contract cap is configured",
        )
    )
    statuses.append(
        _ceiling(
            "open_positions",
            "Concurrent positions",
            float(state.open_positions),
            None if rules.max_open_positions is None else float(rules.max_open_positions),
            detail_ok=f"{state.open_positions} position(s) open",
            detail_breach="more positions are open than the rules permit",
            unassessed="no limit on concurrent positions is configured",
        )
    )
    statuses.append(
        _ceiling(
            "risk_per_trade",
            "Risk per trade",
            state.risk_per_trade,
            rules.max_risk_per_trade,
            detail_ok="risk on the largest open trade is within the cap",
            detail_breach="one open trade risks more than the cap allows",
            unassessed=(
                "no per-trade risk cap is configured"
                if rules.max_risk_per_trade is None
                else "the account state does not report per-trade risk, so this cannot be checked"
            ),
        )
    )

    # ── session ──────────────────────────────────────────────────────────────
    statuses.append(_session_status(rules, state, limitations))

    # ── consistency and tenure ───────────────────────────────────────────────
    statuses.append(_consistency_status(rules, state))
    statuses.append(_trading_days_status(rules, state))

    # ── the operator's own rules ─────────────────────────────────────────────
    for limit in rules.custom_limits:
        statuses.append(_custom_status(limit, rules, state, projected))

    worst = max((s.level for s in statuses), key=lambda level: _SEVERITY[level])
    breaches = tuple(s.key for s in statuses if s.breached)
    # An advisory breach does not stop trading. Minimum trading days is the
    # canonical case: being on day two of a ten-day minimum is not a violation,
    # and treating it as one would refuse every order for the first week.
    advisory = {"trading_days"} | {c.key for c in rules.custom_limits if c.advisory}
    hard = tuple(key for key in breaches if key not in advisory)

    return AccountAssessment(
        rules_id=rules.rules_id,
        rules_name=rules.name,
        as_of=state.as_of,
        level=worst,
        can_trade=not hard,
        balance=round(state.balance, 2),
        equity=round(state.equity, 2),
        loss_floor=round(floor, 2),
        statuses=tuple(statuses),
        breaches=breaches,
        limitations=tuple(limitations),
    )


def _session_status(
    rules: AccountRules, state: AccountState, limitations: list[str]
) -> RuleStatus:
    if not rules.session_windows:
        return RuleStatus(
            key="session", label="Trading session", level=Level.NOT_ASSESSED,
            observed=None, limit=None, detail="no session restriction is configured",
        )
    if rules.timezone.upper() not in {"UTC", "GMT", "Z"}:
        # Saying so beats converting with a library that is not a dependency and
        # then being quietly wrong about a boundary the operator trades on.
        limitations.append(
            f"session windows are declared in {rules.timezone} and are evaluated in UTC; "
            "the boundary shown may be off by the zone's offset"
        )
    moment = state.as_of.astimezone(UTC).time()
    inside = next((w for w in rules.session_windows if w.contains(moment)), None)
    windows = "; ".join(w.describe() for w in rules.session_windows)
    if inside is not None:
        return RuleStatus(
            key="session", label="Trading session", level=Level.OK,
            observed=moment.strftime("%H:%M"), limit=windows,
            detail=f"inside {inside.describe() or 'the permitted window'}",
        )
    # Outside a window is a WARNING rather than a BREACH when the account is
    # flat: nothing has been done wrong, but nothing may be opened either.
    outside_with_exposure = state.open_contracts > 0
    return RuleStatus(
        key="session",
        label="Trading session",
        level=Level.BREACH if outside_with_exposure else Level.WARNING,
        observed=moment.strftime("%H:%M"),
        limit=windows,
        detail=(
            "position is open outside the permitted session"
            if outside_with_exposure
            else "outside the permitted session; no new positions"
        ),
    )


def _consistency_status(rules: AccountRules, state: AccountState) -> RuleStatus:
    if rules.consistency_share is None:
        return RuleStatus(
            key="consistency", label="Consistency", level=Level.NOT_ASSESSED,
            observed=None, limit=None, detail="no consistency rule is configured",
        )
    wins = [profit for profit in state.daily_profits if profit > 0]
    total = sum(wins)
    if not wins or total <= 0:
        return RuleStatus(
            key="consistency", label="Consistency", level=Level.NOT_ASSESSED,
            observed=None, limit=rules.consistency_share,
            detail="no profitable day has settled yet, so there is no share to measure",
        )
    share = max(wins) / total
    return RuleStatus(
        key="consistency",
        label="Consistency",
        level=_headroom_level(
            max(0.0, (rules.consistency_share - share) / rules.consistency_share),
            breached=share > rules.consistency_share,
        ),
        observed=round(share, 4),
        limit=rules.consistency_share,
        buffer=round(rules.consistency_share - share, 4),
        headroom=round(max(0.0, (rules.consistency_share - share) / rules.consistency_share), 4),
        detail=(
            f"best day is {share:.1%} of settled profit; the cap is "
            f"{rules.consistency_share:.0%}"
        ),
    )


def _trading_days_status(rules: AccountRules, state: AccountState) -> RuleStatus:
    if rules.minimum_trading_days is None:
        return RuleStatus(
            key="trading_days", label="Minimum trading days", level=Level.NOT_ASSESSED,
            observed=state.trading_days, limit=None,
            detail="no minimum trading-day count is configured",
        )
    remaining = rules.minimum_trading_days - state.trading_days
    return RuleStatus(
        key="trading_days",
        label="Minimum trading days",
        # A minimum not yet reached is reported as a breach so the summary cannot
        # call the account eligible; `assess` treats it as advisory, so it does
        # not stop trading. The two facts are different and both are true.
        level=Level.OK if remaining <= 0 else Level.BREACH,
        observed=state.trading_days,
        limit=rules.minimum_trading_days,
        buffer=float(remaining),
        headroom=round(min(1.0, state.trading_days / rules.minimum_trading_days), 4),
        detail=(
            "the minimum is met"
            if remaining <= 0
            else f"{remaining} more trading day(s) before a payout is eligible"
        ),
    )


def _custom_status(
    limit: CustomLimit, rules: AccountRules, state: AccountState, projected: int
) -> RuleStatus:
    observed = _observe(limit.metric, rules, state, projected)
    if observed is None:
        return RuleStatus(
            key=limit.key, label=limit.label, level=Level.NOT_ASSESSED,
            observed=None, limit=limit.value,
            detail=f"the account state does not report {limit.metric.value}",
        )
    if limit.comparison == "max":
        return _ceiling(
            limit.key,
            limit.label,
            observed,
            limit.value,
            detail_ok=f"{limit.metric.value} is within the configured maximum",
            detail_breach=f"{limit.metric.value} has reached the configured maximum",
        )
    breached = observed < limit.value
    span = abs(limit.value) or 1.0
    return RuleStatus(
        key=limit.key,
        label=limit.label,
        level=Level.BREACH if breached else Level.OK,
        observed=round(observed, 4),
        limit=limit.value,
        buffer=round(observed - limit.value, 4),
        headroom=round(min(1.0, max(0.0, observed / span)), 4),
        detail=(
            f"{limit.metric.value} is below the configured minimum"
            if breached
            else f"{limit.metric.value} meets the configured minimum"
        ),
    )


def _observe(
    metric: Metric, rules: AccountRules, state: AccountState, projected: int
) -> float | None:
    """Read one metric off the account state.

    Exhaustive by construction: `Metric` is closed and every member appears
    here, so adding a metric without a reading is a `mypy` failure rather than a
    rule that silently reports NOT_ASSESSED forever.
    """
    match metric:
        case Metric.DAILY_LOSS:
            return state.daily_loss
        case Metric.TOTAL_DRAWDOWN:
            return max(0.0, state.high_water_equity - state.equity)
        case Metric.OPEN_CONTRACTS:
            return float(projected)
        case Metric.OPEN_POSITIONS:
            return float(state.open_positions)
        case Metric.ORDER_CONTRACTS:
            return float(state.largest_order_contracts)
        case Metric.TRADES_TODAY:
            return float(state.trades_today)
        case Metric.TRADING_DAYS:
            return float(state.trading_days)
        case Metric.PROFIT:
            # Profit against the account's own starting balance, not against zero:
            # a 50k account sitting at 50,200 is 200 up, not 50,200 up.
            return state.equity - rules.starting_balance
        case Metric.EXPOSURE_NOTIONAL:
            return state.exposure_notional
        case Metric.RISK_PER_TRADE:
            return state.risk_per_trade
        case Metric.LARGEST_DAY_SHARE:
            wins = [p for p in state.daily_profits if p > 0]
            total = sum(wins)
            return None if not wins or total <= 0 else max(wins) / total


class ClosedTrade(FrozenModel):
    """One completed round trip, as the trade ledger already records it."""

    exit_time: datetime
    pnl: float
    contracts: int = 1


def state_from_trades(
    rules: AccountRules,
    trades: tuple[ClosedTrade, ...] | list[ClosedTrade],
    *,
    as_of: datetime | None = None,
) -> AccountState:
    """Derive an account state from trades that actually happened.

    This is the honest bridge between the prop rule engine and the rest of the
    application: a strategy's backtested trades are real records in the ledger,
    and replaying them through a rule set answers "would this strategy have
    survived this contract" without inventing a single number.

    What it deliberately does **not** produce is an open position or an
    unrealised figure. Closed trades carry neither, and guessing them would put
    fiction into the one panel whose whole job is to be trusted.
    """
    ordered = sorted(trades, key=lambda trade: trade.exit_time)
    moment = as_of or (ordered[-1].exit_time if ordered else datetime.now(UTC))
    today = moment.astimezone(UTC).date()

    balance = rules.starting_balance
    high_water = balance
    per_day: dict[date, float] = {}
    largest_order = 0
    for trade in ordered:
        balance += trade.pnl
        high_water = max(high_water, balance)
        day = trade.exit_time.astimezone(UTC).date()
        per_day[day] = per_day.get(day, 0.0) + trade.pnl
        largest_order = max(largest_order, trade.contracts)

    settled = tuple(per_day[day] for day in sorted(per_day) if day != today)
    return AccountState(
        as_of=moment,
        balance=round(balance, 2),
        equity=round(balance, 2),
        high_water_balance=round(high_water, 2),
        high_water_equity=round(high_water, 2),
        realised_today=round(per_day.get(today, 0.0), 2),
        unrealised=0.0,
        open_contracts=0,
        open_positions=0,
        largest_order_contracts=largest_order,
        trades_today=sum(
            1 for trade in ordered if trade.exit_time.astimezone(UTC).date() == today
        ),
        trading_days=len(per_day),
        daily_profits=settled,
        exposure_notional=0.0,
        risk_per_trade=None,
    )
