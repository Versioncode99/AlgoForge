"""The OMS: what it accepts, what it refuses, and whether the book adds up.

The refusals come first because they are the architecture. `proposal -> risk ->
gate -> OMS -> adapter` is only real if the OMS enforces the arrow pointing at
it, so the first four tests here are four ways of trying to skip the gate.

The accounting tests come second and are just as necessary. A book that quietly
double-counts realised P&L is a book whose every downstream number is wrong, and
nothing about it looks broken.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from forge.execution import (
    Clearance,
    ExecutionStore,
    InstrumentRule,
    InstrumentSpec,
    MarketState,
    OrderManagementSystem,
    OrderRefused,
    PaperBroker,
    ProposedOrder,
    Side,
    screen,
)
from forge.execution.gate import GateContext
from forge.risk import PortfolioLimits

NOW = datetime(2026, 3, 4, 15, 0, tzinfo=UTC)
NQ = InstrumentSpec(symbol="NQ", tick_size=0.25, multiplier=20, commission=1.5)


def context() -> GateContext:
    return GateContext(
        now=NOW,
        limits=PortfolioLimits(name="fund", max_leverage=100.0, max_position_weight=1.0),
        capital=10_000_000.0,
        instruments={"NQ": InstrumentRule(symbol="NQ", multiplier=20, adv_notional=5e10,
                                          shortable=True)},
        market={"NQ": MarketState(symbol="NQ", open=True, last_price=20_000,
                                  last_price_at=NOW - timedelta(seconds=5))},
        verdicts={"good": "PASS"},
    )


def order(order_id: str = "o1", **overrides: object) -> ProposedOrder:
    base: dict[str, object] = {
        "order_id": order_id,
        "symbol": "NQ",
        "side": Side.BUY,
        "quantity": 1,
        "strategy_id": "good",
        "reference_price": 20_000.0,
        "data_as_of": NOW,
        "created_at": NOW,
    }
    return ProposedOrder(**{**base, **overrides})  # type: ignore[arg-type]


@pytest.fixture
def oms(tmp_path: Path) -> OrderManagementSystem:
    store = ExecutionStore(tmp_path / "execution.db", starting_cash=10_000_000.0)
    return OrderManagementSystem(store, PaperBroker(instruments={"NQ": NQ}))


def cleared(order_to_send: ProposedOrder) -> Clearance:
    decision = screen(order_to_send, context())
    assert decision.clearance is not None, decision.reasons
    return decision.clearance


# ── nothing routes around the gate ───────────────────────────────────────────


def test_an_order_with_no_clearance_is_refused(oms: OrderManagementSystem) -> None:
    with pytest.raises(OrderRefused, match="no pre-trade clearance"):
        oms.submit(order(), None, reference_price=20_000.0)


def test_an_order_edited_after_clearance_is_refused(oms: OrderManagementSystem) -> None:
    """The clearance is bound to a content hash, so a size change breaks it."""
    clearance = cleared(order())
    tampered = order().model_copy(update={"quantity": 99})
    with pytest.raises(OrderRefused, match="modified after it was screened"):
        oms.submit(tampered, clearance, reference_price=20_000.0)


def test_a_clearance_issued_for_a_different_order_is_refused(
    oms: OrderManagementSystem,
) -> None:
    clearance = cleared(order("o1"))
    with pytest.raises(OrderRefused, match="different order"):
        oms.submit(order("o2"), clearance, reference_price=20_000.0)


def test_the_same_order_cannot_be_submitted_twice(oms: OrderManagementSystem) -> None:
    proposed = order()
    oms.submit(proposed, cleared(proposed), reference_price=20_000.0)
    with pytest.raises(OrderRefused, match="already been submitted"):
        oms.submit(proposed, cleared(proposed), reference_price=20_000.0)


# ── fills are labelled, and cost something ───────────────────────────────────


def test_a_fill_is_labelled_simulated_on_the_record(oms: OrderManagementSystem) -> None:
    proposed = order()
    oms.submit(proposed, cleared(proposed), reference_price=20_000.0)
    fill = oms.store.fills()[0]
    assert fill.simulated is True
    assert fill.venue == "paper"
    assert "not calibrated" in fill.basis
    assert oms.book().simulated is True


def test_a_buy_pays_slippage_and_commission(oms: OrderManagementSystem) -> None:
    proposed = order()
    accepted = oms.submit(proposed, cleared(proposed), reference_price=20_000.0)
    assert accepted.status == "filled"
    # One tick of 0.25 against the buyer.
    assert accepted.average_price == pytest.approx(20_000.25)
    book = oms.book()
    assert book.commission_paid == pytest.approx(1.5)
    assert book.slippage_paid == pytest.approx(5.0)


def test_the_cash_effect_uses_the_contract_multiplier(oms: OrderManagementSystem) -> None:
    """Without it a futures fill is wrong by a factor of twenty."""
    proposed = order()
    oms.submit(proposed, cleared(proposed), reference_price=20_000.0)
    book = oms.book()
    assert book.cash == pytest.approx(10_000_000 - 20_000.25 * 20 - 1.5)


def test_an_order_with_no_reference_price_rests_rather_than_filling_at_an_invented_one(
    oms: OrderManagementSystem,
) -> None:
    # `reference_price=None` on `submit` means "use the order's own"; an order
    # that carries none either is what leaves the simulator with nothing to
    # price against.
    proposed = order(reference_price=None)
    accepted = oms.submit(proposed, cleared(proposed))
    assert accepted.status == "working"
    assert oms.store.fills() == []


def test_an_unmarketable_limit_order_rests(oms: OrderManagementSystem) -> None:
    proposed = order(order_type="limit", limit_price=19_000.0)
    accepted = oms.submit(proposed, cleared(proposed), reference_price=20_000.0)
    assert accepted.status == "working"
    assert oms.store.fills() == []


def test_a_marketable_limit_order_fills_no_worse_than_its_limit(
    oms: OrderManagementSystem,
) -> None:
    # The simulated price is 20,000.25 after a tick of slippage, so a limit at
    # 20,000.50 is marketable and the fill may be no worse than the limit.
    proposed = order(order_type="limit", limit_price=20_000.50)
    accepted = oms.submit(proposed, cleared(proposed), reference_price=20_000.0)
    assert accepted.status == "filled"
    assert accepted.average_price is not None
    assert accepted.average_price <= 20_000.50


# ── the book adds up ─────────────────────────────────────────────────────────


def test_a_round_trip_realises_the_difference_once(oms: OrderManagementSystem) -> None:
    """Adding realised P&L back to cash counts every closing trade twice."""
    buy = order("o1")
    oms.submit(buy, cleared(buy), reference_price=20_000.0)
    sell = order("o2", side=Side.SELL)
    oms.submit(sell, cleared(sell), reference_price=20_050.0)

    book = oms.book()
    # Bought at 20,000.25, sold at 20,049.75: 49.50 points on a 20x multiplier.
    assert book.realised_pnl == pytest.approx(990.0)
    assert book.cash == pytest.approx(10_000_000 + 990.0 - 3.0)
    assert [position.quantity for position in book.positions] == [0]


def test_a_position_crossing_through_flat_realises_against_the_old_side(
    oms: OrderManagementSystem,
) -> None:
    """Netting it as one average leaves a long carrying a short's cost basis."""
    buy = order("o1")
    oms.submit(buy, cleared(buy), reference_price=20_000.0)
    reverse = order("o2", side=Side.SELL, quantity=3)
    oms.submit(reverse, cleared(reverse), reference_price=20_100.0)

    book = oms.book()
    position = book.positions[0]
    assert position.quantity == -2
    # The remainder opens at the fill price rather than at a blended average.
    assert position.average_price == pytest.approx(20_099.75)
    assert book.realised_pnl > 0


def test_reconciliation_is_clean_when_orders_and_fills_agree(
    oms: OrderManagementSystem,
) -> None:
    proposed = order()
    oms.submit(proposed, cleared(proposed), reference_price=20_000.0)
    report = oms.reconcile()
    assert report["reconciled"] is True
    assert report["orders"] == 1 and report["fills"] == 1


def test_reconciliation_reports_a_fill_with_no_order_behind_it(
    oms: OrderManagementSystem, tmp_path: Path
) -> None:
    """The point of reconciling a simulator is to catch this code, not a broker."""
    from forge.execution.oms import Fill

    oms.store.add_fill(
        Fill(fill_id="f_orphan", order_id="never_submitted", symbol="NQ", side=Side.BUY,
             quantity=1, price=20_000.0, filled_at=NOW, venue="paper")
    )
    report = oms.reconcile()
    assert report["reconciled"] is False
    assert any(row["order_id"] == "never_submitted" for row in report["discrepancies"])


def test_an_empty_book_is_honestly_simulated(oms: OrderManagementSystem) -> None:
    book = oms.book()
    assert book.simulated is True
    assert book.venues == ()
    assert book.positions == ()


# ── cancelling ───────────────────────────────────────────────────────────────


def test_a_working_order_can_be_cancelled(oms: OrderManagementSystem) -> None:
    proposed = order(reference_price=None)
    oms.submit(proposed, cleared(proposed))
    cancelled = oms.cancel("o1")
    assert cancelled.status == "cancelled"


def test_a_filled_order_cannot_be_cancelled(oms: OrderManagementSystem) -> None:
    proposed = order()
    oms.submit(proposed, cleared(proposed), reference_price=20_000.0)
    with pytest.raises(OrderRefused, match="filled and cannot be cancelled"):
        oms.cancel("o1")


def test_cancelling_something_that_does_not_exist_is_refused(
    oms: OrderManagementSystem,
) -> None:
    with pytest.raises(OrderRefused, match="no order"):
        oms.cancel("nothing")


# ── an adapter that fails does not lose the order ────────────────────────────


def test_an_adapter_raising_leaves_a_rejected_order_rather_than_nothing(
    tmp_path: Path,
) -> None:
    class Broken:
        name = "broken"
        simulated = True

        def place(self, order_placed, reference_price):
            raise RuntimeError("the venue hung up")

        def cancel(self, order_id: str) -> bool:
            return False

    oms = OrderManagementSystem(ExecutionStore(tmp_path / "broken.db"), Broken())  # type: ignore[arg-type]
    proposed = order()
    accepted = oms.submit(proposed, cleared(proposed), reference_price=20_000.0)
    assert accepted.status == "rejected"
    assert "the venue hung up" in accepted.reason
    # The record survives, which is what makes the failure visible in Operations
    # rather than discovered from a position that will not close.
    assert oms.store.order("o1") is not None
