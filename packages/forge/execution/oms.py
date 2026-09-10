"""Order management: the one road between a cleared order and a fill.

The architectural rule this module exists to make true is

    proposal → risk → pre-trade gate → OMS → broker adapter

and never proposal → broker. Nothing here places an order; the OMS hands a
*cleared* order to an adapter, and the adapter is the only thing that touches a
venue. In this build the only adapter is a local simulator, which is exactly the
right shape for the free version: the seam where a real broker would attach is
present, typed, and empty rather than faked.

**Clearance is verified, not trusted.** `submit` recomputes the order's
fingerprint and compares it against the clearance it was handed. An order edited
after it was screened, or handed a clearance issued for a different order, is
refused. There is no argument that skips this and no privileged caller.

**Simulated fills are labelled at every layer.** Every fill carries
`simulated=True` and the venue that produced it, the book reports it, and the
API surfaces it. A simulated fill rendered as a real one would be the single most
damaging thing this package could do, so the label is on the record rather than
on the screen: it cannot be lost by a component that forgets to draw it.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import Field

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel
from forge.execution.gate import Clearance, ProposedOrder, Side

SCHEMA_VERSION = 1


class OrderRefused(Exception):
    """The OMS would not accept the order. Nothing was placed."""


class Fill(FrozenModel):
    fill_id: str
    order_id: str
    symbol: str
    side: Side
    quantity: int
    price: float
    #: Commission and modelled slippage, as currency, already reflected in the
    #: cash effect. Reported separately so cost is never invisible.
    commission: float = 0.0
    slippage: float = 0.0
    filled_at: datetime
    venue: str
    #: Currency value of one point of price movement. Carried on the fill rather
    #: than looked up later: the cash effect of a futures fill is wrong by a
    #: factor of the multiplier without it, and looking it up at report time
    #: would make an old fill re-price when a contract specification changed.
    multiplier: float = 1.0
    #: Always true in this build. A field rather than a constant because the
    #: field is what a future real adapter has to set to `False` deliberately.
    simulated: bool = True
    #: How the price was arrived at, in words. On a simulated fill this is the
    #: model; on a real one it would be the venue's own report.
    basis: str = ""


class Order(FrozenModel):
    """An order the OMS accepted, and what became of it."""

    order_id: str
    symbol: str
    side: Side
    quantity: int
    order_type: Literal["market", "limit"]
    limit_price: float | None = None
    strategy_id: str = ""
    portfolio_id: str = ""
    status: Literal["accepted", "working", "filled", "cancelled", "rejected"]
    filled_quantity: int = 0
    average_price: float | None = None
    accepted_at: datetime
    updated_at: datetime
    clearance_id: str
    venue: str = ""
    reason: str = ""

    @property
    def open(self) -> bool:
        return self.status in {"accepted", "working"}


class Position(FrozenModel):
    symbol: str
    quantity: int
    average_price: float
    realised_pnl: float = 0.0

    @property
    def side(self) -> str:
        if self.quantity > 0:
            return "LONG"
        return "SHORT" if self.quantity < 0 else "FLAT"


class Book(FrozenModel):
    """The whole state of execution at one instant."""

    cash: float
    positions: tuple[Position, ...]
    open_orders: tuple[Order, ...]
    realised_pnl: float
    commission_paid: float
    slippage_paid: float
    #: True while every fill in the book came from a simulator. Computed from the
    #: fills rather than configured, so it cannot say "real" because somebody
    #: changed a setting.
    simulated: bool
    venues: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@runtime_checkable
class BrokerAdapter(Protocol):
    """Where a real broker would attach.

    Two properties and one method. `simulated` is not optional and not
    defaulted: an adapter author has to state which kind they wrote, and the
    book reads the answer off the fills rather than off a promise.
    """

    name: str
    simulated: bool

    def place(self, order: Order, reference_price: float | None) -> tuple[Fill, ...]:
        """Attempt the order. Returning no fills means it rests as working."""
        ...

    def cancel(self, order_id: str) -> bool:
        ...


class ExecutionStore:
    """Orders, fills and cash, on disk.

    Append-only for fills. An execution record that can be edited is not a
    record, and reconciliation exists precisely to compare what was ordered with
    what came back.
    """

    def __init__(self, path: Path, *, starting_cash: float = 0.0) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS orders ("
                "order_id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
                "accepted_at TEXT NOT NULL, schema_version INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS fills ("
                "fill_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, "
                "filled_at TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS fills_by_order ON fills(order_id)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS execution_state (key TEXT PRIMARY KEY, value TEXT)"
            )
            row = db.execute(
                "SELECT value FROM execution_state WHERE key='starting_cash'"
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO execution_state VALUES ('starting_cash', ?)",
                    (json.dumps(float(starting_cash)),),
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def starting_cash(self) -> float:
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT value FROM execution_state WHERE key='starting_cash'"
            ).fetchone()
        return 0.0 if row is None else float(json.loads(row["value"]))

    def save_order(self, order: Order) -> Order:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO orders VALUES (?, ?, ?, ?) "
                "ON CONFLICT(order_id) DO UPDATE SET payload=excluded.payload",
                (
                    order.order_id,
                    json.dumps(order.model_dump(mode="json")),
                    order.accepted_at.isoformat(),
                    SCHEMA_VERSION,
                ),
            )
        return order

    def order(self, order_id: str) -> Order | None:
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT payload FROM orders WHERE order_id=?", (order_id,)
            ).fetchone()
        return None if row is None else Order.model_validate(json.loads(row["payload"]))

    def orders(self, limit: int = 500) -> list[Order]:
        with closing(self._connect()) as db, db:
            rows = db.execute(
                "SELECT payload FROM orders ORDER BY accepted_at DESC LIMIT ?",
                (max(1, min(limit, 5000)),),
            ).fetchall()
        return [Order.model_validate(json.loads(row["payload"])) for row in rows]

    def add_fill(self, fill: Fill) -> Fill:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO fills VALUES (?, ?, ?, ?)",
                (
                    fill.fill_id,
                    fill.order_id,
                    fill.filled_at.isoformat(),
                    json.dumps(fill.model_dump(mode="json")),
                ),
            )
        return fill

    def fills(self, limit: int = 1000) -> list[Fill]:
        with closing(self._connect()) as db, db:
            rows = db.execute(
                "SELECT payload FROM fills ORDER BY filled_at ASC, fill_id ASC LIMIT ?",
                (max(1, min(limit, 20000)),),
            ).fetchall()
        return [Fill.model_validate(json.loads(row["payload"])) for row in rows]


class OrderManagementSystem:
    """Accepts cleared orders, routes them to an adapter, keeps the book."""

    def __init__(self, store: ExecutionStore, broker: BrokerAdapter) -> None:
        self.store = store
        self.broker = broker

    # ── the only entry point ─────────────────────────────────────────────────
    def submit(
        self,
        order: ProposedOrder,
        clearance: Clearance | None,
        *,
        reference_price: float | None = None,
    ) -> Order:
        """Accept an order that carries valid clearance, and route it.

        `clearance` is required and is verified against the order. Passing
        `None` is not a way to submit an unchecked order — it is refused, and the
        refusal names the gate, because the most likely reason a caller has no
        clearance is that it never went through one.
        """
        if clearance is None:
            raise OrderRefused(
                f"{order.order_id} carries no pre-trade clearance. Every order must be "
                "screened by the gate before the OMS will accept it."
            )
        if not clearance.matches(order):
            raise OrderRefused(
                f"{order.order_id} does not match its clearance: the order was modified "
                "after it was screened, or the clearance belongs to a different order. "
                "Screen it again."
            )
        if self.store.order(order.order_id) is not None:
            raise OrderRefused(f"{order.order_id} has already been submitted")

        now = datetime.now(UTC)
        accepted = Order(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            order_type=order.order_type,
            limit_price=order.limit_price,
            strategy_id=order.strategy_id,
            portfolio_id=order.portfolio_id,
            status="accepted",
            accepted_at=now,
            updated_at=now,
            clearance_id=clearance.clearance_id,
            venue=self.broker.name,
        )
        self.store.save_order(accepted)

        price = reference_price if reference_price is not None else order.reference_price
        try:
            fills = self.broker.place(accepted, price)
        except Exception as exc:
            rejected = accepted.model_copy(
                update={
                    "status": "rejected",
                    "updated_at": datetime.now(UTC),
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
            return self.store.save_order(rejected)

        for fill in fills:
            self.store.add_fill(fill)
        return self.store.save_order(_applied(accepted, fills))

    def cancel(self, order_id: str) -> Order:
        existing = self.store.order(order_id)
        if existing is None:
            raise OrderRefused(f"no order '{order_id}'")
        if not existing.open:
            raise OrderRefused(f"{order_id} is {existing.status} and cannot be cancelled")
        self.broker.cancel(order_id)
        return self.store.save_order(
            existing.model_copy(update={"status": "cancelled", "updated_at": datetime.now(UTC)})
        )

    # ── the book ─────────────────────────────────────────────────────────────
    def book(self) -> Book:
        fills = self.store.fills()
        cash = self.store.starting_cash()
        positions: dict[str, dict[str, float]] = {}
        realised = commission = slippage = 0.0

        for fill in fills:
            signed = fill.quantity if fill.side is Side.BUY else -fill.quantity
            state = positions.setdefault(fill.symbol, {"quantity": 0.0, "average": 0.0,
                                                       "realised": 0.0})
            # `_apply_fill` works in price points; the multiplier turns them into
            # currency exactly once, here. Realised P&L is *not* added back to
            # cash: for a cash-settled instrument the fill's own cash flow
            # already contains it, and adding it again counts every closing
            # trade twice.
            realised_here = _apply_fill(state, signed, fill.price) * fill.multiplier
            realised += realised_here
            state["realised"] += realised_here
            cash -= signed * fill.price * fill.multiplier
            cash -= fill.commission
            commission += fill.commission
            slippage += fill.slippage

        return Book(
            cash=round(cash, 2),
            positions=tuple(
                Position(
                    symbol=symbol,
                    quantity=int(state["quantity"]),
                    average_price=round(state["average"], 6),
                    realised_pnl=round(state["realised"], 2),
                )
                for symbol, state in sorted(positions.items())
                if int(state["quantity"]) != 0 or state["realised"]
            ),
            open_orders=tuple(order for order in self.store.orders() if order.open),
            realised_pnl=round(realised, 2),
            commission_paid=round(commission, 2),
            slippage_paid=round(slippage, 2),
            # `all(())` is True, and an empty book is honestly simulated: nothing
            # in it came from a venue.
            simulated=all(fill.simulated for fill in fills),
            venues=tuple(sorted({fill.venue for fill in fills})),
        )

    def reconcile(self) -> dict[str, Any]:
        """Compare what was ordered against what came back.

        The point of reconciliation in a simulator is not to catch a broker — it
        is to catch *this code*. An order marked filled with no fills behind it,
        or fills totalling more than the order, is a bug in the OMS, and it
        should be visible in Operations rather than discovered from a position
        that will not close.
        """
        orders = {order.order_id: order for order in self.store.orders()}
        totals: dict[str, int] = {}
        for fill in self.store.fills():
            totals[fill.order_id] = totals.get(fill.order_id, 0) + fill.quantity

        discrepancies = []
        for order_id, order in orders.items():
            filled = totals.get(order_id, 0)
            if filled != order.filled_quantity:
                discrepancies.append({
                    "order_id": order_id,
                    "issue": "order's filled quantity disagrees with its fills",
                    "order_says": order.filled_quantity,
                    "fills_say": filled,
                })
            elif filled > order.quantity:
                discrepancies.append({
                    "order_id": order_id,
                    "issue": "more was filled than was ordered",
                    "order_says": order.quantity,
                    "fills_say": filled,
                })
        orphans = sorted(set(totals) - set(orders))
        discrepancies += [
            {"order_id": order_id, "issue": "fills exist for an order that was never recorded"}
            for order_id in orphans
        ]
        return {
            "orders": len(orders),
            "fills": sum(totals.values()),
            "reconciled": not discrepancies,
            "discrepancies": discrepancies,
        }


def _apply_fill(state: dict[str, float], signed: int, price: float) -> float:
    """Update a position with one fill, returning realised P&L.

    Average-cost accounting with signed quantities, which handles the awkward
    case correctly: a fill that crosses through flat realises against the old
    position and opens the remainder at the fill price. Netting it as a single
    average would leave a long position carrying the cost basis of a short.
    """
    quantity = state["quantity"]
    average = state["average"]
    realised = 0.0

    if quantity == 0 or (quantity > 0) == (signed > 0):
        total = quantity + signed
        if total != 0:
            state["average"] = (quantity * average + signed * price) / total
        state["quantity"] = total
        return 0.0

    closing_size = min(abs(signed), abs(quantity))
    direction = 1.0 if quantity > 0 else -1.0
    realised = closing_size * (price - average) * direction
    remaining = quantity + signed
    state["quantity"] = remaining
    if remaining == 0:
        state["average"] = 0.0
    elif (remaining > 0) != (quantity > 0):
        state["average"] = price
    return realised


def _applied(order: Order, fills: tuple[Fill, ...]) -> Order:
    if not fills:
        return order.model_copy(update={"status": "working", "updated_at": datetime.now(UTC)})
    quantity = sum(fill.quantity for fill in fills)
    notional = sum(fill.quantity * fill.price for fill in fills)
    return order.model_copy(
        update={
            "status": "filled" if quantity >= order.quantity else "working",
            "filled_quantity": quantity,
            "average_price": round(notional / quantity, 6) if quantity else None,
            "updated_at": datetime.now(UTC),
        }
    )


def new_order_id(symbol: str, at: datetime, nonce: str = "") -> str:
    return stable_id("order", {"symbol": symbol, "at": at.isoformat(), "nonce": nonce})


class OrderRequest(FrozenModel):
    """What `prepare_orders` produces before anything screens it.

    A separate type from `ProposedOrder` would be duplication; this is the
    field set a caller supplies, and the module that builds proposals fills in
    the identity and the timestamps.
    """

    symbol: str
    side: Side
    quantity: int = Field(gt=0)
    strategy_id: str = ""
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = None
