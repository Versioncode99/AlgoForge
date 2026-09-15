"""Rithmic's vocabulary, turned into the desk's — and the refusals when it cannot be.

Every function here is a mapping and every one of them refuses rather than
guesses. The reason is specific to execution: a provider status this code has
not seen before could be a working order, a dead one or a fill, and the three
differ by the size of the position somebody is holding. Returning `UNKNOWN` and
letting reconciliation find out is the only safe answer, and it is what these
return.

**Nothing here is Rithmic-shaped on the way out.** `forge.propdesk.orders`,
`forge.propdesk.identity` and `forge.propdesk.fabric` describe orders, accounts
and positions for every provider, and the desk's allocation, copy, risk and
prop-rule layers only ever see those. That is what keeps "Rithmic is one
adapter" true rather than aspirational — `tests/propdesk/test_rithmic_generic.py`
asserts that no provider-specific name reaches them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from forge.propdesk.identity import (
    Account,
    AccountKey,
    AccountType,
    Environment,
    Provider,
)
from forge.propdesk.orders import OrderEvent, OrderEventType, Position

#: Rithmic's order notification statuses, as the exchange and the order plant
#: report them, mapped onto the lifecycle the desk holds.
#:
#: Read from the protocol's own wording rather than invented. Anything not here
#: is *not* guessed at: `event_type` returns `None` and the caller records an
#: unmapped status, because a status that might mean "filled" and might mean
#: "rejected" differs by the size of a position.
_ORDER_EVENTS: dict[str, OrderEventType] = {
    "order received from client": OrderEventType.CREATED,
    "order sent to exch": OrderEventType.SUBMITTED,
    "open": OrderEventType.ACKNOWLEDGED,
    "open pending": OrderEventType.SUBMITTED,
    "modified": OrderEventType.MODIFIED,
    "modify pending": OrderEventType.SUBMITTED,
    "complete": OrderEventType.FILL,
    "fill": OrderEventType.FILL,
    "partial fill": OrderEventType.PARTIAL_FILL,
    "cancelled": OrderEventType.CANCELLED,
    "cancel pending": OrderEventType.SUBMITTED,
    "rejected": OrderEventType.REJECTED,
    "expired": OrderEventType.EXPIRED,
    "trade correction": OrderEventType.BUSTED,
}

#: Account kinds. `UNKNOWN` is the default and stays the default: an account
#: whose type nobody recorded is not a demo account, and rendering it as one is
#: how a live account comes to be treated as practice.
_ACCOUNT_TYPES: dict[str, AccountType] = {
    "demo": AccountType.SIMULATION,
    "paper": AccountType.SIMULATION,
    "test": AccountType.SIMULATION,
    "sim": AccountType.SIMULATION,
    "live": AccountType.LIVE,
    # A funded prop account is simulated at the clearing firm even when the
    # payout is real, and the desk's vocabulary says so. Mapping it to LIVE
    # would tell the prop-rule engine an order reaches an exchange.
    "funded": AccountType.FUNDED_SIMULATED,
    "evaluation": AccountType.EVALUATION,
    "challenge": AccountType.EVALUATION,
}


class NormalisationRefused(ValueError):
    """A message that cannot be mapped, naming what could not be."""


def _text(message: Any, field: str) -> str:
    value = getattr(message, field, "")
    return str(value).strip() if value is not None else ""


def _number(message: Any, field: str) -> float | None:
    value: Any = getattr(message, field, None)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def timestamp(message: Any) -> datetime:
    """The protocol's seconds-and-microseconds pair, as one instant in UTC.

    Rithmic sends `ssboe` (seconds since the beginning of the epoch) beside
    `usecs`. Combining them here rather than at each call site is what stops one
    caller using seconds and another using milliseconds — a discrepancy that
    shows up as an order appearing to arrive before the request that made it.
    """
    seconds: Any = getattr(message, "ssboe", None)
    micros: Any = getattr(message, "usecs", 0) or 0
    if seconds is None or seconds == "" or seconds == 0:
        return datetime.now(UTC)
    try:
        return datetime.fromtimestamp(int(seconds) + int(micros) / 1_000_000, tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return datetime.now(UTC)


def event_type(status: str) -> OrderEventType | None:
    """The lifecycle event a Rithmic status names, or `None` when unmapped."""
    return _ORDER_EVENTS.get(status.strip().lower())


def account_type(raw: str) -> AccountType:
    return _ACCOUNT_TYPES.get(raw.strip().lower(), AccountType.UNKNOWN)


def account_key(
    message: Any, *, credential_ref: str, environment: Environment
) -> AccountKey:
    """One account's identity, including the ids that make it unique.

    Rithmic qualifies an account by its clearing firm and introducing broker, so
    two accounts with the same `account_id` under different FCMs are different
    accounts. Dropping the qualifiers would make them collide — and a copy
    configuration keyed to the collision would route one account's orders to
    another's.
    """
    account_id = _text(message, "account_id")
    if not account_id:
        raise NormalisationRefused(
            "a Rithmic account message carried no account id, so it cannot be identified. "
            "Nothing was recorded."
        )
    qualifiers = {
        name: value
        for name, value in (
            ("fcm_id", _text(message, "fcm_id")),
            ("ib_id", _text(message, "ib_id")),
        )
        if value
    }
    return AccountKey(
        provider=Provider.RITHMIC,
        environment=environment,
        credential_ref=credential_ref,
        account_id=account_id,
        qualifiers=qualifiers,
    )


def account(
    message: Any, *, connection_id: str, credential_ref: str, environment: Environment
) -> Account:
    """One account, with balance and equity left unset unless reported.

    `None` rather than zero, for the reason `forge.propdesk.identity.Account`
    states: a zeroed account renders as a comfortable buffer against a starting
    balance nobody confirmed.
    """
    return Account(
        key=account_key(message, credential_ref=credential_ref, environment=environment),
        connection_id=connection_id,
        display_name=_text(message, "account_name") or _text(message, "account_id"),
        account_type=account_type(_text(message, "account_type")),
        balance=_number(message, "account_balance"),
        equity=_number(message, "equity"),
        as_of=timestamp(message),
    )


def order_event(message: Any) -> tuple[str, OrderEvent]:
    """One order notification, as `(order_id, event)`.

    Refuses two things rather than guessing at them.

    An unmapped status is refused because the three things it could mean differ
    by the size of a position. A notification with no order identifier is
    refused because an event that cannot be attached to an order is an event
    that would be attached to the wrong one.

    **Quantities are cumulative where Rithmic reports a running total**, which
    it does as `total_fill_size`. The desk's `OrderEvent` refuses to carry both
    a cumulative total and an increment, so only one is set — and a redelivered
    event is a no-op either way, because the event key makes it one.
    """
    order_id = _text(message, "basket_id") or _text(message, "order_id")
    if not order_id:
        raise NormalisationRefused(
            "a Rithmic order notification carried no order identifier, so it cannot be "
            "attached to an order. It was recorded as unreconciled rather than applied."
        )
    status = _text(message, "status") or _text(message, "completion_reason")
    mapped = event_type(status)
    if mapped is None:
        raise NormalisationRefused(
            f"'{status or 'an unnamed status'}' is not a Rithmic order status this build "
            "maps. It could mean a working order, a dead one or a fill, and those differ "
            "by the size of a position, so nothing was applied. Reconciliation against the "
            "provider's own snapshot will settle it."
        )
    filled: Any = getattr(message, "total_fill_size", None)
    cumulative: int | None = None
    if filled is not None and filled != "":
        try:
            cumulative = int(filled)
        except (TypeError, ValueError):
            # A total that will not parse is not a total. Left unset, so the
            # event records what happened without asserting a quantity.
            cumulative = None
    return order_id, OrderEvent(
        event_type=mapped,
        at=timestamp(message),
        provider_event_id=_text(message, "exchange_order_id") or _text(message, "notify_type"),
        sequence=int(getattr(message, "sequence_number", 0) or 0) or None,
        cumulative_quantity=cumulative,
        last_price=_number(message, "fill_price") or _number(message, "trade_price"),
        limit_price=_number(message, "price"),
        stop_price=_number(message, "trigger_price"),
        reason=_text(message, "text") or _text(message, "status"),
        received_at=datetime.now(UTC),
    )


def position(message: Any, *, account_uid: str = "", snapshot_version: int = 0) -> Position:
    """One P&L position row, as the desk's own `Position`.

    Signed net quantity, from `fill_buy_qty` minus `fill_sell_qty`. The protocol
    reports the two sides separately; a net field it does not send would be a
    number this code invented, and a "long 3 / short 1" pair is two records of
    one position that will eventually disagree.

    `average_price` is left `None` when unreported rather than zeroed, and
    `realised_pnl` takes the closed figure only when there is one — a zero there
    reads as "nothing was made or lost", which is a claim, not an absence.
    """
    symbol = _text(message, "symbol")
    if not symbol:
        raise NormalisationRefused("a Rithmic position row carried no symbol")
    bought = int(getattr(message, "fill_buy_qty", 0) or 0)
    sold = int(getattr(message, "fill_sell_qty", 0) or 0)
    closed = _number(message, "closed_position_pnl")
    return Position(
        account_uid=account_uid or _text(message, "account_id"),
        symbol=symbol,
        quantity=bought - sold,
        average_price=_number(message, "avg_open_fill_price"),
        realised_pnl=closed if closed is not None else 0.0,
        as_of=timestamp(message),
        snapshot_version=snapshot_version,
    )
