"""Secrets, and the discipline of never holding one longer than necessary.

The threat model the research sets out is specific: a copy-trading system is an
attractive target precisely because it holds order authority over every account
it touches, and one of the two products studied must hold the operator's Rithmic
*password* — there is no delegated auth for that provider — while the other left
a legacy plaintext log on disk containing account names and order events. Both
vendors' security claims were unverified.

So this module is built around one rule: **AlgoForge stores credential
metadata; it does not store credential material.**

A `CredentialRecord` says which provider a credential is for, what kind of
secret it is, and *where the secret comes from* — the name of an environment
variable, or a line in the operator's key file, both of which already exist in
this application for market-data keys. The record is what goes in the database,
the audit log, an export or a support bundle, and there is nothing sensitive in
it. The secret itself is read at the moment of use and wrapped in `Secret`,
whose `__repr__` and `__str__` are the redaction rather than a convention
somebody has to remember.

**What this deliberately is not.** It is not an encrypted vault with a master
password, and it does not integrate with an OS keychain. Both would be better;
both need a dependency this repository does not have and a key-management story
it has not earned. Pretending otherwise — a `cipher = base64(secret)` with a
reassuring name — would be worse than saying plainly that the secret lives where
the operator already keeps their provider keys, which is what
`EnvironmentResolver` does.

`redact` is exported and used at every serialisation boundary, and
`tests/propdesk/test_credentials.py` asserts that a record's JSON contains no
value that looks like a secret.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from pydantic import Field, model_validator

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel
from forge.propdesk.identity import AuthMethod, Provider

#: What a redacted value looks like. One constant so a test can search for it.
REDACTED: Final = "[redacted]"

#: Field names whose *values* must never be serialised, whatever they are
#: nested inside. Matched case-insensitively on a substring, because the field
#: that leaks is always the one nobody added to an exact-match list.
SENSITIVE_KEYS: Final[tuple[str, ...]] = (
    "password",
    "secret",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "credential_value",
    "private_key",
    "passphrase",
    "webhook_secret",
)


class CredentialError(Exception):
    """A credential could not be resolved. Nothing was attempted with it."""


class Secret:
    """A string that will not print itself.

    Not encryption and not a security boundary — an attacker with the process
    has the process. It is a boundary against *accident*: the overwhelmingly
    common way a credential reaches a log is an f-string somebody wrote in a
    hurry, and this makes that f-string produce `[redacted]` instead.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise CredentialError("a secret must be a non-empty string")
        self._value = value

    def reveal(self) -> str:
        """The only way out. Named so it is greppable at every call site."""
        return self._value

    @property
    def length(self) -> int:
        return len(self._value)

    @property
    def fingerprint(self) -> str:
        """A stable, non-reversing handle.

        Useful for "is this the same key as last time" without ever comparing or
        showing the key. Truncated because a full digest of a short secret is a
        target for a dictionary attack, and nothing here needs more than
        identity.
        """
        return stable_id("secret", {"v": self._value})[:20]

    def __repr__(self) -> str:
        return f"Secret({REDACTED}, {self.length} chars)"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and self._value == other._value

    def __hash__(self) -> int:
        return hash(("Secret", self._value))

    # Refusing serialisation outright is stronger than redacting it: a `Secret`
    # that appears in a model somebody dumps should be a loud failure at the
    # point of the mistake rather than a quiet `[redacted]` nobody notices is
    # missing.
    def __reduce__(self) -> Any:
        raise CredentialError("a Secret must not be pickled or serialised")


def redact(value: Any, *, keys: tuple[str, ...] = SENSITIVE_KEYS) -> Any:
    """Deep-copy a structure with every sensitive value replaced.

    Applied at serialisation boundaries — the API, the audit log, an export —
    rather than at the point each value is created, because the boundary is
    finite and countable and the creation sites are not.
    """
    if isinstance(value, Secret):
        return REDACTED
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for name, item in value.items():
            lowered = str(name).lower()
            if any(marker in lowered for marker in keys):
                out[str(name)] = REDACTED if item is not None else None
            else:
                out[str(name)] = redact(item, keys=keys)
        return out
    if isinstance(value, list | tuple):
        return [redact(item, keys=keys) for item in value]
    return value


def redact_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """`redact` over an object, typed as one.

    `redact` walks anything, so its return type is genuinely `Any`. Almost every
    caller is redacting a dictionary and wants a dictionary back, and a cast at
    each of those call sites would be a cast nobody checks.
    """
    redacted = redact(dict(value))
    if not isinstance(redacted, dict):  # pragma: no cover - redact preserves shape
        raise CredentialError("redaction did not return a mapping")
    return redacted


class SecretSource(FrozenModel):
    """Where the secret lives. A pointer, never the thing pointed at."""

    #: `env` reads an environment variable; `prompt` means the operator supplies
    #: it per session and nothing is stored anywhere.
    kind: str = Field(pattern=r"^(env|prompt)$")
    #: For `env`, the variable name. Empty for `prompt`.
    name: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def _named_when_needed(self) -> SecretSource:
        if self.kind == "env" and not self.name.strip():
            raise ValueError("an environment-backed secret must name its variable")
        if self.kind == "env" and not re.fullmatch(r"[A-Z][A-Z0-9_]{2,119}", self.name):
            raise ValueError(
                f"'{self.name}' is not an environment variable name; expected "
                "upper-case letters, digits and underscores"
            )
        return self

    def describe(self) -> str:
        return f"environment variable {self.name}" if self.kind == "env" else (
            "supplied by the operator for this session only"
        )


class CredentialRecord(FrozenModel):
    """What is persisted about a credential. Nothing here is sensitive.

    `sources` maps a *role* — "password", "api_key", "refresh_token" — to where
    that piece comes from. A Rithmic credential has a username, which is not a
    secret and is stored, and a password, which is not stored and is named.
    """

    credential_ref: str = Field(min_length=1, max_length=120)
    provider: Provider
    auth_method: AuthMethod
    label: str = Field(min_length=1, max_length=120)
    #: Non-secret identifying fields: a Rithmic system and username, a ProjectX
    #: tenant and username. Validated to contain nothing sensitive.
    public_fields: dict[str, str] = Field(default_factory=dict)
    sources: dict[str, SecretSource] = Field(default_factory=dict)
    created_at: datetime
    last_used_at: datetime | None = None
    note: str = ""

    @model_validator(mode="after")
    def _public_fields_are_public(self) -> CredentialRecord:
        for name in self.public_fields:
            lowered = name.lower()
            if any(marker in lowered for marker in SENSITIVE_KEYS):
                raise ValueError(
                    f"'{name}' names a secret and must not be stored as a public field; "
                    "declare it in `sources` instead so only its location is recorded"
                )
        return self

    def as_dict(self) -> dict[str, Any]:
        """Safe to log, export or send to a browser."""
        return redact_mapping(self.model_dump(mode="json"))

    def describe_requirements(self) -> tuple[str, ...]:
        return tuple(
            f"{role}: {source.describe()}" for role, source in sorted(self.sources.items())
        )


class SecretResolver(Protocol):
    """Turns a `SecretSource` into a `Secret`, or refuses."""

    def resolve(self, source: SecretSource, *, role: str) -> Secret: ...


class EnvironmentResolver:
    """Reads secrets from the process environment and nowhere else.

    This is the honest default for a local application whose operator already
    keeps provider keys in `.env` — the same place `forge.data.live.load_keys`
    reads market-data credentials from. A missing variable is an error naming
    the variable, not a silent empty string that would be sent to a provider as
    a password and lock the account out.
    """

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = environ if environ is not None else os.environ

    def resolve(self, source: SecretSource, *, role: str) -> Secret:
        if source.kind != "env":
            raise CredentialError(
                f"the {role} for this credential is {source.describe()}, which this "
                "resolver cannot read; supply it for the session instead"
            )
        value = self._environ.get(source.name, "")
        if not value:
            raise CredentialError(
                f"{source.name} is not set, so the {role} for this credential is not "
                "available. Nothing was sent to the provider."
            )
        return Secret(value)


class SessionResolver:
    """Secrets the operator supplied for this session, held in memory only.

    Nothing is written to disk, so closing the application forgets them. That is
    the point for a provider with no delegated auth: a password the application
    never persists is a password that cannot be stolen from its database.
    """

    def __init__(self) -> None:
        self._held: dict[str, Secret] = {}

    def supply(self, credential_ref: str, role: str, value: str) -> None:
        self._held[f"{credential_ref}:{role}"] = Secret(value)

    def forget(self, credential_ref: str) -> int:
        prefix = f"{credential_ref}:"
        keys = [key for key in self._held if key.startswith(prefix)]
        for key in keys:
            del self._held[key]
        return len(keys)

    def forget_all(self) -> None:
        self._held.clear()

    def holds(self, credential_ref: str, role: str) -> bool:
        return f"{credential_ref}:{role}" in self._held

    def resolve_for(self, credential_ref: str, role: str) -> Secret:
        secret = self._held.get(f"{credential_ref}:{role}")
        if secret is None:
            raise CredentialError(
                f"no {role} has been supplied for this session. It is not stored, so "
                "it has to be entered again after a restart."
            )
        return secret


class CredentialBroker:
    """The one place a secret is obtained, and the one place it is counted.

    Every resolution is recorded — which credential, which role, when, and
    whether it succeeded — so an audit can answer "what read the Rithmic
    password, and when". The record holds no value, only that a read happened.
    """

    def __init__(
        self,
        *,
        environment: SecretResolver | None = None,
        session: SessionResolver | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.environment = environment or EnvironmentResolver()
        self.session = session or SessionResolver()
        self._now = now
        self.accesses: list[dict[str, Any]] = []

    def resolve(self, record: CredentialRecord, role: str) -> Secret:
        source = record.sources.get(role)
        if source is None:
            self._note(record, role, ok=False, reason="no such role on this credential")
            raise CredentialError(
                f"this credential declares no '{role}'. It declares: "
                f"{', '.join(sorted(record.sources)) or 'nothing'}."
            )
        try:
            secret = (
                self.session.resolve_for(record.credential_ref, role)
                if source.kind == "prompt"
                else self.environment.resolve(source, role=role)
            )
        except CredentialError as exc:
            self._note(record, role, ok=False, reason=str(exc))
            raise
        self._note(record, role, ok=True, reason="")
        return secret

    def available(self, record: CredentialRecord) -> tuple[bool, tuple[str, ...]]:
        """Whether every declared role can be resolved, and what is missing.

        Asked before a connection attempt so the operator is told which variable
        to set rather than watching an authentication fail.
        """
        missing: list[str] = []
        for role in sorted(record.sources):
            try:
                self.resolve(record, role)
            except CredentialError as exc:
                missing.append(f"{role}: {exc}")
        return (not missing, tuple(missing))

    def _note(self, record: CredentialRecord, role: str, *, ok: bool, reason: str) -> None:
        self.accesses.append(
            {
                "at": self._now().isoformat(),
                "credential_ref": record.credential_ref,
                "provider": record.provider.value,
                "role": role,
                "ok": ok,
                "reason": reason,
            }
        )
        del self.accesses[:-500]


def new_credential_ref(provider: Provider, label: str, created_at: datetime) -> str:
    return stable_id(
        "cred", {"provider": provider.value, "label": label, "at": created_at.isoformat()}
    )


#: Which roles each provider's authentication actually needs, from the research.
#: Used to build a credential record that is complete rather than one that fails
#: halfway through a login.
REQUIRED_ROLES: dict[AuthMethod, tuple[str, ...]] = {
    AuthMethod.USERNAME_PASSWORD: ("password",),
    AuthMethod.API_KEY: ("api_key",),
    AuthMethod.OAUTH: ("refresh_token",),
    AuthMethod.TOKEN: ("token",),
    AuthMethod.NONE: (),
}


def required_roles(auth_method: AuthMethod) -> tuple[str, ...]:
    return REQUIRED_ROLES[auth_method]
