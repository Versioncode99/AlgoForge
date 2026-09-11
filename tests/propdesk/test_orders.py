"""The order and position lifecycles, under events that arrive badly.

Every test here is a delivery pattern the research documents as actually
occurring: at-least-once redelivery, no ordering guarantee, partial fills in
sequence, a cancel that lost a race to a fill, and an exchange bust that reduces
a position the desk already believes in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from forge.propdesk import (
    LifecycleError,
    OrderEvent,
    OrderEventType,
    OrderMachine,
    OrderRecord,
    OrderState,
    OrderType,
    Position,
    PositionTransition,
    Side,
    TimeInForce,
    apply_fill,
    classify_transition,
    idempotency_key,
)
from pydantic import ValidationError

NOW = datetime(2026, 9, 11, 15, 0, tzinfo=UTC)


def order(quantity: int = 4, **overrides) -> OrderRecord:
    base: dict = {
        "order_id": "o1",
        "account_uid": "acct",
        "symbol": "MNQ",
        "side": Side.BUY,
        "quantity": quantity,
        "order_type": OrderType.LIMIT,
        "time_in_force": TimeInForce.DAY,
        "limit_price": 20_000.0,
        "created_at": NOW,
        "updated_at": NOW,
    }
    return OrderRecord(**{**base, **overrides})


def event(kind: OrderEventType, *, seconds: int = 0, **fields) -> OrderEvent:
    return OrderEvent(event_type=kind, at=NOW + timedelta(seconds=seconds), **fields)


@pytest.fixture
def machine() -> OrderMachine:
    machine = OrderMachine()
    machine.track(order())
    return machine


# ── the happy path ───────────────────────────────────────────────────────────


def test_an_order_walks_created_submitted_working_partial_filled(machine) -> None:
    for kind, cumulative, expected in (
        (OrderEventType.SUBMITTED, None, OrderState.SUBMITTED),
        (OrderEventType.ACKNOWLEDGED, None, OrderState.WORKING),
        (OrderEventType.PARTIAL_FILL, 1, OrderState.PARTIALLY_FILLED),
        (OrderEventType.PARTIAL_FILL, 3, OrderState.PARTIALLY_FILLED),
        (OrderEventType.FILL, 4, OrderState.FILLED),
    ):
        applied = machine.apply(
            "o1",
            event(
                kind,
                provider_event_id=f"{kind.value}-{cumulative}",
                cumulative_quantity=cumulative,
                last_price=20_000.0,
            ),
        )
        assert applied.accepted, applied.detail
        assert applied.order.state is expected

    final = machine.require("o1")
    assert final.filled_quantity == 4
    assert final.remaining == 0
    assert final.open is False


def test_a_fill_event_with_no_quantity_means_the_whole_order(machine) -> None:
    """Some providers report completion without restating the size.

    Leaving the order FILLED with a filled quantity of zero would make
    reconciliation report a discrepancy on every cycle forever.
    """
    machine.apply("o1", event(OrderEventType.ACKNOWLEDGED, provider_event_id="ack"))
    applied = machine.apply(
        "o1", event(OrderEventType.FILL, provider_event_id="fill", last_price=20_000.0)
    )
    assert applied.accepted
    assert applied.order.state is OrderState.FILLED
    assert applied.order.filled_quantity == 4


def test_an_event_labelled_partial_that_completes_the_order_fills_it(machine) -> None:
    applied = machine.apply(
        "o1",
        event(
            OrderEventType.PARTIAL_FILL, provider_event_id="p", cumulative_quantity=4,
            last_price=20_000.0,
        ),
    )
    assert applied.order.state is OrderState.FILLED


# ── duplicates ───────────────────────────────────────────────────────────────


def test_a_duplicate_event_changes_nothing(machine) -> None:
    """At-least-once delivery must not double a position."""
    first = machine.apply(
        "o1",
        event(
            OrderEventType.PARTIAL_FILL, provider_event_id="fill-1",
            cumulative_quantity=2, last_price=20_000.0,
        ),
    )
    assert first.accepted and first.fill_delta == 2

    second = machine.apply(
        "o1",
        event(
            OrderEventType.PARTIAL_FILL, provider_event_id="fill-1",
            cumulative_quantity=2, last_price=20_000.0,
        ),
    )
    assert second.accepted is False
    assert second.dropped_because == "duplicate"
    assert second.fill_delta == 0
    assert machine.require("o1").filled_quantity == 2


def test_an_event_without_a_provider_id_is_deduped_on_its_content(machine) -> None:
    payload = event(
        OrderEventType.PARTIAL_FILL, cumulative_quantity=1, last_price=20_000.0
    )
    assert machine.apply("o1", payload).accepted is True
    assert machine.apply("o1", payload).dropped_because == "duplicate"


def test_an_increment_reported_twice_does_not_accumulate(machine) -> None:
    """The pattern that over-sizes a follower on a provider without cumulative totals."""
    increment = event(
        OrderEventType.PARTIAL_FILL,
        provider_event_id="inc-1",
        increment_quantity=2,
        last_price=20_000.0,
    )
    machine.apply("o1", increment)
    machine.apply("o1", increment)
    assert machine.require("o1").filled_quantity == 2


def test_an_event_cannot_state_both_a_total_and_an_increment() -> None:
    with pytest.raises(ValidationError, match="either a cumulative quantity"):
        OrderEvent(
            event_type=OrderEventType.PARTIAL_FILL,
            at=NOW,
            cumulative_quantity=2,
            increment_quantity=1,
        )


# ── out of order ─────────────────────────────────────────────────────────────


def test_a_working_event_that_arrives_after_a_fill_is_dropped_as_stale(machine) -> None:
    machine.apply(
        "o1",
        event(
            OrderEventType.FILL, provider_event_id="f", cumulative_quantity=4,
            last_price=20_000.0,
        ),
    )
    late = machine.apply(
        "o1", event(OrderEventType.ACKNOWLEDGED, provider_event_id="late", seconds=-5)
    )
    assert late.accepted is False
    assert late.dropped_because == "stale"
    assert machine.require("o1").state is OrderState.FILLED


def test_a_lower_sequence_number_is_dropped_as_stale_not_as_illegal(machine) -> None:
    """The distinction matters: 'illegal' sends an operator hunting a bug."""
    machine.apply(
        "o1",
        event(
            OrderEventType.PARTIAL_FILL, provider_event_id="a", sequence=10,
            cumulative_quantity=2, last_price=20_000.0,
        ),
    )
    late = machine.apply(
        "o1",
        event(
            OrderEventType.PARTIAL_FILL, provider_event_id="b", sequence=4,
            cumulative_quantity=1, last_price=20_000.0,
        ),
    )
    assert late.dropped_because == "stale"
    assert "arrived after a later one" in late.detail


def test_a_fill_total_that_goes_backwards_is_stale(machine) -> None:
    machine.apply(
        "o1",
        event(
            OrderEventType.PARTIAL_FILL, provider_event_id="a", cumulative_quantity=3,
            last_price=20_000.0,
        ),
    )
    back = machine.apply(
        "o1",
        event(
            OrderEventType.PARTIAL_FILL, provider_event_id="b", cumulative_quantity=1,
            last_price=20_000.0,
        ),
    )
    assert back.dropped_because == "stale"
    assert machine.require("o1").filled_quantity == 3


# ── refusals ─────────────────────────────────────────────────────────────────


def test_an_event_on_a_terminal_order_is_refused(machine) -> None:
    machine.apply("o1", event(OrderEventType.CANCELLED, provider_event_id="c"))
    after = machine.apply(
        "o1",
        event(
            OrderEventType.FILL, provider_event_id="f", cumulative_quantity=4,
            last_price=20_000.0,
        ),
    )
    assert after.dropped_because == "illegal"
    assert machine.require("o1").state is OrderState.CANCELLED


def test_filling_more_than_was_ordered_is_refused_and_named(machine) -> None:
    applied = machine.apply(
        "o1",
        event(
            OrderEventType.FILL, provider_event_id="f", cumulative_quantity=9,
            last_price=20_000.0,
        ),
    )
    assert applied.dropped_because == "illegal"
    assert "reconciliation must resolve it" in applied.detail


def test_a_modification_below_the_filled_quantity_is_refused(machine) -> None:
    machine.apply(
        "o1",
        event(
            OrderEventType.PARTIAL_FILL, provider_event_id="p", cumulative_quantity=3,
            last_price=20_000.0,
        ),
    )
    applied = machine.apply(
        "o1", event(OrderEventType.MODIFIED, provider_event_id="m", quantity=2)
    )
    assert applied.dropped_because == "illegal"
    assert "below the 3 already filled" in applied.detail


def test_tracking_the_same_order_twice_is_refused(machine) -> None:
    with pytest.raises(LifecycleError, match="already tracked"):
        machine.track(order())


def test_applying_to_an_unknown_order_is_refused(machine) -> None:
    with pytest.raises(LifecycleError, match="no order"):
        machine.apply("nope", event(OrderEventType.ACKNOWLEDGED))


# ── modification ─────────────────────────────────────────────────────────────


def test_a_modification_moves_the_price_and_leaves_the_order_working(machine) -> None:
    machine.apply("o1", event(OrderEventType.ACKNOWLEDGED, provider_event_id="ack"))
    applied = machine.apply(
        "o1",
        event(OrderEventType.MODIFIED, provider_event_id="m1", limit_price=19_990.0),
    )
    assert applied.accepted
    assert applied.order.state is OrderState.WORKING
    assert applied.order.limit_price == 19_990.0


# ── busts ────────────────────────────────────────────────────────────────────


def test_a_bust_is_the_one_event_that_reduces_a_fill_total(machine) -> None:
    machine.apply(
        "o1",
        event(
            OrderEventType.FILL, provider_event_id="f", cumulative_quantity=4,
            last_price=20_000.0,
        ),
    )
    busted = machine.apply(
        "o1",
        event(OrderEventType.BUSTED, provider_event_id="b", increment_quantity=2),
    )
    assert busted.accepted
    assert busted.fill_delta == -2
    assert machine.require("o1").filled_quantity == 2
    assert machine.require("o1").state is OrderState.PARTIALLY_FILLED


def test_a_bust_larger_than_the_recorded_fill_is_refused(machine) -> None:
    machine.apply(
        "o1",
        event(
            OrderEventType.PARTIAL_FILL, provider_event_id="p", cumulative_quantity=1,
            last_price=20_000.0,
        ),
    )
    applied = machine.apply(
        "o1", event(OrderEventType.BUSTED, provider_event_id="b", increment_quantity=3)
    )
    assert applied.dropped_because == "illegal"


def test_a_bust_without_a_quantity_is_refused(machine) -> None:
    applied = machine.apply("o1", event(OrderEventType.BUSTED, provider_event_id="b"))
    assert applied.dropped_because == "illegal"
    assert "how many contracts were voided" in applied.detail


# ── average price ────────────────────────────────────────────────────────────


def test_the_average_price_is_weighted_across_partial_fills(machine) -> None:
    machine.apply(
        "o1",
        event(
            OrderEventType.PARTIAL_FILL, provider_event_id="a", cumulative_quantity=2,
            last_price=20_000.0,
        ),
    )
    machine.apply(
        "o1",
        event(
            OrderEventType.FILL, provider_event_id="b", cumulative_quantity=4,
            last_price=20_010.0,
        ),
    )
    assert machine.require("o1").average_price == pytest.approx(20_005.0)


# ── idempotency keys ─────────────────────────────────────────────────────────


def test_the_idempotency_key_is_deterministic_and_fits_a_provider_tag() -> None:
    """A retry must reuse the key, and the key must fit the field it is sent in."""
    args = {
        "group_id": "g",
        "source_order_id": "leader-1",
        "source_marker": "v3",
        "account_uid": "acct",
        "action": "place",
    }
    first, second = idempotency_key(**args), idempotency_key(**args)
    assert first == second
    assert len(first) <= 50


def test_a_different_follower_gets_a_different_key() -> None:
    base = {
        "group_id": "g",
        "source_order_id": "leader-1",
        "source_marker": "v3",
        "action": "place",
    }
    assert idempotency_key(account_uid="a", **base) != idempotency_key(
        account_uid="b", **base
    )


# ── positions ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (0, 0, PositionTransition.UNCHANGED),
        (0, 2, PositionTransition.OPENED),
        (2, 4, PositionTransition.INCREASED),
        (4, 1, PositionTransition.REDUCED),
        (3, 0, PositionTransition.CLOSED),
        (2, -1, PositionTransition.REVERSED),
        (-2, 3, PositionTransition.REVERSED),
        (-1, -4, PositionTransition.INCREASED),
    ],
)
def test_position_transitions_are_named(before, after, expected) -> None:
    assert classify_transition(before, after) is expected


def test_a_fill_that_crosses_flat_realises_and_reopens_at_the_new_price() -> None:
    """Netting a crossing fill into one average leaves a long carrying a short's basis."""
    position = Position(account_uid="a", symbol="MNQ", quantity=2, average_price=20_000.0)
    change = apply_fill(
        position, side=Side.SELL, quantity=5, price=20_010.0, multiplier=2.0
    )
    assert change.transition is PositionTransition.REVERSED
    assert change.after == -3
    # Two contracts closed at ten points, two dollars a point.
    assert change.realised == pytest.approx(2 * 10.0 * 2.0)
    assert change.position.average_price == pytest.approx(20_010.0)


def test_closing_exactly_flat_clears_the_average() -> None:
    position = Position(account_uid="a", symbol="MNQ", quantity=3, average_price=20_000.0)
    change = apply_fill(position, side=Side.SELL, quantity=3, price=20_004.0)
    assert change.transition is PositionTransition.CLOSED
    assert change.position.quantity == 0
    assert change.position.average_price is None


def test_a_zero_quantity_fill_is_refused() -> None:
    with pytest.raises(LifecycleError, match="must be positive"):
        apply_fill(
            Position(account_uid="a", symbol="MNQ"), side=Side.BUY, quantity=0, price=1.0
        )
