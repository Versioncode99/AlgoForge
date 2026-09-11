"""The execution fabric: one interface every provider is reached through.

Nothing above this layer knows which provider an account belongs to. A copy
group, an allocation and a reconciler all speak in normalised commands and
normalised events, and the adapter translates. That is the property that makes
"support a new provider" a new file rather than an edit to the copy engine, and
it is the property the research says the commercial products do not fully have:
one of them hard-codes bracket behaviour per provider *pair*.

Four things live here that a naive `place / cancel` interface would omit, and
each of them is in the research as a failure that actually happened.

**A rate budget per credential, not per account.** Twenty accounts under one
login share one quota. A per-account limiter would let a twenty-follower group
send twenty times the permitted rate and collect a penalty that stalls every
one of them.

**A priority queue in front of that budget.** When the budget is exhausted,
what waits matters enormously: a cancel that queues behind four entries is a
position nobody wanted, held for as long as the queue is. `Priority` orders
flattens first, then cancels, then protective children, then entries, then
non-urgent modifications.

**An outbox written before the command is sent.** A process that dies between
sending and recording has, on restart, no way to tell "sent and unrecorded"
from "never sent". Writing the intent first makes the ambiguity one-sided: an
intent with no acknowledgement is reconciled against the provider's own working
orders, and the deterministic idempotency key means re-sending it is safe.

**Health as a first-class reading.** Not a boolean. Heartbeat age, session time
remaining, consecutive failures and the last error, because "why did copying
stop" is the question a user actually asks and "disconnected" is not an answer.

**What this module does not do.** It does not reach a network. Every adapter in
this build is simulated or refuses, `LIVE_EXECUTION_AVAILABLE` in
`forge.execution.lifecycle` is still `False`, and a test asserts that no module
under `forge.propdesk` imports an HTTP client.
"""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import IntEnum, StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from forge.contracts.models import FrozenModel
from forge.propdesk.credentials import CredentialRecord, redact_mapping
from forge.propdesk.identity import (
    Account,
    AccountCapability,
    AccountKey,
    BrokerConnection,
    ConnectionState,
    Provider,
    ProviderDescriptor,
)
from forge.propdesk.orders import (
    OrderEvent,
    OrderRecord,
    OrderType,
    Position,
    Side,
    TimeInForce,
)


class FabricError(Exception):
    """The fabric refused. Nothing was sent."""


class AdapterUnavailable(FabricError):
    """This provider has no implemented connector in this build.

    A distinct type because it is a *different fact* from a failure: nothing was
    attempted, nothing timed out, and no amount of retrying will change it. A
    surface that reports it as a connection error sends the operator to check
    their password for a connector that does not exist.
    """


class RateLimited(FabricError):
    """The per-credential budget is spent. The command was queued, not dropped."""


class Priority(IntEnum):
    """Lower runs first. The order is the whole point; see the module docstring."""

    FLATTEN = 0
    CANCEL = 1
    PROTECTIVE = 2
    ENTRY = 3
    MODIFY = 4


class CommandKind(StrEnum):
    PLACE = "place"
    MODIFY = "modify"
    CANCEL = "cancel"
    FLATTEN = "flatten"


class OrderIntent(FrozenModel):
    """What the desk wants to happen at a provider.

    Distinct from `OrderRecord`, which is what *did* happen. An intent with no
    record is the state the outbox exists to make recoverable.
    """

    intent_id: str = Field(min_length=1, max_length=80)
    kind: CommandKind
    account_uid: str = Field(min_length=1, max_length=80)
    #: Deterministic; see `forge.propdesk.orders.idempotency_key`.
    idempotency_key: str = Field(min_length=1, max_length=64)
    symbol: str = ""
    side: Side | None = None
    quantity: int | None = Field(default=None, gt=0)
    order_type: OrderType = OrderType.MARKET
    time_in_force: TimeInForce = TimeInForce.DAY
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    #: For MODIFY and CANCEL: which order.
    order_id: str = ""
    #: The validated strategy this order comes from, when it comes from one.
    #: Read by the pre-trade gate's validation check, which refuses a strategy
    #: the judge has not passed.
    strategy_id: str = ""
    #: For a copied trade, who attested that the leader account and this one
    #: belong to the same owner. A copied discretionary trade has no strategy
    #: and never will, so this is the evidence that stands in its place — and
    #: `forge.propdesk.desk` refuses an order that carries neither.
    attestation: str = ""
    priority: Priority = Priority.ENTRY
    reference_price: float | None = Field(default=None, gt=0)
    reason: str = ""
    created_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class Acknowledgement(FrozenModel):
    """What an adapter says came back."""

    accepted: bool
    intent_id: str
    provider_order_id: str = ""
    #: Events the adapter produced synchronously. A provider whose events arrive
    #: on a stream returns none here and the fabric reads them from `poll`.
    events: tuple[OrderEvent, ...] = ()
    #: True when the provider recognised the idempotency key and did nothing.
    duplicate: bool = False
    reason: str = ""
    retryable: bool = False


class ProviderSnapshot(FrozenModel):
    """The provider's own view, which is the authoritative one.

    Every field is what the provider said, not what this application believes.
    Reconciliation replaces local state with this rather than merging, because
    merging is how a position that was closed at the broker survives locally
    forever.
    """

    account_uid: str
    taken_at: datetime
    #: Monotonic per account. An event older than the version that produced the
    #: snapshot must not trigger a correction.
    version: int = Field(default=1, ge=1)
    positions: tuple[Position, ...] = ()
    working_orders: tuple[OrderRecord, ...] = ()
    #: Today's fills, by the provider's own definition of today.
    fill_count: int | None = None
    balance: float | None = None
    equity: float | None = None
    can_trade: bool | None = None
    complete: bool = True
    note: str = ""


class AdapterHealth(FrozenModel):
    """Why copying is or is not working, in numbers rather than a colour."""

    provider: Provider
    connection_id: str
    state: ConnectionState
    heartbeat_age_seconds: float | None = None
    session_expires_in_seconds: float | None = None
    consecutive_failures: int = 0
    #: Fraction of the per-credential budget remaining, 0-1.
    rate_budget_remaining: float | None = None
    queued_commands: int = 0
    last_error: str = ""
    last_snapshot_at: datetime | None = None

    @property
    def tradeable(self) -> bool:
        return self.state is ConnectionState.LIVE

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@runtime_checkable
class ExecutionAdapter(Protocol):
    """Everything the desk needs from a provider, and nothing provider-specific.

    Note the method names: `place`, `modify`, `cancel`, `flatten`. They match
    the naming `forge.execution.oms.BrokerAdapter` already uses, so the two
    execution surfaces in this repository read the same way, and they stay out
    of the way of the boundary scan in `tests/execution/test_boundary.py`, which
    enumerates by hand every function in this repository that could place a real
    order.
    """

    descriptor: ProviderDescriptor
    #: Not optional and not defaulted. An adapter author states which kind they
    #: wrote, and the record carries the answer rather than a promise.
    simulated: bool

    def connect(self, credential: CredentialRecord) -> ConnectionState: ...

    def disconnect(self) -> None: ...

    def authenticate(self, credential: CredentialRecord) -> ConnectionState: ...

    def discover_accounts(self) -> tuple[Account, ...]: ...

    def discover_capabilities(self, key: AccountKey) -> AccountCapability: ...

    def place(self, intent: OrderIntent) -> Acknowledgement: ...

    def modify(self, intent: OrderIntent) -> Acknowledgement: ...

    def cancel(self, intent: OrderIntent) -> Acknowledgement: ...

    def flatten(self, intent: OrderIntent) -> Acknowledgement: ...

    def poll(self) -> tuple[tuple[str, OrderEvent], ...]:
        """Normalised events since the last call, as `(order_id, event)`."""
        ...

    def snapshot(self, account_uid: str) -> ProviderSnapshot: ...

    def health(self) -> AdapterHealth: ...


class RateBudget:
    """A token bucket per credential, with a penalty the provider can impose.

    Refills continuously rather than in steps, because a bucket that refills
    once a minute produces a burst at the top of every minute — which is exactly
    the shape that collects a penalty from a provider measuring a rolling
    window.
    """

    def __init__(
        self,
        *,
        per_minute: int | None,
        burst: int | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        #: `None` means the provider publishes no number. That is not "no
        #: limit": a conservative default is applied and named, because the
        #: alternative is discovering the real limit as a penalty.
        self.per_minute = per_minute if per_minute is not None else 60
        self.published = per_minute is not None
        self.capacity = float(burst if burst is not None else max(1, self.per_minute // 4))
        self._tokens = self.capacity
        self._now = now
        self._last = now()
        self._penalty_until: datetime | None = None

    def _refill(self) -> None:
        moment = self._now()
        elapsed = max(0.0, (moment - self._last).total_seconds())
        self._last = moment
        self._tokens = min(self.capacity, self._tokens + elapsed * self.per_minute / 60.0)

    @property
    def remaining(self) -> float:
        self._refill()
        return round(self._tokens / self.capacity, 4) if self.capacity else 0.0

    def penalised(self) -> bool:
        return self._penalty_until is not None and self._now() < self._penalty_until

    def penalise(self, seconds: float, reason: str = "") -> None:
        """Record a penalty the provider imposed.

        Observed rather than guessed: two of the three researched providers
        return an explicit wait, and honouring it is cheaper than discovering it
        again.
        """
        self._penalty_until = self._now() + timedelta(seconds=max(0.0, seconds))
        self.last_penalty_reason = reason

    def take(self, count: int = 1) -> bool:
        if self.penalised():
            return False
        self._refill()
        if self._tokens < count:
            return False
        self._tokens -= count
        return True


@dataclass(order=True)
class _Queued:
    priority: int
    sequence: int
    intent: OrderIntent = field(compare=False)


class Outbox:
    """Intents written before they are sent, and cleared when acknowledged.

    A crash leaves an intent here with no acknowledgement. On restart that is
    reconciled against the provider's working orders rather than re-sent
    blindly — and because the idempotency key is deterministic, re-sending is
    safe when reconciliation says it never arrived.
    """

    def __init__(self) -> None:
        self._pending: dict[str, OrderIntent] = {}
        self._sent: dict[str, datetime] = {}

    def record(self, intent: OrderIntent) -> None:
        self._pending[intent.intent_id] = intent

    def acknowledge(self, intent_id: str, at: datetime) -> None:
        self._pending.pop(intent_id, None)
        self._sent[intent_id] = at
        # Bounded: the sent set is only used to answer "did we already do this",
        # and reconciliation answers that from the provider beyond a short
        # horizon.
        if len(self._sent) > 5000:
            for key in sorted(self._sent, key=lambda k: self._sent[k])[:1000]:
                del self._sent[key]

    def pending(self) -> tuple[OrderIntent, ...]:
        return tuple(self._pending[key] for key in sorted(self._pending))

    def was_sent(self, intent_id: str) -> bool:
        return intent_id in self._sent

    def __len__(self) -> int:
        return len(self._pending)


class ConnectionRuntime:
    """One connection's live state: adapter, budget, queue, outbox, health."""

    def __init__(
        self,
        connection: BrokerConnection,
        adapter: ExecutionAdapter,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.connection = connection
        self.adapter = adapter
        self.budget = RateBudget(
            per_minute=adapter.descriptor.rate_limit_per_minute, now=now
        )
        self.outbox = Outbox()
        self._queue: list[_Queued] = []
        #: Intents to put back after the current drain finishes. Re-queueing
        #: inside the drain would retry a failed command immediately, in the
        #: same pass, against a provider that has just failed or penalised us —
        #: which is the reconnect-storm shape the rate budget exists to avoid.
        self._retry: list[OrderIntent] = []
        self._sequence = itertools.count()
        self._now = now
        self.consecutive_failures = 0
        self.last_error = ""
        self.last_snapshot_at: datetime | None = None
        self.reconnect_attempts = 0

    # ── queueing ─────────────────────────────────────────────────────────────
    def enqueue(self, intent: OrderIntent) -> None:
        heapq.heappush(
            self._queue, _Queued(int(intent.priority), next(self._sequence), intent)
        )

    def queued(self) -> int:
        return len(self._queue)

    def peek(self) -> OrderIntent | None:
        return self._queue[0].intent if self._queue else None

    def _pop(self) -> OrderIntent:
        return heapq.heappop(self._queue).intent

    # ── sending ──────────────────────────────────────────────────────────────
    def drain(self, limit: int = 50) -> tuple[tuple[OrderIntent, Acknowledgement], ...]:
        """Send as much of the queue as the budget and the state permit.

        Returns pairs so a caller can attribute an acknowledgement to the intent
        that produced it without matching on ids it would have to trust.
        """
        results: list[tuple[OrderIntent, Acknowledgement]] = []
        for _ in range(max(0, limit)):
            intent = self.peek()
            if intent is None:
                break
            if not self._may_send(intent):
                break
            if not self.budget.take():
                break
            self._pop()
            results.append((intent, self._dispatch(intent)))
        for intent in self._retry:
            self.enqueue(intent)
        self._retry.clear()
        return tuple(results)

    def _may_send(self, intent: OrderIntent) -> bool:
        """Whether this connection's state permits this command right now.

        A degraded connection may still cancel and flatten. Refusing those would
        mean that the moment things go wrong is the moment the operator loses
        the ability to get flat, which is precisely backwards.
        """
        state = self.connection.state
        if state is ConnectionState.LIVE:
            return True
        if state is ConnectionState.DEGRADED:
            return intent.priority <= Priority.CANCEL
        return False

    def _dispatch(self, intent: OrderIntent) -> Acknowledgement:
        self.outbox.record(intent)
        try:
            handler = {
                CommandKind.PLACE: self.adapter.place,
                CommandKind.MODIFY: self.adapter.modify,
                CommandKind.CANCEL: self.adapter.cancel,
                CommandKind.FLATTEN: self.adapter.flatten,
            }[intent.kind]
            ack = handler(intent)
        except AdapterUnavailable as exc:
            self.consecutive_failures += 1
            self.last_error = str(exc)
            return Acknowledgement(
                accepted=False, intent_id=intent.intent_id, reason=str(exc), retryable=False
            )
        except Exception as exc:  # an adapter fault is the adapter's, not a crash
            self.consecutive_failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            # A transport that dropped is the case a retry exists for, so the
            # intent goes back on the queue with the same idempotency key. It is
            # requeued here rather than left to the caller because a command
            # that raised has no acknowledgement to inspect, and silently losing
            # it is how a follower misses a trade with nothing in the record.
            self._retry.append(intent)
            return Acknowledgement(
                accepted=False,
                intent_id=intent.intent_id,
                reason=self.last_error,
                retryable=True,
            )
        if ack.accepted:
            self.consecutive_failures = 0
            self.outbox.acknowledge(intent.intent_id, self._now())
        else:
            self.consecutive_failures += 1
            self.last_error = ack.reason
            if ack.retryable:
                # Same intent, same idempotency key. Reusing the key is what
                # makes a retry after a rate-limit penalty safe rather than a
                # second position.
                self._retry.append(intent)
            else:
                self.outbox.acknowledge(intent.intent_id, self._now())
        return ack

    # ── health ───────────────────────────────────────────────────────────────
    def health(self) -> AdapterHealth:
        moment = self._now()
        heartbeat = self.connection.last_heartbeat_at
        expires = self.connection.session_expires_at
        return AdapterHealth(
            provider=self.connection.provider,
            connection_id=self.connection.connection_id,
            state=self.connection.state,
            heartbeat_age_seconds=(
                None if heartbeat is None else round((moment - heartbeat).total_seconds(), 3)
            ),
            session_expires_in_seconds=(
                None if expires is None else round((expires - moment).total_seconds(), 3)
            ),
            consecutive_failures=self.consecutive_failures,
            rate_budget_remaining=self.budget.remaining,
            queued_commands=self.queued(),
            last_error=self.last_error,
            last_snapshot_at=self.last_snapshot_at,
        )

    def backoff_seconds(self) -> float:
        """Exponential, capped, and deliberately not jittered here.

        The cap matters more than the curve: two of the researched providers
        charge a synchronisation request per reconnect out of a shared quota, so
        a tight reconnect loop does not merely fail — it burns the budget the
        eventual successful reconnect needs.
        """
        return float(min(60, 2 ** min(self.reconnect_attempts, 6)))


class ExecutionFabric:
    """The registry of connections, and the one road to a provider.

    Every command in the Prop Desk passes through `dispatch`, which is why the
    rate budget, the priority queue, the outbox and the health reading cannot be
    bypassed by a component that forgot about them.
    """

    def __init__(self, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._runtimes: dict[str, ConnectionRuntime] = {}
        self._accounts: dict[str, str] = {}
        self._now = now

    # ── registration ─────────────────────────────────────────────────────────
    def register(self, connection: BrokerConnection, adapter: ExecutionAdapter) -> None:
        if adapter.descriptor.provider is not connection.provider:
            raise FabricError(
                f"a {adapter.descriptor.provider.value} adapter cannot serve a "
                f"{connection.provider.value} connection"
            )
        self._runtimes[connection.connection_id] = ConnectionRuntime(
            connection, adapter, now=self._now
        )

    def runtime(self, connection_id: str) -> ConnectionRuntime:
        found = self._runtimes.get(connection_id)
        if found is None:
            raise FabricError(f"no connection '{connection_id}' is registered")
        return found

    def connections(self) -> tuple[BrokerConnection, ...]:
        return tuple(r.connection for r in self._runtimes.values())

    def bind_account(self, account_uid: str, connection_id: str) -> None:
        self.runtime(connection_id)
        self._accounts[account_uid] = connection_id

    def runtime_for_account(self, account_uid: str) -> ConnectionRuntime:
        connection_id = self._accounts.get(account_uid)
        if connection_id is None:
            raise FabricError(
                f"account '{account_uid}' is not bound to a connection, so there is "
                "nowhere to send this"
            )
        return self.runtime(connection_id)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def connect(self, connection_id: str, credential: CredentialRecord) -> BrokerConnection:
        runtime = self.runtime(connection_id)
        self._set_state(runtime, ConnectionState.CONNECTING)
        try:
            state = runtime.adapter.connect(credential)
        except AdapterUnavailable as exc:
            runtime.last_error = str(exc)
            return self._set_state(runtime, ConnectionState.FAILED, error=str(exc))
        except Exception as exc:
            runtime.reconnect_attempts += 1
            error = f"{type(exc).__name__}: {exc}"
            return self._set_state(runtime, ConnectionState.FAILED, error=error)
        runtime.reconnect_attempts = 0
        moment = self._now()
        runtime.connection = runtime.connection.model_copy(
            update={
                "state": state,
                "connected_at": moment,
                "last_heartbeat_at": moment,
                "last_error": "",
                "updated_at": moment,
            }
        )
        return runtime.connection

    def disconnect(self, connection_id: str, *, reason: str = "") -> BrokerConnection:
        runtime = self.runtime(connection_id)
        runtime.adapter.disconnect()
        return self._set_state(runtime, ConnectionState.DISCONNECTED, error=reason)

    def mark(
        self, connection_id: str, state: ConnectionState, *, error: str = ""
    ) -> BrokerConnection:
        return self._set_state(self.runtime(connection_id), state, error=error)

    def heartbeat(self, connection_id: str) -> BrokerConnection:
        runtime = self.runtime(connection_id)
        runtime.connection = runtime.connection.model_copy(
            update={"last_heartbeat_at": self._now(), "updated_at": self._now()}
        )
        return runtime.connection

    def _set_state(
        self, runtime: ConnectionRuntime, state: ConnectionState, *, error: str = ""
    ) -> BrokerConnection:
        runtime.connection = runtime.connection.model_copy(
            update={"state": state, "last_error": error, "updated_at": self._now()}
        )
        if error:
            runtime.last_error = error
        return runtime.connection

    # ── discovery ────────────────────────────────────────────────────────────
    def discover_accounts(self, connection_id: str) -> tuple[Account, ...]:
        runtime = self.runtime(connection_id)
        accounts = runtime.adapter.discover_accounts()
        for account in accounts:
            self._accounts[account.account_uid] = connection_id
        return accounts

    def discover_capabilities(self, connection_id: str, key: AccountKey) -> AccountCapability:
        return self.runtime(connection_id).adapter.discover_capabilities(key)

    # ── commands ─────────────────────────────────────────────────────────────
    def dispatch(self, intent: OrderIntent) -> None:
        """Queue one command. Nothing is sent until `flush`.

        Separating them is what makes the priority ordering meaningful: a batch
        of eight follower entries and one flatten, queued together, sends the
        flatten first.
        """
        self.runtime_for_account(intent.account_uid).enqueue(intent)

    def flush(self, limit: int = 50) -> tuple[tuple[OrderIntent, Acknowledgement], ...]:
        results: list[tuple[OrderIntent, Acknowledgement]] = []
        for runtime in self._runtimes.values():
            results.extend(runtime.drain(limit))
        return tuple(results)

    def poll(self) -> tuple[tuple[str, OrderEvent], ...]:
        events: list[tuple[str, OrderEvent]] = []
        for runtime in self._runtimes.values():
            if runtime.connection.state in {
                ConnectionState.DISCONNECTED,
                ConnectionState.FAILED,
            }:
                continue
            events.extend(runtime.adapter.poll())
        return tuple(events)

    def snapshot(self, account_uid: str) -> ProviderSnapshot:
        runtime = self.runtime_for_account(account_uid)
        snapshot = runtime.adapter.snapshot(account_uid)
        runtime.last_snapshot_at = snapshot.taken_at
        return snapshot

    # ── health ───────────────────────────────────────────────────────────────
    def health(self) -> tuple[AdapterHealth, ...]:
        return tuple(runtime.health() for runtime in self._runtimes.values())

    def unhealthy(self) -> tuple[AdapterHealth, ...]:
        return tuple(h for h in self.health() if not h.tradeable)

    def describe(self) -> dict[str, Any]:
        """A safe summary. Redacted, because a connection names a credential."""
        return redact_mapping(
            {
                "connections": [r.connection.as_dict() for r in self._runtimes.values()],
                "health": [h.as_dict() for h in self.health()],
                "bound_accounts": len(self._accounts),
                "pending_intents": sum(len(r.outbox) for r in self._runtimes.values()),
            }
        )
