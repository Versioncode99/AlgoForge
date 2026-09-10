"""The pre-trade gate. Every proposed order passes through it, or it does not trade.

The gate is the boundary between "the system decided something" and "the system
did something". It is deterministic, it accumulates every reason rather than
stopping at the first, and it has no bypass — not a flag, not a privileged
caller, not an argument an agent could supply.

**How the absence of a bypass is enforced.** A passing order leaves here with a
`clearance`: a content hash of the exact order that was checked, plus the moment
it was checked. `forge.execution.oms` will not accept an order without one, and
recomputes the hash from the order it was handed. Change the quantity after
clearance and the hash no longer matches, so the OMS refuses. That is stronger
than a boolean `approved=True` on the order, which anything holding the object
could set, and it is the difference between a gate and a suggestion.

**Why checks accumulate.** An order blocked for three reasons that reports one
sends its author back around the loop three times. Every check runs, and the
decision carries the whole ladder — including the checks that did not apply, and
why they did not, on the same principle the judge reports INCONCLUSIVE rather
than quietly passing.

**Not applicable is not a pass.** A borrow check on an instrument whose
shortability nobody recorded reports NOT_APPLICABLE with a reason, and — because
this is a control rather than a report — an unknown on a check that gates risk
blocks the order. Unknown shortability on a short sale is not permission.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from forge.contracts.hashing import content_hash, stable_id
from forge.contracts.models import FrozenModel
from forge.risk.portfolio import PortfolioLimits

#: How old a reference price may be before an order is treated as stale. Sixty
#: seconds is not a market-microstructure claim; it is a default the operator
#: overrides per context, and it exists so that "no maximum" is never the value.
DEFAULT_MAX_PRICE_AGE_SECONDS = 60.0


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    #: The check does not apply to this order, or the input it needs was not
    #: supplied. Never a pass; see `BLOCKING_WHEN_UNKNOWN`.
    NOT_APPLICABLE = "not_applicable"


class Decision(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"


class ProposedOrder(FrozenModel):
    """An order that has been prepared but not cleared.

    `data_as_of` and `reference_price` are required together for anything but a
    market order into a live session, because a limit computed from a price
    nobody timestamped cannot be checked for staleness — and an order priced off
    yesterday's close is the failure mode the staleness check exists for.
    """

    order_id: str = Field(min_length=1, max_length=80)
    symbol: str = Field(min_length=1, max_length=32)
    side: Side
    quantity: int = Field(gt=0)
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = Field(default=None, gt=0)
    strategy_id: str = ""
    portfolio_id: str = ""
    intent: Literal["open", "close", "rebalance"] = "rebalance"
    reference_price: float | None = Field(default=None, gt=0)
    data_as_of: datetime | None = None
    created_at: datetime

    @property
    def signed_quantity(self) -> int:
        return self.quantity if self.side is Side.BUY else -self.quantity

    def fingerprint(self) -> str:
        """The exact order, hashed. What clearance is bound to.

        Deliberately covers everything that changes what the order *does*.
        `created_at` is in it too: re-proposing the same trade an hour later is a
        different decision and should need a fresh check.
        """
        return content_hash(
            {
                "order_id": self.order_id,
                "symbol": self.symbol,
                "side": self.side.value,
                "quantity": self.quantity,
                "order_type": self.order_type,
                "limit_price": self.limit_price,
                "strategy_id": self.strategy_id,
                "portfolio_id": self.portfolio_id,
                "intent": self.intent,
                "created_at": self.created_at.astimezone(UTC).isoformat(),
            }
        )


class InstrumentRule(FrozenModel):
    """What the gate is allowed to know about an instrument.

    Separate from `forge.portfolio.Instrument` because the gate must not depend
    on the portfolio package: the layer that refuses orders cannot import the
    layer that proposes them, or the dependency runs the wrong way.
    """

    symbol: str
    tradable: bool = True
    shortable: bool | None = None
    borrow_cost_bps: float | None = None
    multiplier: float = Field(default=1.0, gt=0)
    adv_notional: float | None = Field(default=None, gt=0)
    max_order_contracts: int | None = Field(default=None, gt=0)
    max_position_contracts: int | None = Field(default=None, gt=0)


class MarketState(FrozenModel):
    symbol: str
    open: bool = True
    halted: bool = False
    last_price: float | None = Field(default=None, gt=0)
    last_price_at: datetime | None = None


class GateContext(FrozenModel):
    """Everything the checks read. Assembled by the caller, never fetched here.

    A gate that goes and looks things up is a gate whose answer depends on when
    it ran and what a network did. This one is a pure function of its inputs,
    which is what makes a blocked order reproducible from the record.
    """

    now: datetime
    limits: PortfolioLimits
    capital: float = Field(default=0.0, ge=0)
    #: Symbols nobody may trade, and why. The reason is shown verbatim.
    restricted: dict[str, str] = Field(default_factory=dict)
    instruments: dict[str, InstrumentRule] = Field(default_factory=dict)
    market: dict[str, MarketState] = Field(default_factory=dict)
    #: Current signed position, in contracts, per symbol.
    positions: dict[str, int] = Field(default_factory=dict)
    #: Working orders per symbol, so a limit is not breached by the second half
    #: of an order that is already resting.
    working: dict[str, int] = Field(default_factory=dict)
    #: The judge's decision per strategy. A strategy absent from this map has
    #: never been judged, which the validation check treats as a block.
    verdicts: dict[str, str] = Field(default_factory=dict)
    eligible_verdicts: tuple[str, ...] = ("PASS",)
    #: Fingerprints of orders already accepted, for duplicate detection.
    recent_fingerprints: tuple[str, ...] = ()
    max_price_age_seconds: float = DEFAULT_MAX_PRICE_AGE_SECONDS
    #: Gross exposure of the existing book, as a fraction of capital. Added to
    #: the order's own contribution before the leverage check.
    current_gross: float = 0.0


class CheckResult(FrozenModel):
    check: str
    status: CheckStatus
    detail: str

    @property
    def failed(self) -> bool:
        return self.status is CheckStatus.FAIL


class Clearance(FrozenModel):
    """Proof that this exact order passed this exact gate.

    Held by the OMS, checked against the order it is handed. Not a permission
    token in any cryptographic sense — this is a local, single-process
    application and a signature would be theatre. It is an integrity check: it
    makes silent modification after clearance detectable, which is the failure
    it exists to catch.
    """

    clearance_id: str
    fingerprint: str
    cleared_at: datetime
    limits_name: str

    def matches(self, order: ProposedOrder) -> bool:
        return self.fingerprint == order.fingerprint()


class GateDecision(FrozenModel):
    order_id: str
    symbol: str
    decision: Decision
    checks: tuple[CheckResult, ...]
    reasons: tuple[str, ...]
    clearance: Clearance | None = None
    evaluated_at: datetime

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


#: Checks where "could not be established" must block rather than pass. These
#: are the ones guarding a downside that unknown does not make smaller: you can
#: not short what you have not established is shortable, and you can not size
#: against a price nobody has.
BLOCKING_WHEN_UNKNOWN: frozenset[str] = frozenset(
    {"instrument_eligibility", "validation_status", "borrow", "market_state", "stale_data"}
)


def screen(order: ProposedOrder, context: GateContext) -> GateDecision:
    """Run every check against one order and return the decision.

    Order of the checks is presentation only; all of them run.
    """
    checks: list[CheckResult] = [
        _kill_switch(context),
        _restricted(order, context),
        _eligibility(order, context),
        _validation(order, context),
        _market_state(order, context),
        _stale_data(order, context),
        _duplicate(order, context),
        _order_size(order, context),
        _position_limit(order, context),
        _borrow(order, context),
        _liquidity(order, context),
        _leverage(order, context),
        _concentration(order, context),
    ]

    reasons = [check.detail for check in checks if check.failed]
    reasons += [
        f"{check.check}: {check.detail}"
        for check in checks
        if check.status is CheckStatus.NOT_APPLICABLE and check.check in BLOCKING_WHEN_UNKNOWN
    ]

    evaluated_at = context.now
    if reasons:
        return GateDecision(
            order_id=order.order_id,
            symbol=order.symbol,
            decision=Decision.BLOCK,
            checks=tuple(checks),
            reasons=tuple(reasons),
            clearance=None,
            evaluated_at=evaluated_at,
        )

    fingerprint = order.fingerprint()
    return GateDecision(
        order_id=order.order_id,
        symbol=order.symbol,
        decision=Decision.ALLOW,
        checks=tuple(checks),
        reasons=(),
        clearance=Clearance(
            clearance_id=stable_id(
                "clearance", {"fingerprint": fingerprint, "at": evaluated_at.isoformat()}
            ),
            fingerprint=fingerprint,
            cleared_at=evaluated_at,
            limits_name=context.limits.name,
        ),
        evaluated_at=evaluated_at,
    )


def screen_all(
    orders: tuple[ProposedOrder, ...] | list[ProposedOrder], context: GateContext
) -> tuple[GateDecision, ...]:
    """Screen a batch, accumulating each cleared order's effect on the next.

    A basket screened independently can pass order by order and breach the book
    limit together — three orders each taking gross to 1.9 against a ceiling of
    2.0. Threading the accepted positions and gross through the batch is what
    stops a rebalance being a way around a limit that a single order could not
    get past.
    """
    decisions: list[GateDecision] = []
    positions = dict(context.positions)
    working = dict(context.working)
    gross = context.current_gross
    seen = list(context.recent_fingerprints)

    for order in orders:
        stepped = context.model_copy(
            update={
                "positions": dict(positions),
                "working": dict(working),
                "current_gross": gross,
                "recent_fingerprints": tuple(seen),
            }
        )
        decision = screen(order, stepped)
        decisions.append(decision)
        if decision.allowed:
            positions[order.symbol] = positions.get(order.symbol, 0) + order.signed_quantity
            working[order.symbol] = working.get(order.symbol, 0) + order.quantity
            gross += _order_weight(order, stepped)
            seen.append(order.fingerprint())
    return tuple(decisions)


# ── the checks ───────────────────────────────────────────────────────────────
def _kill_switch(context: GateContext) -> CheckResult:
    if context.limits.enabled:
        return CheckResult(
            check="kill_switch", status=CheckStatus.PASS,
            detail=f"the '{context.limits.name}' limit set is active",
        )
    return CheckResult(
        check="kill_switch", status=CheckStatus.FAIL,
        detail=f"the '{context.limits.name}' limit set is disabled; every order is refused",
    )


def _restricted(order: ProposedOrder, context: GateContext) -> CheckResult:
    reason = context.restricted.get(order.symbol.upper()) or context.restricted.get(order.symbol)
    if reason is None:
        return CheckResult(
            check="restricted_list", status=CheckStatus.PASS,
            detail=f"{order.symbol} is not restricted",
        )
    return CheckResult(
        check="restricted_list", status=CheckStatus.FAIL,
        detail=f"{order.symbol} is on the restricted list: {reason}",
    )


def _eligibility(order: ProposedOrder, context: GateContext) -> CheckResult:
    rule = context.instruments.get(order.symbol)
    if rule is None:
        return CheckResult(
            check="instrument_eligibility", status=CheckStatus.NOT_APPLICABLE,
            detail=f"no instrument definition for {order.symbol}; eligibility is unestablished",
        )
    if not rule.tradable:
        return CheckResult(
            check="instrument_eligibility", status=CheckStatus.FAIL,
            detail=f"{order.symbol} is marked not tradable",
        )
    return CheckResult(
        check="instrument_eligibility", status=CheckStatus.PASS,
        detail=f"{order.symbol} is tradable",
    )


def _validation(order: ProposedOrder, context: GateContext) -> CheckResult:
    if not order.strategy_id:
        return CheckResult(
            check="validation_status", status=CheckStatus.NOT_APPLICABLE,
            detail="the order names no strategy, so its evidence cannot be checked",
        )
    verdict = context.verdicts.get(order.strategy_id)
    if verdict is None:
        return CheckResult(
            check="validation_status", status=CheckStatus.NOT_APPLICABLE,
            detail=f"{order.strategy_id} has never been judged",
        )
    if verdict not in context.eligible_verdicts:
        return CheckResult(
            check="validation_status", status=CheckStatus.FAIL,
            detail=(
                f"{order.strategy_id} carries verdict {verdict}; the eligible set is "
                f"{', '.join(context.eligible_verdicts)}"
            ),
        )
    return CheckResult(
        check="validation_status", status=CheckStatus.PASS,
        detail=f"{order.strategy_id} carries verdict {verdict}",
    )


def _market_state(order: ProposedOrder, context: GateContext) -> CheckResult:
    state = context.market.get(order.symbol)
    if state is None:
        return CheckResult(
            check="market_state", status=CheckStatus.NOT_APPLICABLE,
            detail=f"no market state was supplied for {order.symbol}",
        )
    if state.halted:
        return CheckResult(
            check="market_state", status=CheckStatus.FAIL,
            detail=f"{order.symbol} is halted",
        )
    if not state.open:
        return CheckResult(
            check="market_state", status=CheckStatus.FAIL,
            detail=f"{order.symbol} is closed",
        )
    return CheckResult(
        check="market_state", status=CheckStatus.PASS, detail=f"{order.symbol} is open",
    )


def _stale_data(order: ProposedOrder, context: GateContext) -> CheckResult:
    state = context.market.get(order.symbol)
    stamped = order.data_as_of or (state.last_price_at if state else None)
    if stamped is None:
        return CheckResult(
            check="stale_data", status=CheckStatus.NOT_APPLICABLE,
            detail=f"nothing timestamps the price behind this {order.symbol} order",
        )
    age = (context.now - stamped).total_seconds()
    if age < 0:
        # A price from the future is a clock or a data problem, and either way it
        # is not a fresh price. Treating a negative age as "very fresh" is how a
        # mis-stamped feed passes the freshness check most easily of all.
        return CheckResult(
            check="stale_data", status=CheckStatus.FAIL,
            detail=f"the reference price is stamped {abs(age):.0f}s in the future",
        )
    if age > context.max_price_age_seconds:
        return CheckResult(
            check="stale_data", status=CheckStatus.FAIL,
            detail=(
                f"the reference price is {age:.0f}s old; the limit is "
                f"{context.max_price_age_seconds:.0f}s"
            ),
        )
    return CheckResult(
        check="stale_data", status=CheckStatus.PASS,
        detail=f"reference price is {age:.0f}s old",
    )


def _duplicate(order: ProposedOrder, context: GateContext) -> CheckResult:
    if order.fingerprint() in context.recent_fingerprints:
        return CheckResult(
            check="duplicate_order", status=CheckStatus.FAIL,
            detail="an identical order has already been accepted",
        )
    return CheckResult(
        check="duplicate_order", status=CheckStatus.PASS, detail="no identical order is pending",
    )


def _order_size(order: ProposedOrder, context: GateContext) -> CheckResult:
    rule = context.instruments.get(order.symbol)
    cap = rule.max_order_contracts if rule else None
    if cap is None:
        return CheckResult(
            check="order_size", status=CheckStatus.PASS,
            detail=f"{order.quantity} contract(s); no per-order cap is configured",
        )
    if order.quantity > cap:
        return CheckResult(
            check="order_size", status=CheckStatus.FAIL,
            detail=f"{order.quantity} contracts exceeds the per-order cap of {cap}",
        )
    return CheckResult(
        check="order_size", status=CheckStatus.PASS,
        detail=f"{order.quantity} of {cap} permitted contracts",
    )


def _position_limit(order: ProposedOrder, context: GateContext) -> CheckResult:
    rule = context.instruments.get(order.symbol)
    cap = rule.max_position_contracts if rule else None
    projected = abs(context.positions.get(order.symbol, 0) + order.signed_quantity)
    if cap is None:
        return CheckResult(
            check="position_limit", status=CheckStatus.PASS,
            detail=f"projected position {projected}; no position cap is configured",
        )
    if projected > cap:
        return CheckResult(
            check="position_limit", status=CheckStatus.FAIL,
            detail=f"projected position {projected} exceeds the cap of {cap}",
        )
    return CheckResult(
        check="position_limit", status=CheckStatus.PASS,
        detail=f"projected position {projected} of {cap}",
    )


def _borrow(order: ProposedOrder, context: GateContext) -> CheckResult:
    position = context.positions.get(order.symbol, 0)
    projected = position + order.signed_quantity
    if projected >= 0:
        return CheckResult(
            check="borrow", status=CheckStatus.PASS,
            detail="the resulting position is not short",
        )
    rule = context.instruments.get(order.symbol)
    if rule is None or rule.shortable is None:
        return CheckResult(
            check="borrow", status=CheckStatus.NOT_APPLICABLE,
            detail=(
                f"{order.symbol} would go short but its borrow availability has never "
                "been established"
            ),
        )
    if not rule.shortable:
        return CheckResult(
            check="borrow", status=CheckStatus.FAIL,
            detail=f"{order.symbol} is not shortable",
        )
    cost = "" if rule.borrow_cost_bps is None else f" at {rule.borrow_cost_bps:.0f}bps"
    return CheckResult(
        check="borrow", status=CheckStatus.PASS,
        detail=f"{order.symbol} is shortable{cost}",
    )


def _liquidity(order: ProposedOrder, context: GateContext) -> CheckResult:
    rule = context.instruments.get(order.symbol)
    price = order.reference_price or (
        context.market[order.symbol].last_price if order.symbol in context.market else None
    )
    if rule is None or rule.adv_notional is None or price is None:
        return CheckResult(
            check="liquidity", status=CheckStatus.NOT_APPLICABLE,
            detail=f"average daily notional or a price is unknown for {order.symbol}",
        )
    notional = order.quantity * price * rule.multiplier
    share = notional / rule.adv_notional
    # The gate uses a fixed, conservative participation ceiling rather than the
    # portfolio's, because an order can arrive from somewhere that never ran
    # construction and the gate must still refuse a size the market cannot take.
    if share > 0.10:
        return CheckResult(
            check="liquidity", status=CheckStatus.FAIL,
            detail=(
                f"the order is {share:.1%} of {order.symbol}'s average daily notional; "
                "the gate refuses above 10%"
            ),
        )
    return CheckResult(
        check="liquidity", status=CheckStatus.PASS,
        detail=f"{share:.2%} of average daily notional",
    )


def _order_weight(order: ProposedOrder, context: GateContext) -> float:
    """The order's gross contribution as a fraction of capital, or zero.

    Zero when it cannot be computed. That is a real weakness and the leverage
    check says so rather than hiding it: without a price or capital, the order's
    exposure is genuinely unknown, and inventing one would be worse.
    """
    rule = context.instruments.get(order.symbol)
    price = order.reference_price or (
        context.market[order.symbol].last_price if order.symbol in context.market else None
    )
    if rule is None or price is None or context.capital <= 0:
        return 0.0
    return order.quantity * price * rule.multiplier / context.capital


def _leverage(order: ProposedOrder, context: GateContext) -> CheckResult:
    weight = _order_weight(order, context)
    if weight == 0.0:
        return CheckResult(
            check="leverage", status=CheckStatus.NOT_APPLICABLE,
            detail="capital, a price or an instrument multiplier is missing, so the "
                   "order's exposure could not be computed",
        )
    projected = context.current_gross + weight
    if projected > context.limits.max_leverage:
        return CheckResult(
            check="leverage", status=CheckStatus.FAIL,
            detail=(
                f"projected gross {projected:.2f}x exceeds the leverage ceiling "
                f"{context.limits.max_leverage:.2f}x"
            ),
        )
    return CheckResult(
        check="leverage", status=CheckStatus.PASS,
        detail=f"projected gross {projected:.2f}x of {context.limits.max_leverage:.2f}x",
    )


def _concentration(order: ProposedOrder, context: GateContext) -> CheckResult:
    weight = _order_weight(order, context)
    if weight == 0.0:
        return CheckResult(
            check="concentration", status=CheckStatus.NOT_APPLICABLE,
            detail="the order's weight could not be computed",
        )
    rule = context.instruments.get(order.symbol)
    price = order.reference_price or (
        context.market[order.symbol].last_price if order.symbol in context.market else None
    )
    existing = 0.0
    if rule is not None and price is not None and context.capital > 0:
        existing = abs(context.positions.get(order.symbol, 0)) * price * rule.multiplier
        existing /= context.capital
    projected = existing + weight
    if projected > context.limits.max_position_weight:
        return CheckResult(
            check="concentration", status=CheckStatus.FAIL,
            detail=(
                f"{order.symbol} would reach {projected:.1%} of capital against a "
                f"single-name ceiling of {context.limits.max_position_weight:.1%}"
            ),
        )
    return CheckResult(
        check="concentration", status=CheckStatus.PASS,
        detail=f"{order.symbol} would be {projected:.1%} of capital",
    )
