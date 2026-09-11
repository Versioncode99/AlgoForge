"""The execution fabric: rate budgets, priorities, the outbox and adapter contracts.

The adapter contract tests run against every adapter in the build — the one that
executes and the three that refuse — because a refusal that is uniform is a
refusal a caller can handle, and one that is not is a surprise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from forge.propdesk import (
    Acknowledgement,
    AdapterUnavailable,
    CommandKind,
    ConnectionState,
    CredentialRecord,
    ExecutionAdapter,
    ExecutionFabric,
    FabricError,
    OrderIntent,
    Outbox,
    Priority,
    Provider,
    RateBudget,
    Side,
)
from forge.propdesk.adapters import (
    SimulatedAccount,
    SimulatedAdapter,
    projectx_adapter,
    rithmic_adapter,
    tradovate_adapter,
)

NOW = datetime(2026, 9, 11, 15, 30, tzinfo=UTC)


def intent(
    account_uid: str,
    *,
    kind: CommandKind = CommandKind.PLACE,
    priority: Priority = Priority.ENTRY,
    key: str = "k1",
    quantity: int = 1,
    **overrides,
) -> OrderIntent:
    base: dict = {
        "intent_id": f"i-{key}",
        "kind": kind,
        "account_uid": account_uid,
        "idempotency_key": key,
        "symbol": "MNQ",
        "side": Side.BUY,
        "quantity": quantity,
        "priority": priority,
        "reference_price": 20_000.0,
        "created_at": NOW,
    }
    return OrderIntent(**{**base, **overrides})


# ── the rate budget ──────────────────────────────────────────────────────────


def test_a_budget_without_a_published_limit_is_not_unlimited() -> None:
    """An undocumented limit is discovered as a penalty, so a default applies."""
    budget = RateBudget(per_minute=None, now=lambda: NOW)
    assert budget.published is False
    assert budget.per_minute > 0


def test_a_budget_refuses_once_its_tokens_are_spent() -> None:
    clock = [NOW]
    budget = RateBudget(per_minute=60, burst=2, now=lambda: clock[0])
    assert budget.take() and budget.take()
    assert budget.take() is False


def test_a_budget_refills_continuously_rather_than_in_steps() -> None:
    clock = [NOW]
    budget = RateBudget(per_minute=60, burst=2, now=lambda: clock[0])
    budget.take()
    budget.take()
    clock[0] = NOW + timedelta(seconds=1)
    assert budget.take() is True


def test_a_provider_penalty_stops_the_budget_for_its_duration() -> None:
    clock = [NOW]
    budget = RateBudget(per_minute=600, burst=10, now=lambda: clock[0])
    budget.penalise(30.0, reason="penalty ticket")
    assert budget.take() is False
    clock[0] = NOW + timedelta(seconds=31)
    assert budget.take() is True


# ── the priority queue ───────────────────────────────────────────────────────


def test_a_flatten_queued_last_is_sent_first(fabric, adapter) -> None:
    """A cancel behind four entries is a position nobody wanted, for that long."""
    uid = adapter.account_uid("F1")
    for index in range(3):
        fabric.dispatch(intent(uid, key=f"entry-{index}", priority=Priority.ENTRY))
    fabric.dispatch(
        intent(uid, key="flat", kind=CommandKind.FLATTEN, priority=Priority.FLATTEN)
    )
    results = fabric.flush()
    assert results[0][0].kind is CommandKind.FLATTEN


def test_priority_order_is_flatten_cancel_protective_entry_modify() -> None:
    assert (
        Priority.FLATTEN
        < Priority.CANCEL
        < Priority.PROTECTIVE
        < Priority.ENTRY
        < Priority.MODIFY
    )


# ── connection state gating ──────────────────────────────────────────────────


def test_a_disconnected_connection_sends_nothing(fabric, adapter, connection) -> None:
    fabric.mark(connection.connection_id, ConnectionState.DISCONNECTED)
    fabric.dispatch(intent(adapter.account_uid("F1")))
    assert fabric.flush() == ()
    assert fabric.runtime(connection.connection_id).queued() == 1


def test_a_degraded_connection_still_lets_exposure_be_reduced(
    fabric, adapter, connection
) -> None:
    """The moment things go wrong must not be the moment getting flat stops working."""
    fabric.mark(connection.connection_id, ConnectionState.DEGRADED)
    uid = adapter.account_uid("F1")
    fabric.dispatch(intent(uid, key="entry", priority=Priority.ENTRY))
    fabric.dispatch(
        intent(uid, key="flat", kind=CommandKind.FLATTEN, priority=Priority.FLATTEN)
    )
    results = fabric.flush()
    assert [item[0].kind for item in results] == [CommandKind.FLATTEN]


# ── the outbox ───────────────────────────────────────────────────────────────


def test_an_intent_is_recorded_before_it_is_sent_and_cleared_on_acknowledgement(
    fabric, adapter, connection
) -> None:
    runtime = fabric.runtime(connection.connection_id)
    fabric.dispatch(intent(adapter.account_uid("F1")))
    assert len(runtime.outbox) == 0  # nothing sent yet, so nothing pending
    fabric.flush()
    assert len(runtime.outbox) == 0
    assert runtime.outbox.was_sent("i-k1") is True


def test_an_outbox_keeps_an_intent_that_was_never_acknowledged() -> None:
    outbox = Outbox()
    outbox.record(intent("acct"))
    assert len(outbox.pending()) == 1
    outbox.acknowledge("i-k1", NOW)
    assert outbox.pending() == ()


# ── retries reuse the key ────────────────────────────────────────────────────


def test_a_retryable_failure_requeues_the_same_idempotency_key(
    fabric, adapter, connection
) -> None:
    """A retry that changed its key would be a second position, not a retry."""
    adapter.fail_next = 1
    uid = adapter.account_uid("F1")
    fabric.dispatch(intent(uid, key="once"))
    first = fabric.flush()
    assert first[0][1].accepted is False
    assert first[0][1].retryable is True

    second = fabric.flush()
    assert second[0][0].idempotency_key == "once"
    assert second[0][1].accepted is True


def test_a_terminal_rejection_is_not_retried(fabric, adapter, connection) -> None:
    adapter.reject_next = 1
    fabric.dispatch(intent(adapter.account_uid("F1"), key="rejected"))
    first = fabric.flush()
    assert first[0][1].accepted is False
    assert first[0][1].retryable is False
    assert fabric.flush() == ()


def test_the_provider_recognises_a_repeated_idempotency_key(fabric, adapter) -> None:
    uid = adapter.account_uid("F1")
    fabric.dispatch(intent(uid, key="same"))
    fabric.flush()
    fabric.dispatch(intent(uid, key="same", intent_id="i-second"))
    result = fabric.flush()
    assert result[0][1].duplicate is True


# ── provider rejections ──────────────────────────────────────────────────────


def test_a_contract_cap_rejection_names_the_cap(fabric, adapter) -> None:
    """The most common reason a copied order fails on a smaller follower."""
    fabric.dispatch(intent(adapter.account_uid("F2"), key="big", quantity=5))
    result = fabric.flush()
    assert result[0][1].accepted is False
    assert "maximum position limit" in result[0][1].reason


def test_an_offline_follower_reports_that_nothing_was_sent(fabric, adapter) -> None:
    adapter.offline = True
    fabric.dispatch(intent(adapter.account_uid("F1"), key="offline"))
    result = fabric.flush()
    assert result[0][1].accepted is False
    assert "nothing was sent" in result[0][1].reason


# ── health ───────────────────────────────────────────────────────────────────


def test_health_reports_numbers_rather_than_a_colour(fabric, connection) -> None:
    health = fabric.health()[0]
    assert health.provider is Provider.SIMULATED
    assert health.tradeable is True
    assert health.rate_budget_remaining is not None
    assert health.heartbeat_age_seconds is not None


def test_a_failed_connection_appears_in_the_unhealthy_list(fabric, connection) -> None:
    fabric.mark(connection.connection_id, ConnectionState.FAILED, error="token expired")
    assert [h.connection_id for h in fabric.unhealthy()] == [connection.connection_id]
    assert fabric.health()[0].last_error == "token expired"


def test_reconnect_backoff_is_capped(fabric, connection) -> None:
    runtime = fabric.runtime(connection.connection_id)
    runtime.reconnect_attempts = 50
    assert runtime.backoff_seconds() == 60.0


def test_the_described_fabric_carries_no_secret(fabric) -> None:
    described = fabric.describe()
    assert "connections" in described and "health" in described
    assert "password" not in str(described).lower()


# ── binding ──────────────────────────────────────────────────────────────────


def test_an_unbound_account_is_refused_with_a_reason(fabric) -> None:
    with pytest.raises(FabricError, match="not bound to a connection"):
        fabric.dispatch(intent("nobody"))


def test_an_adapter_cannot_serve_a_different_provider(connection, credential) -> None:
    fabric = ExecutionFabric(now=lambda: NOW)
    with pytest.raises(FabricError, match="cannot serve"):
        fabric.register(connection, rithmic_adapter())


# ── adapter contract, across every adapter in the build ──────────────────────

ALL_ADAPTERS = [
    pytest.param(SimulatedAdapter, id="simulated"),
    pytest.param(rithmic_adapter, id="rithmic"),
    pytest.param(tradovate_adapter, id="tradovate"),
    pytest.param(projectx_adapter, id="projectx"),
]


@pytest.mark.parametrize("factory", ALL_ADAPTERS)
def test_every_adapter_satisfies_the_protocol(factory) -> None:
    assert isinstance(factory(), ExecutionAdapter)


@pytest.mark.parametrize("factory", ALL_ADAPTERS)
def test_every_adapter_declares_whether_it_is_simulated(factory) -> None:
    assert isinstance(factory().simulated, bool)


@pytest.mark.parametrize("factory", ALL_ADAPTERS)
def test_every_adapter_polls_without_raising(factory) -> None:
    """Polling every adapter in one pass must not be broken by one that cannot."""
    assert isinstance(factory().poll(), tuple)


@pytest.mark.parametrize("factory", ALL_ADAPTERS)
def test_every_adapter_disconnects_without_raising(factory) -> None:
    factory().disconnect()


@pytest.mark.parametrize(
    "factory", [rithmic_adapter, tradovate_adapter, projectx_adapter]
)
@pytest.mark.parametrize(
    "verb", ["connect", "authenticate", "discover_accounts", "snapshot"]
)
def test_a_declared_adapter_refuses_every_verb_with_a_reason(factory, verb) -> None:
    adapter = factory()
    credential = CredentialRecord(
        credential_ref="c",
        provider=adapter.descriptor.provider,
        auth_method=adapter.descriptor.auth_method,
        label="test",
        created_at=NOW,
    )
    arguments = {
        "connect": (credential,),
        "authenticate": (credential,),
        "discover_accounts": (),
        "snapshot": ("acct",),
    }[verb]
    with pytest.raises(AdapterUnavailable, match="no live"):
        getattr(adapter, verb)(*arguments)


@pytest.mark.parametrize(
    "factory", [rithmic_adapter, tradovate_adapter, projectx_adapter]
)
def test_a_declared_adapter_refuses_to_place_and_says_what_is_outstanding(
    factory,
) -> None:
    adapter = factory()
    with pytest.raises(AdapterUnavailable) as caught:
        adapter.place(intent("acct"))
    assert "no credential was read" in str(caught.value)
    assert adapter.work.external, "a declared adapter states its external blockers"


def test_a_declared_adapter_refusal_is_not_retried_by_the_fabric(credential) -> None:
    """A connector that does not exist will not start existing on a retry."""
    from forge.propdesk import BrokerConnection, Environment

    connection = BrokerConnection(
        connection_id="conn-rithmic",
        provider=Provider.RITHMIC,
        environment=Environment.DEMO,
        label="Rithmic",
        credential_ref="cred",
        state=ConnectionState.LIVE,
        created_at=NOW,
        updated_at=NOW,
    )
    fabric = ExecutionFabric(now=lambda: NOW)
    fabric.register(connection, rithmic_adapter())
    fabric.bind_account("acct", "conn-rithmic")
    fabric.dispatch(intent("acct"))
    results = fabric.flush()
    assert results[0][1].accepted is False
    assert results[0][1].retryable is False
    assert fabric.flush() == ()


def test_connecting_a_declared_adapter_fails_rather_than_looking_connected(
    credential,
) -> None:
    from forge.propdesk import BrokerConnection, Environment

    connection = BrokerConnection(
        connection_id="conn-tv",
        provider=Provider.TRADOVATE,
        environment=Environment.DEMO,
        label="Tradovate",
        credential_ref="cred",
        created_at=NOW,
        updated_at=NOW,
    )
    fabric = ExecutionFabric(now=lambda: NOW)
    fabric.register(connection, tradovate_adapter())
    result = fabric.connect("conn-tv", credential)
    assert result.state is ConnectionState.FAILED
    assert "no live" in result.last_error


# ── the simulator behaves like a provider ────────────────────────────────────


def test_the_simulator_produces_genuine_partial_fills() -> None:
    adapter = SimulatedAdapter(fill_chunk=1, now=lambda: NOW)
    adapter.add_account(SimulatedAccount("A"))
    adapter.mark("MNQ", 20_000.0)
    adapter.connect(
        CredentialRecord(
            credential_ref="c",
            provider=Provider.SIMULATED,
            auth_method=adapter.descriptor.auth_method,
            label="l",
            created_at=NOW,
        )
    )
    ack = adapter.place(intent(adapter.account_uid("A"), quantity=3))
    assert ack.accepted
    events = adapter.poll()
    fills = [event for _, event in events if event.cumulative_quantity is not None]
    assert [event.cumulative_quantity for event in fills] == [1, 2, 3]


def test_the_simulator_rests_a_limit_the_price_has_not_reached() -> None:
    from forge.propdesk import OrderType

    adapter = SimulatedAdapter(now=lambda: NOW)
    adapter.add_account(SimulatedAccount("A"))
    adapter.mark("MNQ", 20_000.0)
    ack = adapter.place(
        intent(
            adapter.account_uid("A"),
            order_type=OrderType.LIMIT,
            limit_price=19_000.0,
            reference_price=20_000.0,
        )
    )
    assert ack.accepted
    snapshot = adapter.snapshot(adapter.account_uid("A"))
    assert len(snapshot.working_orders) == 1
    assert snapshot.positions == ()


def test_the_simulator_reports_a_cancel_that_lost_its_race(adapter) -> None:
    """The routine cause of divergence: the order filled before the cancel landed."""
    uid = adapter.account_uid("F1")
    ack = adapter.place(intent(uid, key="filled"))
    result = adapter.cancel(
        intent(uid, key="cancel", kind=CommandKind.CANCEL, order_id=ack.provider_order_id)
    )
    assert result.accepted is False
    assert "already filled" in result.reason


def test_the_simulator_can_be_made_to_duplicate_events(adapter) -> None:
    adapter.duplicate_next = 10
    adapter.place(intent(adapter.account_uid("F1"), key="dup"))
    events = adapter.poll()
    identifiers = [event.provider_event_id for _, event in events]
    assert len(identifiers) != len(set(identifiers))


def test_an_acknowledgement_from_the_simulator_is_the_shared_shape(adapter) -> None:
    ack = adapter.place(intent(adapter.account_uid("F1"), key="shape"))
    assert isinstance(ack, Acknowledgement)
    assert ack.intent_id == "i-shape"
