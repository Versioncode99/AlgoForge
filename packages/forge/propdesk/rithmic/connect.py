"""Build a live `RithmicSession` from an SDK, a credential and a gateway.

The adapter takes a `connect_with` callable rather than building its own session,
which is what let every behaviour in `adapter.py` be tested with a fake. This is
the real one, and it is the only place in the package where the four pieces meet:
the operator's SDK (for the vocabulary), the credential broker (for the
password), the WebSocket transport (for the socket) and the gateway URI.

**Three things it refuses rather than assumes.**

*The gateway.* AlgoForge does not ship a Rithmic host name. The endpoints differ
per system and per environment, they are in the operator's own documentation, and
a connector that guessed one would be sending a login — with a password on it —
to an address nobody chose. It comes from the credential's public fields or from
the environment, and its absence is an error naming the setting.

*The system.* Rithmic logins name a system. There is no sensible default, so a
credential that does not name one is refused before a socket is opened.

*The password.* Resolved through `CredentialBroker` at the moment of connecting
and never held: it goes from the broker into `PlantSession.open`, which passes it
to the vocabulary's message builder and does not retain it either. Nothing in
this module stores it, logs it, or puts it in an exception.
"""

from __future__ import annotations

import os
import ssl
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from forge.propdesk.credentials import CredentialBroker, CredentialError, CredentialRecord
from forge.propdesk.rithmic.sdk import RithmicSdk, SdkMissing
from forge.propdesk.rithmic.session import (
    Plant,
    PlantSession,
    RithmicSession,
    SessionError,
    Transport,
    Vocabulary,
)
from forge.propdesk.rithmic.transport import WebSocketTransport
from forge.propdesk.rithmic.vocabulary import VocabularyUnavailable
from forge.propdesk.rithmic.vocabulary import build as build_vocabulary

#: Where the gateway URI may be configured when the credential does not carry
#: one. A URI is not a secret — it is a host name the operator reads out of
#: Rithmic's own documentation — so it lives beside the SDK path rather than in
#: the credential broker.
GATEWAY_ENV = "ALGOFORGE_RITHMIC_GATEWAY"

#: Which plants a read-only connection opens. The ticker and history plants are
#: not opened: this build reads accounts and positions, and a plant nobody reads
#: is a login, a heartbeat and a rate-limit slot spent on nothing.
READ_ONLY_PLANTS: tuple[Plant, ...] = (Plant.ORDER, Plant.PNL)


class NotConfigured(SessionError):
    """A connection was asked for and something it needs was not set.

    Separate from an authentication failure on purpose. "You have not told me
    which gateway" and "Rithmic rejected your password" send the operator to
    entirely different places, and one error type for both sends them to the
    wrong one half the time.
    """


def gateway_for(credential: CredentialRecord) -> str:
    """The URI to connect to, from the credential or the environment.

    Checked for scheme here rather than left to the transport, so a misconfigured
    gateway is refused before a credential is resolved — there is no reason to
    read a password in order to find out the address is wrong.
    """
    uri = (credential.public_fields.get("gateway_uri") or "").strip()
    if not uri:
        uri = os.environ.get(GATEWAY_ENV, "").strip()
    if not uri:
        raise NotConfigured(
            "No Rithmic gateway is configured. AlgoForge does not ship one: the "
            "endpoint differs per system and environment and is in your own Rithmic "
            f"documentation. Set it as the credential's `gateway_uri`, or as {GATEWAY_ENV}."
        )
    if not uri.startswith("wss://"):
        raise NotConfigured(
            "the configured Rithmic gateway is not a wss:// URI. The login frame "
            "carries your password, so a plaintext or non-WebSocket endpoint is "
            "refused rather than attempted."
        )
    return uri


def system_for(credential: CredentialRecord) -> str:
    system = (credential.public_fields.get("system_name") or "").strip()
    if not system:
        raise NotConfigured(
            "this Rithmic credential names no system. A login must name one — "
            "`RequestRithmicSystemInfo` lists what your account may use — and there is "
            "no default worth guessing. Set it as the credential's `system_name`."
        )
    return system


def user_for(credential: CredentialRecord) -> str:
    user = (credential.public_fields.get("user") or "").strip()
    if not user:
        raise NotConfigured(
            "this Rithmic credential names no user. Set it as the credential's `user`; "
            "a username is not a secret and is stored, the password is neither."
        )
    return user


def trust_anchor(sdk: RithmicSdk) -> ssl.SSLContext:
    """A verifying TLS context, anchored on the archive's CA parameter file.

    The archive ships its own trust anchor, and Rithmic's gateways present
    certificates from it. Falling back to the system store when the file is
    absent is deliberate and is the *stricter* of the two failure modes: it
    verifies against a smaller set, so a connection that should not be trusted
    fails rather than being waved through.
    """
    context = ssl.create_default_context()
    certificate = sdk.certificate
    if certificate.exists():
        try:
            context.load_verify_locations(cafile=str(certificate))
        except ssl.SSLError as exc:
            raise NotConfigured(
                f"the SDK's TLS trust anchor at {certificate} could not be loaded: {exc}"
            ) from exc
    return context


@dataclass
class SessionFactory:
    """Builds a `RithmicSession` for one credential, on demand.

    Holds the SDK and the compiled vocabulary because compiling 155 `.proto`
    files takes seconds and doing it per reconnect would turn a network blip into
    a visible outage. It holds no credential and no secret.
    """

    broker: CredentialBroker
    workspace: Path | None = None
    plants: Sequence[Plant] = READ_ONLY_PLANTS
    #: Injected so the tests drive the whole factory with no socket. Defaults to
    #: the real WebSocket client.
    transport_factory: Callable[[], Transport] = WebSocketTransport
    #: Injected likewise; defaults to compiling the operator's SDK.
    vocabulary_factory: Callable[[], Vocabulary] | None = None
    on_event: Callable[[str, dict[str, Any]], None] | None = None

    _vocabulary: Vocabulary | None = field(default=None, init=False, repr=False)

    def vocabulary(self) -> Vocabulary:
        if self._vocabulary is None:
            if self.vocabulary_factory is not None:
                self._vocabulary = self.vocabulary_factory()
            else:
                try:
                    self._vocabulary = build_vocabulary(workspace=self.workspace)
                except SdkMissing as exc:  # pragma: no cover - build() re-raises as below
                    raise NotConfigured(str(exc)) from exc
        return self._vocabulary

    def __call__(self, credential: CredentialRecord) -> RithmicSession:
        """Open every plant, or close the ones that opened and raise.

        A half-open connection is not a degraded connection: the desk's `ready`
        is all-or-nothing precisely because an order plant that is up while the
        PnL plant is down looks like a working connection with no positions.
        """
        gateway = gateway_for(credential)
        system = system_for(credential)
        user = user_for(credential)
        vocabulary = self.vocabulary()
        verify = self._verify()

        try:
            secret = self.broker.resolve(credential, "password")
        except CredentialError as exc:
            # The broker's message names the missing source, not the value.
            raise NotConfigured(f"the Rithmic password could not be resolved: {exc}") from exc

        opened: list[PlantSession] = []
        try:
            for plant in self.plants:
                session = PlantSession(
                    plant,
                    vocabulary=vocabulary,
                    transport=self.transport_factory(),
                    uri=gateway,
                    verify=verify,
                    on_event=self.on_event,
                )
                # Tracked before it is opened, not after: a plant that fails
                # during `open` may already have a connected socket, and
                # appending afterwards would leave exactly that one behind.
                opened.append(session)
                session.open(system=system, user=user, password=secret.reveal())
        except Exception:
            self._close(opened)
            raise
        return RithmicSession(opened)

    def _verify(self) -> Any:
        if self.vocabulary_factory is not None and self.workspace is None:
            # A test-injected vocabulary means there may be no SDK on disk; the
            # transport is injected too in that case, so there is nothing to
            # anchor and nothing that reaches TLS.
            return None
        try:
            return trust_anchor(RithmicSdk.discover(self.workspace))
        except SdkMissing as exc:
            raise NotConfigured(str(exc)) from exc

    @staticmethod
    def _close(sessions: Iterable[PlantSession]) -> None:
        for session in sessions:
            try:
                session.close()
            except Exception:
                continue


def connector(
    broker: CredentialBroker,
    *,
    workspace: Path | None = None,
    plants: Sequence[Plant] = READ_ONLY_PLANTS,
) -> SessionFactory:
    """The factory an adapter uses in production."""
    return SessionFactory(broker=broker, workspace=workspace, plants=plants)


def why_not(workspace: Path | None = None) -> str:
    """What stands between this installation and a Rithmic connection, or "".

    Answered as one sentence the interface can show. The order is the order the
    operator has to fix them in: the SDK, then the compiler, then the gateway.
    """
    from forge.propdesk.rithmic.vocabulary import available

    try:
        RithmicSdk.discover(workspace)
    except SdkMissing:
        return (
            "the operator's R | Protocol SDK is not installed; AlgoForge does not ship "
            "one, and the vocabulary is read from its .proto files"
        )
    ready, missing = available()
    if not ready:
        return missing
    if not os.environ.get(GATEWAY_ENV, "").strip():
        return (
            "no default Rithmic gateway is configured, so a connection needs one on the "
            f"credential ({GATEWAY_ENV} sets a default)"
        )
    return ""


__all__ = [
    "GATEWAY_ENV",
    "READ_ONLY_PLANTS",
    "NotConfigured",
    "SessionFactory",
    "VocabularyUnavailable",
    "connector",
    "gateway_for",
    "system_for",
    "trust_anchor",
    "user_for",
    "why_not",
]
