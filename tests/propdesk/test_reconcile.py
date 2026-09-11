"""Reconciliation: the provider is authoritative and we are a cache.

Every case here is one the research documents: a follower position the desk did
not open, a working order at the provider with no local counterpart, an account
the provider has locked, a late event that would otherwise drive a correction
into a snapshot that has already superseded it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from forge.propdesk import (
    CommandKind,
    DivergenceKind,
    LocalView,
    OrderRecord,
    OrderState,
    OrderType,
    Position,
    Priority,
    ProviderSnapshot,
    ReconciliationEngine,
    ReconciliationPolicy,
    ReconciliationState,
    Severity,
    Side,
    Trigger,
    orphan_orders,
)

NOW = datetime(2026, 9, 11, 15, 30, tzinfo=UTC)
LATER = NOW + timedelta(seconds=30)
ACCOUNT = "acct-1"


def snapshot(
    *,
    positions: tuple[Position, ...] = (),
    orders: tuple[OrderRecord, ...] = (),
    version: int = 2,
    balance: float | None = None,
    can_trade: bool | None = None,
    complete: bool = True,
    at: datetime = NOW,
) -> ProviderSnapshot:
    return ProviderSnapshot(
        account_uid=ACCOUNT,
        taken_at=at,
        version=version,
        positions=positions,
        working_orders=orders,
        balance=balance,
        can_trade=can_trade,
        complete=complete,
    )


def position(quantity: int, symbol: str = "MNQ") -> Position:
    return Position(
        account_uid=ACCOUNT, symbol=symbol, quantity=quantity, average_price=20_000.0
    )


def working(order_id: str, quantity: int = 1) -> OrderRecord:
    return OrderRecord(
        order_id=order_id,
        account_uid=ACCOUNT,
        symbol="MNQ",
        side=Side.BUY,
        quantity=quantity,
        order_type=OrderType.LIMIT,
        limit_price=19_900.0,
        state=OrderState.WORKING,
        created_at=NOW,
        updated_at=NOW,
    )


def engine(**policy) -> ReconciliationEngine:
    return ReconciliationEngine(
        ReconciliationPolicy(**policy), now=lambda: policy.pop("_now", NOW)
    )


# ── agreement ────────────────────────────────────────────────────────────────


def test_agreement_reports_in_sync() -> None:
    subject = ReconciliationEngine(now=lambda: NOW)
    report = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT, positions=(position(2),)),
        snapshot=snapshot(positions=(position(2),)),
        trigger=Trigger.PERIODIC,
    )
    assert report.in_sync
    assert report.divergences == ()


# ── the four position disagreements ──────────────────────────────────────────


def test_a_position_the_desk_did_not_open_is_untracked_not_phantom() -> None:
    """A manual trade on a follower, or a fill whose event never arrived."""
    subject = ReconciliationEngine(now=lambda: NOW)
    report = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT),
        snapshot=snapshot(positions=(position(3),)),
        trigger=Trigger.PERIODIC,
    )
    kinds = {d.kind for d in report.divergences}
    assert DivergenceKind.UNTRACKED_POSITION in kinds
    assert report.state is ReconciliationState.DIVERGED


def test_a_position_the_provider_does_not_hold_is_phantom() -> None:
    subject = ReconciliationEngine(now=lambda: NOW)
    report = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT, positions=(position(2),)),
        snapshot=snapshot(),
        trigger=Trigger.PERIODIC,
    )
    assert {d.kind for d in report.divergences} == {DivergenceKind.PHANTOM_POSITION}


def test_an_incomplete_snapshot_never_proves_a_position_was_closed() -> None:
    """Absence is only evidence when the listing was complete."""
    subject = ReconciliationEngine(now=lambda: NOW)
    report = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT, positions=(position(2),)),
        snapshot=snapshot(complete=False),
        trigger=Trigger.PERIODIC,
    )
    kinds = {d.kind for d in report.divergences}
    assert DivergenceKind.PHANTOM_POSITION not in kinds
    assert DivergenceKind.INCOMPLETE_SNAPSHOT in kinds
    assert any("incomplete" in limitation for limitation in report.limitations)


def test_a_size_disagreement_is_reported_with_both_numbers() -> None:
    subject = ReconciliationEngine(now=lambda: NOW)
    report = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT, positions=(position(4),)),
        snapshot=snapshot(positions=(position(2),)),
        trigger=Trigger.PERIODIC,
    )
    found = next(d for d in report.divergences if d.kind is DivergenceKind.POSITION_SIZE)
    assert found.local == 4
    assert found.remote == 2


def test_an_account_holding_its_books_correctly_can_still_miss_its_target() -> None:
    """A follower whose order was rejected has consistent books and no position."""
    subject = ReconciliationEngine(now=lambda: NOW)
    report = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT),
        snapshot=snapshot(),
        trigger=Trigger.EVENT,
        target_positions={"MNQ": 2},
    )
    assert report.state is ReconciliationState.DIVERGED
    assert "the desk intends" in report.divergences[0].detail


# ── orders ───────────────────────────────────────────────────────────────────


def test_a_working_order_with_no_local_counterpart_is_an_orphan() -> None:
    subject = ReconciliationEngine(now=lambda: NOW)
    report = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT),
        snapshot=snapshot(orders=(working("prov-1"),)),
        trigger=Trigger.PERIODIC,
    )
    assert {d.kind for d in report.divergences} == {DivergenceKind.ORPHAN_ORDER}


def test_a_local_order_the_provider_does_not_list_is_information_not_a_correction() -> None:
    """It filled, was cancelled, or never landed. A trade does not fix it."""
    subject = ReconciliationEngine(now=lambda: NOW)
    report = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT, working_orders=(working("local-1"),)),
        snapshot=snapshot(),
        trigger=Trigger.PERIODIC,
    )
    found = report.divergences[0]
    assert found.kind is DivergenceKind.MISSING_ORDER
    assert found.severity is Severity.INFORMATION
    assert report.in_sync


def test_a_filled_quantity_disagreement_is_reported() -> None:
    subject = ReconciliationEngine(now=lambda: NOW)
    local_order = working("o", quantity=4)
    remote_order = local_order.model_copy(update={"filled_quantity": 2})
    report = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT, working_orders=(local_order,)),
        snapshot=snapshot(orders=(remote_order,)),
        trigger=Trigger.PERIODIC,
    )
    assert {d.kind for d in report.divergences} == {DivergenceKind.ORDER_STATE}


def test_orphan_orders_are_listed_against_a_set_of_known_ids() -> None:
    orders = (working("a"), working("b"))
    assert [o.order_id for o in orphan_orders(snapshot(orders=orders), {"a"})] == ["b"]


# ── account state ────────────────────────────────────────────────────────────


def test_an_account_the_provider_has_locked_quarantines_it() -> None:
    subject = ReconciliationEngine(now=lambda: NOW)
    report = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT),
        snapshot=snapshot(can_trade=False),
        trigger=Trigger.PERIODIC,
    )
    assert report.state is ReconciliationState.QUARANTINED
    assert report.corrections == ()


def test_a_quarantine_is_not_lifted_by_a_clean_pass() -> None:
    """The condition may simply not be visible this cycle. A person lifts it."""
    subject = ReconciliationEngine(now=lambda: NOW)
    subject.reconcile(
        local=LocalView(account_uid=ACCOUNT),
        snapshot=snapshot(can_trade=False),
        trigger=Trigger.PERIODIC,
    )
    clean = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT),
        snapshot=snapshot(version=3, can_trade=True),
        trigger=Trigger.PERIODIC,
    )
    assert clean.state is ReconciliationState.QUARANTINED
    subject.release(ACCOUNT)
    assert subject.state(ACCOUNT) is ReconciliationState.UNKNOWN


def test_a_balance_disagreement_is_reported_but_not_traded_on() -> None:
    subject = ReconciliationEngine(now=lambda: NOW)
    report = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT, balance=50_000.0),
        snapshot=snapshot(balance=49_950.0),
        trigger=Trigger.END_OF_SESSION,
    )
    found = report.divergences[0]
    assert found.kind is DivergenceKind.BALANCE
    assert found.severity is Severity.INFORMATION


# ── the staleness guard ──────────────────────────────────────────────────────


def test_an_event_older_than_the_latest_snapshot_is_stale() -> None:
    """Without this the engine corrects toward an event the provider superseded."""
    subject = ReconciliationEngine(now=lambda: NOW)
    subject.reconcile(
        local=LocalView(account_uid=ACCOUNT),
        snapshot=snapshot(version=7),
        trigger=Trigger.PERIODIC,
    )
    assert subject.is_stale(ACCOUNT, 6) is True
    assert subject.is_stale(ACCOUNT, 7) is False
    assert subject.is_stale(ACCOUNT, 9) is False


# ── adoption ─────────────────────────────────────────────────────────────────


def test_adopting_replaces_local_belief_rather_than_merging_it() -> None:
    subject = ReconciliationEngine(now=lambda: NOW)
    adopted = subject.adopt(snapshot(positions=(position(3),), version=5, balance=1.0))
    assert [p.quantity for p in adopted.positions] == [3]
    assert adopted.snapshot_version == 5
    assert adopted.positions[0].snapshot_version == 5


def test_adoption_drops_a_flat_position_rather_than_carrying_a_zero() -> None:
    subject = ReconciliationEngine(now=lambda: NOW)
    adopted = subject.adopt(snapshot(positions=(position(0),)))
    assert adopted.positions == ()


# ── corrections ──────────────────────────────────────────────────────────────


def test_a_correction_is_proposed_once_the_tolerance_window_has_passed() -> None:
    clock = [NOW]
    subject = ReconciliationEngine(
        ReconciliationPolicy(tolerance_seconds=5.0), now=lambda: clock[0]
    )
    first = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT),
        snapshot=snapshot(),
        trigger=Trigger.EVENT,
        target_positions={"MNQ": 2},
    )
    assert first.corrections == ()  # inside the window

    clock[0] = NOW + timedelta(seconds=10)
    second = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT),
        snapshot=snapshot(version=3),
        trigger=Trigger.EVENT,
        target_positions={"MNQ": 2},
    )
    assert len(second.corrections) == 1
    intent = second.corrections[0]
    assert intent.side is Side.BUY
    assert intent.quantity == 2
    assert intent.priority is Priority.ENTRY


def test_a_correction_that_reduces_exposure_is_prioritised_as_a_flatten() -> None:
    subject = ReconciliationEngine(
        ReconciliationPolicy(tolerance_seconds=0.0, close_untracked_positions=True),
        now=lambda: NOW,
    )
    report = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT),
        snapshot=snapshot(positions=(position(3),)),
        trigger=Trigger.EVENT,
    )
    assert [i.priority for i in report.corrections] == [Priority.FLATTEN]
    assert report.corrections[0].side is Side.SELL


def test_an_untracked_position_is_left_alone_unless_the_policy_says_otherwise() -> None:
    """It may be the operator trading by hand on their own account."""
    subject = ReconciliationEngine(
        ReconciliationPolicy(tolerance_seconds=0.0), now=lambda: NOW
    )
    report = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT),
        snapshot=snapshot(positions=(position(3),)),
        trigger=Trigger.EVENT,
    )
    assert report.corrections == ()


def test_recovery_passes_do_not_correct_by_default() -> None:
    """Replaying an hour of entries on reconnect opens positions nobody wanted."""
    subject = ReconciliationEngine(
        ReconciliationPolicy(tolerance_seconds=0.0), now=lambda: NOW
    )
    for trigger in (Trigger.STARTUP, Trigger.RECONNECT):
        report = subject.reconcile(
            local=LocalView(account_uid=ACCOUNT),
            snapshot=snapshot(version=2 + trigger.value.count("t")),
            trigger=trigger,
            target_positions={"MNQ": 2},
        )
        assert report.corrections == ()
        assert any("does not correct on a" in item for item in report.limitations)


def test_quarantining_on_divergence_proposes_nothing() -> None:
    subject = ReconciliationEngine(
        ReconciliationPolicy(quarantine_on_divergence=True, tolerance_seconds=0.0),
        now=lambda: NOW,
    )
    report = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT),
        snapshot=snapshot(),
        trigger=Trigger.EVENT,
        target_positions={"MNQ": 2},
    )
    assert report.state is ReconciliationState.QUARANTINED
    assert report.corrections == ()


def test_an_orphan_order_is_cancelled_rather_than_traded_against() -> None:
    subject = ReconciliationEngine(
        ReconciliationPolicy(tolerance_seconds=0.0), now=lambda: NOW
    )
    report = subject.reconcile(
        local=LocalView(account_uid=ACCOUNT),
        snapshot=snapshot(orders=(working("prov-9"),)),
        trigger=Trigger.EVENT,
    )
    assert [i.kind for i in report.corrections] == [CommandKind.CANCEL]
    assert report.corrections[0].priority is Priority.CANCEL


def test_corrections_carry_a_deterministic_idempotency_key() -> None:
    """A correction re-proposed from the same snapshot must not double up."""
    subject = ReconciliationEngine(
        ReconciliationPolicy(tolerance_seconds=0.0), now=lambda: NOW
    )
    keys = set()
    for _ in range(2):
        engine_two = ReconciliationEngine(
            ReconciliationPolicy(tolerance_seconds=0.0), now=lambda: NOW
        )
        report = engine_two.reconcile(
            local=LocalView(account_uid=ACCOUNT),
            snapshot=snapshot(version=4),
            trigger=Trigger.EVENT,
            target_positions={"MNQ": 1},
        )
        keys.add(report.corrections[0].idempotency_key)
    assert len(keys) == 1
    assert subject.state(ACCOUNT) is ReconciliationState.UNKNOWN
