"""The Rithmic adapter: what it does, what it refuses, and what it claims.

Three properties, and the third is the one that matters most.

It **reads** — accounts, positions, health — through the desk's own interface.
It **refuses to send**, every verb, with a sentence saying a person has to
confirm a first Test order. And it **does not overstate**: the capability matrix
is the exact vocabulary the brief requires, a connection upgrades a capability
no further than `CONNECTED_NOT_VERIFIED`, and nothing here can reach
`VERIFIED_*` without a verification this environment cannot perform.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from forge.propdesk.credentials import AuthMethod, CredentialRecord
from forge.propdesk.fabric import AdapterUnavailable, CommandKind, OrderIntent
from forge.propdesk.identity import (
    AccountType,
    ConnectionState,
    Environment,
    Provider,
)
from forge.propdesk.orders import Side
from forge.propdesk.rithmic import normalise
from forge.propdesk.rithmic.adapter import BASELINE, Capability, RithmicAdapter
from forge.propdesk.rithmic.session import Plant, RithmicSession, SessionState

from tests.propdesk.test_rithmic_session import (  # reuse the fake protocol
    TEMPLATES,
    FakeTransport,
    Message,
    login_ok,
    plant,
)


def _session(*plants: Plant) -> tuple[RithmicSession, dict[Plant, FakeTransport]]:
    sessions = []
    sockets: dict[Plant, FakeTransport] = {}
    for which in plants:
        session, socket, _ = plant(which=which)
        socket.queue(login_ok())
        session.open(system="Rithmic Test", user="u", password="p")
        sessions.append(session)
        sockets[which] = socket
    return RithmicSession(sessions), sockets


def _credential(ref: str = "rithmic-test") -> CredentialRecord:
    return CredentialRecord(
        credential_ref=ref,
        provider=Provider.RITHMIC,
        auth_method=AuthMethod.USERNAME_PASSWORD,
        label="Rithmic Test",
        created_at=datetime.now(UTC),
    )


def _intent() -> OrderIntent:
    return OrderIntent(
        intent_id="i1",
        kind=CommandKind.PLACE,
        account_uid="A1",
        idempotency_key="k1",
        symbol="NQZ6",
        side=Side.BUY,
        quantity=1,
        created_at=datetime.now(UTC),
    )


def _connected() -> tuple[RithmicAdapter, dict[Plant, FakeTransport]]:
    session, sockets = _session(Plant.ORDER, Plant.PNL)
    adapter = RithmicAdapter(connect_with=lambda credential: session)
    adapter.connect(_credential())
    return adapter, sockets


# ── the refusals ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("verb", ["place", "modify", "cancel", "flatten"])
def test_every_order_verb_refuses_and_says_a_person_must_confirm(verb: str) -> None:
    adapter, _ = _connected()
    intent = _intent()
    with pytest.raises(AdapterUnavailable) as refusal:
        getattr(adapter, verb)(intent)
    message = str(refusal.value)
    assert "will not" in message
    assert "Nothing was sent" in message
    assert "a person confirms" in message


def test_nothing_was_sent_on_the_wire_by_a_refused_order() -> None:
    """The refusal is before the socket, not a rejection after it."""
    adapter, sockets = _connected()
    before = {which: len(socket.sent) for which, socket in sockets.items()}
    with pytest.raises(AdapterUnavailable):
        adapter.place(_intent())
    assert {which: len(socket.sent) for which, socket in sockets.items()} == before


def test_an_adapter_with_no_transport_refuses_and_reads_no_credential() -> None:
    adapter = RithmicAdapter(workspace=Path("/nonexistent"))
    with pytest.raises(AdapterUnavailable) as refusal:
        adapter.connect(_credential())
    assert "No Rithmic R | Protocol SDK was found" in str(refusal.value)
    assert "does not ship one" in str(refusal.value)


def test_reading_without_a_connection_refuses_rather_than_returning_empty() -> None:
    """An empty tuple would present as a connection with no accounts."""
    adapter = RithmicAdapter()
    with pytest.raises(AdapterUnavailable, match="no connection is open"):
        adapter.discover_accounts()
    with pytest.raises(AdapterUnavailable, match="no connection is open"):
        adapter.snapshot("A1")


def test_a_degraded_connection_refuses_a_snapshot_rather_than_taking_a_partial_one() -> None:
    adapter, _ = _connected()
    adapter._session.plants[Plant.PNL].health.state = SessionState.DEGRADED
    adapter._session.plants[Plant.PNL].health.state = SessionState.RECONNECTING
    with pytest.raises(AdapterUnavailable, match="would be incomplete"):
        adapter.snapshot("A1")


# ── reading ──────────────────────────────────────────────────────────────────


def test_accounts_are_discovered_and_normalised() -> None:
    adapter, sockets = _connected()
    sockets[Plant.ORDER].queue(
        Message(template_id=TEMPLATES["account_list_response"], account_id="A1",
                account_name="Test One", fcm_id="F1", ib_id="I1",
                account_type="funded", rq_handler_rp_code=[]),
        Message(template_id=TEMPLATES["account_list_response"], account_id="A2",
                account_name="Test Two", fcm_id="F1", ib_id="I1",
                account_type="evaluation", rq_handler_rp_code=["0"]),
    )
    accounts = adapter.discover_accounts()
    assert [a.key.account_id for a in accounts] == ["A1", "A2"]
    assert accounts[0].account_type is AccountType.FUNDED_SIMULATED
    assert accounts[1].account_type is AccountType.EVALUATION
    # Qualified, so two accounts under different clearing firms cannot collide.
    assert accounts[0].key.qualifiers == {"fcm_id": "F1", "ib_id": "I1"}
    # Balance stays unset rather than zeroed.
    assert accounts[0].balance is None


def test_one_unreadable_account_row_does_not_discard_the_others() -> None:
    adapter, sockets = _connected()
    sockets[Plant.ORDER].queue(
        Message(template_id=TEMPLATES["account_list_response"], account_id="",
                rq_handler_rp_code=[]),
        Message(template_id=TEMPLATES["account_list_response"], account_id="A2",
                rq_handler_rp_code=["0"]),
    )
    accounts = adapter.discover_accounts()
    assert [a.key.account_id for a in accounts] == ["A2"]
    assert adapter.as_dict()["unmapped"], "the dropped row was not recorded"


def test_a_snapshot_with_an_unreadable_row_is_marked_incomplete() -> None:
    """Reconciliation *replaces* local state with a snapshot.

    Saying a partial one is complete is how a position the provider reported,
    and this build could not read, comes to be treated as closed.
    """
    adapter, sockets = _connected()
    sockets[Plant.PNL].queue(
        Message(template_id=TEMPLATES["account_list_response"], symbol="",
                rq_handler_rp_code=[]),
        Message(template_id=TEMPLATES["account_list_response"], symbol="NQZ6",
                fill_buy_qty=3, fill_sell_qty=1, avg_open_fill_price=20000.25,
                rq_handler_rp_code=["0"]),
    )
    # The PnL plant answers `position_list`, whose response id the fake
    # vocabulary shares with the account list.
    adapter._session.plants[Plant.PNL].vocabulary.templates.setdefault(
        "position_list", TEMPLATES["account_list"]
    )
    adapter._session.plants[Plant.PNL].vocabulary.templates.setdefault(
        "position_list_response", TEMPLATES["account_list_response"]
    )
    snapshot = adapter.snapshot("A1")
    assert [p.symbol for p in snapshot.positions] == ["NQZ6"]
    assert snapshot.positions[0].quantity == 2
    assert snapshot.complete is False
    assert "could not be read" in snapshot.note


def test_health_reports_disconnected_before_a_connection_and_live_after() -> None:
    adapter = RithmicAdapter()
    assert adapter.health().state is ConnectionState.DISCONNECTED
    connected, _ = _connected()
    assert connected.health().state is ConnectionState.LIVE


def test_capabilities_are_never_filled_in_from_nothing() -> None:
    """`forge.propdesk.policy` treats an unrecorded permission as not granted."""
    adapter, _ = _connected()
    capability = adapter.discover_capabilities(
        # any key; the answer does not depend on it
        next(iter([]), None) or _any_key()
    )
    assert capability == type(capability)()


def _any_key() -> Any:
    from forge.propdesk.identity import AccountKey

    return AccountKey(
        provider=Provider.RITHMIC,
        environment=Environment.DEMO,
        credential_ref="rithmic-test",
        account_id="A1",
    )


# ── what it claims ───────────────────────────────────────────────────────────


def test_the_capability_vocabulary_is_the_one_the_brief_requires() -> None:
    assert {c.value for c in Capability} == {
        "NOT_IMPLEMENTED",
        "IMPLEMENTED_NOT_CONNECTED",
        "CONNECTED_NOT_VERIFIED",
        "VERIFIED_READ_ONLY_IN_RITHMIC_TEST",
        "VERIFIED_ORDER_LIFECYCLE_IN_RITHMIC_TEST",
        "BLOCKED",
    }


def test_nothing_claims_verified_without_a_verification() -> None:
    """The claim this build is least entitled to make, asserted absent."""
    adapter, _ = _connected()
    for row in adapter.capabilities():
        assert not row.status.value.startswith("VERIFIED"), row.name


def test_connecting_raises_a_capability_no_further_than_connected_not_verified() -> None:
    offline = RithmicAdapter()
    before = {row.name: row.status for row in offline.capabilities()}
    adapter, _ = _connected()
    after = {row.name: row.status for row in adapter.capabilities()}
    for name, status in before.items():
        if status is Capability.IMPLEMENTED_NOT_CONNECTED:
            assert after[name] is Capability.CONNECTED_NOT_VERIFIED, name
        else:
            assert after[name] is status, name


def test_order_release_is_not_implemented_and_test_verification_is_blocked() -> None:
    claims = {row.name: row.status for row in RithmicAdapter().capabilities()}
    assert claims["order_release"] is Capability.NOT_IMPLEMENTED
    assert claims["rithmic_test_verification"] is Capability.BLOCKED


def test_every_capability_states_what_it_rests_on() -> None:
    for row in BASELINE:
        assert len(row.detail) > 40, f"{row.name} has a label rather than a statement"


def test_the_adapter_says_it_cannot_send_and_is_not_a_simulator() -> None:
    payload = RithmicAdapter().as_dict()
    assert payload["can_send_orders"] is False
    # Not simulated either: an adapter reporting simulated fills while calling
    # itself a Rithmic connector is the confusion the flag exists to prevent.
    assert payload["simulated"] is False
    assert RithmicAdapter().simulated is False


# ── normalisation refuses rather than guessing ───────────────────────────────


def test_an_unmapped_order_status_refuses_rather_than_being_guessed_at() -> None:
    with pytest.raises(normalise.NormalisationRefused) as refusal:
        normalise.order_event(Message(basket_id="B1", status="quantum superposition"))
    assert "differ by the size of a position" in str(refusal.value)


def test_a_notification_with_no_order_id_refuses() -> None:
    with pytest.raises(normalise.NormalisationRefused, match="no order identifier"):
        normalise.order_event(Message(status="open"))


@pytest.mark.parametrize(
    ("status", "expected"),
    [("open", "acknowledged"), ("complete", "fill"), ("partial fill", "partial_fill"),
     ("cancelled", "cancelled"), ("rejected", "rejected"), ("trade correction", "busted")],
)
def test_the_statuses_this_build_maps_produce_the_lifecycle_events(
    status: str, expected: str
) -> None:
    _, event = normalise.order_event(
        Message(basket_id="B1", status=status, total_fill_size=2, ssboe=1_780_000_000)
    )
    assert event.event_type.value == expected
    assert event.cumulative_quantity == 2


def test_a_fill_total_that_will_not_parse_leaves_the_quantity_unset() -> None:
    _, event = normalise.order_event(
        Message(basket_id="B1", status="fill", total_fill_size="not a number")
    )
    assert event.cumulative_quantity is None


def test_the_protocols_seconds_and_microseconds_become_one_utc_instant() -> None:
    at = normalise.timestamp(Message(ssboe=1_780_000_000, usecs=500_000))
    assert at.tzinfo is UTC
    assert at == datetime.fromtimestamp(1_780_000_000.5, tz=UTC)


def test_a_message_with_no_timestamp_is_stamped_now_rather_than_at_the_epoch() -> None:
    at = normalise.timestamp(Message())
    assert (datetime.now(UTC) - at).total_seconds() < 5
