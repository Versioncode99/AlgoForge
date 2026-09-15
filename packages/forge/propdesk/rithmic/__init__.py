"""Rithmic, behind the desk's own interfaces and redistributing none of its SDK.

Seven files, and the split is deliberate.

``sdk``
    Finds the operator's licensed copy and describes it — version, template
    version, file names and digests. Nothing from the archive is committed, for
    the reason its own licence gives; see `docs/ADR-0001-rithmic-transport.md`.
``redaction``
    What may be written down about a message, on an allow-list. This protocol
    carries passwords, emails, street addresses and account numbers.
``session``
    The connection state machine per plant: heartbeats, correlation, multi-part
    responses, sequence gaps, jittered reconnection and resubscription. Tested
    end to end against a fake transport with no SDK and no network.
``transport``
    An RFC 6455 WebSocket client on the standard library — the one module in
    the desk besides the economic calendar that may open a socket, and the one
    that knows nothing about orders. `tests/execution/test_boundary.py` holds
    that exemption to exactly those two and asserts this one cannot trade.
``vocabulary``
    Compiles the operator's `.proto` files and reads the template ids out of
    the descriptors. `protobuf` and a compiler are optional dependencies,
    imported inside the functions that need them.
``connect``
    Where the SDK, the credential broker, the transport and the gateway meet.
    Refuses rather than guesses a gateway, a system or a user.
``normalise`` / ``adapter``
    Rithmic's vocabulary turned into the desk's, and the desk's
    `ExecutionAdapter` implemented over it. Order translation is written and
    **refuses to send**.
"""

from forge.propdesk.rithmic.adapter import BASELINE, Capability, CapabilityState, RithmicAdapter
from forge.propdesk.rithmic.connect import (
    GATEWAY_ENV,
    READ_ONLY_PLANTS,
    NotConfigured,
    SessionFactory,
    connector,
    why_not,
)
from forge.propdesk.rithmic.sdk import (
    RECONCILED_TEMPLATE_VERSION,
    SDK_ENV,
    CompatibilityManifest,
    RithmicSdk,
    SdkMissing,
)
from forge.propdesk.rithmic.session import (
    Backoff,
    Plant,
    PlantSession,
    RithmicSession,
    SessionError,
    SessionState,
    Vocabulary,
)
from forge.propdesk.rithmic.transport import TransportError, WebSocketTransport
from forge.propdesk.rithmic.vocabulary import VocabularyUnavailable

__all__ = [
    "BASELINE",
    "GATEWAY_ENV",
    "READ_ONLY_PLANTS",
    "RECONCILED_TEMPLATE_VERSION",
    "SDK_ENV",
    "Backoff",
    "Capability",
    "CapabilityState",
    "CompatibilityManifest",
    "NotConfigured",
    "Plant",
    "PlantSession",
    "RithmicAdapter",
    "RithmicSdk",
    "RithmicSession",
    "SdkMissing",
    "SessionError",
    "SessionFactory",
    "SessionState",
    "TransportError",
    "Vocabulary",
    "VocabularyUnavailable",
    "WebSocketTransport",
    "connector",
    "why_not",
]
