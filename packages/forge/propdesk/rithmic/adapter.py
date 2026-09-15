"""Rithmic behind the desk's own interface, refusing what it has not verified.

This implements `forge.propdesk.fabric.ExecutionAdapter`, which is the only
shape the desk knows. Allocation, copy relationships, risk rules, prop-rule
checks and the order book upstream of here have never heard of Rithmic and do
not start to now: everything provider-specific is in this file, in
`session.py`, and in the vocabulary the SDK loader builds.

**What it will and will not do, stated once.**

* Read-only work — connect, discover the systems, discover accounts, take a
  snapshot, report health — runs when the SDK is present and the operator has
  supplied a credential.
* Order work is *translated and tested* and **refuses to send**. `place`,
  `modify`, `cancel` and `flatten` raise until `order_release` is enabled, which
  nothing in this repository enables. Sending a first order into the Rithmic
  Test environment is a separate step that needs a person to name the account,
  instrument, side, quantity and order type, and it is not something an adapter
  may decide it is ready for.
* Nothing is simulated. `simulated = False` here means what it means on the
  declared adapters: this produces no fills. An adapter that reported simulated
  fills while claiming to be a Rithmic connector would be the exact confusion
  the fabric's `simulated` flag exists to prevent.

**A capability this build has not verified is refused, not attempted.** The
capability record is not a wish list: it is what was observed against a stated
template version, and `CapabilityState` carries which.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from forge.propdesk.credentials import CredentialRecord
from forge.propdesk.fabric import (
    Acknowledgement,
    AdapterHealth,
    AdapterUnavailable,
    OrderIntent,
    ProviderSnapshot,
)
from forge.propdesk.identity import (
    RITHMIC_DESCRIPTOR,
    Account,
    AccountCapability,
    AccountKey,
    ConnectionState,
    Environment,
)
from forge.propdesk.orders import OrderEvent, Position
from forge.propdesk.rithmic import normalise
from forge.propdesk.rithmic.redaction import scrub
from forge.propdesk.rithmic.sdk import RithmicSdk, SdkMissing
from forge.propdesk.rithmic.session import (
    Plant,
    RithmicSession,
    SessionError,
    SessionState,
)


class Capability(StrEnum):
    """What this build claims about one Rithmic capability.

    The vocabulary the brief asks for, used literally. The distinction between
    the middle three is the whole point: code that exists, a connection that was
    made, and a capability that was *observed working* are three different
    claims, and collapsing them is how "Rithmic support" comes to mean nothing.
    """

    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    IMPLEMENTED_NOT_CONNECTED = "IMPLEMENTED_NOT_CONNECTED"
    CONNECTED_NOT_VERIFIED = "CONNECTED_NOT_VERIFIED"
    VERIFIED_READ_ONLY_IN_RITHMIC_TEST = "VERIFIED_READ_ONLY_IN_RITHMIC_TEST"
    VERIFIED_ORDER_LIFECYCLE_IN_RITHMIC_TEST = "VERIFIED_ORDER_LIFECYCLE_IN_RITHMIC_TEST"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class CapabilityState:
    """One capability, its status, and what the status rests on."""

    name: str
    status: Capability
    detail: str
    #: The SDK template version the claim was made against. A claim without one
    #: is a claim about nothing in particular.
    template_version: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.name,
            "status": self.status.value,
            "detail": self.detail,
            "template_version": self.template_version,
        }


#: What this build has, before any connection is made. Every entry here is a
#: statement that can be checked against the code, and
#: `tests/propdesk/test_rithmic_adapter.py::test_no_capability_claims_more_than
#: _the_code_does` checks them.
BASELINE: tuple[CapabilityState, ...] = (
    CapabilityState(
        "transport",
        Capability.IMPLEMENTED_NOT_CONNECTED,
        "A TLS WebSocket client written against the standard library: RFC 6455 "
        "handshake, masked binary frames, fragmentation, control frames and a "
        "bounded frame ceiling. Exercised against a real loopback socket, not a "
        "mock. It has never been pointed at a Rithmic gateway.",
    ),
    CapabilityState(
        "vocabulary",
        Capability.IMPLEMENTED_NOT_CONNECTED,
        "Template ids, message classes and the per-plant infra_type are compiled "
        "from the operator's own .proto files at run time and read out of the "
        "descriptors, so nothing is transcribed. Exercised against protos written "
        "for the test in the same shape; no part of the licensed archive is here.",
    ),
    CapabilityState(
        "connection_state_machine",
        Capability.IMPLEMENTED_NOT_CONNECTED,
        "Nine states, heartbeats at the interval the login response names, bounded "
        "jittered reconnect, subscription restoration and a graceful logout.",
    ),
    CapabilityState(
        "authentication",
        Capability.IMPLEMENTED_NOT_CONNECTED,
        "Login per plant with the operator's credential. Not verified against Rithmic: "
        "this environment has no credential and the agreements have not been accepted.",
    ),
    CapabilityState(
        "system_discovery",
        Capability.IMPLEMENTED_NOT_CONNECTED,
        "The system list is requested before login, so the operator chooses a named "
        "system rather than one being assumed.",
    ),
    CapabilityState(
        "account_discovery",
        Capability.IMPLEMENTED_NOT_CONNECTED,
        "Multi-part account responses are collected to completion and normalised, "
        "qualified by FCM and IB id so two accounts cannot collide.",
    ),
    CapabilityState(
        "order_state_mapping",
        Capability.IMPLEMENTED_NOT_CONNECTED,
        "Rithmic statuses onto the desk's lifecycle. An unmapped status refuses rather "
        "than guessing, because the readings differ by the size of a position.",
    ),
    CapabilityState(
        "position_and_pnl",
        Capability.IMPLEMENTED_NOT_CONNECTED,
        "Net quantity from the two reported sides; P&L left unset when not reported.",
    ),
    CapabilityState(
        "market_data",
        Capability.NOT_IMPLEMENTED,
        "Subscription and unsubscription are wired for resubscribe-after-reconnect; "
        "no tick handling is implemented and none is claimed.",
    ),
    CapabilityState(
        "order_release",
        Capability.NOT_IMPLEMENTED,
        "Translation exists and is tested against fixtures. Sending is refused: a first "
        "order in Rithmic Test is a separate step a person confirms, naming the account, "
        "instrument, side, quantity and order type.",
    ),
    CapabilityState(
        "rithmic_test_verification",
        Capability.BLOCKED,
        "Needs a Rithmic Test credential and a prior interactive login through R | Trader "
        "or R | Trader Pro to accept the required agreements. Neither is available here, "
        "and neither may be bypassed.",
    ),
)


@dataclass
class RithmicAdapter:
    """The desk's Rithmic connector.

    Constructed with a session factory rather than building its own, so the
    tests drive the whole adapter over a fake transport with no SDK, no network
    and no credential — which is the only way the refusals can be asserted.
    """

    environment: Environment = Environment.DEMO
    workspace: Path | None = None
    #: Builds a session for a credential. `None` means the adapter has no way to
    #: connect, which is the state of every installation without the SDK.
    connect_with: Callable[[CredentialRecord], RithmicSession] | None = None
    descriptor: Any = field(default=RITHMIC_DESCRIPTOR)
    #: Not a simulator. This produces no fills of any kind.
    simulated: bool = False

    _session: RithmicSession | None = field(default=None, init=False, repr=False)
    _credential: str = field(default="", init=False, repr=False)
    _events: list[tuple[str, OrderEvent]] = field(default_factory=list, init=False, repr=False)
    _unmapped: list[str] = field(default_factory=list, init=False, repr=False)
    _last_error: str = field(default="", init=False, repr=False)

    # ── what this build claims ───────────────────────────────────────────────
    def capabilities(self) -> tuple[CapabilityState, ...]:
        """The capability matrix, with the template version it was read against.

        A connection upgrades nothing on its own. `CONNECTED_NOT_VERIFIED` is as
        far as connecting gets a capability: verification is an observation of
        the capability working, and this method cannot make one.
        """
        try:
            manifest = RithmicSdk.discover(self.workspace).manifest()
            version = manifest.template_version
        except SdkMissing:
            version = ""
        connected = self._session is not None and self._session.ready
        rows: list[CapabilityState] = []
        for entry in BASELINE:
            status = entry.status
            if connected and status is Capability.IMPLEMENTED_NOT_CONNECTED:
                status = Capability.CONNECTED_NOT_VERIFIED
            rows.append(
                CapabilityState(
                    name=entry.name,
                    status=status,
                    detail=entry.detail,
                    template_version=version,
                )
            )
        return tuple(rows)

    def _refuse(self, verb: str, why: str) -> AdapterUnavailable:
        return AdapterUnavailable(scrub(f"AlgoForge will not {verb} through Rithmic: {why}"))

    def _require_session(self, verb: str) -> RithmicSession:
        if self._session is None:
            raise self._refuse(verb, "no connection is open.")
        if not self._session.ready:
            degraded = ", ".join(self._session.health()["degraded"]) or "the connection"
            raise self._refuse(
                verb,
                f"{degraded} is not ready. A snapshot taken through a degraded plant "
                "would be incomplete and would read as complete.",
            )
        return self._session

    # ── connection ───────────────────────────────────────────────────────────
    def connect(self, credential: CredentialRecord) -> ConnectionState:
        if self.connect_with is None:
            try:
                RithmicSdk.discover(self.workspace)
            except SdkMissing as exc:
                raise self._refuse("connect", str(exc)) from exc
            raise self._refuse(
                "connect",
                "the SDK is present but no transport was configured for this adapter. "
                "Nothing was sent and no credential was read.",
            )
        try:
            self._session = self.connect_with(credential)
        except SessionError as exc:
            self._last_error = scrub(str(exc))
            raise self._refuse("connect", str(exc)) from exc
        self._credential = credential.credential_ref
        return ConnectionState.LIVE if self._session.ready else ConnectionState.DEGRADED

    def authenticate(self, credential: CredentialRecord) -> ConnectionState:
        # Connecting *is* authenticating in this protocol: a plant login carries
        # the credential, so a separate step would be a second login.
        return self.connect(credential)

    def disconnect(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    # ── reading ──────────────────────────────────────────────────────────────
    def discover_accounts(self) -> tuple[Account, ...]:
        session = self._require_session("discover accounts")
        order = session[Plant.ORDER]
        try:
            reply = order.request("account_list", {}, timeout=20.0)
        except SessionError as exc:
            raise self._refuse("discover accounts", str(exc)) from exc
        rows = reply if isinstance(reply, tuple) else (reply,)
        found: list[Account] = []
        for row in rows:
            try:
                found.append(
                    normalise.account(
                        row,
                        connection_id=self._credential or "rithmic",
                        credential_ref=self._credential or "rithmic",
                        environment=self.environment,
                    )
                )
            except normalise.NormalisationRefused as exc:
                # One unusable row does not discard the rest. It is recorded so
                # a desk showing eight of nine accounts can say why.
                self._unmapped.append(scrub(str(exc)))
        return tuple(found)

    def discover_capabilities(self, key: AccountKey) -> AccountCapability:
        """What the provider says this account may do.

        Everything unknown, and deliberately. `forge.propdesk.policy` treats an
        unrecorded permission as *not granted*, and an adapter that filled these
        in from nothing would turn "we have not asked" into "the firm allows
        it" — which is the one direction this must never fail in.
        """
        self._require_session("read account capabilities")
        return AccountCapability()

    def snapshot(self, account_uid: str) -> ProviderSnapshot:
        session = self._require_session("take a snapshot")
        pnl = session[Plant.PNL]
        try:
            reply = pnl.request("position_list", {"account_id": account_uid}, timeout=20.0)
        except SessionError as exc:
            raise self._refuse("take a snapshot", str(exc)) from exc
        rows = reply if isinstance(reply, tuple) else (reply,)
        positions: list[Position] = []
        refused = 0
        for row in rows:
            try:
                positions.append(normalise.position(row, account_uid=account_uid))
            except normalise.NormalisationRefused as exc:
                refused += 1
                self._unmapped.append(scrub(str(exc)))
        return ProviderSnapshot(
            account_uid=account_uid,
            taken_at=datetime.now(UTC),
            positions=tuple(positions),
            # A snapshot missing rows is not a complete snapshot, and
            # reconciliation *replaces* local state with one. Saying so is what
            # stops a position the provider reported, and this build could not
            # read, from being treated as closed.
            complete=refused == 0,
            note=(
                "" if refused == 0
                else f"{refused} position row(s) could not be read and are not in this snapshot"
            ),
        )

    def poll(self) -> tuple[tuple[str, OrderEvent], ...]:
        """Events since the last call, already normalised.

        Pumps every plant first, so a caller polling this is also what keeps the
        heartbeats going — a session nobody reads is a session that times out.
        """
        if self._session is None:
            return ()
        self._session.pump(timeout=0.05)
        self._session.heartbeat()
        drained, self._events = tuple(self._events), []
        return drained

    def observe(self, message: Any) -> None:
        """Take one order notification into the pending queue.

        Separated from `poll` so the session's message routing can hand
        notifications in without knowing what the desk does with them, and so a
        test can drive normalisation without a transport.
        """
        try:
            self._events.append(normalise.order_event(message))
        except normalise.NormalisationRefused as exc:
            self._unmapped.append(scrub(str(exc)))

    # ── writing: refused ─────────────────────────────────────────────────────
    def _refuse_to_send(self, verb: str) -> AdapterUnavailable:
        return AdapterUnavailable(
            f"AlgoForge will not {verb} through Rithmic. The translation is written and "
            "tested against fixtures, and sending is switched off: a first order in the "
            "Rithmic Test environment is a separate step a person confirms, naming the "
            "account, instrument, side, quantity, order type and price fields, and how it "
            "will be cancelled. Nothing was sent."
        )

    def place(self, intent: OrderIntent) -> Acknowledgement:
        raise self._refuse_to_send("place an order")

    def modify(self, intent: OrderIntent) -> Acknowledgement:
        raise self._refuse_to_send("modify an order")

    def cancel(self, intent: OrderIntent) -> Acknowledgement:
        raise self._refuse_to_send("cancel an order")

    def flatten(self, intent: OrderIntent) -> Acknowledgement:
        raise self._refuse_to_send("flatten a position")

    # ── health ───────────────────────────────────────────────────────────────
    def health(self) -> AdapterHealth:
        if self._session is None:
            return AdapterHealth(
                provider=self.descriptor.provider,
                connection_id=self._credential,
                state=ConnectionState.DISCONNECTED,
                last_error=self._last_error or "no Rithmic connection is open",
            )
        detail = self._session.health()
        degraded = bool(detail["degraded"])
        return AdapterHealth(
            provider=self.descriptor.provider,
            connection_id=self._credential,
            state=(
                ConnectionState.LIVE
                if self._session.ready and not degraded
                else ConnectionState.DEGRADED
            ),
            last_error=self._last_error
            or (f"degraded plants: {', '.join(detail['degraded'])}" if degraded else ""),
        )

    def as_dict(self) -> dict[str, Any]:
        """Everything the desk shows about this adapter, with nothing implied."""
        session = self._session.health() if self._session is not None else None
        return {
            "descriptor": self.descriptor.as_dict(),
            # Two claims, not one. This build can read from Rithmic and cannot
            # execute at it, and a single "implemented" flag could only say one
            # of those.
            "live_connector_implemented": False,
            "read_only_connector_implemented": True,
            "can_send_orders": False,
            "simulated": False,
            "environment": self.environment.value,
            "session": session,
            "capabilities": [row.as_dict() for row in self.capabilities()],
            "unmapped": list(self._unmapped[-20:]),
            "states": [state.value for state in SessionState],
        }
