"""Secrets: what is stored, what is not, and what a redaction covers.

The central claim these tests defend is that AlgoForge stores credential
*metadata* and never credential material — asserted against the database on
disk, not against an intention.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pytest
from forge.propdesk import (
    REDACTED,
    AuthMethod,
    CredentialBroker,
    CredentialError,
    CredentialRecord,
    EnvironmentResolver,
    PropDeskStore,
    Provider,
    Secret,
    SecretSource,
    SessionResolver,
    new_credential_ref,
    redact,
    required_roles,
)
from pydantic import ValidationError

NOW = datetime(2026, 9, 11, 15, 30, tzinfo=UTC)
PASSWORD = "correct-horse-battery-staple"


def record(**overrides) -> CredentialRecord:
    base: dict = {
        "credential_ref": "cred-1",
        "provider": Provider.RITHMIC,
        "auth_method": AuthMethod.USERNAME_PASSWORD,
        "label": "My Rithmic login",
        "public_fields": {"system": "Rithmic Paper Trading", "username": "trader1"},
        "sources": {"password": SecretSource(kind="env", name="RITHMIC_PASSWORD")},
        "created_at": NOW,
    }
    return CredentialRecord(**{**base, **overrides})


# ── the Secret wrapper ───────────────────────────────────────────────────────


def test_a_secret_does_not_print_itself() -> None:
    """The overwhelmingly common leak is an f-string somebody wrote in a hurry."""
    secret = Secret(PASSWORD)
    assert PASSWORD not in repr(secret)
    assert PASSWORD not in str(secret)
    assert PASSWORD not in f"{secret}"
    assert REDACTED in repr(secret)


def test_a_secret_reveals_only_through_a_greppable_name() -> None:
    assert Secret(PASSWORD).reveal() == PASSWORD


def test_a_secret_refuses_to_be_serialised() -> None:
    """A loud failure at the mistake beats a quiet redaction nobody notices."""
    import pickle

    with pytest.raises(CredentialError, match="must not be pickled"):
        pickle.dumps(Secret(PASSWORD))


def test_a_secret_fingerprint_identifies_without_revealing() -> None:
    first, second = Secret(PASSWORD), Secret(PASSWORD)
    assert first.fingerprint == second.fingerprint
    assert PASSWORD not in first.fingerprint
    assert Secret("other").fingerprint != first.fingerprint


def test_an_empty_secret_is_refused() -> None:
    with pytest.raises(CredentialError, match="non-empty"):
        Secret("")


# ── redaction ────────────────────────────────────────────────────────────────


def test_redaction_reaches_nested_structures() -> None:
    payload = {
        "connections": [
            {"label": "x", "credential": {"password": PASSWORD, "username": "trader"}}
        ],
        "api_key": "sk-live-12345",
    }
    cleaned = redact(payload)
    assert PASSWORD not in json.dumps(cleaned)
    assert "sk-live-12345" not in json.dumps(cleaned)
    assert cleaned["connections"][0]["credential"]["username"] == "trader"


def test_redaction_matches_on_a_substring_of_the_field_name() -> None:
    """The field that leaks is always the one nobody added to an exact-match list."""
    cleaned = redact({"tradovate_refresh_token": "abc", "my_api_key_v2": "def"})
    assert cleaned == {"tradovate_refresh_token": REDACTED, "my_api_key_v2": REDACTED}


def test_redaction_preserves_a_null_rather_than_inventing_a_value() -> None:
    assert redact({"password": None}) == {"password": None}


# ── records store pointers, not secrets ──────────────────────────────────────


def test_a_public_field_named_like_a_secret_is_refused() -> None:
    with pytest.raises(ValidationError, match="must not be stored as a public field"):
        record(public_fields={"password": PASSWORD})


def test_a_record_names_where_its_secret_lives_rather_than_holding_it() -> None:
    item = record()
    assert item.sources["password"].name == "RITHMIC_PASSWORD"
    assert PASSWORD not in json.dumps(item.model_dump(mode="json"))
    assert item.describe_requirements() == (
        "password: environment variable RITHMIC_PASSWORD",
    )


def test_an_environment_source_must_name_a_plausible_variable() -> None:
    with pytest.raises(ValidationError):
        SecretSource(kind="env", name="not a variable")
    with pytest.raises(ValidationError, match="must name its variable"):
        SecretSource(kind="env")


def test_a_stored_credential_holds_no_secret_value(tmp_path, monkeypatch) -> None:
    """Read back from the database on disk, not from the object in memory."""
    monkeypatch.setenv("RITHMIC_PASSWORD", PASSWORD)
    store = PropDeskStore(tmp_path / "desk.db")
    store.save_credential(record())

    with sqlite3.connect(tmp_path / "desk.db") as db:
        rows = db.execute("SELECT payload FROM credentials").fetchall()
    dumped = " ".join(row[0] for row in rows)
    assert PASSWORD not in dumped
    assert "RITHMIC_PASSWORD" in dumped  # the pointer is stored; the value is not


def test_no_table_in_the_desk_database_has_a_secret_column(tmp_path) -> None:
    store = PropDeskStore(tmp_path / "desk.db")
    with sqlite3.connect(store.path) as db:
        tables = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        columns = {
            column[1].lower()
            for table in tables
            for column in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
    for marker in ("password", "secret", "api_key", "token"):
        assert not any(marker in column for column in columns), marker


# ── resolution ───────────────────────────────────────────────────────────────


def test_a_missing_environment_variable_names_itself(monkeypatch) -> None:
    monkeypatch.delenv("RITHMIC_PASSWORD", raising=False)
    broker = CredentialBroker(environment=EnvironmentResolver(), now=lambda: NOW)
    with pytest.raises(CredentialError, match="RITHMIC_PASSWORD is not set"):
        broker.resolve(record(), "password")


def test_a_blank_environment_variable_is_not_a_password(monkeypatch) -> None:
    """An empty string sent to a provider as a password locks the account out."""
    monkeypatch.setenv("RITHMIC_PASSWORD", "")
    broker = CredentialBroker(environment=EnvironmentResolver(), now=lambda: NOW)
    with pytest.raises(CredentialError, match="is not set"):
        broker.resolve(record(), "password")


def test_a_present_variable_resolves(monkeypatch) -> None:
    monkeypatch.setenv("RITHMIC_PASSWORD", PASSWORD)
    broker = CredentialBroker(environment=EnvironmentResolver(), now=lambda: NOW)
    assert broker.resolve(record(), "password").reveal() == PASSWORD


def test_an_undeclared_role_is_refused_with_what_is_declared() -> None:
    broker = CredentialBroker(now=lambda: NOW)
    with pytest.raises(CredentialError, match="It declares: password"):
        broker.resolve(record(), "api_key")


def test_availability_reports_what_is_missing_before_a_connection_is_attempted(
    monkeypatch,
) -> None:
    monkeypatch.delenv("RITHMIC_PASSWORD", raising=False)
    broker = CredentialBroker(environment=EnvironmentResolver(), now=lambda: NOW)
    available, missing = broker.available(record())
    assert available is False
    assert "RITHMIC_PASSWORD" in missing[0]


def test_every_resolution_is_recorded_without_the_value(monkeypatch) -> None:
    monkeypatch.setenv("RITHMIC_PASSWORD", PASSWORD)
    broker = CredentialBroker(environment=EnvironmentResolver(), now=lambda: NOW)
    broker.resolve(record(), "password")
    assert len(broker.accesses) == 1
    assert broker.accesses[0]["role"] == "password"
    assert broker.accesses[0]["ok"] is True
    assert PASSWORD not in json.dumps(broker.accesses)


def test_a_failed_resolution_is_recorded_too(monkeypatch) -> None:
    """A log holding only successes cannot show forty failed attempts."""
    monkeypatch.delenv("RITHMIC_PASSWORD", raising=False)
    broker = CredentialBroker(environment=EnvironmentResolver(), now=lambda: NOW)
    with pytest.raises(CredentialError):
        broker.resolve(record(), "password")
    assert broker.accesses[0]["ok"] is False


# ── session-held secrets ─────────────────────────────────────────────────────


def test_a_session_secret_is_never_written_anywhere() -> None:
    session = SessionResolver()
    item = record(sources={"password": SecretSource(kind="prompt")})
    broker = CredentialBroker(session=session, now=lambda: NOW)

    with pytest.raises(CredentialError, match="not stored"):
        broker.resolve(item, "password")

    session.supply(item.credential_ref, "password", PASSWORD)
    assert broker.resolve(item, "password").reveal() == PASSWORD
    assert PASSWORD not in json.dumps(item.model_dump(mode="json"))


def test_forgetting_a_credential_drops_every_role_it_held() -> None:
    session = SessionResolver()
    session.supply("cred-1", "password", PASSWORD)
    session.supply("cred-1", "api_key", "k")
    session.supply("cred-2", "password", "other")
    assert session.forget("cred-1") == 2
    assert session.holds("cred-1", "password") is False
    assert session.holds("cred-2", "password") is True


def test_the_environment_resolver_refuses_a_prompt_source() -> None:
    broker = CredentialBroker(now=lambda: NOW)
    item = record(sources={"password": SecretSource(kind="prompt")})
    with pytest.raises(CredentialError, match="not stored"):
        broker.resolve(item, "password")


# ── provider requirements ────────────────────────────────────────────────────


def test_each_authentication_method_declares_the_roles_it_needs() -> None:
    assert required_roles(AuthMethod.USERNAME_PASSWORD) == ("password",)
    assert required_roles(AuthMethod.API_KEY) == ("api_key",)
    assert required_roles(AuthMethod.OAUTH) == ("refresh_token",)
    assert required_roles(AuthMethod.NONE) == ()


def test_oauth_stores_a_refresh_capability_rather_than_a_password() -> None:
    """Delegated auth is preferred wherever a provider offers it."""
    assert "password" not in required_roles(AuthMethod.OAUTH)


def test_a_credential_reference_is_deterministic() -> None:
    first = new_credential_ref(Provider.RITHMIC, "My login", NOW)
    assert first == new_credential_ref(Provider.RITHMIC, "My login", NOW)
    assert first != new_credential_ref(Provider.TRADOVATE, "My login", NOW)
