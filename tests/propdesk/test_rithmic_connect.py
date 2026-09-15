"""The session factory, and the proof that it reaches the production path.

Two things are asserted here that no other file can assert.

**Configuration is refused, not guessed.** A gateway, a system and a user are
three things AlgoForge does not have defaults for, and the tests below check that
each absence produces its own message before a socket is opened or a password is
read. The ordering matters: an operator who has not set a gateway should not
first be told their password is missing.

**The adapter is actually wired.** `test_the_api_serves_the_real_adapter_when_the
_sdk_is_present` goes through `forge_api.propdesk.adapter_for` — the function the
service itself calls — rather than constructing a `RithmicAdapter` directly, so a
connector that existed only in its own tests would fail here.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from forge.propdesk.credentials import (
    CredentialBroker,
    CredentialRecord,
    SecretSource,
)
from forge.propdesk.identity import AuthMethod, ConnectionState, Environment, Provider
from forge.propdesk.rithmic import RithmicAdapter
from forge.propdesk.rithmic.connect import (
    GATEWAY_ENV,
    READ_ONLY_PLANTS,
    NotConfigured,
    SessionFactory,
    connector,
    gateway_for,
    system_for,
    trust_anchor,
    user_for,
    why_not,
)
from forge.propdesk.rithmic.sdk import SDK_ENV, RithmicSdk
from forge.propdesk.rithmic.session import Plant

NOW = datetime(2026, 3, 2, 14, 30, tzinfo=UTC)
PASSWORD_ENV = "ALGOFORGE_TEST_RITHMIC_PASSWORD"


# ── fixtures ─────────────────────────────────────────────────────────────────
def credential(**public: str) -> CredentialRecord:
    fields = {
        "gateway_uri": "wss://gateway.example.test/",
        "system_name": "Rithmic Test",
        "user": "operator",
    }
    fields.update(public)
    return CredentialRecord(
        credential_ref="rithmic-test",
        provider=Provider.RITHMIC,
        auth_method=AuthMethod.USERNAME_PASSWORD,
        label="Rithmic Test",
        public_fields={k: v for k, v in fields.items() if v},
        sources={"password": SecretSource(kind="env", name=PASSWORD_ENV)},
        created_at=NOW,
    )


@pytest.fixture
def broker(monkeypatch) -> CredentialBroker:
    monkeypatch.setenv(PASSWORD_ENV, "not-a-real-password")
    return CredentialBroker(now=lambda: NOW)


class FakeTransport:
    """A transport that records and answers nothing. The session never opens."""

    def __init__(self) -> None:
        self.connected: list[str] = []
        self.sent: list[bytes] = []
        self.closed = False

    def connect(self, uri: str, *, verify: Any) -> None:
        self.connected.append(uri)

    def send(self, payload: bytes) -> None:
        self.sent.append(payload)

    def receive(self, timeout: float) -> bytes | None:
        return None

    def close(self) -> None:
        self.closed = True


def fake_vocabulary():
    """A vocabulary with every role bound, built without protobuf or an SDK."""
    from forge.propdesk.rithmic.session import Vocabulary

    templates = {
        "login": 10,
        "login_response": 11,
        "logout": 12,
        "heartbeat": 18,
        "heartbeat_response": 19,
    }

    class Message:
        def __init__(self, role: str, fields: dict[str, Any]) -> None:
            self.role = role
            for key, value in fields.items():
                setattr(self, key, value)

    return Vocabulary(
        templates=templates,
        infra=dict.fromkeys(Plant, 1),
        build=lambda role, fields: Message(role, dict(fields)),
        decode=lambda payload: Message("unknown", {"template_id": 0}),
        encode=lambda message: b"frame",
    )


@pytest.fixture(autouse=True)
def no_ambient_configuration(monkeypatch):
    """No test here may pass because the machine running it has an SDK."""
    monkeypatch.delenv(SDK_ENV, raising=False)
    monkeypatch.delenv(GATEWAY_ENV, raising=False)


# ── configuration is refused, not guessed ────────────────────────────────────
def test_a_missing_gateway_names_the_setting_rather_than_a_default() -> None:
    with pytest.raises(NotConfigured) as caught:
        gateway_for(credential(gateway_uri=""))
    assert GATEWAY_ENV in str(caught.value)
    assert "does not ship one" in str(caught.value)


def test_a_gateway_may_come_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv(GATEWAY_ENV, "wss://from-the-environment.example.test/")
    assert gateway_for(credential(gateway_uri="")) == "wss://from-the-environment.example.test/"


def test_the_credential_s_gateway_wins_over_the_environment(monkeypatch) -> None:
    monkeypatch.setenv(GATEWAY_ENV, "wss://default.example.test/")
    assert gateway_for(credential()) == "wss://gateway.example.test/"


@pytest.mark.parametrize(
    "uri", ["ws://gateway.example.test/", "https://gateway.example.test/", "gateway.example.test"]
)
def test_a_gateway_that_is_not_wss_is_refused_before_a_password_is_read(uri: str) -> None:
    """The login frame carries the password; the scheme is not a preference."""
    with pytest.raises(NotConfigured, match="not a wss:// URI"):
        gateway_for(credential(gateway_uri=uri))


def test_a_credential_with_no_system_is_refused() -> None:
    with pytest.raises(NotConfigured, match="names no system"):
        system_for(credential(system_name=""))


def test_a_credential_with_no_user_is_refused() -> None:
    with pytest.raises(NotConfigured, match="names no user"):
        user_for(credential(user=""))


def test_the_gateway_is_checked_before_the_password_is_resolved(broker) -> None:
    """An operator who has not set a gateway is not sent to look at their password."""
    factory = SessionFactory(
        broker=broker,
        transport_factory=FakeTransport,
        vocabulary_factory=fake_vocabulary,
    )
    with pytest.raises(NotConfigured, match="No Rithmic gateway is configured"):
        factory(credential(gateway_uri=""))
    assert broker.accesses == [], "no credential was read to discover a missing setting"


def test_an_unresolvable_password_names_the_source_and_not_the_value(monkeypatch) -> None:
    monkeypatch.delenv(PASSWORD_ENV, raising=False)
    factory = SessionFactory(
        broker=CredentialBroker(now=lambda: NOW),
        transport_factory=FakeTransport,
        vocabulary_factory=fake_vocabulary,
    )
    with pytest.raises(NotConfigured) as caught:
        factory(credential())
    message = str(caught.value)
    assert "password could not be resolved" in message
    assert PASSWORD_ENV in message


# ── opening ──────────────────────────────────────────────────────────────────
def test_a_plant_that_does_not_come_up_closes_the_ones_that_did(broker) -> None:
    """A half-open connection reads as a working connection with no positions."""
    opened: list[FakeTransport] = []

    def transport() -> FakeTransport:
        made = FakeTransport()
        opened.append(made)
        return made

    factory = SessionFactory(
        broker=broker,
        transport_factory=transport,
        vocabulary_factory=fake_vocabulary,
    )
    # The fake transport answers nothing, so the first login times out.
    with pytest.raises(Exception, match=r"login|authenticat"):
        factory(credential())
    assert opened, "a transport was created"
    assert all(t.closed for t in opened), "every transport opened was closed again"


def test_a_read_only_connection_opens_only_the_plants_it_reads() -> None:
    """A plant nobody reads is a login, a heartbeat and a rate-limit slot."""
    assert READ_ONLY_PLANTS == (Plant.ORDER, Plant.PNL)
    assert Plant.TICKER not in READ_ONLY_PLANTS
    assert Plant.HISTORY not in READ_ONLY_PLANTS


def test_the_password_is_not_retained_on_the_factory(broker) -> None:
    factory = SessionFactory(
        broker=broker,
        transport_factory=FakeTransport,
        vocabulary_factory=fake_vocabulary,
    )
    with pytest.raises(Exception):  # noqa: B017 - the open fails; the point is what is left
        factory(credential())
    leaked = [
        name
        for name, value in vars(factory).items()
        if isinstance(value, str) and "not-a-real-password" in value
    ]
    assert leaked == []


# ── TLS ──────────────────────────────────────────────────────────────────────
def test_the_trust_anchor_prefers_the_archive_s_own_certificate(tmp_path: Path) -> None:
    """The archive ships a CA parameter file and the gateways present from it.

    Nothing below asks a TLS context to enumerate what it trusts, and that is
    the point. On Windows, pip's vendored `truststore` is injected into `ssl`,
    and what it replaces is the context *class* — so `ssl.SSLContext(...)` is
    not the stdlib class there either, and `cert_store_stats()` and
    `get_ca_certs()` both raise `NotImplementedError` on what it returns. An
    earlier version of this test built its own context to step around
    `ssl.create_default_context()` and failed anyway, for exactly that reason.

    `load_verify_locations` is the portable surface: every implementation of it,
    truststore's included, hands the file to a real OpenSSL context, so it
    returns for a certificate and raises for anything else. That distinction
    carries the whole claim, and it is exercised in both directions here — on
    the archive's anchor and on a file of prose — so a `load_verify_locations`
    that accepted whatever it was given could not leave this test green.

    What `trust_anchor` itself does with the file is pinned twice over: below,
    where the platform will still enumerate, and by
    `test_an_unreadable_trust_anchor_is_refused_rather_than_skipped`, which is
    portable and fails if the file is ignored.
    """
    import ssl

    root = tmp_path / "RProtocolAPI" / "0.89.0.0"
    (root / "proto").mkdir(parents=True)
    (root / "etc").mkdir()
    anchor = root / "etc" / "rithmic_ssl_cert_auth_params"
    anchor.write_text(_throwaway_ca())

    # The file really is a loadable certificate authority: this returns.
    ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cafile=str(anchor))

    # And that is a real distinction, not a call that accepts anything.
    prose = tmp_path / "not-a-certificate"
    prose.write_text("this is not a certificate")
    with pytest.raises(ssl.SSLError):
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cafile=str(prose))

    context = trust_anchor(RithmicSdk(root=root))
    assert context.verify_mode is ssl.CERT_REQUIRED
    try:
        loaded = context.get_ca_certs()
    except NotImplementedError:  # pragma: no cover - Windows/truststore only
        return
    assert any(
        entry.get("subject") and "forge-test-ca" in str(entry["subject"])
        for entry in loaded
    ), "the archive's anchor was loaded"


def test_an_unreadable_trust_anchor_is_refused_rather_than_skipped(tmp_path: Path) -> None:
    """Silently falling back to the system store would widen what is trusted."""
    root = tmp_path / "sdk"
    (root / "proto").mkdir(parents=True)
    (root / "etc").mkdir()
    (root / "etc" / "rithmic_ssl_cert_auth_params").write_text("this is not a certificate")
    with pytest.raises(NotConfigured, match="could not be loaded"):
        trust_anchor(RithmicSdk(root=root))


def test_a_trust_anchor_is_never_a_context_that_does_not_verify(tmp_path: Path) -> None:
    import ssl

    root = tmp_path / "sdk"
    (root / "proto").mkdir(parents=True)
    context = trust_anchor(RithmicSdk(root=root))
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True


# ── what is blocking this installation ───────────────────────────────────────
def test_why_not_names_the_sdk_first() -> None:
    assert "SDK is not installed" in why_not()


def test_why_not_names_the_gateway_once_the_sdk_and_compiler_are_present(
    monkeypatch, tmp_path: Path
) -> None:
    pytest.importorskip("grpc_tools")
    root = tmp_path / "sdk"
    (root / "proto").mkdir(parents=True)
    monkeypatch.setenv(SDK_ENV, str(root))
    assert "gateway" in why_not()


def test_why_not_is_empty_when_nothing_is_blocking(monkeypatch, tmp_path: Path) -> None:
    pytest.importorskip("grpc_tools")
    root = tmp_path / "sdk"
    (root / "proto").mkdir(parents=True)
    monkeypatch.setenv(SDK_ENV, str(root))
    monkeypatch.setenv(GATEWAY_ENV, "wss://gateway.example.test/")
    assert why_not() == ""


# ── the production path ──────────────────────────────────────────────────────
def connection(provider: Provider = Provider.RITHMIC):
    from forge.propdesk import BrokerConnection

    return BrokerConnection(
        connection_id="conn-1",
        provider=provider,
        environment=Environment.DEMO,
        label="Rithmic Test",
        credential_ref="rithmic-test",
        state=ConnectionState.DISCONNECTED,
        created_at=NOW,
        updated_at=NOW,
    )


def test_the_api_serves_the_declared_adapter_when_no_sdk_is_present(broker) -> None:
    from forge.propdesk.adapters import DeclaredAdapter
    from forge_api.propdesk import adapter_for

    adapter = adapter_for(connection(), broker=broker)
    assert isinstance(adapter, DeclaredAdapter)


def test_the_api_serves_the_real_adapter_when_the_sdk_is_present(
    broker, monkeypatch, tmp_path: Path
) -> None:
    """The wiring test: this goes through the function the service itself calls."""
    pytest.importorskip("grpc_tools")
    from forge_api.propdesk import adapter_for

    root = tmp_path / "sdk"
    (root / "proto").mkdir(parents=True)
    monkeypatch.setenv(SDK_ENV, str(root))
    monkeypatch.setenv(GATEWAY_ENV, "wss://gateway.example.test/")

    adapter = adapter_for(connection(), broker=broker)
    assert isinstance(adapter, RithmicAdapter)
    assert adapter.connect_with is not None, "the adapter was given a way to connect"
    assert adapter.environment is Environment.DEMO


def test_the_real_adapter_still_refuses_to_send(broker, monkeypatch, tmp_path: Path) -> None:
    """Being wired in is not being allowed to trade."""
    pytest.importorskip("grpc_tools")
    from forge.propdesk.fabric import AdapterUnavailable, CommandKind, OrderIntent
    from forge.propdesk.orders import Side
    from forge_api.propdesk import adapter_for

    root = tmp_path / "sdk"
    (root / "proto").mkdir(parents=True)
    monkeypatch.setenv(SDK_ENV, str(root))
    monkeypatch.setenv(GATEWAY_ENV, "wss://gateway.example.test/")

    adapter = adapter_for(connection(), broker=broker)
    intent = OrderIntent(
        intent_id="i-1",
        kind=CommandKind.PLACE,
        account_uid="acct",
        idempotency_key="key-1",
        symbol="ESZ5",
        side=Side.BUY,
        quantity=1,
        created_at=NOW,
    )
    for verb in ("place", "modify", "cancel", "flatten"):
        with pytest.raises(AdapterUnavailable, match="will not"):
            getattr(adapter, verb)(intent)


def test_a_connection_without_a_broker_never_gets_a_live_connector(
    monkeypatch, tmp_path: Path
) -> None:
    """A connector that cannot resolve a password would fail on the first click."""
    from forge.propdesk.adapters import DeclaredAdapter
    from forge_api.propdesk import adapter_for

    root = tmp_path / "sdk"
    (root / "proto").mkdir(parents=True)
    monkeypatch.setenv(SDK_ENV, str(root))
    monkeypatch.setenv(GATEWAY_ENV, "wss://gateway.example.test/")
    assert isinstance(adapter_for(connection()), DeclaredAdapter)


def test_connector_is_the_factory_the_api_uses(broker) -> None:
    built = connector(broker, workspace=None)
    assert isinstance(built, SessionFactory)
    assert built.plants == READ_ONLY_PLANTS


def test_a_session_factory_reuses_one_compiled_vocabulary(broker) -> None:
    """Compiling 155 protos per reconnect turns a blip into a visible outage."""
    calls = []

    def once():
        calls.append(1)
        return fake_vocabulary()

    factory = SessionFactory(
        broker=broker, transport_factory=FakeTransport, vocabulary_factory=once
    )
    first = factory.vocabulary()
    assert factory.vocabulary() is first
    assert len(calls) == 1


# ── nothing here leaks the environment it ran in ─────────────────────────────
def test_no_test_in_this_file_left_the_sdk_variable_set() -> None:
    assert not os.environ.get(SDK_ENV, "")


def _throwaway_ca() -> str:
    """A self-signed CA certificate, generated fresh for this test.

    Generated rather than pasted: a certificate committed to a repository is a
    certificate somebody eventually trusts. Its private key is discarded the
    moment this returns, it never reaches a socket, and it stands in for the
    archive's CA parameter file only so `load_verify_locations` has something
    real to parse.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "forge-test-ca")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2025, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2035, 1, 1, tzinfo=UTC))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode()
