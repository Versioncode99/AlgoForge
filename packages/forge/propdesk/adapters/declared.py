"""The three real providers, declared honestly and unable to trade.

There is a temptation, building an execution fabric, to write an adapter that
looks finished — a `place` that constructs the right JSON, a `connect` that
would work if a socket were opened — and to describe the result as "Rithmic
support, pending credentials". That would be a lie of a specific and expensive
kind: the interface would offer the connection, the operator would enter a
password, and the failure would arrive as something that looks like their
mistake.

So these adapters do exactly two things, both of them true.

**They declare the surface.** Every one carries the provider's real descriptor —
authentication method, account key fields, bracket model, idempotency field,
published rate limit, session lifetime, documented limitations — taken from the
forensic research and the providers' own documentation. `RequiredWork` states
what a live connector would have to satisfy before it could exist: not a
sprint plan, but the external gates. Rithmic's conformance process and
Tradovate's OAuth application are not code, and no amount of engineering makes
them optional.

**They refuse every command, with the reason.** `AdapterUnavailable` is raised
rather than a connection being attempted, so the operator is told that this
build has no connector for the provider rather than being sent to check a
password.

When a live connector is genuinely built, it replaces one of these classes and
sets `live_connector_implemented=True` on the descriptor — one field, one
deliberate edit, and the tests that assert this build cannot trade will fail
loudly, which is the moment to think about it.
"""

from __future__ import annotations

from typing import Any

from forge.contracts.models import FrozenModel
from forge.propdesk.credentials import CredentialRecord
from forge.propdesk.fabric import (
    Acknowledgement,
    AdapterHealth,
    AdapterUnavailable,
    OrderIntent,
    ProviderSnapshot,
)
from forge.propdesk.identity import (
    PROJECTX_DESCRIPTOR,
    RITHMIC_DESCRIPTOR,
    TRADOVATE_DESCRIPTOR,
    Account,
    AccountCapability,
    AccountKey,
    ConnectionState,
    ProviderDescriptor,
)
from forge.propdesk.orders import OrderEvent


class RequiredWork(FrozenModel):
    """What stands between this declaration and a working connector.

    Split into engineering and external because they fail differently: the first
    is time, and the second is somebody else's decision. A plan that lists them
    together produces an estimate that is wrong in the direction that matters.
    """

    engineering: tuple[str, ...] = ()
    #: Approvals, agreements, registrations and subscriptions. Not buyable with
    #: engineering time.
    external: tuple[str, ...] = ()
    #: Credentials a connector would need at run time.
    credentials: tuple[str, ...] = ()
    #: Things that would remain uncertain even with all of the above.
    open_questions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class DeclaredAdapter:
    """An adapter that knows what it would take, and does nothing.

    Implements the full `ExecutionAdapter` protocol so the fabric can hold it,
    the interface can list it, and a contract test can check that the refusal is
    uniform — an adapter that refused `place` and quietly returned an empty
    tuple from `discover_accounts` would present as a connection with no
    accounts rather than as a connector that does not exist.
    """

    def __init__(self, descriptor: ProviderDescriptor, work: RequiredWork) -> None:
        if descriptor.live_connector_implemented:
            raise ValueError(
                f"{descriptor.provider.value} declares a live connector, so it must not "
                "be served by DeclaredAdapter, which refuses everything"
            )
        self.descriptor = descriptor
        self.work = work
        # An adapter that cannot execute is not a simulator either. Claiming
        # `simulated=True` would put it in the same category as something that
        # produces fills, and it produces nothing.
        self.simulated = False

    # ── the refusal ──────────────────────────────────────────────────────────
    def _refuse(self, verb: str) -> AdapterUnavailable:
        return AdapterUnavailable(
            f"AlgoForge has no live {self.descriptor.display_name} connector, so it "
            f"cannot {verb}. The provider's interface is declared here so the desk can "
            "reason about it; nothing was sent, and no credential was read. "
            + (
                "Outstanding external requirements: "
                + "; ".join(self.work.external)
                if self.work.external
                else "See the provider's documentation for what a connector requires."
            )
        )

    def connect(self, credential: CredentialRecord) -> ConnectionState:
        raise self._refuse("connect")

    def disconnect(self) -> None:
        # Disconnecting something never connected is a no-op, not an error. The
        # fabric calls this on shutdown for every registered adapter.
        return None

    def authenticate(self, credential: CredentialRecord) -> ConnectionState:
        raise self._refuse("authenticate")

    def discover_accounts(self) -> tuple[Account, ...]:
        raise self._refuse("discover accounts")

    def discover_capabilities(self, key: AccountKey) -> AccountCapability:
        raise self._refuse("discover capabilities")

    def place(self, intent: OrderIntent) -> Acknowledgement:
        raise self._refuse("place an order")

    def modify(self, intent: OrderIntent) -> Acknowledgement:
        raise self._refuse("modify an order")

    def cancel(self, intent: OrderIntent) -> Acknowledgement:
        raise self._refuse("cancel an order")

    def flatten(self, intent: OrderIntent) -> Acknowledgement:
        raise self._refuse("flatten a position")

    def poll(self) -> tuple[tuple[str, OrderEvent], ...]:
        # No connection, so no events. An empty tuple is the true answer and,
        # unlike an exception, lets the fabric poll every adapter in one pass.
        return ()

    def snapshot(self, account_uid: str) -> ProviderSnapshot:
        raise self._refuse("take a snapshot")

    def health(self) -> AdapterHealth:
        return AdapterHealth(
            provider=self.descriptor.provider,
            connection_id="",
            state=ConnectionState.DISCONNECTED,
            last_error=f"no live {self.descriptor.display_name} connector exists in this build",
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "descriptor": self.descriptor.as_dict(),
            "live_connector_implemented": False,
            "required_work": self.work.as_dict(),
        }


RITHMIC_WORK = RequiredWork(
    engineering=(
        "Implement R | Protocol over WebSocket with protobuf framing, one login "
        "and one heartbeat per plant (order, PnL, ticker, history).",
        "Map rithmic and exchange order notifications onto the normalised order "
        "lifecycle, including the bracket-modify case that one commercial vendor "
        "shipped a fix for after reading it as a cancellation.",
        "Account discovery over the order plant, keyed by system, FCM, IB and account id.",
        "Carry the deterministic idempotency key in the protocol's user tag field.",
        "Session recovery: re-login per plant and resubscribe after a transport drop.",
    ),
    external=(
        "Obtain the R | API+ development kit; the protocol specification is not public.",
        "Pass Rithmic's conformance process for the application before it may reach "
        "production systems.",
        "Agree commercial terms; API fees may apply per user.",
    ),
    credentials=(
        "A Rithmic system name, username and password per connection. There is no "
        "delegated authentication, so a connector holds the password for the session.",
    ),
    open_questions=(
        "Whether one Rithmic credential may hold concurrent sessions from a platform "
        "and this application on every prop system. Vendor documentation reports "
        "'already logged in' conflicts.",
        "Whether replay APIs are available on the systems the operator's accounts use.",
    ),
)

TRADOVATE_WORK = RequiredWork(
    engineering=(
        "OAuth authorisation-code flow, token storage as a refresh capability rather "
        "than a password, and renewal before the ~90-minute expiry.",
        "Single WebSocket with the documented frame protocol, one synchronisation "
        "request per socket, and 2.5-second heartbeats.",
        "Map order, orderVersion, commandReport and executionReport onto the "
        "normalised lifecycle, including trade-cancel and trade-correct.",
        "Order-strategy support for bracket and OCO placement.",
        "Parse the penalty-ticket response and feed the wait into the rate budget "
        "rather than retrying into it.",
    ),
    external=(
        "Register an OAuth application and be admitted to the partner programme; "
        "third-party access is not self-serve.",
        "Market-data licensing, if the connector is to read prices as well as route "
        "orders.",
    ),
    credentials=(
        "An OAuth client id and secret for the application, held by the operator of "
        "the application rather than by the trader.",
        "A per-trader refresh token obtained through the authorisation flow.",
    ),
    open_questions=(
        "Whether a reconnect storm across many accounts under one login can be kept "
        "inside the shared per-user quota in practice.",
    ),
)

PROJECTX_WORK = RequiredWork(
    engineering=(
        "API-key login for a JWT, validation and renewal inside the 24-hour lifetime.",
        "SignalR user and market hubs, with every subscription re-invoked on "
        "reconnect — the documentation requires it and a missed resubscribe is a "
        "silent event gap rather than an error.",
        "Map gateway order, trade and position events onto the normalised lifecycle, "
        "including the voided-trade flag.",
        "Carry the deterministic idempotency key in the per-account unique custom tag.",
        "Per-tenant configuration: the gateway host differs per prop firm.",
    ),
    external=(
        "The trader must hold a ProjectX API subscription for their tenant.",
        "Confirm, in writing, that the deployment topology satisfies the tenant "
        "firm's rule on where order placement may originate. At least one firm "
        "requires a personal device and prohibits remote servers.",
    ),
    credentials=("A tenant, a username and an API key per connection.",),
    open_questions=(
        "The gateway documents no event ordering or duplicate-delivery guarantee, so "
        "the dedupe and net-position convergence this desk implements are load-bearing "
        "rather than defensive.",
        "The execution path of accounts flagged live, as distinct from simulated, is "
        "not documented publicly.",
    ),
)


def rithmic_adapter() -> DeclaredAdapter:
    return DeclaredAdapter(RITHMIC_DESCRIPTOR, RITHMIC_WORK)


def tradovate_adapter() -> DeclaredAdapter:
    return DeclaredAdapter(TRADOVATE_DESCRIPTOR, TRADOVATE_WORK)


def projectx_adapter() -> DeclaredAdapter:
    return DeclaredAdapter(PROJECTX_DESCRIPTOR, PROJECTX_WORK)
