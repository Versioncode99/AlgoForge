"""One product, one navigation. The destinations, their tabs, and where old links go.

**What this replaces.** Three mode manifests, each declaring its own twenty-odd
sections, and a home screen whose first question was "Normal, Prop Firm or AI?".
Between them they offered sixty-two rail entries for a product with about
thirty distinct screens, and the duplication was not the worst of it: the mode
had to be chosen before anything could be seen, and the same word meant
different things depending on which one was open. `risk` was the book's exposure
in Normal and an account's cushion in Prop Firm; `book` was the fund overview in
one and the trade record in the other. A link was only interpretable if you
knew which mode had produced it.

**What replaces it.** Eight destinations, each owning its own tabs. A tab is a
view *inside* a destination rather than a row in the rail, which is the whole of
the simplification: Validation and Evidence are still there, still one click
away, and no longer competing with Campaigns for the reader's attention. The
route is the destination and `?tab=` names the view, so a link means the same
thing everywhere and the rail is eight rows instead of twenty-one.

**What it deliberately does not do.** It does not decide what an AI actor may
do. That was the *other* job the mode carried, and conflating navigation with
authority is why removing one looked like it had to remove the other. Authority
now lives in `forge.product.authority`, is set explicitly, and defaults to the
narrowest of the three modes rather than to whichever one the rail happened to
be drawn from.

**Engine anatomy is not product hierarchy.** The orchestrator, the action
registry and the raw activity stream are still running, still audited, and still
reachable — under Settings, where somebody goes to inspect machinery, rather
than in the rail beside Strategies, where somebody goes to work. `activity` and
`orchestrator` rendered the same component from two different rail entries,
which is the clearest possible statement that neither was a destination.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Tab:
    """One view inside a destination.

    `tab` is what `?tab=` carries. It is stable and lowercase because it appears
    in links people keep.
    """

    tab: str
    label: str
    detail: str
    #: Panels this tab contributes to a workspace layout, same vocabulary as
    #: `forge.workstation.PanelKind`.
    panel_kinds: tuple[str, ...] = ()
    #: Machinery rather than product. Rendered behind a disclosure, and excluded
    #: from the command palette's default listing.
    advanced: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "tab": self.tab,
            "label": self.label,
            "detail": self.detail,
            "panel_kinds": list(self.panel_kinds),
            "advanced": self.advanced,
        }


@dataclass(frozen=True)
class Destination:
    """One row in the rail."""

    route: str
    label: str
    detail: str
    #: The rail heading this sits under. Four headings, not eight.
    group: str
    tabs: tuple[Tab, ...] = ()
    panel_kinds: tuple[str, ...] = ()

    @property
    def default_tab(self) -> str:
        return self.tabs[0].tab if self.tabs else ""

    def tab(self, name: str) -> Tab | None:
        return next((t for t in self.tabs if t.tab == name), None)

    def panels(self) -> tuple[str, ...]:
        """Every panel kind this destination can contribute, tabs included."""
        seen: list[str] = list(self.panel_kinds)
        for t in self.tabs:
            for kind in t.panel_kinds:
                if kind not in seen:
                    seen.append(kind)
        return tuple(seen)

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "label": self.label,
            "detail": self.detail,
            "group": self.group,
            "panel_kinds": list(self.panels()),
            "tabs": [t.as_dict() for t in self.tabs],
            "default_tab": self.default_tab,
        }


# ── the manifest ─────────────────────────────────────────────────────────────
# Rail order is the order somebody meets the work: what is happening, the thing
# they came to ask, the research, the strategies it produced, the book those
# strategies run in, and the machinery last.

DESTINATIONS: tuple[Destination, ...] = (
    Destination(
        route="home",
        label="Home",
        detail="Where things stand, and what to do next",
        group="Workspace",
        tabs=(
            Tab("summary", "Summary", "The loop, and where each stage is", ("activity",)),
            Tab(
                "workspace",
                "Panels",
                "Your own arrangement of charts, books and readouts",
                ("chart", "watchlist", "notes"),
            ),
        ),
    ),
    Destination(
        route="chat",
        label="Chat",
        detail="Ask AlgoForge. It answers from this instance, and can act on it",
        group="Workspace",
        panel_kinds=("agent",),
    ),
    Destination(
        route="research",
        label="Research",
        detail="Questions, the experiments that answer them, and what the evidence supports",
        group="Research",
        tabs=(
            Tab("workbench", "Workbench", "Ask a question and get back to the trades"),
            Tab("findings", "Findings", "Sources, claims and replication gaps",
                ("research_library",)),
            Tab("experiments", "Experiments", "What was tried, and what came back",
                ("experiments",)),
            Tab("validation", "Validation", "Walk-forward, CSCV and CPCV", ("validation",)),
            Tab("evidence", "Evidence", "Why a result is trusted, or not", ("evidence",)),
        ),
    ),
    Destination(
        route="campaigns",
        label="Campaigns",
        detail="Autonomous research programmes: their objective, their window and their frontier",
        group="Research",
        tabs=(
            Tab("all", "Campaigns", "Every campaign, running and finished"),
            Tab("control", "Control", "Workers, agents and what the engine is doing now"),
            Tab("lineage", "Lineage", "Experiment ancestry", ("lineage",)),
            Tab("memory", "Memory", "Classified failures, so a dead end is only walked once",
                ("research_memory",)),
            Tab("pipeline", "Pipeline", "The engine graph, with live counts", advanced=True),
            Tab("automation", "Automation", "Objectives with declared steps, run verbatim",
                advanced=True),
        ),
    ),
    Destination(
        route="strategies",
        label="Strategies",
        detail="Build, run and judge; then see what the trades actually did",
        group="Research",
        tabs=(
            Tab("library", "Library", "Every strategy, its family and its standing",
                ("strategies",)),
            Tab("runs", "Runs", "The immutable execution ledger", ("runs",)),
            Tab("trades", "Trades", "Historical trades on the bars they happened on", ("chart",)),
        ),
    ),
    Destination(
        route="markets",
        label="Markets",
        detail="The archives this instance holds, and what they cover",
        group="Research",
        tabs=(
            Tab("charts", "Charts", "Candles over the local archives", ("chart",)),
            Tab("data", "Data", "Coverage, provenance and freshness", ("data_health",)),
        ),
    ),
    Destination(
        route="trading",
        label="Trading",
        detail="The book: from signals to sizes to orders, and what each one cost",
        group="Trading",
        tabs=(
            Tab("overview", "Overview", "Capital, exposure and the state of the loop",
                ("fund_summary", "activity")),
            Tab("book", "Positions & Orders", "Paper book and working orders",
                ("positions", "orders", "account")),
            Tab("portfolio", "Portfolio", "From signals to sizes, inside constraints",
                ("portfolio", "positions")),
            Tab("risk", "Risk", "Exposure, leverage, concentration and tail", ("risk",)),
            Tab("gate", "Pre-trade gate", "Every proposed order, checked",
                ("pretrade_gate", "orders")),
            Tab("execution", "Execution", "Orders, fills and what they cost",
                ("orders", "positions")),
            Tab("performance", "Performance", "Return, risk and where both came from", ("runs",)),
            Tab("operations", "Operations", "Reconciliation, jobs and health",
                ("positions", "account", "logs"), advanced=True),
        ),
    ),
    Destination(
        route="propdesk",
        label="Prop Desk",
        detail="Funded and evaluation accounts, their rules, and how close each one is",
        group="Trading",
        tabs=(
            Tab("accounts", "Accounts", "Every connected account, its provider and its health",
                ("desk_accounts",)),
            Tab("status", "Status", "Balance, equity, and where this account stands today",
                ("prop", "account")),
            Tab("rules", "Rules", "The contract this account is held to", ("prop",)),
            Tab("drawdown", "Drawdown", "Buffer to the floor, and how it trails",
                ("prop", "risk")),
            Tab("daily", "Daily loss", "What is left of today", ("prop", "risk")),
            Tab("target", "Profit target", "Distance, and what it takes", ("prop",)),
            Tab("allocation", "Allocation", "Which validated strategy each account runs, and why",
                ("desk_allocation",)),
            Tab("copy", "Copy", "Leaders, followers, sizing and execution status", ("desk_copy",)),
            Tab("risk", "Risk controls", "Per-account exposure, size and the boundaries you set",
                ("desk_risk", "risk", "positions")),
            Tab("limits", "Limits", "Firm permissions, contract caps and copy limits",
                ("desk_accounts",)),
            Tab("news", "News", "Scheduled economic events and blackout windows",
                ("desk_news",)),
            Tab("simulation", "Simulation", "How this account fares over many paths"),
            Tab("activity", "Activity", "Orders, refusals, reconciliation and allocation changes",
                ("desk_activity",), advanced=True),
            Tab("ai", "AI control", "What AI may control here, what it may never touch",
                ("desk_ai",), advanced=True),
        ),
    ),
    Destination(
        route="settings",
        label="Settings",
        detail="Models, data, connections, appearance and what an assistant may do",
        group="System",
        tabs=(
            Tab("general", "General", "Appearance, sound and storage"),
            Tab("models", "Models", "One default model, and where a feature should differ"),
            Tab("data", "Data", "Providers, archives and download cost"),
            Tab("connections", "Connections", "Brokers and prop providers, and their health"),
            Tab("permissions", "Permissions", "What an assistant may do on your behalf"),
            Tab("approvals", "Approvals", "Consequential actions waiting for you",
                ("approvals",)),
            Tab("audit", "Audit", "Who did what, on what, and with what result", ("audit",),
                advanced=True),
            Tab("diagnostics", "Diagnostics", "Actions, the activity stream and the orchestrator",
                ("activity", "agent"), advanced=True),
        ),
    ),
)

BY_ROUTE: dict[str, Destination] = {d.route: d for d in DESTINATIONS}

#: Rail groups, in order, with their destinations.
GROUPS: tuple[str, ...] = ("Workspace", "Research", "Trading", "System")


# ── deep-link compatibility ──────────────────────────────────────────────────
#: Every route the three mode manifests ever offered, and where it is now.
#:
#: Kept rather than dropped because links outlive navigation: an artifact card
#: minted last week points at `#evidence`, a bookmark points at `#orchestrator`,
#: and a saved workspace sidebar can name either. A link that silently lands on
#: Home explains nothing, so `resolve` returns the destination *and* the tab and
#: the shell rewrites the hash — the reader sees where they ended up.
#:
#: Two collisions are resolved here rather than left to the caller, because the
#: mode used to resolve them and there is no mode now. `risk` meant the book's
#: exposure in Normal and an account's cushion in Prop Firm: it goes to the
#: book, and the account's is one click away at `propdesk?tab=drawdown`. `book`
#: meant the fund overview in Normal and the trade record in Prop Firm: it goes
#: to the overview.
LEGACY_ROUTES: dict[str, str] = {
    # workspace
    "overview": "home?tab=summary",
    "workspace": "home?tab=workspace",
    # chat
    "assistant": "chat",
    "agents": "settings?tab=diagnostics",
    # research
    "lab": "research?tab=workbench",
    "experiments": "research?tab=experiments",
    "validation": "research?tab=validation",
    "evidence": "research?tab=evidence",
    # campaigns
    "research_control": "campaigns?tab=control",
    "pipeline": "campaigns?tab=pipeline",
    "memory": "campaigns?tab=memory",
    "lineage": "campaigns?tab=lineage",
    "missions": "campaigns?tab=automation",
    # strategies
    "runs": "strategies?tab=runs",
    "trades": "strategies?tab=trades",
    # markets
    "charts": "markets?tab=charts",
    "data": "markets?tab=data",
    # trading
    "book": "trading?tab=overview",
    "positions": "trading?tab=book",
    "portfolio": "trading?tab=portfolio",
    "risk": "trading?tab=risk",
    "gate": "trading?tab=gate",
    "execution": "trading?tab=execution",
    "operations": "trading?tab=operations",
    "performance": "trading?tab=performance",
    # prop desk
    "account": "propdesk?tab=status",
    "desk": "propdesk?tab=accounts",
    "rules": "propdesk?tab=rules",
    "drawdown": "propdesk?tab=drawdown",
    "daily": "propdesk?tab=daily",
    "target": "propdesk?tab=target",
    "allocation": "propdesk?tab=allocation",
    "copy": "propdesk?tab=copy",
    "limits": "propdesk?tab=limits",
    "risk_management": "propdesk?tab=risk",
    "ai_management": "propdesk?tab=ai",
    "news": "propdesk?tab=news",
    "desk_activity": "propdesk?tab=activity",
    "simulation": "propdesk?tab=simulation",
    # machinery, reclassified rather than deleted
    "actions": "settings?tab=diagnostics",
    "activity": "settings?tab=diagnostics",
    "orchestrator": "settings?tab=diagnostics",
    "approvals": "settings?tab=approvals",
    "audit": "settings?tab=audit",
}


@dataclass(frozen=True)
class Resolved:
    """Where a link lands, and whether it had to be moved to get there."""

    route: str
    tab: str
    #: True when the link named a route this product no longer has and was
    #: translated. The shell rewrites the hash so the reader can see it.
    redirected: bool = False
    #: Extra query parameters the original link carried, preserved verbatim.
    params: dict[str, str] = field(default_factory=dict)

    @property
    def hash(self) -> str:
        parts = [f"tab={self.tab}"] if self.tab else []
        parts += [f"{k}={v}" for k, v in self.params.items()]
        return f"{self.route}?{'&'.join(parts)}" if parts else self.route

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "tab": self.tab,
            "redirected": self.redirected,
            "params": dict(self.params),
            "hash": self.hash,
        }


def resolve(link: str) -> Resolved:
    """Turn any link this product has ever minted into a current destination.

    Accepts `route`, `route?tab=x`, `route:tab`, `#route?strategy=abc`, and
    every legacy route above. An unknown route lands on Home and says it was
    redirected rather than pretending Home is where the link pointed.
    """
    cleaned = link[1:] if link.startswith("#") else link
    route, _, query = cleaned.partition("?")
    # `route:tab` is the path-safe spelling a saved sidebar stores, because a
    # sidebar item's route travels in a URL path where `?` would end it. Both
    # spellings mean the same destination, and both arrive here.
    if ":" in route and not query:
        route, _, colon_tab = route.partition(":")
        query = f"tab={colon_tab}" if colon_tab else ""
    params: dict[str, str] = {}
    for pair in query.split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        if key and key not in params:
            params[key] = value
    asked_tab = params.pop("tab", "")

    redirected = False
    if route not in BY_ROUTE:
        target = LEGACY_ROUTES.get(route)
        if target is None:
            return Resolved(route="home", tab="summary", redirected=True, params=params)
        redirected = True
        route, _, target_query = target.partition("?")
        if target_query.startswith("tab="):
            asked_tab = target_query[4:]

    destination = BY_ROUTE[route]
    if not destination.tabs:
        return Resolved(route=route, tab="", redirected=redirected, params=params)
    if asked_tab and destination.tab(asked_tab) is not None:
        return Resolved(route=route, tab=asked_tab, redirected=redirected, params=params)
    # A tab this destination does not have is a moved link, not a missing
    # screen: land on the default and say the link was translated.
    return Resolved(
        route=route,
        tab=destination.default_tab,
        redirected=redirected or bool(asked_tab),
        params=params,
    )


def catalogue() -> list[dict[str, Any]]:
    """The whole manifest, as the shell and an agent both read it."""
    return [d.as_dict() for d in DESTINATIONS]


def panel_kinds_for(route: str, tab: str = "") -> tuple[str, ...]:
    """Which panels belong to a destination, so "add what I am looking at" works."""
    destination = BY_ROUTE.get(route)
    if destination is None:
        return ()
    if tab:
        found = destination.tab(tab)
        if found is not None:
            return found.panel_kinds or destination.panel_kinds
    return destination.panels()
