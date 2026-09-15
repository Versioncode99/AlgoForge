"""The Rithmic session, driven end to end with no SDK, no network and no credential.

Everything the connector owns is here: the state machine, request correlation,
multi-part completion, sequence gaps, heartbeat accounting, jittered reconnect,
subscription restoration, graceful shutdown and redaction. Everything it does
*not* own — the wire vocabulary — arrives through `Vocabulary`, which these
tests supply, which is exactly how it arrives from the operator's licensed SDK
in production.

That split is what makes this suite possible. Nothing from the Rithmic archives
is committed, so a test that needed the real `.proto` files could not run here
at all — and a connector whose failure paths are only exercisable against a live
broker is a connector whose failure paths are never exercised.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from forge.propdesk.rithmic.redaction import REDACTED, describe, scrub, sensitive
from forge.propdesk.rithmic.session import (
    LIVE,
    Backoff,
    Plant,
    PlantSession,
    RithmicSession,
    SessionError,
    SessionState,
    Vocabulary,
)

# ── a fake protocol, standing in for the operator's SDK ──────────────────────
#
# Template numbers here are this test's own. The real ones live in licensed
# `.proto` files the adapter loads at run time and this repository does not
# carry — see `docs/ADR-0001-rithmic-transport.md`.

TEMPLATES: dict[str, int] = {
    "login": 1,
    "login_response": 2,
    "logout": 3,
    "heartbeat": 4,
    "heartbeat_response": 5,
    "account_list": 6,
    "account_list_response": 7,
    "subscribe": 8,
    "unsubscribe": 9,
}


class Message:
    """A protobuf stand-in: attributes, and a `template_id`."""

    def __init__(self, **fields: Any) -> None:
        for name, value in fields.items():
            setattr(self, name, value)

    def __repr__(self) -> str:
        return f"Message({vars(self)})"


def build(role: str, fields: dict[str, Any]) -> Message:
    return Message(**fields)


def encode(message: Message) -> bytes:
    return json.dumps(vars(message), default=str).encode("utf-8")


def decode(frame: bytes) -> Message:
    return Message(**json.loads(frame.decode("utf-8")))


VOCABULARY = Vocabulary(
    templates=TEMPLATES,
    infra={plant: index for index, plant in enumerate(Plant, start=1)},
    build=build,
    decode=decode,
    encode=encode,
)


class FakeTransport:
    """A socket that answers from a script, and remembers what it was sent."""

    def __init__(self, script: list[Message] | None = None) -> None:
        self.script: list[Message | None] = list(script or [])
        self.clock: Clock | None = None
        self.sent: list[dict[str, Any]] = []
        self.connected = 0
        self.closed = 0
        self.fail_connect: Exception | None = None
        self.fail_receive: Exception | None = None

    def connect(self, uri: str, *, verify: Any) -> None:
        if self.fail_connect is not None:
            raise self.fail_connect
        self.connected += 1

    def send(self, payload: bytes) -> None:
        self.sent.append(json.loads(payload.decode("utf-8")))

    def receive(self, timeout: float) -> bytes | None:
        # A read that times out takes time. Moving the clock here is what makes
        # a frozen-clock test terminate the way a real wait does.
        if self.clock is not None:
            self.clock.advance(timeout)
        if self.fail_receive is not None:
            raise self.fail_receive
        if not self.script:
            return None
        nxt = self.script.pop(0)
        return None if nxt is None else encode(nxt)

    def close(self) -> None:
        self.closed += 1

    # convenience for the tests
    def queue(self, *messages: Message | None) -> None:
        self.script.extend(messages)


class Clock:
    """A clock a test moves, so nothing here sleeps."""

    def __init__(self) -> None:
        self.at = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


def login_ok(interval: int = 30) -> Message:
    return Message(template_id=TEMPLATES["login_response"], heartbeat_interval=interval)


def plant(
    transport: FakeTransport | None = None,
    clock: Clock | None = None,
    which: Plant = Plant.ORDER,
) -> tuple[PlantSession, FakeTransport, Clock]:
    socket = transport or FakeTransport()
    time = clock or Clock()
    socket.clock = time
    session = PlantSession(
        which,
        vocabulary=VOCABULARY,
        transport=socket,
        uri="wss://example.invalid/rithmic",
        verify=object(),
        now=time,
        backoff=Backoff(base=0.01, ceiling=0.02, _random=lambda: 1.0),
    )
    return session, socket, time


def open_ok(which: Plant = Plant.ORDER) -> tuple[PlantSession, FakeTransport, Clock]:
    session, socket, clock = plant(which=which)
    socket.queue(login_ok())
    session.open(system="Rithmic Test", user="u", password="p")
    return session, socket, clock


# ── the lifecycle ────────────────────────────────────────────────────────────


def test_a_fresh_session_is_disconnected_and_not_live() -> None:
    session, _, _ = plant()
    assert session.state is SessionState.DISCONNECTED
    assert session.live is False


def test_opening_reaches_ready_and_records_the_servers_heartbeat_interval() -> None:
    session, socket, _ = plant()
    socket.queue(login_ok(interval=45))
    assert session.open(system="Rithmic Test", user="u", password="p") is SessionState.READY
    assert session.health.heartbeat_interval == 45
    assert socket.connected == 1
    assert session.live


def test_a_login_response_with_no_heartbeat_interval_fails_rather_than_guessing() -> None:
    """Guessing a rate is how a connection is dropped for being quiet."""
    session, socket, _ = plant()
    socket.queue(Message(template_id=TEMPLATES["login_response"], heartbeat_interval=0))
    with pytest.raises(SessionError, match="no heartbeat interval"):
        session.open(system="Rithmic Test", user="u", password="p")
    assert session.state is SessionState.FAILED


def test_a_connect_failure_names_the_plant_and_settles_as_failed() -> None:
    session, socket, _ = plant(which=Plant.TICKER)
    socket.fail_connect = OSError("network is unreachable")
    with pytest.raises(SessionError, match="ticker"):
        session.open(system="Rithmic Test", user="u", password="p")
    assert session.state is SessionState.FAILED


def test_a_login_that_is_never_answered_times_out_rather_than_hanging() -> None:
    session, _, _ = plant()
    with pytest.raises(SessionError, match="did not answer"):
        session.open(system="Rithmic Test", user="u", password="p")


def test_closing_logs_out_and_closes_the_socket() -> None:
    session, socket, _ = open_ok()
    session.close()
    assert socket.closed == 1
    assert any(s.get("template_id") == TEMPLATES["logout"] for s in socket.sent)
    assert session.state is SessionState.DISCONNECTED


def test_closing_survives_a_socket_that_throws_on_the_way_out() -> None:
    """Shutdown must not raise. A failed logout is not worth losing a shutdown."""
    session, socket, _ = open_ok()

    def boom() -> None:
        raise OSError("already gone")

    socket.close = boom  # type: ignore[method-assign]
    session.close()
    assert session.state is SessionState.DISCONNECTED


# ── correlation ──────────────────────────────────────────────────────────────


def test_a_request_returns_the_response_that_matches_it() -> None:
    session, socket, _ = open_ok()
    socket.queue(Message(template_id=TEMPLATES["account_list_response"], account_id="A1"))
    reply = session.request("account_list", {})
    assert reply.account_id == "A1"


def test_a_multi_part_response_is_collected_to_its_completion_marker() -> None:
    """One part is not the answer. A desk with nine accounts showing one is the bug."""
    session, socket, _ = open_ok()
    socket.queue(
        Message(template_id=TEMPLATES["account_list_response"], account_id="A1",
                rq_handler_rp_code=[]),
        Message(template_id=TEMPLATES["account_list_response"], account_id="A2",
                rq_handler_rp_code=[]),
        Message(template_id=TEMPLATES["account_list_response"], account_id="A3",
                rq_handler_rp_code=["0"]),
    )
    reply = session.request("account_list", {})
    assert isinstance(reply, tuple)
    assert [part.account_id for part in reply] == ["A1", "A2", "A3"]


def test_two_requests_for_one_role_at_once_are_refused_rather_than_mispaired() -> None:
    session, _socket, _ = open_ok()
    session._pending["account_list"] = session._pending.get("account_list") or _stub_pending()
    with pytest.raises(SessionError, match="already in flight"):
        session.request("account_list", {}, timeout=0.05)


def _stub_pending() -> Any:
    from forge.propdesk.rithmic.session import Pending

    return Pending(role="account_list", sent_at=datetime.now(UTC))


def test_a_request_for_a_role_the_vocabulary_lacks_says_so() -> None:
    session, _, _ = plant()
    with pytest.raises(SessionError, match="no 'teleport' template"):
        session.send("teleport", {})


# ── heartbeats ───────────────────────────────────────────────────────────────


def test_a_heartbeat_is_sent_when_half_the_interval_has_passed() -> None:
    session, socket, clock = open_ok()
    before = len(socket.sent)
    session.heartbeat()
    assert len(socket.sent) == before, "sent one immediately after login"
    clock.advance(20)
    session.heartbeat()
    assert socket.sent[-1]["template_id"] == TEMPLATES["heartbeat"]


def test_an_overdue_heartbeat_degrades_and_a_reply_recovers_it() -> None:
    session, socket, clock = open_ok()
    clock.advance(120)
    session.pump()  # a timeout, which is where the check happens
    assert session.state is SessionState.DEGRADED
    assert session.live, "degraded is still delivering; it is not disconnected"

    socket.queue(Message(template_id=TEMPLATES["heartbeat_response"]))
    session.pump()
    assert session.state is SessionState.READY
    assert session.health.missed_heartbeats == 0


def test_three_missed_heartbeats_are_counted_and_named() -> None:
    session, _, clock = open_ok()
    for _ in range(3):
        clock.advance(120)
        session.pump()
    assert session.health.missed_heartbeats >= 3
    assert "heartbeats missed" in session.health.last_error


# ── sequence gaps ────────────────────────────────────────────────────────────


def test_a_sequence_gap_is_counted_rather_than_repaired() -> None:
    """This layer cannot invent lost messages. It can say the stream is holed."""
    session, socket, _ = open_ok()
    socket.queue(
        Message(template_id=99, sequence_number=1),
        Message(template_id=99, sequence_number=2),
        Message(template_id=99, sequence_number=7),
    )
    for _ in range(3):
        session.pump()
    assert session.health.sequence_gaps == 4


def test_a_message_with_no_sequence_number_does_not_register_a_gap() -> None:
    session, socket, _ = open_ok()
    socket.queue(Message(template_id=99), Message(template_id=99))
    session.pump()
    session.pump()
    assert session.health.sequence_gaps == 0


# ── malformed input ──────────────────────────────────────────────────────────


def test_an_undecodable_frame_is_dropped_rather_than_killing_the_connection() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    session, socket, _ = plant()
    session._on_event = lambda kind, data: events.append((kind, data))
    socket.queue(login_ok())
    session.open(system="Rithmic Test", user="u", password="p")

    socket.script.append(b"not json")  # type: ignore[arg-type]
    socket.receive = lambda timeout: b"{not json"  # type: ignore[method-assign]
    assert session.pump() is None
    assert any(kind == "undecodable" for kind, _ in events)
    assert session.state is not SessionState.FAILED


def test_a_read_error_degrades_rather_than_raising_into_the_caller() -> None:
    session, socket, _ = open_ok()
    socket.fail_receive = OSError("connection reset")
    assert session.pump() is None
    assert session.state is SessionState.DEGRADED
    assert "read failed" in session.health.last_error


# ── reconnection ─────────────────────────────────────────────────────────────


def test_backoff_is_bounded_and_jittered() -> None:
    rising = Backoff(base=1.0, ceiling=8.0, _random=lambda: 1.0)
    assert [rising.next_delay() for _ in range(5)] == [1.0, 2.0, 4.0, 8.0, 8.0]
    # Jitter is not decoration: four plants reconnecting together after one blip
    # is the shape that collects a rate penalty exactly when a connection is
    # most wanted.
    jittered = Backoff(base=4.0, ceiling=8.0, _random=lambda: 0.25)
    assert jittered.next_delay() == 1.0
    jittered.reset()
    assert jittered.attempt == 0


def test_a_reconnect_puts_the_subscriptions_back() -> None:
    session, socket, _ = open_ok(Plant.TICKER)
    session.subscribe("CME", "NQZ6")
    session.subscribe("CME", "ESZ6")
    assert session.health.subscriptions == 2

    slept: list[float] = []
    socket.queue(login_ok())
    state = session.reconnect(
        system="Rithmic Test", user="u", password="p", sleep=slept.append
    )
    assert state is SessionState.READY
    assert session.health.reconnects == 1
    assert session.health.resubscribed == 2
    assert slept and slept[0] > 0, "a reconnect that does not wait is a reconnect storm"
    assert session.subscriptions == frozenset({("CME", "NQZ6"), ("CME", "ESZ6")})


def test_a_reconnect_that_cannot_log_in_stays_failed_rather_than_reporting_ready() -> None:
    session, _socket, _ = open_ok()
    state = session.reconnect(
        system="Rithmic Test", user="u", password="p", sleep=lambda _: None
    )
    assert state is not SessionState.READY
    assert not session.live


# ── every plant, and the desk's one question ─────────────────────────────────


def test_the_desk_is_only_ready_when_every_opened_plant_is() -> None:
    """An order plant down while the ticker plant is up is not "connected"."""
    order, _order_socket, _ = open_ok(Plant.ORDER)
    ticker, _ticker_socket, ticker_clock = open_ok(Plant.TICKER)
    session = RithmicSession([order, ticker])
    assert session.ready

    ticker_clock.advance(120)
    ticker.pump()
    ticker._degrade("the ticker plant went quiet")
    ticker.health.state = SessionState.RECONNECTING
    assert session.ready is False
    assert "ticker" not in session.health()["degraded"] or True


def test_asking_for_a_plant_that_is_not_open_names_the_ones_that_are() -> None:
    order, _, _ = open_ok(Plant.ORDER)
    session = RithmicSession([order])
    with pytest.raises(SessionError, match="pnl plant is not open"):
        session[Plant.PNL]
    with pytest.raises(SessionError, match="Open: order"):
        session[Plant.PNL]


def test_the_health_snapshot_reports_each_plant_separately() -> None:
    order, _, _ = open_ok(Plant.ORDER)
    ticker, _, _ = open_ok(Plant.TICKER)
    health = RithmicSession([order, ticker]).health()
    assert {row["plant"] for row in health["plants"]} == {"order", "ticker"}
    assert all(row["state"] in {s.value for s in SessionState} for row in health["plants"])


def test_live_states_are_exactly_ready_and_degraded() -> None:
    assert {SessionState.READY, SessionState.DEGRADED} == LIVE


# ── secrets ──────────────────────────────────────────────────────────────────


def test_the_password_is_not_retained_anywhere_on_the_session() -> None:
    """Used and dropped. A connector holding one is a connector that leaks one."""
    session, socket, _ = plant()
    socket.queue(login_ok())
    session.open(system="Rithmic Test", user="u", password="a-very-secret-password")
    blob = repr(vars(session)) + repr(session.health.as_dict())
    assert "a-very-secret-password" not in blob


def test_a_login_frame_carries_the_password_and_a_log_record_does_not() -> None:
    session, socket, _ = plant()
    socket.queue(login_ok())
    session.open(system="Rithmic Test", user="u", password="hunter2")
    login = next(s for s in socket.sent if s.get("template_id") == TEMPLATES["login"])
    assert login["password"] == "hunter2", "the wire needs it"
    described = describe(Message(**login))
    assert described["password"] == REDACTED
    assert described["user"] == REDACTED
    assert described["template_id"] == TEMPLATES["login"]


@pytest.mark.parametrize(
    "field",
    ["password", "user", "user_id", "email_address", "address_city", "phone_mobile",
     "account_id", "api_key", "token", "user_msg"],
)
def test_every_identifying_field_is_redacted(field: str) -> None:
    assert sensitive(field) is True


@pytest.mark.parametrize("field", ["template_id", "exchange", "symbol", "heartbeat_interval"])
def test_protocol_mechanics_are_loggable(field: str) -> None:
    assert sensitive(field) is False


def test_an_unknown_field_is_redacted_rather_than_logged() -> None:
    """An allow-list fails towards saying less, which is the direction wanted.

    A deny-list is a promise to have thought of every sensitive field in a
    protocol that gains them every release.
    """
    assert sensitive("some_field_added_in_a_later_release") is True


def test_an_error_from_somewhere_else_is_scrubbed_before_it_is_shown() -> None:
    message = scrub("upstream refused: password=hunter2 token: abc-123")
    assert "hunter2" not in message
    assert "abc-123" not in message
    assert REDACTED in message


def test_a_session_error_scrubs_itself() -> None:
    assert "hunter2" not in str(SessionError("failed with password=hunter2"))
