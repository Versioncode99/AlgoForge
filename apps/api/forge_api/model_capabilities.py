"""What a model can do, and what to do when a call fails.

Two contracts the TradingAgents audit found missing, kept together because they
answer the same question at two moments: *can this model do the job* before the
call, and *what does this failure mean* after it.

**Capability, declared as data.** Before this, whether a model supports
structured output or tool calling was an assumption distributed across call
sites, so nothing could answer "can this model do X?" without reading every
caller. The upstream pattern worth taking is that adding a model is a table edit
rather than a code edit, and the table records *why* each flag is set.

The check belongs at selection rather than at the call. A role that needs
structured output should never be assigned a model that cannot produce it, which
is stricter than trying the call and falling back to free text: a fallback that
silently degrades the contract is how an unparseable answer becomes a verdict.

**Failure, sized by reaction.** Doc 1 §10 lists seven failure classes and reads
like a request for seven types. The upstream rule is better: *the number of types
is the number of distinct router reactions, not the number of human-describable
causes.* Applying it here gives four, because that is how many different things
the caller does:

============================  ================================================
Class                         What the caller does
============================  ================================================
``TRANSIENT``                 Bounded retry, then fall back. Rate limiting is
                              here with a longer backoff -- a different
                              parameter, not a different reaction.
``NOT_ENTITLED``              Stop. Authentication, quota and policy refusals
                              all mean this key will not be served, and
                              retrying is how a systemic failure gets hidden
                              behind a slow one. Safety refusals are here and
                              are never circumvented.
``INCOMPATIBLE``              Re-route to a model that has the capability. The
                              table above is what makes this reachable rather
                              than a guess.
``UNUSABLE``                  One correction attempt, then REVIEW. The output
                              arrived and could not be trusted, which is not
                              the same as no output.
============================  ================================================

A taxonomy larger than the set of behaviours is a maintenance cost with no
decision attached to it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Capability(StrEnum):
    """Something a role may require of a model."""

    #: Returns JSON matching a supplied schema, not prose that resembles it.
    STRUCTURED_OUTPUT = "structured_output"
    #: Accepts a tool list and returns calls against it.
    TOOL_CALLING = "tool_calling"
    #: Emits tokens as they are produced.
    STREAMING = "streaming"
    #: Holds a long document without truncating the middle of it.
    LONG_CONTEXT = "long_context"


@dataclass(frozen=True)
class ModelCapabilities:
    """What one model accepts, as the catalogue declares it."""

    structured_output: bool
    tool_calling: bool
    streaming: bool
    #: Input tokens. Zero means the provider did not say, which is treated as
    #: "not long" rather than "unlimited" -- guessing upward is how a prompt
    #: gets silently truncated.
    context_tokens: int = 0

    #: Above this a model counts as long-context. A threshold rather than a
    #: measurement: the question a caller asks is "will a dossier fit", and
    #: everything at or above this answers yes for the documents this system
    #: produces.
    LONG_CONTEXT_TOKENS = 100_000

    def has(self, capability: Capability) -> bool:
        if capability is Capability.STRUCTURED_OUTPUT:
            return self.structured_output
        if capability is Capability.TOOL_CALLING:
            return self.tool_calling
        if capability is Capability.STREAMING:
            return self.streaming
        return self.context_tokens >= self.LONG_CONTEXT_TOKENS

    def missing(self, required: Sequence[Capability]) -> tuple[Capability, ...]:
        """Which of `required` this model does not have, in the order asked."""
        return tuple(capability for capability in required if not self.has(capability))


#: What a model is assumed to do when the catalogue says nothing.
#:
#: Permissive on the first three because every currently served model does them
#: and refusing by default would make an unlisted model unusable rather than
#: merely uncharacterised. Conservative on context for the opposite reason: a
#: wrong "yes" there truncates a prompt silently, and a wrong "no" only routes a
#: long job to a model that was going to handle it anyway.
UNDECLARED = ModelCapabilities(structured_output=True, tool_calling=True, streaming=True)


def capabilities_for(
    model: str, catalogue: Sequence[Mapping[str, Any]]
) -> ModelCapabilities:
    """What the catalogue says this model can do.

    Reads the provider's own entry rather than a second table keyed by model
    name, because two tables drift and the provider's is the one that gets
    updated. A model the catalogue does not list gets `UNDECLARED`.
    """
    for entry in catalogue:
        if entry.get("id") != model:
            continue
        declared = entry.get("capabilities")
        fields = declared if isinstance(declared, Mapping) else entry
        return ModelCapabilities(
            structured_output=_flag(fields, "structured_output", UNDECLARED.structured_output),
            tool_calling=_flag(fields, "tool_calling", UNDECLARED.tool_calling),
            streaming=_flag(fields, "streaming", UNDECLARED.streaming),
            context_tokens=_count(fields, "context_tokens"),
        )
    return UNDECLARED


def _flag(fields: Mapping[str, Any], key: str, fallback: bool) -> bool:
    value = fields.get(key)
    return bool(value) if isinstance(value, bool) else fallback


def _count(fields: Mapping[str, Any], key: str) -> int:
    value = fields.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def capable(
    model: str, required: Sequence[Capability], catalogue: Sequence[Mapping[str, Any]]
) -> bool:
    """Whether `model` can do everything `required` asks."""
    return not capabilities_for(model, catalogue).missing(required)


class Failure(StrEnum):
    """Why a model call failed, named for what the caller should do."""

    TRANSIENT = "transient"
    NOT_ENTITLED = "not_entitled"
    INCOMPATIBLE = "incompatible"
    UNUSABLE = "unusable"


#: Status codes that mean this key will not be served, whatever the wording.
#:
#: 402 is quota, 403 is policy or entitlement, 401 is authentication. All three
#: are the same reaction: stop. Retrying any of them is how a systemic failure
#: gets reported as a slow one.
_NOT_ENTITLED_STATUS = frozenset({401, 402, 403})

#: Codes worth another attempt. 429 is here with a longer backoff rather than in
#: a class of its own: waiting longer is a parameter, not a different reaction.
_TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})

#: Words a provider uses when it will not serve this request at all. Matched on
#: the message because status codes are not reliable across providers -- but only
#: after the status has had its say, so a provider that is precise is trusted.
_REFUSAL_WORDS = (
    "api key",
    "unauthorized",
    "unauthorised",
    "forbidden",
    "quota",
    "billing",
    "insufficient_quota",
    "content policy",
    "safety",
)

_INCOMPATIBLE_WORDS = (
    "does not support",
    "unsupported",
    "not supported",
    "unrecognized parameter",
    "unknown parameter",
    "invalid parameter",
)


def classify(status: int | None, message: str = "") -> Failure:
    """What kind of failure this is, and therefore what to do about it.

    Status first, message second. A provider that returns a precise code is
    believed; the word list only decides cases where it did not.

    Anything unrecognised is `TRANSIENT`, because the alternative defaults are
    worse: calling an unknown failure `NOT_ENTITLED` stops work that might have
    succeeded, and calling it `UNUSABLE` claims an output arrived when none did.
    A bounded retry costs one attempt and reveals which it actually was.
    """
    if status in _NOT_ENTITLED_STATUS:
        return Failure.NOT_ENTITLED
    if status in _TRANSIENT_STATUS:
        return Failure.TRANSIENT

    lowered = message.lower()
    if any(word in lowered for word in _REFUSAL_WORDS):
        return Failure.NOT_ENTITLED
    if any(word in lowered for word in _INCOMPATIBLE_WORDS):
        return Failure.INCOMPATIBLE
    return Failure.TRANSIENT


def retryable(failure: Failure) -> bool:
    """Whether trying the same model again could possibly help.

    `UNUSABLE` is retryable exactly once with a correction, which the caller
    tracks; this answers the narrower question of whether the *same call*
    repeated has any chance, and for a malformed output it does not.
    """
    return failure is Failure.TRANSIENT


#: How long to wait before a retry, by class. Rate limiting is TRANSIENT with a
#: longer wait, which is the whole difference between it and a 503.
def backoff_seconds(failure: Failure, attempt: int, *, rate_limited: bool = False) -> float:
    """Seconds to wait before attempt number `attempt` (1-based).

    Returns 0 for anything that should not be retried, so a caller that ignores
    `retryable` still does not hammer a provider that has refused it.
    """
    if not retryable(failure):
        return 0.0
    base = 5.0 if rate_limited else 0.5
    return float(min(base * (2 ** max(0, attempt - 1)), 60.0))
