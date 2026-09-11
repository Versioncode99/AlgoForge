"""The one adapter that executes. It fills, and it says so on every event.

This is a real implementation of the whole `ExecutionAdapter` surface, not a
stub: it holds accounts, tracks working orders, produces partial fills, honours
cancellation races, rejects what a provider would reject, and answers `snapshot`
from its own book so the reconciliation engine has something authoritative to
disagree with. That last point is why it exists in this shape — a reconciler
tested only against a store it also wrote is a reconciler tested against
nothing.

It is also the place the awkward cases can be *made to happen on purpose*.
`fail_next`, `reject_next`, `duplicate_next` and `drop_events` exist so the
tests can produce a rejected follower order, a rate-limit penalty, an
at-least-once redelivery and a silent event gap — the four failure modes the
research names as the actual causes of divergence in production copiers.

Every fill it produces carries `simulated=True` through to the record. There is
no configuration that makes this adapter claim otherwise.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from forge.contracts.hashing import stable_id
from forge.propdesk.credentials import CredentialRecord
from forge.propdesk.fabric import (
    Acknowledgement,
    AdapterHealth,
    CommandKind,
    OrderIntent,
    ProviderSnapshot,
)
from forge.propdesk.identity import (
    SIMULATED_DESCRIPTOR,
    Account,
    AccountCapability,
    AccountKey,
    AccountType,
    BracketModel,
    ConnectionState,
    Environment,
    Provider,
)
from forge.propdesk.orders import (
    LifecycleError,
    OrderEvent,
    OrderEventType,
    OrderRecord,
    OrderState,
    OrderType,
    Position,
    Side,
    apply_fill,
    new_order_id,
)


class SimulatedAccount:
    """One account inside the simulator, with its own book."""

    def __init__(
        self,
        account_id: str,
        *,
        display_name: str = "",
        balance: float = 50_000.0,
        max_contracts: int | None = None,
        can_trade: bool = True,
        account_type: AccountType = AccountType.SIMULATION,
    ) -> None:
        self.account_id = account_id
        self.display_name = display_name or account_id
        self.balance = balance
        self.max_contracts = max_contracts
        self.can_trade = can_trade
        self.account_type = account_type
        self.positions: dict[str, Position] = {}
        self.realised = 0.0


class SimulatedAdapter:
    """A provider that fills against a reference price, and admits it.

    The pricing model is the same one `forge.execution.paper` uses and carries
    the same caveat: a whole number of ticks of slippage against the taker, no
    queue position, no depth. It is a model, not a calibration, and nothing
    downstream may present a number from it as a venue's behaviour.
    """

    def __init__(
        self,
        *,
        connection_id: str = "sim",
        credential_ref: str = "sim-credential",
        slippage_ticks: float = 1.0,
        tick_size: float = 0.25,
        multiplier: float = 1.0,
        #: Fills this many contracts per event, so a larger order produces
        #: genuine partial fills rather than one instantaneous complete one.
        fill_chunk: int | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.descriptor = SIMULATED_DESCRIPTOR
        self.simulated = True
        self.connection_id = connection_id
        self.credential_ref = credential_ref
        self.slippage_ticks = slippage_ticks
        self.tick_size = tick_size
        self.multiplier = multiplier
        self.fill_chunk = fill_chunk
        self._now = now
        self._accounts: dict[str, SimulatedAccount] = {}
        self._orders: dict[str, OrderRecord] = {}
        self._by_key: dict[str, str] = {}
        self._pending_events: list[tuple[str, OrderEvent]] = []
        self._state = ConnectionState.DISCONNECTED
        self._version = itertools.count(1)
        self._versions: dict[str, int] = {}
        self._marks: dict[str, float] = {}
        self.commands: list[OrderIntent] = []

        # ── the levers the tests pull ────────────────────────────────────────
        #: Raise on the next command, as a transport fault would.
        self.fail_next = 0
        #: Reject the next command with a provider-style reason.
        self.reject_next = 0
        self.reject_reason = "the provider rejected the order"
        #: Emit every event twice, as an at-least-once stream does.
        self.duplicate_next = 0
        #: Swallow produced events, so reconciliation has a gap to find.
        self.drop_events = False
        #: Refuse commands entirely, as a disconnected follower does.
        self.offline = False

    # ── accounts ─────────────────────────────────────────────────────────────
    def add_account(self, account: SimulatedAccount) -> Account:
        self._accounts[account.account_id] = account
        return self._account_model(account)

    def account(self, account_id: str) -> SimulatedAccount:
        found = self._accounts.get(account_id)
        if found is None:
            raise LifecycleError(f"the simulator holds no account '{account_id}'")
        return found

    def _key(self, account_id: str) -> AccountKey:
        return AccountKey(
            provider=Provider.SIMULATED,
            environment=Environment.SIMULATION,
            credential_ref=self.credential_ref,
            account_id=account_id,
        )

    def _account_model(self, account: SimulatedAccount) -> Account:
        key = self._key(account.account_id)
        equity = account.balance + sum(
            self._unrealised(account, position) for position in account.positions.values()
        )
        return Account(
            key=key,
            connection_id=self.connection_id,
            display_name=account.display_name,
            account_type=account.account_type,
            venue_id="algoforge-sim",
            capability=self.discover_capabilities(key),
            balance=round(account.balance, 2),
            equity=round(equity, 2),
            as_of=self._now(),
        )

    def _unrealised(self, account: SimulatedAccount, position: Position) -> float:
        mark = self._marks.get(position.symbol)
        if mark is None or position.average_price is None or position.quantity == 0:
            # No mark, no number. A zero here would read as flat.
            return 0.0
        return (mark - position.average_price) * position.quantity * self.multiplier

    def account_uid(self, account_id: str) -> str:
        return self._key(account_id).key_id

    def mark(self, symbol: str, price: float) -> None:
        """The reference price fills and marks are computed from."""
        self._marks[symbol] = price

    # ── the adapter interface ────────────────────────────────────────────────
    def connect(self, credential: CredentialRecord) -> ConnectionState:
        self._state = ConnectionState.LIVE
        return self._state

    def disconnect(self) -> None:
        self._state = ConnectionState.DISCONNECTED

    def authenticate(self, credential: CredentialRecord) -> ConnectionState:
        self._state = ConnectionState.LIVE
        return self._state

    def discover_accounts(self) -> tuple[Account, ...]:
        return tuple(
            self._account_model(self._accounts[key]) for key in sorted(self._accounts)
        )

    def discover_capabilities(self, key: AccountKey) -> AccountCapability:
        account = self._accounts.get(key.account_id)
        return AccountCapability(
            order_types=("market", "limit", "stop"),
            time_in_force=("day",),
            bracket_model=BracketModel.EMULATED,
            trailing_stop_supported=False,
            max_contracts=None if account is None else account.max_contracts,
            can_trade=None if account is None else account.can_trade,
            liquidation_only=False,
            note="AlgoForge simulator: fills are modelled, not calibrated.",
        )

    def place(self, intent: OrderIntent) -> Acknowledgement:
        return self._command(intent, self._place)

    def modify(self, intent: OrderIntent) -> Acknowledgement:
        return self._command(intent, self._modify)

    def cancel(self, intent: OrderIntent) -> Acknowledgement:
        return self._command(intent, self._cancel)

    def flatten(self, intent: OrderIntent) -> Acknowledgement:
        return self._command(intent, self._flatten)

    def poll(self) -> tuple[tuple[str, OrderEvent], ...]:
        events = tuple(self._pending_events)
        self._pending_events.clear()
        return events

    def snapshot(self, account_uid: str) -> ProviderSnapshot:
        account_id = self._resolve(account_uid)
        account = self.account(account_id)
        version = next(self._version)
        self._versions[account_uid] = version
        equity = account.balance + sum(
            self._unrealised(account, position) for position in account.positions.values()
        )
        return ProviderSnapshot(
            account_uid=account_uid,
            taken_at=self._now(),
            version=version,
            positions=tuple(
                position for position in account.positions.values() if not position.flat
            ),
            working_orders=tuple(
                order
                for order in self._orders.values()
                if order.account_uid == account_uid and order.open
            ),
            fill_count=sum(
                1
                for order in self._orders.values()
                if order.account_uid == account_uid and order.filled_quantity
            ),
            balance=round(account.balance, 2),
            equity=round(equity, 2),
            can_trade=account.can_trade,
        )

    def health(self) -> AdapterHealth:
        return AdapterHealth(
            provider=Provider.SIMULATED,
            connection_id=self.connection_id,
            state=self._state,
            heartbeat_age_seconds=0.0,
            consecutive_failures=0,
        )

    # ── command plumbing ─────────────────────────────────────────────────────
    def _command(
        self, intent: OrderIntent, handler: Callable[[OrderIntent], Acknowledgement]
    ) -> Acknowledgement:
        self.commands.append(intent)
        if self.offline:
            return Acknowledgement(
                accepted=False,
                intent_id=intent.intent_id,
                reason="this follower's connection is down; nothing was sent",
                retryable=True,
            )
        if self.fail_next > 0:
            self.fail_next -= 1
            raise ConnectionError("the simulated transport dropped")
        if self.reject_next > 0:
            self.reject_next -= 1
            return Acknowledgement(
                accepted=False,
                intent_id=intent.intent_id,
                reason=self.reject_reason,
                retryable=False,
            )
        existing = self._by_key.get(intent.idempotency_key)
        if existing is not None and intent.kind is CommandKind.PLACE:
            # What a provider with a unique client tag does. Reported as a
            # duplicate rather than as a success, so the caller can tell the two
            # apart in the record.
            return Acknowledgement(
                accepted=True,
                intent_id=intent.intent_id,
                provider_order_id=existing,
                duplicate=True,
                reason="this idempotency key was already accepted",
            )
        return handler(intent)

    def _emit(self, order_id: str, event: OrderEvent) -> None:
        if self.drop_events:
            return
        self._pending_events.append((order_id, event))
        if self.duplicate_next > 0:
            self.duplicate_next -= 1
            self._pending_events.append((order_id, event))

    def _resolve(self, account_uid: str) -> str:
        for account_id in self._accounts:
            if self.account_uid(account_id) == account_uid:
                return account_id
        raise LifecycleError(f"the simulator holds no account with uid '{account_uid}'")

    # ── the commands themselves ──────────────────────────────────────────────
    def _place(self, intent: OrderIntent) -> Acknowledgement:
        if intent.side is None or intent.quantity is None or not intent.symbol:
            return Acknowledgement(
                accepted=False,
                intent_id=intent.intent_id,
                reason="a placement needs a symbol, a side and a quantity",
            )
        account_id = self._resolve(intent.account_uid)
        account = self.account(account_id)
        if not account.can_trade:
            return Acknowledgement(
                accepted=False,
                intent_id=intent.intent_id,
                reason="the provider reports this account cannot trade",
            )

        position = account.positions.get(intent.symbol, Position(
            account_uid=intent.account_uid, symbol=intent.symbol
        ))
        projected = abs(position.quantity + intent.side.sign * intent.quantity)
        if account.max_contracts is not None and projected > account.max_contracts:
            # The rejection a real provider produces, and the reason a copied
            # order most often fails on a smaller follower.
            return Acknowledgement(
                accepted=False,
                intent_id=intent.intent_id,
                reason=(
                    f"maximum position limit reached: {projected} contract(s) would "
                    f"exceed the account's cap of {account.max_contracts}"
                ),
            )

        moment = self._now()
        order_id = new_order_id(
            intent.account_uid, intent.symbol, moment, nonce=intent.idempotency_key
        )
        record = OrderRecord(
            order_id=order_id,
            account_uid=intent.account_uid,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            limit_price=intent.limit_price,
            stop_price=intent.stop_price,
            state=OrderState.SUBMITTED,
            client_tag=intent.idempotency_key,
            provider_order_id=order_id,
            created_at=moment,
            updated_at=moment,
        )
        self._orders[order_id] = record
        self._by_key[intent.idempotency_key] = order_id
        self._emit(
            order_id,
            OrderEvent(
                event_type=OrderEventType.SUBMITTED,
                at=moment,
                provider_event_id=f"{order_id}:submitted",
                sequence=1,
            ),
        )

        price = self._fill_price(intent)
        if price is None:
            self._working(order_id, moment)
            return Acknowledgement(
                accepted=True, intent_id=intent.intent_id, provider_order_id=order_id
            )
        self._fill(order_id, price, moment)
        return Acknowledgement(
            accepted=True, intent_id=intent.intent_id, provider_order_id=order_id
        )

    def _fill_price(self, intent: OrderIntent) -> float | None:
        """What this order fills at, or `None` if it rests.

        A limit that the reference price has not reached does not fill. The
        simulator does not advance time, so it rests as a genuine working order
        rather than being filled later by a clock nobody is running.
        """
        reference = intent.reference_price or self._marks.get(intent.symbol)
        if reference is None or reference <= 0 or intent.side is None:
            return None
        slip = self.slippage_ticks * self.tick_size * intent.side.sign
        price = reference + slip
        if intent.order_type is OrderType.MARKET:
            return round(price, 8)
        if intent.order_type is OrderType.LIMIT:
            if intent.limit_price is None:
                return None
            marketable = (
                price <= intent.limit_price
                if intent.side is Side.BUY
                else price >= intent.limit_price
            )
            if not marketable:
                return None
            return round(
                min(price, intent.limit_price)
                if intent.side is Side.BUY
                else max(price, intent.limit_price),
                8,
            )
        # A stop that the reference has not touched rests.
        if intent.stop_price is None:
            return None
        triggered = (
            reference >= intent.stop_price
            if intent.side is Side.BUY
            else reference <= intent.stop_price
        )
        return round(price, 8) if triggered else None

    def _working(self, order_id: str, moment: datetime) -> None:
        self._orders[order_id] = self._orders[order_id].model_copy(
            update={"state": OrderState.WORKING, "updated_at": moment}
        )
        self._emit(
            order_id,
            OrderEvent(
                event_type=OrderEventType.ACKNOWLEDGED,
                at=moment,
                provider_event_id=f"{order_id}:working",
                sequence=2,
            ),
        )

    def _fill(self, order_id: str, price: float, moment: datetime) -> None:
        order = self._orders[order_id]
        account = self.account(self._resolve(order.account_uid))
        chunk = self.fill_chunk or order.quantity
        sequence = 2
        filled = 0
        while filled < order.quantity:
            step = min(chunk, order.quantity - filled)
            filled += step
            sequence += 1
            position = account.positions.get(
                order.symbol, Position(account_uid=order.account_uid, symbol=order.symbol)
            )
            change = apply_fill(
                position,
                side=order.side,
                quantity=step,
                price=price,
                multiplier=self.multiplier,
                at=moment,
            )
            account.positions[order.symbol] = change.position
            account.balance += change.realised
            account.realised += change.realised
            self._emit(
                order_id,
                OrderEvent(
                    event_type=(
                        OrderEventType.FILL
                        if filled >= order.quantity
                        else OrderEventType.PARTIAL_FILL
                    ),
                    at=moment,
                    provider_event_id=f"{order_id}:fill:{filled}",
                    sequence=sequence,
                    cumulative_quantity=filled,
                    last_price=price,
                ),
            )
        self._orders[order_id] = order.model_copy(
            update={
                "state": OrderState.FILLED,
                "filled_quantity": order.quantity,
                "average_price": price,
                "updated_at": moment,
            }
        )

    def _modify(self, intent: OrderIntent) -> Acknowledgement:
        order = self._orders.get(intent.order_id)
        if order is None:
            return Acknowledgement(
                accepted=False, intent_id=intent.intent_id, reason="no such order"
            )
        if not order.open:
            return Acknowledgement(
                accepted=False,
                intent_id=intent.intent_id,
                reason=f"the order is {order.state.value} and cannot be modified",
            )
        moment = self._now()
        updates: dict[str, Any] = {"updated_at": moment}
        if intent.limit_price is not None:
            updates["limit_price"] = intent.limit_price
        if intent.stop_price is not None:
            updates["stop_price"] = intent.stop_price
        if intent.quantity is not None:
            updates["quantity"] = intent.quantity
        self._orders[order.order_id] = order.model_copy(update=updates)
        self._emit(
            order.order_id,
            OrderEvent(
                event_type=OrderEventType.MODIFIED,
                at=moment,
                provider_event_id=f"{order.order_id}:modified:{moment.isoformat()}",
                limit_price=intent.limit_price,
                stop_price=intent.stop_price,
                quantity=intent.quantity,
            ),
        )
        return Acknowledgement(
            accepted=True, intent_id=intent.intent_id, provider_order_id=order.order_id
        )

    def _cancel(self, intent: OrderIntent) -> Acknowledgement:
        order = self._orders.get(intent.order_id)
        if order is None:
            return Acknowledgement(
                accepted=False, intent_id=intent.intent_id, reason="no such order"
            )
        if not order.open:
            # The cancel/fill race, which the research names as a routine cause
            # of divergence. Reported honestly so the reconciler sees it.
            return Acknowledgement(
                accepted=False,
                intent_id=intent.intent_id,
                reason=(
                    f"the order could not be cancelled: it is already "
                    f"{order.state.value}"
                ),
            )
        moment = self._now()
        self._orders[order.order_id] = order.model_copy(
            update={"state": OrderState.CANCELLED, "updated_at": moment}
        )
        self._emit(
            order.order_id,
            OrderEvent(
                event_type=OrderEventType.CANCELLED,
                at=moment,
                provider_event_id=f"{order.order_id}:cancelled",
            ),
        )
        return Acknowledgement(
            accepted=True, intent_id=intent.intent_id, provider_order_id=order.order_id
        )

    def _flatten(self, intent: OrderIntent) -> Acknowledgement:
        account_id = self._resolve(intent.account_uid)
        account = self.account(account_id)
        symbols = (
            [intent.symbol] if intent.symbol else list(account.positions)
        )
        moment = self._now()
        closed = 0
        for symbol in symbols:
            position = account.positions.get(symbol)
            if position is None or position.flat:
                continue
            price = self._marks.get(symbol)
            if price is None:
                return Acknowledgement(
                    accepted=False,
                    intent_id=intent.intent_id,
                    reason=(
                        f"no reference price for {symbol}, so the simulator cannot "
                        "price a liquidation; nothing was closed"
                    ),
                    retryable=True,
                )
            side = Side.SELL if position.quantity > 0 else Side.BUY
            quantity = abs(position.quantity)
            order_id = stable_id(
                "pdflat", {"account": intent.account_uid, "symbol": symbol,
                           "at": moment.isoformat()}
            )
            record = OrderRecord(
                order_id=order_id,
                account_uid=intent.account_uid,
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type=OrderType.MARKET,
                state=OrderState.SUBMITTED,
                client_tag=intent.idempotency_key,
                provider_order_id=order_id,
                created_at=moment,
                updated_at=moment,
            )
            self._orders[order_id] = record
            self._fill(order_id, price, moment)
            closed += quantity
        return Acknowledgement(
            accepted=True,
            intent_id=intent.intent_id,
            reason=f"{closed} contract(s) closed" if closed else "already flat",
        )
