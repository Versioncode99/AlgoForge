"""Broker-authoritative reconciliation: the provider is right, and we are a cache.

The single most important sentence in the forensic research is that correctness
in a copy system comes from continuous reconciliation back to broker state, not
from perfect replication. Every serious failure mode it documents — divergence
after a partial fill, an order that filled before its cancel arrived, a follower
that was offline for four minutes, a fill the exchange later busted — is a case
where the local model and the provider disagree, and where the provider is
right.

So this engine has one rule and several jobs.

**The rule.** A `ProviderSnapshot` replaces local state. It is not merged with
it. Merging is how a position closed at the broker survives locally forever, and
how a working order the provider has never heard of stays on the screen.

**The jobs**, which the research names as five distinct reconciliation moments
with different tolerances:

* `STARTUP` — nothing is trusted; rebuild, and do not correct automatically,
  because the difference between "we were down for an hour" and "something is
  wrong" is not visible from inside the process.
* `RECONNECT` — same, scoped to the affected connection. Missed leader events
  are *not* replayed: the price has moved, and a copier that replays an hour of
  entries on reconnect is a copier that opens a position nobody wanted.
* `EVENT` — after each fill. The tight loop, with a tolerance window before any
  correction, because a follower that is one second behind is not diverged.
* `PERIODIC` — silent drift: missed events, busted trades, a manual trade on a
  follower.
* `END_OF_SESSION` — accounting: fills, fees and balances, which is what the
  daily-loss baseline is computed from.

**The staleness guard.** An event older than the snapshot that produced the
current state must not drive a correction. Without it the sequence is: snapshot
says flat, a late fill event arrives, the engine "corrects" by opening a
position to match an event the provider has already superseded. One commercial
product implements exactly this guard and logs when it fires; it is not an
optimisation.

**Corrections are proposals.** `plan_corrections` returns intents. It does not
send them. They go through the same prop-rule and pre-trade gates as any other
order, because a corrective market order is still an order, and an engine that
could route around the gate to fix its own bookkeeping would be the largest hole
in the desk.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import Field

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel
from forge.propdesk.fabric import CommandKind, OrderIntent, Priority, ProviderSnapshot
from forge.propdesk.orders import (
    OrderRecord,
    OrderState,
    OrderType,
    Position,
    Side,
    idempotency_key,
)


class Trigger(StrEnum):
    STARTUP = "startup"
    RECONNECT = "reconnect"
    EVENT = "event"
    PERIODIC = "periodic"
    END_OF_SESSION = "end_of_session"


class DivergenceKind(StrEnum):
    """What kind of disagreement this is. The taxonomy from the research."""

    #: A position the provider holds that this application did not know about.
    #: A manual trade on a follower, or a fill whose event never arrived.
    UNTRACKED_POSITION = "untracked_position"
    #: A position this application believes in that the provider does not hold.
    PHANTOM_POSITION = "phantom_position"
    #: Both hold a position; the sizes differ.
    POSITION_SIZE = "position_size"
    #: A working order at the provider with no live local counterpart.
    ORPHAN_ORDER = "orphan_order"
    #: A local working order the provider has never heard of, or has finished.
    MISSING_ORDER = "missing_order"
    ORDER_STATE = "order_state"
    BALANCE = "balance"
    #: The provider says this account cannot trade.
    ACCOUNT_LOCKED = "account_locked"
    #: The snapshot itself was incomplete, so absence proves nothing.
    INCOMPLETE_SNAPSHOT = "incomplete_snapshot"


class Severity(StrEnum):
    #: Worth recording; nothing to do.
    INFORMATION = "information"
    #: A correction is available and safe to propose.
    CORRECTABLE = "correctable"
    #: Something is wrong that a trade will not fix. Copying should stop.
    QUARANTINE = "quarantine"


class Divergence(FrozenModel):
    """One disagreement between this application and a provider."""

    divergence_id: str
    account_uid: str
    kind: DivergenceKind
    severity: Severity
    symbol: str = ""
    order_id: str = ""
    local: float | str | None = None
    remote: float | str | None = None
    detail: str
    observed_at: datetime
    snapshot_version: int = 0

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ReconciliationState(StrEnum):
    IN_SYNC = "in_sync"
    DIVERGED = "diverged"
    CORRECTING = "correcting"
    #: Copying disabled for this account pending a person. The "disable
    #: replication on breach" behaviour, made explicit.
    QUARANTINED = "quarantined"
    #: Nothing has been reconciled yet, which is not the same as in sync.
    UNKNOWN = "unknown"


class ReconciliationReport(FrozenModel):
    """One reconciliation pass over one account."""

    account_uid: str
    trigger: Trigger
    at: datetime
    snapshot_version: int
    state: ReconciliationState
    divergences: tuple[Divergence, ...] = ()
    #: Positions and orders adopted from the provider, replacing local belief.
    positions_adopted: int = 0
    orders_adopted: int = 0
    #: Events dropped for being older than the snapshot.
    stale_events_dropped: int = 0
    #: Present only when the caller asked for corrections *and* the trigger and
    #: policy permit them.
    corrections: tuple[OrderIntent, ...] = ()
    limitations: tuple[str, ...] = ()

    @property
    def in_sync(self) -> bool:
        return self.state is ReconciliationState.IN_SYNC

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ReconciliationPolicy(FrozenModel):
    """What the operator has decided about correcting divergence.

    Every one of these is a policy question the research says both commercial
    products expose and then default differently from their own documentation.
    Making them explicit here is the point; there is no hidden default.
    """

    #: How long a divergence may persist before a correction is proposed. Zero
    #: means correct immediately, which the research says produces corrections
    #: for followers that were merely a second behind.
    tolerance_seconds: float = Field(default=3.0, ge=0)
    #: Whether the engine may propose trades to close a divergence at all.
    correct: bool = True
    #: Whether a position the provider holds that the desk did not open may be
    #: closed. Off by default: it might be the operator trading by hand.
    close_untracked_positions: bool = False
    #: Cancel provider-side working orders with no local counterpart.
    cancel_orphan_orders: bool = True
    #: Stop copying on divergence rather than correcting it.
    quarantine_on_divergence: bool = False
    #: Never correct after a startup or reconnect pass. The price has moved and
    #: this application does not know what happened while it was away.
    correct_on_recovery: bool = False
    #: How far a balance may differ before it is reported.
    balance_tolerance: float = Field(default=0.01, ge=0)

    def corrects_on(self, trigger: Trigger) -> bool:
        if not self.correct or self.quarantine_on_divergence:
            return False
        if trigger in {Trigger.STARTUP, Trigger.RECONNECT}:
            return self.correct_on_recovery
        return True


class LocalView(FrozenModel):
    """What this application believes about one account, before reconciling."""

    account_uid: str
    positions: tuple[Position, ...] = ()
    working_orders: tuple[OrderRecord, ...] = ()
    balance: float | None = None
    #: The snapshot version this belief was last built from.
    snapshot_version: int = 0

    def position(self, symbol: str) -> Position | None:
        return next((p for p in self.positions if p.symbol == symbol), None)


class ReconciliationEngine:
    """Compares belief with the provider, and says what to do about it."""

    def __init__(
        self,
        policy: ReconciliationPolicy | None = None,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.policy = policy or ReconciliationPolicy()
        self._now = now
        #: When each (account, symbol) first diverged, so the tolerance window
        #: measures persistence rather than restarting every pass.
        self._diverged_since: dict[tuple[str, str], datetime] = {}
        self._state: dict[str, ReconciliationState] = {}
        self._versions: dict[str, int] = {}

    # ── the staleness guard ──────────────────────────────────────────────────
    def is_stale(self, account_uid: str, event_version: int) -> bool:
        """Whether an event predates the snapshot the current state came from.

        A caller that skips this and applies the event anyway will produce a
        position the provider has already contradicted, and the next pass will
        dutifully "correct" back — a loop that trades on every cycle.
        """
        return event_version < self._versions.get(account_uid, 0)

    def state(self, account_uid: str) -> ReconciliationState:
        return self._state.get(account_uid, ReconciliationState.UNKNOWN)

    def release(self, account_uid: str) -> None:
        """Lift a quarantine. Deliberately a separate, explicit call."""
        self._state[account_uid] = ReconciliationState.UNKNOWN
        for key in [k for k in self._diverged_since if k[0] == account_uid]:
            del self._diverged_since[key]

    # ── the pass ─────────────────────────────────────────────────────────────
    def reconcile(
        self,
        *,
        local: LocalView,
        snapshot: ProviderSnapshot,
        trigger: Trigger,
        target_positions: dict[str, int] | None = None,
        stale_events_dropped: int = 0,
    ) -> ReconciliationReport:
        """One pass.

        `target_positions` is what the desk *intends* the account to hold — the
        copy engine's net target, or an allocation's. When it is absent the
        engine reconciles the provider against local belief only, which catches
        bookkeeping errors but not "the follower should be long two and is
        flat". Both questions matter and they are different questions.
        """
        moment = self._now()
        divergences: list[Divergence] = []
        limitations: list[str] = []

        if not snapshot.complete:
            # An incomplete snapshot cannot support an absence claim. Reporting
            # a phantom position from a partial listing is how a reconciler
            # flattens something real.
            divergences.append(
                self._divergence(
                    snapshot,
                    DivergenceKind.INCOMPLETE_SNAPSHOT,
                    Severity.INFORMATION,
                    detail=(
                        "the provider's snapshot is incomplete, so nothing absent from "
                        f"it is treated as closed. {snapshot.note}".strip()
                    ),
                    at=moment,
                )
            )
            limitations.append(
                "absence was not treated as evidence: the snapshot was incomplete"
            )

        remote_positions = {
            position.symbol: position for position in snapshot.positions if not position.flat
        }
        local_positions = {p.symbol: p for p in local.positions if not p.flat}

        divergences += self._position_divergences(
            snapshot=snapshot,
            remote=remote_positions,
            local=local_positions,
            target=target_positions,
            complete=snapshot.complete,
            at=moment,
        )
        divergences += self._order_divergences(
            snapshot=snapshot, local=local, complete=snapshot.complete, at=moment
        )
        divergences += self._account_divergences(snapshot=snapshot, local=local, at=moment)

        self._versions[snapshot.account_uid] = max(
            snapshot.version, self._versions.get(snapshot.account_uid, 0)
        )

        state = self._settle(snapshot.account_uid, divergences)
        corrections: tuple[OrderIntent, ...] = ()
        if state is ReconciliationState.DIVERGED and self.policy.corrects_on(trigger):
            corrections = self.plan_corrections(
                snapshot=snapshot,
                divergences=tuple(divergences),
                target_positions=target_positions,
                at=moment,
            )
            if corrections:
                state = ReconciliationState.CORRECTING
                self._state[snapshot.account_uid] = state
        elif state is ReconciliationState.DIVERGED and not self.policy.corrects_on(trigger):
            limitations.append(
                f"no correction was proposed: the policy does not correct on a "
                f"{trigger.value} pass"
            )

        return ReconciliationReport(
            account_uid=snapshot.account_uid,
            trigger=trigger,
            at=moment,
            snapshot_version=snapshot.version,
            state=state,
            divergences=tuple(divergences),
            positions_adopted=len(remote_positions),
            orders_adopted=len(snapshot.working_orders),
            stale_events_dropped=stale_events_dropped,
            corrections=corrections,
            limitations=tuple(limitations),
        )

    def adopt(self, snapshot: ProviderSnapshot) -> LocalView:
        """The provider's view, taken as the new local view.

        A replacement, not a merge — see the module docstring. Returned rather
        than mutated so a caller can see exactly what it is adopting.
        """
        return LocalView(
            account_uid=snapshot.account_uid,
            positions=tuple(
                position.model_copy(update={"snapshot_version": snapshot.version})
                for position in snapshot.positions
                if not position.flat
            ),
            working_orders=snapshot.working_orders,
            balance=snapshot.balance,
            snapshot_version=snapshot.version,
        )

    # ── the comparisons ──────────────────────────────────────────────────────
    def _position_divergences(
        self,
        *,
        snapshot: ProviderSnapshot,
        remote: dict[str, Position],
        local: dict[str, Position],
        target: dict[str, int] | None,
        complete: bool,
        at: datetime,
    ) -> list[Divergence]:
        found: list[Divergence] = []
        symbols = set(remote) | set(local) | set(target or {})

        for symbol in sorted(symbols):
            remote_qty = remote[symbol].quantity if symbol in remote else 0
            local_qty = local[symbol].quantity if symbol in local else 0
            wanted = None if target is None else target.get(symbol, 0)

            if remote_qty != local_qty:
                if local_qty == 0:
                    kind = DivergenceKind.UNTRACKED_POSITION
                    detail = (
                        f"the provider holds {remote_qty:+d} {symbol} that this desk did "
                        "not open. It may be a manual trade on this account, or a fill "
                        "whose event never arrived."
                    )
                elif remote_qty == 0:
                    if not complete:
                        continue
                    kind = DivergenceKind.PHANTOM_POSITION
                    detail = (
                        f"this desk believes {local_qty:+d} {symbol}; the provider holds "
                        "none. The provider is authoritative."
                    )
                else:
                    kind = DivergenceKind.POSITION_SIZE
                    detail = (
                        f"the provider holds {remote_qty:+d} {symbol}; this desk believes "
                        f"{local_qty:+d}"
                    )
                found.append(
                    self._divergence(
                        snapshot, kind, Severity.CORRECTABLE, symbol=symbol,
                        local=local_qty, remote=remote_qty, detail=detail, at=at,
                    )
                )

            # The other question: not "do the books agree" but "is the account
            # holding what the desk intends". A follower whose order was
            # rejected has consistent books and the wrong position.
            if wanted is not None and remote_qty != wanted:
                found.append(
                    self._divergence(
                        snapshot,
                        DivergenceKind.POSITION_SIZE,
                        Severity.CORRECTABLE,
                        symbol=symbol,
                        local=wanted,
                        remote=remote_qty,
                        detail=(
                            f"the desk intends {wanted:+d} {symbol} on this account; the "
                            f"provider holds {remote_qty:+d}"
                        ),
                        at=at,
                    )
                )
        return found

    def _order_divergences(
        self, *, snapshot: ProviderSnapshot, local: LocalView, complete: bool, at: datetime
    ) -> list[Divergence]:
        found: list[Divergence] = []
        remote_orders = {order.order_id: order for order in snapshot.working_orders}
        local_orders = {order.order_id: order for order in local.working_orders if order.open}

        for order_id in sorted(set(remote_orders) - set(local_orders)):
            order = remote_orders[order_id]
            found.append(
                self._divergence(
                    snapshot,
                    DivergenceKind.ORPHAN_ORDER,
                    Severity.CORRECTABLE,
                    symbol=order.symbol,
                    order_id=order_id,
                    remote=order.state.value,
                    detail=(
                        f"a working {order.side.value} {order.quantity} {order.symbol} at "
                        "the provider has no live counterpart here"
                    ),
                    at=at,
                )
            )

        for order_id in sorted(set(local_orders) - set(remote_orders)):
            if not complete:
                continue
            order = local_orders[order_id]
            found.append(
                self._divergence(
                    snapshot,
                    DivergenceKind.MISSING_ORDER,
                    Severity.INFORMATION,
                    symbol=order.symbol,
                    order_id=order_id,
                    local=order.state.value,
                    detail=(
                        "this desk holds this as working; the provider does not list it. "
                        "It filled, was cancelled, or was never accepted, and the local "
                        "record is superseded."
                    ),
                    at=at,
                )
            )

        for order_id in sorted(set(local_orders) & set(remote_orders)):
            local_order, remote_order = local_orders[order_id], remote_orders[order_id]
            if local_order.filled_quantity != remote_order.filled_quantity:
                found.append(
                    self._divergence(
                        snapshot,
                        DivergenceKind.ORDER_STATE,
                        Severity.INFORMATION,
                        symbol=local_order.symbol,
                        order_id=order_id,
                        local=local_order.filled_quantity,
                        remote=remote_order.filled_quantity,
                        detail=(
                            "the filled quantity disagrees; a fill event was missed or "
                            "arrived out of order"
                        ),
                        at=at,
                    )
                )
        return found

    def _account_divergences(
        self, *, snapshot: ProviderSnapshot, local: LocalView, at: datetime
    ) -> list[Divergence]:
        found: list[Divergence] = []
        if snapshot.can_trade is False:
            found.append(
                self._divergence(
                    snapshot,
                    DivergenceKind.ACCOUNT_LOCKED,
                    Severity.QUARANTINE,
                    remote="cannot trade",
                    detail=(
                        "the provider reports this account cannot trade. Copying and "
                        "allocation to it stop until that changes; a correction would "
                        "be rejected."
                    ),
                    at=at,
                )
            )
        if (
            snapshot.balance is not None
            and local.balance is not None
            and abs(snapshot.balance - local.balance) > self.policy.balance_tolerance
        ):
            found.append(
                self._divergence(
                    snapshot,
                    DivergenceKind.BALANCE,
                    Severity.INFORMATION,
                    local=round(local.balance, 2),
                    remote=round(snapshot.balance, 2),
                    detail=(
                        "the balance disagrees with the provider's; fees or a fill this "
                        "desk did not see. The provider's figure is adopted."
                    ),
                    at=at,
                )
            )
        return found

    def _divergence(
        self,
        snapshot: ProviderSnapshot,
        kind: DivergenceKind,
        severity: Severity,
        *,
        detail: str,
        at: datetime,
        symbol: str = "",
        order_id: str = "",
        local: float | str | None = None,
        remote: float | str | None = None,
    ) -> Divergence:
        return Divergence(
            divergence_id=stable_id(
                "diverge",
                {
                    "account": snapshot.account_uid,
                    "kind": kind.value,
                    "symbol": symbol,
                    "order": order_id,
                    "version": snapshot.version,
                },
            ),
            account_uid=snapshot.account_uid,
            kind=kind,
            severity=severity,
            symbol=symbol,
            order_id=order_id,
            local=local,
            remote=remote,
            detail=detail,
            observed_at=at,
            snapshot_version=snapshot.version,
        )

    # ── settlement ───────────────────────────────────────────────────────────
    def _settle(
        self, account_uid: str, divergences: list[Divergence]
    ) -> ReconciliationState:
        if self._state.get(account_uid) is ReconciliationState.QUARANTINED:
            # A quarantine is lifted by a person, not by a clean pass. The
            # condition that caused it may simply not be visible this cycle.
            return ReconciliationState.QUARANTINED
        if any(d.severity is Severity.QUARANTINE for d in divergences):
            self._state[account_uid] = ReconciliationState.QUARANTINED
            return ReconciliationState.QUARANTINED
        actionable = [d for d in divergences if d.severity is Severity.CORRECTABLE]
        if not actionable:
            self._state[account_uid] = ReconciliationState.IN_SYNC
            for key in [k for k in self._diverged_since if k[0] == account_uid]:
                del self._diverged_since[key]
            return ReconciliationState.IN_SYNC
        if self.policy.quarantine_on_divergence:
            self._state[account_uid] = ReconciliationState.QUARANTINED
            return ReconciliationState.QUARANTINED
        self._state[account_uid] = ReconciliationState.DIVERGED
        return ReconciliationState.DIVERGED

    def persisted(self, account_uid: str, symbol: str, at: datetime) -> bool:
        """Whether this divergence has outlasted the tolerance window."""
        key = (account_uid, symbol)
        since = self._diverged_since.setdefault(key, at)
        return (at - since) >= timedelta(seconds=self.policy.tolerance_seconds)

    # ── corrections ──────────────────────────────────────────────────────────
    def plan_corrections(
        self,
        *,
        snapshot: ProviderSnapshot,
        divergences: Iterable[Divergence],
        target_positions: dict[str, int] | None,
        at: datetime,
    ) -> tuple[OrderIntent, ...]:
        """Intents that would close the divergence. Nothing is sent.

        These carry `Priority.FLATTEN` when they reduce exposure and
        `Priority.ENTRY` when they add it — so that under a rate-limit penalty a
        correction that gets an account *out* of something goes first.
        """
        intents: list[OrderIntent] = []
        handled: set[str] = set()

        for divergence in divergences:
            if divergence.severity is not Severity.CORRECTABLE:
                continue
            if divergence.kind is DivergenceKind.ORPHAN_ORDER:
                if not self.policy.cancel_orphan_orders:
                    continue
                intents.append(
                    self._intent(
                        snapshot,
                        kind=CommandKind.CANCEL,
                        order_id=divergence.order_id,
                        symbol=divergence.symbol,
                        priority=Priority.CANCEL,
                        reason=divergence.detail,
                        at=at,
                    )
                )
                continue

            if divergence.kind is DivergenceKind.UNTRACKED_POSITION and not (
                self.policy.close_untracked_positions
            ):
                continue
            if not divergence.symbol or divergence.symbol in handled:
                continue

            remote = int(divergence.remote) if isinstance(divergence.remote, int | float) else 0
            wanted = (
                0
                if target_positions is None
                else int(target_positions.get(divergence.symbol, 0))
            )
            if divergence.kind is DivergenceKind.UNTRACKED_POSITION and target_positions is None:
                wanted = 0
            delta = wanted - remote
            if delta == 0:
                continue
            handled.add(divergence.symbol)

            if not self.persisted(snapshot.account_uid, divergence.symbol, at):
                # Inside the tolerance window. A follower that is two seconds
                # behind is not diverged, and correcting it is a round trip of
                # cost for nothing.
                continue

            reducing = abs(wanted) < abs(remote)
            intents.append(
                self._intent(
                    snapshot,
                    kind=CommandKind.PLACE,
                    symbol=divergence.symbol,
                    side=Side.BUY if delta > 0 else Side.SELL,
                    quantity=abs(delta),
                    priority=Priority.FLATTEN if reducing else Priority.ENTRY,
                    reason=(
                        f"reconciliation: bring {divergence.symbol} from {remote:+d} to "
                        f"{wanted:+d}"
                    ),
                    at=at,
                )
            )
        return tuple(intents)

    def _intent(
        self,
        snapshot: ProviderSnapshot,
        *,
        kind: CommandKind,
        at: datetime,
        symbol: str = "",
        side: Side | None = None,
        quantity: int | None = None,
        order_id: str = "",
        priority: Priority = Priority.ENTRY,
        reason: str = "",
    ) -> OrderIntent:
        key = idempotency_key(
            group_id="reconcile",
            source_order_id=order_id or symbol,
            source_marker=f"v{snapshot.version}",
            account_uid=snapshot.account_uid,
            action=kind.value,
        )
        return OrderIntent(
            intent_id=stable_id("intent", {"key": key, "at": at.isoformat()}),
            kind=kind,
            account_uid=snapshot.account_uid,
            idempotency_key=key,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=OrderType.MARKET,
            order_id=order_id,
            priority=priority,
            reason=reason,
            created_at=at,
        )


def orphan_orders(
    snapshot: ProviderSnapshot, known_order_ids: Iterable[str]
) -> tuple[OrderRecord, ...]:
    """Working orders at the provider this desk has no record of.

    Split out because it is also the check a startup pass runs against the
    outbox: an intent that was sent but never acknowledged either produced one
    of these, or produced nothing.
    """
    known = set(known_order_ids)
    return tuple(
        order
        for order in snapshot.working_orders
        if order.order_id not in known and order.state is not OrderState.CANCELLED
    )
