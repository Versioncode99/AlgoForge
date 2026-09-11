"""The order and position lifecycles, driven by events that arrive badly.

Events from a broker do not arrive once, in order, exactly as many times as they
happened. The research is explicit that at least one provider documents "no
ordering sequences or duplicate warnings", and the strongest commercial engine
observed logs "already processed at Working status, skipping" hundreds of times
a session. So this module is written for the arrival pattern that actually
occurs:

* **Duplicates.** The same event delivered twice must change nothing the second
  time. Every event carries a `event_key`, and the ledger refuses a repeat.
* **Out of order.** A `Working` that arrives after the `Filled` it preceded must
  not un-fill the order. Progress is monotonic in two quantities — the state's
  rank and the cumulative filled quantity — and an event that would move either
  backwards is recorded as stale and dropped.
* **Partial fills.** Cumulative quantity is authoritative; incremental quantity
  is derived. A provider that reports `cumQty` and one that reports `lastQty`
  both land in the same place, and a repeated increment cannot double-count.
* **Rejections and busts.** A rejection is terminal with a reason, and a busted
  fill *reduces* the cumulative quantity, which is the one legitimate way it
  goes down. That case is handled explicitly rather than by the monotonicity
  rule, because a bust that could not be applied would leave a position the
  broker no longer agrees with.

**Why the machine is immutable.** `apply` returns a new `OrderRecord`. A
half-applied transition — quantity updated, state not — is the kind of bug that
surfaces as a position that will not close, and returning a new value makes it
unrepresentable.

**Position lifecycle.** `classify_transition` names what a fill did to a
position: opened, increased, reduced, closed or reversed. Reversal matters far
more than it looks: for the instant a follower is flat while a leader is short,
two accounts of one owner hold opposite sides of correlated products, which
nearly every prop firm prohibits. Naming the transition is what lets the risk
layer see it coming.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from forge.contracts.hashing import content_hash, stable_id
from forge.contracts.models import FrozenModel


class LifecycleError(Exception):
    """An illegal transition. The record is unchanged."""


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(StrEnum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class OrderState(StrEnum):
    """The canonical lifecycle. Every provider's enum maps onto this."""

    CREATED = "created"
    SUBMITTED = "submitted"
    WORKING = "working"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


TERMINAL_STATES = frozenset(
    {OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED, OrderState.EXPIRED}
)

#: How far along the lifecycle a state is. Used to detect an event that arrived
#: late: a `WORKING` after a `FILLED` is stale, not a regression to apply.
#: `PARTIALLY_FILLED` and `WORKING` share nothing useful in rank terms — a
#: partial fill is strictly further along — and modification does not move it.
_RANK: dict[OrderState, int] = {
    OrderState.CREATED: 0,
    OrderState.SUBMITTED: 1,
    OrderState.WORKING: 2,
    OrderState.PARTIALLY_FILLED: 3,
    OrderState.FILLED: 4,
    OrderState.CANCELLED: 4,
    OrderState.REJECTED: 4,
    OrderState.EXPIRED: 4,
}

_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    # A provider event about an order is itself proof the order reached the
    # provider, so `CREATED` accepts everything `SUBMITTED` does. Refusing a
    # fill because this application had not yet written down that it submitted
    # the order would strand a record whose order is demonstrably live — and the
    # one moment that happens is a fast fill racing the local write, which is
    # exactly when being stranded costs the most.
    OrderState.CREATED: frozenset(
        {
            OrderState.SUBMITTED,
            OrderState.WORKING,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
        }
    ),
    OrderState.SUBMITTED: frozenset(
        {
            OrderState.WORKING,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.REJECTED,
            OrderState.CANCELLED,
            OrderState.EXPIRED,
        }
    ),
    OrderState.WORKING: frozenset(
        {
            OrderState.WORKING,  # a modification leaves it working
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.REJECTED,
            OrderState.EXPIRED,
        }
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.EXPIRED,
            # A partially filled order can be rejected on the remainder by some
            # providers; refusing it here would strand the record.
            OrderState.REJECTED,
        }
    ),
    OrderState.FILLED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.EXPIRED: frozenset(),
}


class OrderEventType(StrEnum):
    CREATED = "created"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    MODIFIED = "modified"
    PARTIAL_FILL = "partial_fill"
    FILL = "fill"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    #: A fill the provider later voided. The one event that reduces cumulative
    #: quantity.
    BUSTED = "busted"


class OrderOrigin(StrEnum):
    """Why this order exists. Carried on the record so a reconciler can tell a
    copied order from one the operator placed by hand on the same account —
    which is the distinction every follower-protection rule turns on."""

    MANUAL = "manual"
    STRATEGY = "strategy"
    COPY = "copy"
    #: Placed by the reconciliation engine to close a divergence.
    CORRECTION = "correction"
    #: A protective child order.
    BRACKET = "bracket"


class BracketRole(StrEnum):
    ENTRY = "entry"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"


class OrderEvent(FrozenModel):
    """One thing that happened to one order, as reported.

    `cumulative_quantity` is the provider's own running total where it reports
    one. When a provider reports only an increment, the caller sets
    `increment_quantity` and the machine adds it — but the event key still makes
    a repeat a no-op, which is what stops an at-least-once delivery from
    doubling a position.
    """

    event_type: OrderEventType
    at: datetime
    #: The provider's identifier for this event, when it has one. The primary
    #: dedupe key; without one the machine derives a key from the content.
    provider_event_id: str = ""
    #: A provider version or sequence number, when one exists.
    sequence: int | None = None
    cumulative_quantity: int | None = Field(default=None, ge=0)
    increment_quantity: int | None = Field(default=None, ge=0)
    last_price: float | None = Field(default=None, gt=0)
    #: New working prices after a modification.
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    quantity: int | None = Field(default=None, gt=0)
    reason: str = ""
    received_at: datetime | None = None

    @model_validator(mode="after")
    def _quantity_is_expressed_once(self) -> OrderEvent:
        if self.cumulative_quantity is not None and self.increment_quantity is not None:
            raise ValueError(
                "an event states either a cumulative quantity or an increment, not both: "
                "reconciling two disagreeing totals is not a decision this machine can make"
            )
        return self

    def key(self, order_id: str) -> str:
        """What makes two deliveries of one event the same event.

        The provider's id when there is one. Otherwise a hash of what the event
        *says*, which catches a genuine re-delivery and, deliberately, does not
        catch two identical partial fills of the same size at the same price at
        the same instant. That collision is rarer than duplicate delivery, and
        the failure mode of the alternative — treating a real second fill as a
        duplicate — is an under-sized position that reconciliation then has to
        discover, which is strictly worse than an over-count reconciliation
        would also catch.
        """
        if self.provider_event_id:
            return f"{order_id}:{self.provider_event_id}"
        return f"{order_id}:" + content_hash(
            {
                "type": self.event_type.value,
                "at": self.at.isoformat(),
                "seq": self.sequence,
                "cum": self.cumulative_quantity,
                "inc": self.increment_quantity,
                "px": self.last_price,
                "limit": self.limit_price,
                "stop": self.stop_price,
                "qty": self.quantity,
            }
        )[:32]


_EVENT_STATE: dict[OrderEventType, OrderState] = {
    OrderEventType.CREATED: OrderState.CREATED,
    OrderEventType.SUBMITTED: OrderState.SUBMITTED,
    OrderEventType.ACKNOWLEDGED: OrderState.WORKING,
    OrderEventType.MODIFIED: OrderState.WORKING,
    OrderEventType.PARTIAL_FILL: OrderState.PARTIALLY_FILLED,
    OrderEventType.FILL: OrderState.FILLED,
    OrderEventType.CANCELLED: OrderState.CANCELLED,
    OrderEventType.REJECTED: OrderState.REJECTED,
    OrderEventType.EXPIRED: OrderState.EXPIRED,
    OrderEventType.BUSTED: OrderState.PARTIALLY_FILLED,
}


class OrderRecord(FrozenModel):
    """One order and everything known about it.

    `client_tag` is the deterministic idempotency key. Where a provider offers a
    tag field — and all three researched providers do — it is sent as that tag,
    so a retry after a rate-limit penalty reuses the same key and the provider
    itself refuses the duplicate rather than the copier hoping it will not
    happen.
    """

    order_id: str = Field(min_length=1, max_length=80)
    account_uid: str = Field(min_length=1, max_length=80)
    symbol: str = Field(min_length=1, max_length=32)
    side: Side
    quantity: int = Field(gt=0)
    order_type: OrderType
    time_in_force: TimeInForce = TimeInForce.DAY
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    state: OrderState = OrderState.CREATED
    filled_quantity: int = Field(default=0, ge=0)
    average_price: float | None = None
    origin: OrderOrigin = OrderOrigin.MANUAL
    client_tag: str = ""
    provider_order_id: str = ""
    #: The leader order this mirrors, when it is a copy.
    source_order_id: str = ""
    strategy_id: str = ""
    bracket_role: BracketRole = BracketRole.ENTRY
    parent_order_id: str = ""
    oco_group: str = ""
    reason: str = ""
    created_at: datetime
    updated_at: datetime
    #: Provider sequence high-water mark, for staleness detection.
    last_sequence: int | None = None

    @property
    def open(self) -> bool:
        return self.state not in TERMINAL_STATES

    @property
    def remaining(self) -> int:
        return max(0, self.quantity - self.filled_quantity)

    @property
    def rank(self) -> int:
        return _RANK[self.state]

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["remaining"] = self.remaining
        payload["open"] = self.open
        return payload


class Applied(FrozenModel):
    """What `apply` did, so a caller can react without re-deriving it."""

    order: OrderRecord
    accepted: bool
    #: `duplicate`, `stale`, `illegal` — or empty when accepted.
    dropped_because: Literal["", "duplicate", "stale", "illegal"] = ""
    detail: str = ""
    #: Contracts this event added to the filled quantity. Negative on a bust.
    fill_delta: int = 0
    fill_price: float | None = None

    @property
    def filled_here(self) -> bool:
        return self.fill_delta != 0


def new_order_id(account_uid: str, symbol: str, at: datetime, nonce: str = "") -> str:
    return stable_id(
        "pdorder",
        {"account": account_uid, "symbol": symbol, "at": at.isoformat(), "nonce": nonce},
    )


def idempotency_key(
    *,
    group_id: str,
    source_order_id: str,
    source_marker: str,
    account_uid: str,
    action: str,
) -> str:
    """The deterministic key a follower command carries.

    `source_marker` is whatever pins this command to one moment of the leader's
    order: its version on a mirror, its fill id on a market-on-fill. Two
    computations of the key from the same facts produce the same string, which
    is what makes a retry safe and a replay a no-op.

    Truncated to 32 characters after the prefix because the provider tag fields
    that carry it are short — one is documented at fifty characters — and a key
    that does not fit is a key that silently is not sent.
    """
    digest = content_hash(
        {
            "group": group_id,
            "source": source_order_id,
            "marker": source_marker,
            "account": account_uid,
            "action": action,
        }
    )
    return f"af{digest[:30]}"


class OrderMachine:
    """Applies events to orders, and remembers which events it has seen.

    The processed-event ledger is bounded: it holds the keys for orders it still
    tracks, and forgetting a terminal order's keys is safe because a further
    event on a terminal order is refused by the transition table anyway.
    """

    def __init__(self) -> None:
        self._orders: dict[str, OrderRecord] = {}
        self._seen: dict[str, set[str]] = {}

    # ── the collection ───────────────────────────────────────────────────────
    def track(self, order: OrderRecord) -> OrderRecord:
        if order.order_id in self._orders:
            raise LifecycleError(f"order '{order.order_id}' is already tracked")
        self._orders[order.order_id] = order
        self._seen[order.order_id] = set()
        return order

    def get(self, order_id: str) -> OrderRecord | None:
        return self._orders.get(order_id)

    def require(self, order_id: str) -> OrderRecord:
        found = self._orders.get(order_id)
        if found is None:
            raise LifecycleError(f"no order '{order_id}' is tracked")
        return found

    def orders(self) -> tuple[OrderRecord, ...]:
        return tuple(self._orders[key] for key in sorted(self._orders))

    def working(self, account_uid: str | None = None) -> tuple[OrderRecord, ...]:
        return tuple(
            order
            for order in self.orders()
            if order.open and (account_uid is None or order.account_uid == account_uid)
        )

    def seen(self, order_id: str) -> frozenset[str]:
        return frozenset(self._seen.get(order_id, set()))

    # ── the transition ───────────────────────────────────────────────────────
    def apply(self, order_id: str, event: OrderEvent) -> Applied:
        order = self.require(order_id)
        key = event.key(order_id)
        if key in self._seen[order_id]:
            return Applied(
                order=order,
                accepted=False,
                dropped_because="duplicate",
                detail=f"{event.event_type.value} was already applied to this order",
            )

        result = _transition(order, event)
        if result.accepted:
            self._orders[order_id] = result.order
            self._seen[order_id].add(key)
        return result

    def apply_all(self, order_id: str, events: tuple[OrderEvent, ...]) -> tuple[Applied, ...]:
        return tuple(self.apply(order_id, event) for event in events)

    def forget(self, order_id: str) -> None:
        self._orders.pop(order_id, None)
        self._seen.pop(order_id, None)


def _transition(order: OrderRecord, event: OrderEvent) -> Applied:
    target = _EVENT_STATE[event.event_type]

    if event.event_type is OrderEventType.BUSTED:
        return _bust(order, event)

    if event.event_type is OrderEventType.MODIFIED:
        # A modification changes an order's working parameters; it does not move
        # it along the lifecycle. Mapping it to WORKING and running the ordinary
        # guards would read a stop moved after a partial fill as an event that
        # arrived late, and drop it — while the stop sat at its old price.
        if not order.open:
            return Applied(
                order=order,
                accepted=False,
                dropped_because="illegal",
                detail=f"the order is {order.state.value} and cannot be modified",
            )
        target = OrderState.WORKING if order.state is OrderState.CREATED else order.state

    # Staleness before legality. An event that arrived late is not an illegal
    # transition — it is a legal transition that already happened — and
    # reporting it as illegal would send an operator looking for a bug.
    if (
        event.sequence is not None
        and order.last_sequence is not None
        and event.sequence < order.last_sequence
    ):
        return Applied(
            order=order,
            accepted=False,
            dropped_because="stale",
            detail=(
                f"sequence {event.sequence} is behind {order.last_sequence}; this "
                "event arrived after a later one"
            ),
        )

    cumulative = _cumulative(order, event)
    if cumulative is not None and cumulative < order.filled_quantity:
        return Applied(
            order=order,
            accepted=False,
            dropped_because="stale",
            detail=(
                f"reports {cumulative} filled against {order.filled_quantity} already "
                "recorded; a fill total does not go backwards outside a bust"
            ),
        )

    if (
        _RANK[target] < order.rank
        and order.state is not OrderState.WORKING
        and event.event_type is not OrderEventType.MODIFIED
    ):
        return Applied(
            order=order,
            accepted=False,
            dropped_because="stale",
            detail=f"{event.event_type.value} is behind {order.state.value}",
        )

    if target not in _TRANSITIONS[order.state]:
        legal = ", ".join(sorted(s.value for s in _TRANSITIONS[order.state])) or "nothing"
        return Applied(
            order=order,
            accepted=False,
            dropped_because="illegal",
            detail=f"{order.state.value} may move to {legal}; not to {target.value}",
        )

    filled = order.filled_quantity if cumulative is None else cumulative
    delta = filled - order.filled_quantity

    # A provider that reports a fill event with no quantity at all means the
    # whole order. Not assuming that would leave an order marked FILLED with a
    # filled quantity of zero, which reconciliation would then report as a
    # discrepancy every cycle.
    if event.event_type is OrderEventType.FILL and cumulative is None:
        filled = order.quantity
        delta = filled - order.filled_quantity

    if filled > order.quantity:
        return Applied(
            order=order,
            accepted=False,
            dropped_because="illegal",
            detail=(
                f"{filled} filled exceeds the order's {order.quantity}; the provider "
                "and this record disagree and reconciliation must resolve it"
            ),
        )

    # A fill event that does not complete the order is a partial fill whatever
    # the provider called it, and the reverse: an event labelled partial that
    # completes the order leaves the order FILLED.
    state = target
    if target in {OrderState.PARTIALLY_FILLED, OrderState.FILLED}:
        state = OrderState.FILLED if filled >= order.quantity else OrderState.PARTIALLY_FILLED
        if filled == 0:
            # A "partial fill" of nothing is an acknowledgement.
            state = OrderState.WORKING

    average = order.average_price
    if delta > 0 and event.last_price is not None:
        previous = (order.average_price or 0.0) * order.filled_quantity
        average = (previous + event.last_price * delta) / filled if filled else None

    updates: dict[str, Any] = {
        "state": state,
        "filled_quantity": filled,
        "average_price": None if average is None else round(average, 8),
        "updated_at": event.at,
        "reason": event.reason or order.reason,
    }
    if event.sequence is not None:
        updates["last_sequence"] = max(event.sequence, order.last_sequence or event.sequence)
    if event.event_type is OrderEventType.MODIFIED:
        if event.limit_price is not None:
            updates["limit_price"] = event.limit_price
        if event.stop_price is not None:
            updates["stop_price"] = event.stop_price
        if event.quantity is not None:
            if event.quantity < order.filled_quantity:
                return Applied(
                    order=order,
                    accepted=False,
                    dropped_because="illegal",
                    detail=(
                        f"a modification to {event.quantity} is below the "
                        f"{order.filled_quantity} already filled"
                    ),
                )
            updates["quantity"] = event.quantity

    return Applied(
        order=order.model_copy(update=updates),
        accepted=True,
        fill_delta=delta,
        fill_price=event.last_price if delta > 0 else None,
        detail=f"{order.state.value} -> {state.value}",
    )


def _cumulative(order: OrderRecord, event: OrderEvent) -> int | None:
    if event.cumulative_quantity is not None:
        return event.cumulative_quantity
    if event.increment_quantity is not None:
        return order.filled_quantity + event.increment_quantity
    return None


def _bust(order: OrderRecord, event: OrderEvent) -> Applied:
    """A fill the provider voided.

    The only path by which filled quantity decreases. It is handled apart from
    the ordinary transition because every guard there is built on the assumption
    that fills accumulate, and a bust that hit those guards would be dropped as
    stale — leaving this application holding a position the provider says was
    never opened.
    """
    busted = event.increment_quantity or event.quantity or 0
    if busted <= 0:
        return Applied(
            order=order,
            accepted=False,
            dropped_because="illegal",
            detail="a bust must say how many contracts were voided",
        )
    if busted > order.filled_quantity:
        return Applied(
            order=order,
            accepted=False,
            dropped_because="illegal",
            detail=(
                f"a bust of {busted} exceeds the {order.filled_quantity} recorded as "
                "filled; the provider and this record disagree"
            ),
        )
    filled = order.filled_quantity - busted
    state = (
        OrderState.PARTIALLY_FILLED
        if 0 < filled < order.quantity
        else OrderState.WORKING
        if filled == 0
        else OrderState.FILLED
    )
    return Applied(
        order=order.model_copy(
            update={
                "filled_quantity": filled,
                "state": state,
                "average_price": order.average_price if filled else None,
                "updated_at": event.at,
                "reason": event.reason or "a fill was voided by the provider",
            }
        ),
        accepted=True,
        fill_delta=-busted,
        fill_price=event.last_price,
        detail=f"{busted} contract(s) voided",
    )


# ── positions ────────────────────────────────────────────────────────────────


class PositionTransition(StrEnum):
    UNCHANGED = "unchanged"
    OPENED = "opened"
    INCREASED = "increased"
    REDUCED = "reduced"
    CLOSED = "closed"
    #: Through flat to the other side in one step. The transition the hedging
    #: rules care about, and the one most likely to leave a follower briefly
    #: opposite another of the same owner's accounts.
    REVERSED = "reversed"


class PositionState(StrEnum):
    FLAT = "flat"
    LONG = "long"
    SHORT = "short"


class Position(FrozenModel):
    """Net position in one product on one account.

    Signed quantity throughout. A "long 3 / short 1" pair is not a position; it
    is two records of the same position that will disagree, and every provider
    researched reports a net figure.
    """

    account_uid: str
    symbol: str
    quantity: int = 0
    average_price: float | None = None
    realised_pnl: float = 0.0
    as_of: datetime | None = None
    #: Which snapshot this came from, for the staleness guard. An event older
    #: than the last authoritative snapshot must not drive a correction.
    snapshot_version: int = 0

    @property
    def state(self) -> PositionState:
        if self.quantity > 0:
            return PositionState.LONG
        return PositionState.SHORT if self.quantity < 0 else PositionState.FLAT

    @property
    def flat(self) -> bool:
        return self.quantity == 0

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["state"] = self.state.value
        return payload


def classify_transition(before: int, after: int) -> PositionTransition:
    if before == after:
        return PositionTransition.UNCHANGED
    if before == 0:
        return PositionTransition.OPENED
    if after == 0:
        return PositionTransition.CLOSED
    if (before > 0) != (after > 0):
        return PositionTransition.REVERSED
    return (
        PositionTransition.INCREASED
        if abs(after) > abs(before)
        else PositionTransition.REDUCED
    )


class PositionChange(FrozenModel):
    position: Position
    transition: PositionTransition
    before: int
    after: int
    realised: float = 0.0


def apply_fill(
    position: Position,
    *,
    side: Side,
    quantity: int,
    price: float,
    multiplier: float = 1.0,
    at: datetime | None = None,
) -> PositionChange:
    """Update a net position with one fill, in price points times a multiplier.

    Average-cost accounting with signed quantities, so a fill that crosses
    through flat realises against the old position and opens the remainder at
    the fill price. Netting that as a single average leaves a long carrying the
    cost basis of a short, which then reports a loss as a profit.
    """
    if quantity <= 0:
        raise LifecycleError("a fill quantity must be positive")
    signed = side.sign * quantity
    before = position.quantity
    average = position.average_price or 0.0
    realised = 0.0

    if before == 0 or (before > 0) == (signed > 0):
        after = before + signed
        average = (
            (before * average + signed * price) / after if after else 0.0
        )
    else:
        closing = min(abs(signed), abs(before))
        direction = 1.0 if before > 0 else -1.0
        realised = closing * (price - average) * direction * multiplier
        after = before + signed
        if after == 0:
            average = 0.0
        elif (after > 0) != (before > 0):
            average = price

    return PositionChange(
        position=position.model_copy(
            update={
                "quantity": after,
                "average_price": round(average, 8) if after else None,
                "realised_pnl": round(position.realised_pnl + realised, 6),
                "as_of": at or position.as_of,
            }
        ),
        transition=classify_transition(before, after),
        before=before,
        after=after,
        realised=round(realised, 6),
    )
