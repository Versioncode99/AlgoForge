"""The Rithmic session: what state a connection is in, and how it got there.

This is the part of a connector that has to be right when things go wrong, and
it is written so that it can be exercised without a network, a credential or the
SDK. Everything provider-specific — which template id means "login", which
message class carries a heartbeat — arrives through `Vocabulary`, which the SDK
loader builds from the operator's own `.proto` files. The state machine, the
correlation, the backoff, the resubscription and the health snapshot are
AlgoForge's, and `tests/propdesk/test_rithmic_session.py` drives all of them
against a fake transport.

**Plants are separate connections, and that is the protocol's shape rather than
a design choice.** Rithmic's infrastructure separates ticker, order, history and
P&L; a login names which one it is joining. So a session holds one connection
per plant, each with its own state, its own heartbeat and its own reconnect —
and the desk's health snapshot reports them separately, because "connected" when
the order plant is down and the ticker plant is up is not a true answer.

**Nothing here sends an order.** `forge.propdesk.rithmic.adapter` does the
translating, and it refuses until the capabilities the session discovered say
the account can. The separation is deliberate: this file can be read in full by
somebody asking "can this thing trade by accident?" and the answer is visible.
"""

from __future__ import annotations

import random
import threading
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from forge.propdesk.rithmic.redaction import describe, scrub


class Plant(StrEnum):
    """The infrastructures a login can join.

    Named for what they carry rather than numbered, because a number in a log is
    a number somebody has to look up. The numeric `infra_type` a login sends
    comes from the vocabulary, not from here.
    """

    TICKER = "ticker"
    ORDER = "order"
    HISTORY = "history"
    PNL = "pnl"
    REPOSITORY = "repository"


class SessionState(StrEnum):
    """Where one plant's connection is.

    `DEGRADED` is separate from `RECONNECTING` on purpose. A connection that is
    up but has missed heartbeats, or whose subscriptions did not all come back,
    is still delivering — treating it as disconnected would throw away data that
    is arriving, and treating it as `READY` would claim a completeness it does
    not have.
    """

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    AUTHENTICATING = "AUTHENTICATING"
    DISCOVERING = "DISCOVERING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    RECONNECTING = "RECONNECTING"
    FAILED = "FAILED"
    STOPPING = "STOPPING"


#: States from which work may be sent. Everything else queues or refuses, and
#: the caller is told which.
LIVE: frozenset[SessionState] = frozenset({SessionState.READY, SessionState.DEGRADED})

#: Terminal for this attempt. `FAILED` needs an operator; `STOPPING` was asked
#: for. Neither reconnects on its own.
SETTLED: frozenset[SessionState] = frozenset({SessionState.FAILED, SessionState.STOPPING})


class SessionError(RuntimeError):
    """A session problem, already scrubbed of anything secret-shaped."""

    def __init__(self, message: str) -> None:
        super().__init__(scrub(message))


class Transport(Protocol):
    """The socket, abstracted so the session can be tested without one.

    Deliberately four methods. A transport that also knew how to log in would
    put half the state machine somewhere that needs a network to exercise.
    """

    def connect(self, uri: str, *, verify: Any) -> None: ...

    def send(self, payload: bytes) -> None: ...

    def receive(self, timeout: float) -> bytes | None:
        """The next frame, or `None` on timeout. Never blocks indefinitely."""
        ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class Vocabulary:
    """The wire vocabulary, loaded from the operator's SDK rather than written.

    Template ids and message classes live in licensed `.proto` files that
    AlgoForge does not redistribute, and transcribing them would be both a
    licence problem and a correctness one: 0.89.0.0 changed a scalar field into
    a repeated one and deprecated an entitlement flag, so a table copied from
    the reference guide shipping *inside* that archive would decode it wrongly.
    See `docs/ADR-0001-rithmic-transport.md`.

    A test supplies a fake, which is how every behaviour below is exercised with
    no SDK present.
    """

    #: Template id by role: "login", "logout", "heartbeat", "system_info", and
    #: the response ids they are answered with.
    templates: Mapping[str, int]
    #: `infra_type` by plant, as the protocol numbers them.
    infra: Mapping[Plant, int]
    #: Build a request message for a role, given its fields.
    build: Callable[[str, Mapping[str, Any]], Any]
    #: Turn a received frame into a message with a `template_id`.
    decode: Callable[[bytes], Any]
    #: Turn a message into a frame.
    encode: Callable[[Any], bytes]

    def template(self, role: str) -> int:
        try:
            return self.templates[role]
        except KeyError:
            raise SessionError(
                f"the loaded Rithmic vocabulary has no '{role}' template. The SDK may be "
                "a version this adapter has not been reconciled against."
            ) from None


@dataclass
class Backoff:
    """Bounded exponential backoff with jitter.

    Jitter is not decoration. Four plants reconnecting after one network blip
    reconnect *together* without it, which is the shape that collects a rate
    penalty from the far end at exactly the moment a connection is most wanted.
    """

    base: float = 0.5
    ceiling: float = 30.0
    factor: float = 2.0
    attempt: int = 0
    _random: Callable[[], float] = field(default=random.random, repr=False)

    def next_delay(self) -> float:
        raw = min(self.ceiling, self.base * (self.factor**self.attempt))
        self.attempt += 1
        # Full jitter: uniform in [0, raw]. Retains the ceiling while removing
        # the synchronisation that makes a thundering herd.
        return raw * self._random()

    def reset(self) -> None:
        self.attempt = 0


@dataclass
class PlantHealth:
    """One plant's condition, as the desk shows it."""

    plant: Plant
    state: SessionState = SessionState.DISCONNECTED
    since: datetime = field(default_factory=lambda: datetime.now(UTC))
    heartbeat_interval: float = 0.0
    last_heartbeat: datetime | None = None
    last_message: datetime | None = None
    #: Missed heartbeats since the last one arrived. Two is degraded, not down:
    #: a connection that is late is still a connection.
    missed_heartbeats: int = 0
    reconnects: int = 0
    subscriptions: int = 0
    resubscribed: int = 0
    last_error: str = ""
    sequence_gaps: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "plant": self.plant.value,
            "state": self.state.value,
            "since": self.since.isoformat(),
            "heartbeat_interval": self.heartbeat_interval,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "last_message": self.last_message.isoformat() if self.last_message else None,
            "missed_heartbeats": self.missed_heartbeats,
            "reconnects": self.reconnects,
            "subscriptions": self.subscriptions,
            "resubscribed": self.resubscribed,
            "sequence_gaps": self.sequence_gaps,
            "last_error": self.last_error,
        }


@dataclass
class Pending:
    """One request awaiting its response, and what it is waiting for.

    Rithmic answers some requests in several parts, ending with one carrying a
    completion marker. A caller that took the first part as the answer would see
    one account on a desk with nine, so a pending request collects parts and is
    not finished until the marker arrives.
    """

    role: str
    sent_at: datetime
    parts: list[Any] = field(default_factory=list)
    complete: bool = False

    def add(self, message: Any, *, final: bool) -> None:
        self.parts.append(message)
        self.complete = final


class PlantSession:
    """One connection to one plant, and everything that happens to it.

    Drives the whole lifecycle: connect, authenticate against the system the
    operator named, discover, run, notice a missed heartbeat, reconnect with
    backoff, put the subscriptions back, and log out cleanly. Every transition
    is recorded on `health`, because a desk that cannot say *why* a plant is
    degraded reports a colour rather than a fact.
    """

    def __init__(
        self,
        plant: Plant,
        *,
        vocabulary: Vocabulary,
        transport: Transport,
        uri: str,
        verify: Any,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        backoff: Backoff | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.plant = plant
        self.vocabulary = vocabulary
        self.transport = transport
        self.uri = uri
        self.verify = verify
        self.now = now
        self.backoff = backoff or Backoff()
        self.health = PlantHealth(plant=plant, since=now())
        self._lock = threading.RLock()
        self._pending: dict[str, Pending] = {}
        self._subscriptions: set[tuple[str, str]] = set()
        self._sequence: int | None = None
        self._on_event = on_event or (lambda kind, data: None)

    # ── state ────────────────────────────────────────────────────────────────
    def _enter(self, state: SessionState, *, error: str = "") -> None:
        with self._lock:
            if self.health.state is state and not error:
                return
            self.health.state = state
            self.health.since = self.now()
            if error:
                self.health.last_error = scrub(error)
        self._on_event(
            "state",
            {"plant": self.plant.value, "state": state.value, "error": self.health.last_error},
        )

    @property
    def state(self) -> SessionState:
        return self.health.state

    @property
    def live(self) -> bool:
        return self.health.state in LIVE

    # ── lifecycle ────────────────────────────────────────────────────────────
    def open(self, *, system: str, user: str, password: str) -> SessionState:
        """Connect, log in, and come up ready — or fail saying which step did.

        `password` is used and not retained: it is passed to the vocabulary's
        message builder and never stored on this object, never logged, and never
        put in an error. `test_the_password_is_not_retained_anywhere` asserts it.
        """
        self._enter(SessionState.CONNECTING)
        try:
            self.transport.connect(self.uri, verify=self.verify)
        except Exception as exc:
            self._enter(SessionState.FAILED, error=f"could not connect: {exc}")
            raise SessionError(
                f"could not connect to the {self.plant.value} plant: {exc}"
            ) from exc

        self._enter(SessionState.AUTHENTICATING)
        try:
            reply = self.request(
                "login",
                {
                    "system_name": system,
                    "user": user,
                    "password": password,
                    "infra_type": self.vocabulary.infra[self.plant],
                },
                timeout=20.0,
            )
        except SessionError:
            self._enter(SessionState.FAILED, error="authentication did not complete")
            raise

        interval = float(getattr(reply, "heartbeat_interval", 0) or 0)
        if interval <= 0:
            # Refused rather than defaulted. The interval is the server's to
            # choose, and guessing one is how a connection is dropped for being
            # quiet in a way nothing in the log explains.
            self._enter(
                SessionState.FAILED, error="the login response named no heartbeat interval"
            )
            raise SessionError(
                f"the {self.plant.value} plant's login response carried no heartbeat "
                "interval, so there is no safe rate to send at."
            )
        with self._lock:
            self.health.heartbeat_interval = interval
            self.health.last_heartbeat = self.now()
        self._enter(SessionState.DISCOVERING)
        self._enter(SessionState.READY)
        self.backoff.reset()
        return self.state

    def close(self, *, graceful: bool = True) -> None:
        """Log out and close. A graceful close tells the far end it meant it."""
        self._enter(SessionState.STOPPING)
        if graceful and self.health.state is not SessionState.DISCONNECTED:
            # Shutdown must not raise. A logout that fails is a logout the far
            # end will time out instead, which is not worth taking down a
            # shutdown path for.
            with suppress(Exception):
                self.send("logout", {})
        with suppress(Exception):
            self.transport.close()
        self._enter(SessionState.DISCONNECTED)

    # ── sending ──────────────────────────────────────────────────────────────
    def send(self, role: str, fields: Mapping[str, Any]) -> None:
        message = self.vocabulary.build(
            role, {**fields, "template_id": self.vocabulary.template(role)}
        )
        self.transport.send(self.vocabulary.encode(message))

    def request(
        self, role: str, fields: Mapping[str, Any], *, timeout: float = 10.0
    ) -> Any:
        """Send and wait for the matching response, including a multi-part one.

        Correlated by role rather than by a request id, because the protocol's
        correlation is the response's own template id. One outstanding request
        per role at a time, which is what the session's own callers need; a
        second while one is in flight is a caller error and says so rather than
        silently pairing the wrong reply to the wrong request.
        """
        with self._lock:
            if role in self._pending:
                raise SessionError(f"a '{role}' request is already in flight on this plant")
            self._pending[role] = Pending(role=role, sent_at=self.now())
        try:
            self.send(role, fields)
            deadline = self.now() + timedelta(seconds=timeout)
            # Bounded by reads as well as by the clock. `now` is injected — a
            # test freezes it, and a caller could hand in one that does not
            # advance — so a deadline alone is a loop that can fail to
            # terminate. The bound is four times the reads a real wait needs,
            # so it never ends one first.
            reads = 0
            limit = max(16, int(timeout / 0.25) * 4)
            while self.now() < deadline and reads < limit:
                reads += 1
                if not self.pump(timeout=0.25):
                    continue
                with self._lock:
                    waiting = self._pending.get(role)
                    if waiting is not None and waiting.complete:
                        del self._pending[role]
                        parts = waiting.parts
                        return parts[-1] if len(parts) == 1 else tuple(parts)
            raise SessionError(
                f"the {self.plant.value} plant did not answer '{role}' within {timeout:g}s"
            )
        finally:
            with self._lock:
                self._pending.pop(role, None)

    # ── receiving ────────────────────────────────────────────────────────────
    def pump(self, *, timeout: float = 0.25) -> Any | None:
        """Read one frame, route it, and keep the health record honest.

        Returns the decoded message, or `None` on timeout. Heartbeat accounting
        happens here rather than on a timer, so a session nobody is reading does
        not report itself healthy.
        """
        try:
            frame = self.transport.receive(timeout)
        except Exception as exc:
            self._degrade(f"read failed: {exc}")
            return None
        if frame is None:
            self._check_heartbeat()
            return None

        try:
            message = self.vocabulary.decode(frame)
        except Exception as exc:
            # A frame that will not decode is reported and dropped. Raising
            # would take down a connection over one malformed message, which is
            # a worse outcome than losing the message.
            self._on_event(
                "undecodable",
                {"plant": self.plant.value, "error": scrub(str(exc)), "bytes": len(frame)},
            )
            return None

        with self._lock:
            self.health.last_message = self.now()
        self._route(message)
        return message

    def _route(self, message: Any) -> None:
        template = int(getattr(message, "template_id", 0) or 0)
        if template == self.vocabulary.templates.get("heartbeat_response"):
            with self._lock:
                self.health.last_heartbeat = self.now()
                self.health.missed_heartbeats = 0
                recovered = self.health.state is SessionState.DEGRADED
            if recovered:
                self._enter(SessionState.READY)
            return

        self._track_sequence(message)

        for role, pending in list(self._pending.items()):
            expected = self.vocabulary.templates.get(f"{role}_response")
            if expected is not None and template == expected:
                pending.add(message, final=self._is_final(message))
                return

        self._on_event("message", {"plant": self.plant.value, **describe(message)})

    @staticmethod
    def _is_final(message: Any) -> bool:
        """Whether this part ends a multi-part response.

        Rithmic marks the end with `rq_handler_rp_code`; a part without it is a
        row. A response carrying neither is single-part, which is the common
        case and is treated as complete.
        """
        marker = getattr(message, "rq_handler_rp_code", None)
        if marker is None:
            return True
        return bool(len(marker) > 0 if hasattr(marker, "__len__") else marker)

    def _track_sequence(self, message: Any) -> None:
        """Count gaps rather than trying to repair them.

        A gap means messages were lost, and this layer cannot invent them. What
        it can do is record that the stream is incomplete, so a reconciliation
        pass knows not to trust a position derived from it.
        """
        raw = getattr(message, "sequence_number", None)
        if raw is None:
            return
        try:
            number = int(raw)
        except (TypeError, ValueError):
            return
        with self._lock:
            previous = self._sequence
            self._sequence = number
            if previous is not None and number > previous + 1:
                self.health.sequence_gaps += number - previous - 1
                gap = number - previous - 1
            else:
                gap = 0
        if gap:
            self._on_event("sequence_gap", {"plant": self.plant.value, "missing": gap})

    # ── heartbeats and recovery ──────────────────────────────────────────────
    def heartbeat(self) -> None:
        """Send one, if the interval says it is due. Cheap to call often."""
        with self._lock:
            interval = self.health.heartbeat_interval
            last = self.health.last_heartbeat
        if interval <= 0 or not self.live:
            return
        if last is not None and (self.now() - last).total_seconds() < interval / 2:
            return
        try:
            self.send("heartbeat", {})
        except Exception as exc:
            self._degrade(f"heartbeat failed: {exc}")

    def _check_heartbeat(self) -> None:
        with self._lock:
            interval = self.health.heartbeat_interval
            last = self.health.last_heartbeat
            if interval <= 0 or last is None:
                return
            overdue = (self.now() - last).total_seconds() > interval * 2
            if not overdue:
                return
            self.health.missed_heartbeats += 1
            missed = self.health.missed_heartbeats
        if missed >= 3:
            self._degrade(f"{missed} heartbeats missed on the {self.plant.value} plant")
        elif self.health.state is SessionState.READY:
            self._enter(SessionState.DEGRADED, error="a heartbeat is overdue")

    def _degrade(self, reason: str) -> None:
        if self.health.state in SETTLED:
            return
        self._enter(SessionState.DEGRADED, error=reason)

    def reconnect(
        self, *, system: str, user: str, password: str, sleep: Callable[[float], None]
    ) -> SessionState:
        """One reconnect attempt, after a jittered delay, putting state back.

        Returns the state it reached. The caller decides whether to try again,
        because "how many times" is a policy question and this is a mechanism.
        """
        if self.health.state in SETTLED:
            return self.health.state
        self._enter(SessionState.RECONNECTING)
        with self._lock:
            self.health.reconnects += 1
            wanted = set(self._subscriptions)
        sleep(self.backoff.next_delay())
        with suppress(Exception):
            self.transport.close()
        try:
            self.open(system=system, user=user, password=password)
        except SessionError:
            return self.health.state
        # The subscriptions are the reason a reconnect is not just a new
        # connection. A session that came back without them is a session
        # delivering nothing, reporting itself READY.
        restored = 0
        for exchange, symbol in sorted(wanted):
            try:
                self.subscribe(exchange, symbol)
                restored += 1
            except Exception as exc:
                self._degrade(f"could not resubscribe {exchange}/{symbol}: {exc}")
        with self._lock:
            self.health.resubscribed = restored
            incomplete = restored < len(wanted)
        if incomplete:
            self._enter(
                SessionState.DEGRADED,
                error=f"{restored} of {len(wanted)} subscriptions came back",
            )
        return self.health.state

    # ── subscriptions ────────────────────────────────────────────────────────
    def subscribe(self, exchange: str, symbol: str) -> None:
        self.send("subscribe", {"exchange": exchange, "symbol": symbol})
        with self._lock:
            self._subscriptions.add((exchange, symbol))
            self.health.subscriptions = len(self._subscriptions)

    def unsubscribe(self, exchange: str, symbol: str) -> None:
        self.send("unsubscribe", {"exchange": exchange, "symbol": symbol})
        with self._lock:
            self._subscriptions.discard((exchange, symbol))
            self.health.subscriptions = len(self._subscriptions)

    @property
    def subscriptions(self) -> frozenset[tuple[str, str]]:
        with self._lock:
            return frozenset(self._subscriptions)


class RithmicSession:
    """Every plant, as one thing the desk can ask about.

    A desk asking "is Rithmic connected?" is asking a question with four
    answers, and this returns all four. `ready` is deliberately conservative:
    every plant that was opened has to be live, because an order plant that is
    down while the ticker plant is up is not a connection somebody should be
    allowed to trade through.
    """

    def __init__(self, plants: Iterable[PlantSession]) -> None:
        self.plants: dict[Plant, PlantSession] = {p.plant: p for p in plants}

    def __getitem__(self, plant: Plant) -> PlantSession:
        try:
            return self.plants[plant]
        except KeyError:
            opened = ", ".join(sorted(p.value for p in self.plants)) or "none"
            raise SessionError(
                f"the {plant.value} plant is not open on this connection. Open: {opened}."
            ) from None

    @property
    def ready(self) -> bool:
        return bool(self.plants) and all(p.live for p in self.plants.values())

    def heartbeat(self) -> None:
        for plant in self.plants.values():
            plant.heartbeat()

    def pump(self, *, timeout: float = 0.25) -> None:
        for plant in self.plants.values():
            plant.pump(timeout=timeout)

    def close(self) -> None:
        for plant in self.plants.values():
            plant.close()

    def health(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "plants": [p.health.as_dict() for p in self.plants.values()],
            "degraded": [
                p.plant.value
                for p in self.plants.values()
                if p.state is SessionState.DEGRADED
            ],
        }
