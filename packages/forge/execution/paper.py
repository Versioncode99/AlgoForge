"""A local execution simulator. The only adapter this build has, and it says so.

What it models: a fill at the reference price moved against you by a configured
number of ticks, plus commission. What it does not model: queue position, partial
fills against real depth, adverse selection, or anything about how a venue
actually behaves under load. Those need market data this application does not
have, and approximating them would produce numbers that look calibrated and are
not.

So every fill it produces carries `simulated=True`, the venue name `paper`, and a
`basis` string naming the model that priced it. The README's position on the rest
of the application applies here word for word: fills are modelled, not
calibrated, and nothing downstream may present them as anything else.

A limit order that the reference price has not reached does not fill. It rests as
working and stays there — this simulator does not advance time, so a resting
order is a genuine open order rather than one that will be quietly filled later
by a clock nobody is running.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from forge.contracts.hashing import stable_id
from forge.execution.gate import Side
from forge.execution.oms import Fill, Order


@dataclass(frozen=True)
class InstrumentSpec:
    """What the simulator needs to price a fill in currency."""

    symbol: str
    tick_size: float = 0.25
    multiplier: float = 1.0
    #: Currency, per contract, per side.
    commission: float = 0.0


@dataclass
class PaperBroker:
    """Fills at the reference price plus a fixed slippage in ticks.

    `slippage_ticks` is deliberately a whole number of ticks rather than a
    percentage: slippage in a futures book is a queue phenomenon measured in
    ticks, and expressing it in basis points would make it scale with price in a
    way that is wrong for exactly the instruments this application trades.
    """

    name: str = "paper"
    simulated: bool = True
    slippage_ticks: float = 1.0
    instruments: dict[str, InstrumentSpec] = field(default_factory=dict)
    #: Orders that were placed but did not fill, so `cancel` has something to
    #: answer about. The OMS owns the durable record; this is the adapter's own
    #: view, which a real one would keep too.
    _resting: set[str] = field(default_factory=set)

    def place(self, order: Order, reference_price: float | None) -> tuple[Fill, ...]:
        if reference_price is None or reference_price <= 0:
            # No price, no fill, and no invented one. The order rests, and
            # Operations shows a working order with nothing behind it — which is
            # the true state of affairs.
            self._resting.add(order.order_id)
            return ()

        spec = self.instruments.get(order.symbol, InstrumentSpec(symbol=order.symbol))
        direction = 1.0 if order.side is Side.BUY else -1.0
        slip_points = self.slippage_ticks * spec.tick_size * direction
        price = reference_price + slip_points

        if order.order_type == "limit":
            if order.limit_price is None:
                self._resting.add(order.order_id)
                return ()
            marketable = (
                price <= order.limit_price
                if order.side is Side.BUY
                else price >= order.limit_price
            )
            if not marketable:
                self._resting.add(order.order_id)
                return ()
            # A marketable limit fills at its own limit at worst, which is the
            # one guarantee a limit order actually gives.
            price = min(price, order.limit_price) if order.side is Side.BUY else max(
                price, order.limit_price
            )

        filled_at = datetime.now(UTC)
        return (
            Fill(
                fill_id=stable_id(
                    "fill", {"order": order.order_id, "at": filled_at.isoformat()}
                ),
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                price=round(price, 8),
                commission=round(spec.commission * order.quantity, 4),
                slippage=round(abs(slip_points) * spec.multiplier * order.quantity, 4),
                filled_at=filled_at,
                venue=self.name,
                multiplier=spec.multiplier,
                simulated=True,
                basis=(
                    f"simulated: reference {reference_price:g} "
                    f"{'+' if direction > 0 else '-'} {self.slippage_ticks:g} tick(s) "
                    f"of {spec.tick_size:g}; not calibrated against any venue"
                ),
            ),
        )

    def cancel(self, order_id: str) -> bool:
        """Whether there was a resting order to cancel.

        `False` for an order this adapter never rested is the honest answer, and
        the OMS still marks its own record cancelled: the two are different
        facts, and a simulator claiming to have cancelled something it never held
        would hide a real desynchronisation from reconciliation.
        """
        resting = order_id in self._resting
        self._resting.discard(order_id)
        return resting
