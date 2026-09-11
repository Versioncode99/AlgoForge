"""The four operating environments, and what each one is for.

AlgoForge is one platform. A *mode* is not a separate product and not a
licence tier: it is a declaration of what the operator came here to do, and
everything downstream — which sections appear, which layout is seeded, what an
AI actor is permitted to do on their behalf — is derived from it.

**Why the manifest lives here rather than in the interface.** The navigation
used to exist only as a React constant, which meant an agent asked "what can I
see in Prop Firm mode" had to be told by a person. One declaration, read by the
HTTP API, the action registry and the shell alike, is what stops the answer
from depending on which surface is asking.

**What a mode is not.** It is not a permission boundary on the *human*. Nothing
here removes a capability from the operator: every mode can reach validation,
every mode can reach the judge, and switching modes never deletes work. The
restriction a mode carries applies to the *AI actor* — see
`forge.modes.permissions` — because that is the actor whose reach has to be
bounded rather than merely tidy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class WorkspaceMode(StrEnum):
    """The four environments the product opens into."""

    NORMAL = "normal"
    PROP_FIRM = "prop_firm"
    AI = "ai"
    HEDGE_FUND = "hedge_fund"


class Stance(StrEnum):
    """How much an AI actor may do without a person in the way.

    Only Hedge Fund mode offers the choice, because it is the only mode with a
    workflow long enough for the distinction to mean anything. Both stances are
    bounded by the same deterministic controls: *autonomous* moves the approval
    gate, it does not remove the risk engine, the pre-trade gate or the kill
    switch. See `forge.modes.permissions`.
    """

    HUMAN_IN_THE_LOOP = "human_in_the_loop"
    AUTONOMOUS = "autonomous"


@dataclass(frozen=True)
class Section:
    """One destination inside a mode.

    `route` is the identifier the shell navigates to and the action registry
    names. `panel_kinds` says which workspace panels belong to this section, so
    "add the thing I am looking at to my layout" has an answer that does not
    require the interface to hold a second mapping.
    """

    route: str
    label: str
    detail: str
    group: str
    panel_kinds: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "label": self.label,
            "detail": self.detail,
            "group": self.group,
            "panel_kinds": list(self.panel_kinds),
        }


@dataclass(frozen=True)
class ModeDescriptor:
    """Everything a surface needs to open a mode without knowing about modes."""

    mode: WorkspaceMode
    name: str
    tagline: str
    purpose: str
    #: The workspace template seeded the first time this mode is entered. §22:
    #: the operator should not have to assemble their own tools to begin.
    workspace_template: str
    sections: tuple[Section, ...]
    #: Empty for every mode but Hedge Fund. An empty tuple means "this mode has
    #: no stance", which is different from "it has one and it is the default".
    stances: tuple[Stance, ...] = ()
    #: Stated limitations, shown in the interface rather than discovered. A mode
    #: that cannot do something says so where the operator is standing.
    limitations: tuple[str, ...] = ()

    @property
    def default_stance(self) -> Stance | None:
        return self.stances[0] if self.stances else None

    def section(self, route: str) -> Section | None:
        return next((s for s in self.sections if s.route == route), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "name": self.name,
            "tagline": self.tagline,
            "purpose": self.purpose,
            "workspace_template": self.workspace_template,
            "sections": [s.as_dict() for s in self.sections],
            "stances": [s.value for s in self.stances],
            "default_stance": self.default_stance.value if self.default_stance else None,
            "limitations": list(self.limitations),
        }


# ── the manifests ────────────────────────────────────────────────────────────
# Ordered as they appear in the shell. Every route here has a view behind it and
# every panel kind names a `PanelKind` that renders; `tests/modes` asserts both,
# because a manifest that offers a destination with nothing behind it is exactly
# the fake functionality the product rules refuse.

_NORMAL = ModeDescriptor(
    mode=WorkspaceMode.NORMAL,
    name="Normal",
    tagline="Your trading environment.",
    purpose=(
        "Trade, analyse and monitor markets without a firm's constraints or an "
        "institutional workflow in the way. The least opinionated of the four: it "
        "supplies the tools and leaves the arrangement to you."
    ),
    workspace_template="normal_desk",
    sections=(
        Section("overview", "Overview", "Where things stand", "Desk", ("activity",)),
        Section("workspace", "Workspace", "Your panels, arranged your way", "Desk",
                ("chart", "watchlist", "notes")),
        Section("charts", "Charts", "Candles over the local archives", "Markets",
                ("chart",)),
        Section("trades", "Strategy Trades", "Historical trades on the bars they happened on",
                "Markets", ("chart",)),
        Section("strategies", "Strategies", "Build, run and judge", "Strategy",
                ("strategies",)),
        Section("runs", "Runs", "Immutable execution ledger", "Strategy", ("runs",)),
        Section("validation", "Validation", "Walk-forward, CSCV and CPCV", "Strategy",
                ("validation",)),
        Section("evidence", "Evidence", "Why a result is trusted, or not", "Strategy",
                ("evidence",)),
        Section("positions", "Positions & Orders", "Paper book and working orders", "Book",
                ("positions", "orders", "account")),
        Section("performance", "Performance", "Return, risk and where both came from", "Book",
                ("runs",)),
        Section("data", "Data Health", "Coverage and provenance", "Data", ("data_health",)),
        Section("assistant", "Assistant", "Ask the ledger, or ask for a strategy", "System",
                ("agent",)),
        Section("settings", "Settings", "Providers, data and storage", "System", ()),
    ),
    limitations=(
        "Paper only. No live-order path exists anywhere in this application.",
        "Fills are modelled from bar data, not calibrated against a broker.",
    ),
)

_PROP_FIRM = ModeDescriptor(
    mode=WorkspaceMode.PROP_FIRM,
    name="Prop Firm",
    tagline="Trade within account constraints.",
    purpose=(
        "Operate a funded or evaluation account against its own rule set. The "
        "workspace is arranged around one question — how close am I to breaching "
        "— and the rule engine that answers it is deterministic and configurable, "
        "not a copy of any one firm's contract."
    ),
    workspace_template="prop_desk",
    sections=(
        Section("account", "Account Status", "Balance, equity and today", "Account",
                ("prop", "account")),
        Section("rules", "Rules", "The contract this account is held to", "Account", ("prop",)),
        Section("drawdown", "Drawdown", "Buffer to the floor, and how it trails", "Risk",
                ("prop", "risk")),
        Section("daily", "Daily Loss", "What is left of today", "Risk", ("prop", "risk")),
        Section("target", "Profit Target", "Distance, and what it takes", "Risk", ("prop",)),
        Section("risk", "Risk", "Exposure and per-trade size", "Risk", ("risk", "positions")),
        Section("desk", "Prop Desk", "Every connected account, its provider and its health",
                "Desk", ("desk_accounts",)),
        Section("allocation", "Allocation",
                "Which validated strategy each account should be running, and why",
                "Desk", ("desk_allocation",)),
        Section("copy", "Copy Trader", "Leaders, followers, sizing and execution status",
                "Desk", ("desk_copy",)),
        Section("limits", "Desk Limits",
                "Firm permissions, contract caps, copy limits and drawdown protection",
                "Desk", ("desk_accounts", "risk")),
        Section("news", "News", "Scheduled economic events and blackout windows",
                "Desk", ("desk_news",)),
        Section("desk_activity", "Desk Activity",
                "Orders, refusals, reconciliation passes and allocation changes",
                "Desk", ("desk_activity",)),
        Section("book", "Trades", "What was done, and what it cost", "Record",
                ("positions", "orders")),
        Section("simulation", "Rule Simulation", "How this account fares over many paths",
                "Record", ()),
        Section("performance", "Performance", "Return and consistency", "Record", ("runs",)),
        Section("strategies", "Strategies", "Build, run and judge", "Strategy",
                ("strategies",)),
        Section("validation", "Validation", "Walk-forward, CSCV and CPCV", "Strategy",
                ("validation",)),
        Section("assistant", "Assistant", "Ask the ledger, or ask for a strategy", "System",
                ("agent",)),
        Section("settings", "Settings", "Providers, data and storage", "System", ()),
    ),
    limitations=(
        "Rule sets are supplied by you. AlgoForge makes no claim about what any "
        "named firm's live contract says.",
        "An assessment describes the configured rules, not a firm's discretion.",
        "The Prop Desk has no live broker connector. Rithmic, Tradovate and "
        "ProjectX are declared with their real interfaces and refuse every "
        "command; only the local simulator executes, and it labels every fill "
        "simulated.",
        "Firm permissions start UNKNOWN and you record them. An unrecorded rule "
        "is never treated as permission.",
    ),
)

_AI = ModeDescriptor(
    mode=WorkspaceMode.AI,
    name="AI",
    tagline="Build, analyse and automate with AI.",
    purpose=(
        "Use AI across research, strategy work and automation, reaching the same "
        "bounded action registry the interface uses. Every call is schema-checked, "
        "logged and refusable; there is no verb here the interface does not also "
        "have."
    ),
    workspace_template="ai_desk",
    sections=(
        Section("assistant", "AI Workspace", "Ask, and watch what it does", "AI", ("agent",)),
        Section("agents", "Agents", "Specialists and their contracts", "AI", ("agent",)),
        Section("actions", "Actions", "The verbs, their schemas and who may call them",
                "AI", ()),
        Section("activity", "Activity", "Every call, in order", "AI", ("activity",)),
        Section("strategies", "Strategies", "Build, run and judge", "Work", ("strategies",)),
        Section("research", "Research", "Sources and replication gaps", "Work",
                ("research_library",)),
        Section("experiments", "Experiments", "What was tried", "Work", ("experiments",)),
        Section("validation", "Validation", "Walk-forward, CSCV and CPCV", "Work",
                ("validation",)),
        Section("evidence", "Evidence", "Why a result is trusted, or not", "Work",
                ("evidence",)),
        Section("missions", "Automation", "Objectives, declared steps, run verbatim",
                "Work", ()),
        Section(
            "campaigns",
            "Research Campaign",
            "The objective, the frontier and what it is doing now",
            "Autonomous",
            (),
        ),
        Section("pipeline", "Engine Pipeline", "The graph, with live counts", "Autonomous", ()),
        Section("memory", "Research Memory", "Classified failures", "Autonomous",
                ("research_memory",)),
        Section("lineage", "Lineage", "Experiment ancestry", "Autonomous", ("lineage",)),
        Section("lab", "Research Lab", "Ask a question, get back to the trades", "Work", ()),
        Section("settings", "Settings", "Providers, data and storage", "System", ()),
    ),
    limitations=(
        "AI reaches only the registered actions. There is no arbitrary-code verb.",
        "A model that is not configured is reported as unavailable, never simulated.",
    ),
)

_HEDGE_FUND = ModeDescriptor(
    mode=WorkspaceMode.HEDGE_FUND,
    name="Hedge Fund",
    tagline="Research, construct, manage and execute quantitative portfolios.",
    purpose=(
        "An operating layer over the whole quantitative loop: point-in-time data, "
        "alpha research, the judge, portfolio construction, a deterministic risk "
        "engine, a pre-trade gate nothing routes around, and simulated execution "
        "through an OMS. AI orchestrates the loop; it does not own the controls."
    ),
    workspace_template="fund_command",
    sections=(
        Section("fund", "Fund Overview", "NAV, exposure and the state of the loop",
                "Command", ("activity",)),
        Section("data", "Data", "Point-in-time datasets and their provenance", "Loop",
                ("data_health",)),
        Section("research", "Research", "Hypotheses and the experiments testing them",
                "Loop", ("experiments", "research_library")),
        Section("lab", "Research Lab", "Ask a question, get back to the trades", "Loop", ()),
        Section("memory", "Research Memory", "What has already been disproven", "Loop",
                ("research_memory",)),
        Section("alpha", "Alpha", "Candidate signals and what supports them", "Loop",
                ("strategies",)),
        Section("validation", "Validation", "The G0-G13 ladder, unchanged", "Loop",
                ("validation", "evidence")),
        Section("portfolio", "Portfolio", "From signals to sizes, inside constraints",
                "Loop", ("positions",)),
        Section("risk", "Risk", "Exposure, leverage, concentration and tail", "Loop",
                ("risk",)),
        Section("gate", "Pre-Trade Gate", "Every proposed order, checked", "Loop",
                ("orders",)),
        Section("execution", "Execution", "Orders, fills and what they cost", "Loop",
                ("orders", "positions")),
        Section("operations", "Operations", "Book, reconciliation, jobs and health",
                "Loop", ("positions", "account", "logs")),
        Section("performance", "Performance", "Where the return came from", "Loop",
                ("runs",)),
        Section("orchestrator", "AI Orchestrator", "What the AI did, and what it was refused",
                "Command", ("agent", "activity")),
        Section("approvals", "Approvals", "Consequential actions awaiting a person",
                "Command", ()),
        Section("audit", "Audit Log", "Who did what, on what, and with what result",
                "Command", ()),
        Section("settings", "Settings", "Providers, data and storage", "System", ()),
    ),
    stances=(Stance.HUMAN_IN_THE_LOOP, Stance.AUTONOMOUS),
    limitations=(
        "Execution is simulated locally. No broker, OMS vendor or venue is connected.",
        "No commercial factor model is installed. Factor exposure is reported only "
        "against loadings you supply.",
        "Point-in-time integrity is asserted per dataset, and datasets that cannot "
        "guarantee it say so rather than being treated as though they could.",
    ),
)

MODES: dict[WorkspaceMode, ModeDescriptor] = {
    descriptor.mode: descriptor
    for descriptor in (_NORMAL, _PROP_FIRM, _AI, _HEDGE_FUND)
}

#: Display order. Deliberately not `WorkspaceMode` iteration order by accident —
#: the home screen reads this, and the sequence is a product decision.
MODE_ORDER: tuple[WorkspaceMode, ...] = (
    WorkspaceMode.NORMAL,
    WorkspaceMode.PROP_FIRM,
    WorkspaceMode.AI,
    WorkspaceMode.HEDGE_FUND,
)


def descriptor(mode: WorkspaceMode | str) -> ModeDescriptor:
    """The manifest for one mode.

    Accepts the string form so an HTTP path parameter does not have to be
    converted by every caller, and raises with the valid set rather than a bare
    `KeyError`, because this refusal is shown to people.
    """
    try:
        key = WorkspaceMode(mode)
    except ValueError:
        valid = ", ".join(m.value for m in MODE_ORDER)
        raise KeyError(f"no mode '{mode}'. Modes: {valid}") from None
    return MODES[key]


def catalogue() -> list[dict[str, Any]]:
    """Every mode, in display order, as the home screen renders them."""
    return [MODES[mode].as_dict() for mode in MODE_ORDER]


def parse_stance(mode: WorkspaceMode, stance: str | None) -> Stance | None:
    """Validate a stance *against the mode that would hold it*.

    A stance on a mode that has none is a caller error rather than something to
    ignore: silently dropping it is how a request to run autonomously ends up
    looking like it was honoured.
    """
    available = MODES[mode].stances
    if stance is None:
        return available[0] if available else None
    if not available:
        raise ValueError(f"{mode.value} mode has no operating stances")
    try:
        parsed = Stance(stance)
    except ValueError:
        raise ValueError(
            f"no stance '{stance}'. Available: {', '.join(s.value for s in available)}"
        ) from None
    if parsed not in available:
        raise ValueError(f"{mode.value} mode does not offer the {parsed.value} stance")
    return parsed
