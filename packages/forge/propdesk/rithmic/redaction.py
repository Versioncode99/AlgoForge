"""What may be written down about a Rithmic message, and what may never be.

A protobuf message from this protocol can carry a password, a user id, an email
address, a street address, a phone number and an account identifier — the
0.88.0.0 release added nine of those to the login-info response alone. A
connector that logged messages for debugging would write all of it to disk, on
the day somebody most needed a log.

So nothing here logs a message. It logs a *description* of one: the template
id, the field names present, and the values of fields on a small allow-list of
things that are neither secret nor identifying. Everything else is reported by
name with its value replaced.

**An allow-list, not a deny-list, and the direction is the whole point.** A
deny-list is a promise to have thought of every sensitive field in a protocol
that gains fields every release; it fails silently and in the wrong direction
the first time one is added. An allow-list fails towards saying less.
"""

from __future__ import annotations

import re
from typing import Any

#: Fields safe to write down: protocol mechanics and market structure. Nothing
#: identifying a person, an account, or a credential.
#:
#: Adding to this list is a decision about what may reach a log file. `user_msg`
#: is deliberately absent even though it is a protocol field: it is operator
#: text and can contain anything.
LOGGABLE: frozenset[str] = frozenset(
    {
        "template_id",
        "rp_code",
        "rq_handler_rp_code",
        "heartbeat_interval",
        "infra_type",
        "system_name",
        "exchange",
        "symbol",
        "trade_exchange",
        "trade_route",
        "order_type",
        "transaction_type",
        "duration",
        "quantity",
        "price_type",
        "status",
        "notify_type",
        "completion_reason",
        "is_snapshot",
        "sequence_number",
        "ssboe",
        "usecs",
    }
)

#: Substrings that make a field name sensitive whatever else is true of it.
#: Used only to *strengthen* the allow-list above: a name matching one of these
#: is redacted even if somebody adds it to `LOGGABLE` by mistake.
_ALWAYS_SENSITIVE = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "email",
    "address",
    "phone",
    "user",
    "account",
    "login",
    "name_of",
    "ssn",
    "tax",
)

#: What a redacted value reads as. A fixed string rather than the value's
#: length or a hash: both leak, and neither helps anybody debugging.
REDACTED = "«redacted»"

_SECRET_LOOKING = re.compile(
    r"(?i)\b(?:password|passwd|secret|token|api[_-]?key|bearer)\b\s*[:=]\s*\S+"
)


def sensitive(field: str) -> bool:
    """Whether a field name may not have its value written down."""
    lowered = field.lower()
    if any(mark in lowered for mark in _ALWAYS_SENSITIVE):
        return True
    return lowered not in LOGGABLE


def describe(message: Any) -> dict[str, Any]:
    """One protobuf message, as something safe to log.

    Reads the set fields through the protobuf descriptor rather than `vars`, so
    an unset optional is absent rather than reported with a default — the
    difference between "the server did not send a heartbeat interval" and "the
    server sent zero".
    """
    fields: dict[str, Any] = {}
    lister = getattr(message, "ListFields", None)
    if callable(lister):
        for descriptor, value in lister():
            fields[descriptor.name] = REDACTED if sensitive(descriptor.name) else value
    else:
        # A fake or a plain object in a test. Same rule, same direction.
        for name, value in sorted(vars(message).items()):
            if name.startswith("_"):
                continue
            fields[name] = REDACTED if sensitive(name) else value
    return fields


def scrub(text: str) -> str:
    """A message for a human, with anything secret-shaped taken out.

    Applied to text AlgoForge did not compose — an exception string from a
    socket library, a server's rejection reason — because those can quote the
    request that produced them.
    """
    def hide(match: re.Match[str]) -> str:
        label = match.group(0).split("=")[0].split(":")[0].rstrip()
        return f"{label}={REDACTED}"

    return _SECRET_LOOKING.sub(hide, text)
