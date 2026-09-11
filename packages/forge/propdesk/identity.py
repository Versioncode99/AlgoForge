"""Who holds the account, who routes the order, and who merely draws the chart.

The single most expensive mistake a multi-account execution system can make is
treating a *platform* as an *execution venue*. The forensic research is
unambiguous about it: in one commercial copier the "NinjaTrader" connection is a
Tradovate OAuth form and the "Tradesea" connection is the Rithmic form. Both are
front ends whose accounts live somewhere else. A design that lists them beside
Rithmic and Tradovate as peers has already lost the ability to say where an
order actually goes.

So five concepts are separated here and none of them is a synonym for another:

* **Provider** — the execution/account infrastructure a connection authenticates
  to. It holds the account records and routes or simulates the orders. This is
  the layer an adapter integrates with, and it is a closed set.
* **Platform** — a front end. It draws charts and sends order entry to whatever
  it is connected to. It owns no account and executes nothing. Some platforms
  *bind* to a provider (NinjaTrader Brokerage accounts are Tradovate accounts);
  that binding is declared rather than assumed.
* **ExecutionVenue** — where matching happens: an exchange, a provider's
  simulator, or this application's local simulator. A prop "demo" account fills
  in a provider simulator, which does not fill like the exchange, and the
  distinction has to survive into the record.
* **DataFeed** — market data distribution. It never routes an order, and
  `routes_orders` is a `Literal[False]` so that no configuration can claim
  otherwise.
* **Account** — a trading ledger inside a provider, identified by an
  `AccountKey` that is provider-qualified.

**Why the display name is not part of the key.** One of the two products studied
keyed its copy configuration by account *name* and had to ship a release note
saying that account renames no longer silently delete copy-trade
configurations. A name is a label a person edits; an identity is not. `AccountKey`
holds provider, environment, credential reference and the provider's own account
id, and `display_name` lives on `Account` where it can change freely.

**Why one credential means many accounts.** All three researched providers fan a
single login out to every account under it, and rate limits and sessions attach
to the credential rather than to the account. `BrokerConnection` is therefore the
unit of session, health and rate budget, and `Account` points at it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel


class Provider(StrEnum):
    """Execution/account infrastructures. Closed on purpose.

    Adding one is a code change with an adapter behind it, which is what stops
    the connection picker from ever offering something that cannot execute.
    """

    #: This application's own simulator. The only provider that executes
    #: anything in this build, and it says so on every fill.
    SIMULATED = "simulated"
    RITHMIC = "rithmic"
    TRADOVATE = "tradovate"
    PROJECTX = "projectx"


class Platform(StrEnum):
    """Front ends. None of these executes an order or owns an account."""

    ALGOFORGE = "algoforge"
    NINJATRADER_DESKTOP = "ninjatrader_desktop"
    QUANTOWER = "quantower"
    TRADINGVIEW = "tradingview"
    R_TRADER_PRO = "r_trader_pro"
    TOPSTEPX_WEB = "topstepx_web"
    TRADOVATE_WEB = "tradovate_web"
    TRADESEA = "tradesea"
    SIERRA_CHART = "sierra_chart"


class Environment(StrEnum):
    """Which side of a provider a connection is on.

    `EVALUATION` and `FUNDED` are not provider environments in their own right —
    at every researched provider a prop account is a simulated/demo account
    inside the provider — but the operator needs the distinction and it belongs
    on the *account*, not here. See `AccountType`.
    """

    SIMULATION = "simulation"
    DEMO = "demo"
    LIVE = "live"


class AuthMethod(StrEnum):
    OAUTH = "oauth"
    API_KEY = "api_key"
    USERNAME_PASSWORD = "username_password"
    TOKEN = "token"
    NONE = "none"


class VenueKind(StrEnum):
    EXCHANGE = "exchange"
    #: The provider's own simulator. Prop evaluation and funded-sim accounts
    #: fill here, and it does not fill like the exchange.
    PROVIDER_SIMULATOR = "provider_simulator"
    #: This application's simulator.
    LOCAL_SIMULATOR = "local_simulator"


class ExecutionVenue(FrozenModel):
    """Where matching happens."""

    venue_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    kind: VenueKind
    #: True only for a real exchange. A simulator produces a fill; it does not
    #: match against the book everyone else is trading.
    matches_public_book: bool = False

    @model_validator(mode="after")
    def _only_an_exchange_matches(self) -> ExecutionVenue:
        if self.matches_public_book and self.kind is not VenueKind.EXCHANGE:
            raise ValueError(
                f"{self.venue_id} is a {self.kind.value} and cannot match against the "
                "public book; a simulator produces a modelled fill"
            )
        return self


class DataFeed(FrozenModel):
    """Market data distribution. Never an order path."""

    feed_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    #: A constant, not a setting. A data vendor that appeared to route orders in
    #: a configuration file would be the exact confusion this module exists to
    #: prevent.
    routes_orders: Literal[False] = False
    note: str = ""


class BracketModel(StrEnum):
    """How a provider expresses a protective child order.

    The four shapes found in the research. They are not interchangeable: a
    provider with `POSITION_BRACKET` attaches brackets to the *position* rather
    than to the entry order, and a dollar-denominated bracket does not survive a
    cross-contract mapping at all.
    """

    NATIVE_OSO = "native_oso"
    PER_ORDER = "per_order"
    POSITION_BRACKET = "position_bracket"
    #: The copier would have to emulate it with linked orders it manages itself.
    EMULATED = "emulated"
    UNKNOWN = "unknown"


class AccountType(StrEnum):
    SIMULATION = "simulation"
    EVALUATION = "evaluation"
    FUNDED_SIMULATED = "funded_simulated"
    LIVE = "live"
    UNKNOWN = "unknown"


class ConnectionState(StrEnum):
    """The connection lifecycle, as the research describes it.

    `DEGRADED` is separate from `FAILED` because a rate-limit penalty is not a
    dropped socket: commands still go, they queue, and cancels must not queue
    behind entries. `REAUTHORIZING` is separate from `AUTHENTICATING` because
    one is a startup step and the other needs a person.
    """

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    AUTHENTICATING = "authenticating"
    SYNCING = "syncing"
    LIVE = "live"
    DEGRADED = "degraded"
    REAUTHORIZING = "reauthorizing"
    FAILED = "failed"


#: States in which the fabric may send a new entry. Cancels and flattens are
#: permitted in `DEGRADED` too — see `forge.propdesk.fabric`.
TRADEABLE_STATES = frozenset({ConnectionState.LIVE})


class ProviderDescriptor(FrozenModel):
    """What is known about a provider, stated once.

    Every field here is a claim about the outside world, so `evidence` names
    where it came from and `live_connector_implemented` is the field that says
    whether this build can actually reach it. It is `False` for everything but
    the simulator, and the adapter that carries a `False` refuses every command
    rather than failing at a socket.
    """

    provider: Provider
    display_name: str
    #: The one sentence that stops the layer confusion. "Execution
    #: infrastructure that holds the account records", not "a trading platform".
    what_it_is: str
    auth_method: AuthMethod
    environments: tuple[Environment, ...]
    #: Fields that, together with the credential, name one account at this
    #: provider. Rithmic needs fcm/ib/account; Tradovate needs an account id.
    account_key_fields: tuple[str, ...]
    bracket_model: BracketModel = BracketModel.UNKNOWN
    #: Provider field usable as a deterministic idempotency key, when one exists.
    idempotency_field: str = ""
    supports_modify: bool = True
    supports_flatten: bool = True
    #: One credential exposing many accounts. True at all three researched
    #: providers, and the reason the rate budget is per connection.
    multi_account_per_credential: bool = True
    #: Requests per window, as documented. `None` means no published number,
    #: which is not the same as no limit.
    rate_limit_per_minute: int | None = None
    session_seconds: int | None = None
    #: False everywhere in this build. The single field a future live adapter
    #: has to set to `True` deliberately.
    live_connector_implemented: bool = False
    documentation_url: str = ""
    evidence: str = ""
    limitations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class PlatformBinding(FrozenModel):
    """What a front end is actually connected to.

    `provider is None` means the platform holds no account of its own and this
    application cannot reach it — NinjaTrader Desktop is the case: its only
    external control surfaces are a local file interface and in-platform
    add-ons, neither of which is a provider API. Saying so is the point; the
    alternative is a connection picker that lists it beside Rithmic.
    """

    platform: Platform
    display_name: str
    #: The provider a platform's *accounts* live in, when it aliases one.
    provider: Provider | None = None
    #: True when the platform is only a signal source (charts, alerts) rather
    #: than a place accounts live.
    signal_source_only: bool = False
    note: str = ""
    evidence: str = ""


class AccountKey(FrozenModel):
    """The canonical, provider-qualified identity of one trading ledger.

    Deliberately excludes the display name. It also excludes balance, status and
    anything else that changes: an identity that moves is not an identity, and
    the copy configuration keyed to it would move with it.
    """

    provider: Provider
    environment: Environment
    #: Which credential this account was discovered through. Two accounts with
    #: the same provider id under different credentials are different accounts.
    credential_ref: str = Field(min_length=1, max_length=120)
    #: The provider's own identifier for the account.
    account_id: str = Field(min_length=1, max_length=120)
    #: Provider-specific extras that are part of the identity — Rithmic's
    #: fcm/ib ids, a ProjectX tenant, a Tradovate organisation.
    qualifiers: dict[str, str] = Field(default_factory=dict)

    @property
    def canonical(self) -> str:
        """A single string that round-trips the identity into a log or a key."""
        extra = "".join(
            f"/{name}={self.qualifiers[name]}" for name in sorted(self.qualifiers)
        )
        return (
            f"{self.provider.value}:{self.environment.value}:"
            f"{self.credential_ref}:{self.account_id}{extra}"
        )

    @property
    def key_id(self) -> str:
        return stable_id("acctkey", self.model_dump(mode="json"))


class AccountCapability(FrozenModel):
    """What this account can actually be asked to do.

    Read from the adapter rather than assumed from the provider, because the
    same provider serves accounts with different contract caps and different
    permissions. A capability nobody established is `None` or an empty tuple,
    and the compatibility engine treats both as unknown rather than as yes.
    """

    order_types: tuple[str, ...] = ()
    time_in_force: tuple[str, ...] = ()
    bracket_model: BracketModel = BracketModel.UNKNOWN
    trailing_stop_supported: bool | None = None
    #: The provider's or firm's own contract cap where the adapter reports one.
    max_contracts: int | None = None
    #: Whether this account is permitted to trade *right now*, per the provider.
    can_trade: bool | None = None
    liquidation_only: bool | None = None
    #: Products the provider says are available. Empty means "not established",
    #: which is not "all of them".
    products: tuple[str, ...] = ()
    note: str = ""

    @property
    def established(self) -> bool:
        return bool(self.order_types)


class BrokerConnection(FrozenModel):
    """One authenticated session to one provider, exposing many accounts.

    The unit of session, health, reconnection and rate budget — because that is
    what the providers themselves attach those things to.
    """

    connection_id: str = Field(min_length=1, max_length=80)
    provider: Provider
    environment: Environment
    label: str = Field(min_length=1, max_length=120)
    #: A pointer into the credential store. Never a secret; see
    #: `forge.propdesk.credentials`.
    credential_ref: str = Field(min_length=1, max_length=120)
    state: ConnectionState = ConnectionState.DISCONNECTED
    #: The front end the operator drives, when they told us. Informational: it
    #: changes nothing about routing.
    platform: Platform | None = None
    connected_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    session_expires_at: datetime | None = None
    last_error: str = ""
    created_at: datetime
    updated_at: datetime

    @property
    def healthy(self) -> bool:
        return self.state is ConnectionState.LIVE

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class Account(FrozenModel):
    """A trading ledger inside a provider, as this application holds it.

    Balance and equity are `None` until a provider reports them. Not zero: a
    zeroed account renders as a comfortable buffer against a starting balance
    nobody confirmed, which is the failure mode `forge.prop.accounts` already
    refuses and this module refuses for the same reason.
    """

    key: AccountKey
    connection_id: str = Field(min_length=1, max_length=80)
    #: Mutable. Deliberately not part of the identity.
    display_name: str = ""
    account_type: AccountType = AccountType.UNKNOWN
    venue_id: str = ""
    capability: AccountCapability = AccountCapability()
    balance: float | None = None
    equity: float | None = None
    #: The account's rule set in `forge.prop`, when the operator has linked one.
    #: Without it the desk knows the account exists and knows nothing about what
    #: it is permitted to do.
    prop_account_id: str | None = None
    #: The firm-permission policy in `forge.propdesk.policy`, when recorded.
    policy_id: str | None = None
    as_of: datetime | None = None
    hidden: bool = False

    @property
    def account_uid(self) -> str:
        """This application's stable handle for the account."""
        return self.key.key_id

    @property
    def name(self) -> str:
        return self.display_name or self.key.account_id

    @property
    def state_known(self) -> bool:
        return self.balance is not None and self.equity is not None

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["account_uid"] = self.account_uid
        payload["canonical"] = self.key.canonical
        return payload


# ── the catalogue ────────────────────────────────────────────────────────────
# Every claim below is sourced from the forensic dossier or from the provider's
# own documentation, and `live_connector_implemented` is False for all of them
# but the simulator. A descriptor is a statement of what an adapter would have
# to satisfy, not a statement that one exists.

SIMULATED_DESCRIPTOR = ProviderDescriptor(
    provider=Provider.SIMULATED,
    display_name="AlgoForge Simulator",
    what_it_is=(
        "This application's own execution simulator. It holds the account "
        "records it was given and fills against a reference price with modelled "
        "slippage. It is not a venue and it is not calibrated against one."
    ),
    auth_method=AuthMethod.NONE,
    environments=(Environment.SIMULATION,),
    account_key_fields=("account_id",),
    bracket_model=BracketModel.EMULATED,
    idempotency_field="client_tag",
    rate_limit_per_minute=None,
    live_connector_implemented=True,
    evidence="local",
    limitations=(
        "Fills are modelled, not calibrated against any venue.",
        "No queue position, no depth, no adverse selection.",
    ),
)

RITHMIC_DESCRIPTOR = ProviderDescriptor(
    provider=Provider.RITHMIC,
    display_name="Rithmic (R | Protocol)",
    what_it_is=(
        "Order-routing and account infrastructure. Accounts live on a Rithmic "
        "'system' and orders route through the order plant to the exchange, or "
        "to Rithmic's simulator on paper and prop systems."
    ),
    auth_method=AuthMethod.USERNAME_PASSWORD,
    environments=(Environment.DEMO, Environment.LIVE),
    account_key_fields=("system", "fcm_id", "ib_id", "account_id"),
    bracket_model=BracketModel.NATIVE_OSO,
    idempotency_field="user_tag",
    rate_limit_per_minute=None,
    live_connector_implemented=False,
    documentation_url="https://www.rithmic.com/apis",
    evidence="dossier 10, 25 — protocol spec private; corroborated by open-source clients",
    limitations=(
        "Password custody: there is no delegated auth, so a connector holds the "
        "operator's Rithmic password for the life of the session.",
        "Conformance-gated: an application must pass Rithmic's conformance "
        "process before it may connect to production.",
        "Session conflicts are reported between concurrent logins on one credential.",
        "No published rate limit; cancel throttling is documented.",
    ),
)

TRADOVATE_DESCRIPTOR = ProviderDescriptor(
    provider=Provider.TRADOVATE,
    display_name="Tradovate / NinjaTrader Brokerage",
    what_it_is=(
        "Execution and account infrastructure. Prop-firm accounts are `demo` "
        "accounts inside a prop organisation on this backend. NinjaTrader "
        "*Brokerage* accounts are Tradovate accounts; NinjaTrader Desktop is a "
        "separate thing and is a platform."
    ),
    auth_method=AuthMethod.OAUTH,
    environments=(Environment.DEMO, Environment.LIVE),
    account_key_fields=("account_id",),
    bracket_model=BracketModel.NATIVE_OSO,
    idempotency_field="custom_tag_50",
    rate_limit_per_minute=80,
    session_seconds=5400,
    live_connector_implemented=False,
    documentation_url="https://api.tradovate.com/",
    evidence="dossier 11, 25 — official documentation",
    limitations=(
        "Access tokens last about 90 minutes and must be renewed before expiry.",
        "Rate limits are per user, not per account, and are enforced with "
        "penalty tickets that must be waited out.",
        "One synchronisation request per socket; reconnect storms consume the quota.",
        "A personal API key requires a funded live account, so prop users depend "
        "on a third party's OAuth application.",
    ),
)

PROJECTX_DESCRIPTOR = ProviderDescriptor(
    provider=Provider.PROJECTX,
    display_name="ProjectX Gateway (TopstepX and other tenants)",
    what_it_is=(
        "Execution and account infrastructure operated per prop-firm tenant. "
        "TopstepX is Topstep's branded tenant of it, not a separate provider."
    ),
    auth_method=AuthMethod.API_KEY,
    environments=(Environment.DEMO, Environment.LIVE),
    account_key_fields=("tenant", "account_id"),
    bracket_model=BracketModel.PER_ORDER,
    idempotency_field="custom_tag",
    rate_limit_per_minute=200,
    session_seconds=86400,
    live_connector_implemented=False,
    documentation_url="https://gateway.docs.projectx.com/",
    evidence="dossier 13, 25 — official gateway documentation",
    limitations=(
        "The end user pays a separate monthly API subscription.",
        "Real-time hubs require every subscription to be re-invoked after a reconnect.",
        "No documented event ordering or duplicate-delivery guarantee, so "
        "dedupe and net-position convergence are mandatory rather than optional.",
        "At least one firm operating a tenant requires that order placement "
        "originate from the trader's personal device. Whether a given deployment "
        "satisfies that is a compliance question this application cannot answer.",
    ),
)

PROVIDER_DESCRIPTORS: dict[Provider, ProviderDescriptor] = {
    Provider.SIMULATED: SIMULATED_DESCRIPTOR,
    Provider.RITHMIC: RITHMIC_DESCRIPTOR,
    Provider.TRADOVATE: TRADOVATE_DESCRIPTOR,
    Provider.PROJECTX: PROJECTX_DESCRIPTOR,
}


PLATFORM_BINDINGS: dict[Platform, PlatformBinding] = {
    Platform.ALGOFORGE: PlatformBinding(
        platform=Platform.ALGOFORGE,
        display_name="AlgoForge",
        provider=Provider.SIMULATED,
        note="This application, driving its own simulator.",
    ),
    Platform.NINJATRADER_DESKTOP: PlatformBinding(
        platform=Platform.NINJATRADER_DESKTOP,
        display_name="NinjaTrader Desktop",
        provider=None,
        note=(
            "A charting and order-entry front end. It holds only local Sim "
            "accounts; everything else it shows belongs to whichever provider it "
            "is connected to. Its external control surfaces are a local file "
            "interface and in-platform add-ons, neither of which is a provider "
            "API, so this application does not connect to it. NinjaTrader "
            "*Brokerage* accounts are Tradovate accounts and are reached through "
            "the Tradovate provider."
        ),
        evidence="dossier 12, 14 — official platform documentation",
    ),
    Platform.TRADESEA: PlatformBinding(
        platform=Platform.TRADESEA,
        display_name="Tradesea",
        provider=Provider.RITHMIC,
        note="A front end on Rithmic. Its accounts are Rithmic accounts.",
        evidence="dossier 14 — observed: the vendor's Tradesea form is the Rithmic form",
    ),
    Platform.TOPSTEPX_WEB: PlatformBinding(
        platform=Platform.TOPSTEPX_WEB,
        display_name="TopstepX (web)",
        provider=Provider.PROJECTX,
        note="A branded front end on a ProjectX tenant.",
        evidence="dossier 13",
    ),
    Platform.TRADOVATE_WEB: PlatformBinding(
        platform=Platform.TRADOVATE_WEB,
        display_name="Tradovate (web)",
        provider=Provider.TRADOVATE,
    ),
    Platform.R_TRADER_PRO: PlatformBinding(
        platform=Platform.R_TRADER_PRO,
        display_name="R | Trader Pro",
        provider=Provider.RITHMIC,
    ),
    Platform.QUANTOWER: PlatformBinding(
        platform=Platform.QUANTOWER,
        display_name="Quantower",
        provider=None,
        note=(
            "A multi-connection front end. Its accounts belong to whichever "
            "upstream provider it is connected to."
        ),
    ),
    Platform.SIERRA_CHART: PlatformBinding(
        platform=Platform.SIERRA_CHART,
        display_name="Sierra Chart",
        provider=None,
        note="A front end. Accounts belong to the upstream provider.",
    ),
    Platform.TRADINGVIEW: PlatformBinding(
        platform=Platform.TRADINGVIEW,
        display_name="TradingView",
        provider=None,
        signal_source_only=True,
        note=(
            "A charting front end and an alert source. It is not an account "
            "system: orders placed from it land in the broker account it is "
            "linked to, and that account is what this application would watch."
        ),
    ),
}


LOCAL_SIMULATOR_VENUE = ExecutionVenue(
    venue_id="algoforge-sim",
    name="AlgoForge local simulator",
    kind=VenueKind.LOCAL_SIMULATOR,
    matches_public_book=False,
)

#: Named so a reader can see that a data vendor is modelled and that it does not
#: route. The catalogue is small because the desk does not need it to be large;
#: it needs the concept to exist.
DATA_FEEDS: dict[str, DataFeed] = {
    "dxfeed": DataFeed(
        feed_id="dxfeed",
        name="dxFeed",
        note=(
            "A market-data vendor. Accounts marketed as 'dxFeed accounts' live "
            "in the issuing firm's own execution backend; the feed itself routes "
            "nothing."
        ),
    ),
    "databento": DataFeed(
        feed_id="databento",
        name="Databento",
        note="Historical and live CME data. This application already reads it for research.",
    ),
}


def descriptor(provider: Provider) -> ProviderDescriptor:
    return PROVIDER_DESCRIPTORS[provider]


def binding(platform: Platform) -> PlatformBinding:
    return PLATFORM_BINDINGS[platform]


def provider_for_platform(platform: Platform) -> Provider | None:
    """Which provider a platform's accounts live in, if any.

    `None` is a real answer and the important one: it means this application
    cannot reach the platform's accounts through it, and the operator has to
    connect to the upstream provider instead.
    """
    return PLATFORM_BINDINGS[platform].provider


def new_connection_id(provider: Provider, label: str, created_at: datetime) -> str:
    return stable_id(
        "conn",
        {"provider": provider.value, "label": label, "at": created_at.isoformat()},
    )


def catalogue() -> dict[str, Any]:
    """The whole layer model, for a surface that has to draw it.

    One declaration read by the API, the action registry and the interface, so a
    provider added here appears everywhere without an edit — and, more to the
    point, a provider whose connector is not implemented says so everywhere at
    once.
    """
    return {
        "providers": [d.as_dict() for d in PROVIDER_DESCRIPTORS.values()],
        "platforms": [b.model_dump(mode="json") for b in PLATFORM_BINDINGS.values()],
        "data_feeds": [f.model_dump(mode="json") for f in DATA_FEEDS.values()],
        "venues": [LOCAL_SIMULATOR_VENUE.model_dump(mode="json")],
        "live_connectors_implemented": [
            d.provider.value for d in PROVIDER_DESCRIPTORS.values()
            if d.live_connector_implemented
        ],
        "generated_at": datetime.now(UTC).isoformat(),
    }
