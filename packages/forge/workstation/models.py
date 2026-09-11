"""The user's workstation: what is on screen, and where.

**On the word "workspace".** It means two unrelated things in this codebase and
conflating them would be expensive. `forge.vault.location.Workspace` is a *place
on disk* — where research is stored. A `Workspace` here is a *screen layout* —
which panels a person has open and how they are arranged. One is infrastructure
the operator never thinks about; the other is the thing they build and keep.
They are versioned separately, stored separately, and one can be deleted without
touching the other.

A layout is not research. Nothing in this module can affect a verdict, an
artifact or a holdout, and that separation is deliberate: rearranging panels
must never be able to corrupt evidence.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel
from forge.workstation.sidebar import Sidebar, default_sidebar_for

# The layout grid. Twelve columns is the usual compromise: divisible by 2, 3, 4
# and 6, so halves, thirds and quarters all land on whole numbers.
GRID_COLUMNS = 12
MAX_ROWS = 100
MAX_PANELS = 40


class PanelKind(StrEnum):
    """What a panel shows.

    A closed set, for the same reason the action registry is closed: the agent
    can add panels, and "add a panel of any kind you like" is not a bounded
    capability. Adding a kind is a code change with a component behind it, which
    is what stops the registry from ever describing something that cannot render.
    """

    # Market
    CHART = "chart"
    WATCHLIST = "watchlist"
    REPLAY = "replay"
    # Research
    STRATEGIES = "strategies"
    EXPERIMENTS = "experiments"
    RUNS = "runs"
    VALIDATION = "validation"
    EVIDENCE = "evidence"
    RESEARCH_MEMORY = "research_memory"
    LINEAGE = "lineage"
    DATA_HEALTH = "data_health"
    RESEARCH_LIBRARY = "research_library"
    # Execution. Present as surfaces long before anything can trade; see
    # `forge.execution` for the boundary that keeps them inert.
    DOM = "dom"
    ORDER_TICKET = "order_ticket"
    POSITIONS = "positions"
    ORDERS = "orders"
    ACCOUNT = "account"
    RISK = "risk"
    PROP = "prop"
    # The Prop Desk. Many accounts behind one set of deterministic controls;
    # see `forge.propdesk`. Separate kinds from the single-account `PROP` panel
    # because the question is different: that one asks how close *this* account
    # is to breaching, and these ask what every account is doing and why.
    DESK_ACCOUNTS = "desk_accounts"
    DESK_COPY = "desk_copy"
    DESK_ALLOCATION = "desk_allocation"
    DESK_NEWS = "desk_news"
    DESK_ACTIVITY = "desk_activity"
    DESK_RISK = "desk_risk"
    DESK_AI = "desk_ai"
    # Fund. The Hedge Fund loop's own surfaces, each backed by a deterministic
    # engine rather than by a narrative: `forge.portfolio`, `forge.risk.portfolio`,
    # `forge.execution.gate` and the approval and audit stores. They are panel
    # kinds and not merely screens so a fund operator can put the gate next to
    # the portfolio it is refusing orders from.
    FUND_SUMMARY = "fund_summary"
    PORTFOLIO = "portfolio"
    PRETRADE_GATE = "pretrade_gate"
    APPROVALS = "approvals"
    AUDIT = "audit"
    # Operations
    AGENT = "agent"
    ACTIVITY = "activity"
    LOGS = "logs"
    NOTES = "notes"


#: Panels whose content is execution rather than research. Kept as data so the
#: boundary can be asserted in a test rather than remembered by a reader.
EXECUTION_PANELS = frozenset(
    {
        PanelKind.DOM,
        PanelKind.ORDER_TICKET,
        PanelKind.POSITIONS,
        PanelKind.ORDERS,
        PanelKind.ACCOUNT,
        PanelKind.RISK,
        PanelKind.DESK_ACCOUNTS,
        PanelKind.DESK_COPY,
        PanelKind.DESK_ALLOCATION,
        PanelKind.DESK_ACTIVITY,
        # Risk sizing is execution: the fraction it moves is what an order is
        # sized from. The AI panel is here for the same reason — it is where
        # autonomous deployment is switched on.
        PanelKind.DESK_RISK,
        PanelKind.DESK_AI,
    }
)


class Panel(FrozenModel):
    """One rectangle on the grid.

    Geometry is in grid units, not pixels: a layout built on a 2560px monitor
    has to mean something on a 1280px one, and pixels do not survive that.
    """

    panel_id: str
    kind: PanelKind
    title: str = ""
    x: int = Field(ge=0, lt=GRID_COLUMNS)
    y: int = Field(ge=0, lt=MAX_ROWS)
    width: int = Field(ge=1, le=GRID_COLUMNS)
    height: int = Field(ge=1, le=MAX_ROWS)
    #: Panel-specific state: a chart's symbol and timeframe, a table's filter.
    #: Free-form because each kind needs different things, and a schema per kind
    #: would have to be versioned per kind.
    settings: dict[str, Any] = Field(default_factory=dict)
    #: Panels sharing a link group follow each other's symbol and timeframe.
    #: `None` means the panel is independent, which is the default.
    link_group: str | None = None
    collapsed: bool = False

    @model_validator(mode="after")
    def _fits_the_grid(self) -> Panel:
        if self.x + self.width > GRID_COLUMNS:
            raise ValueError(
                f"panel {self.panel_id} runs off the grid: "
                f"x={self.x} + width={self.width} > {GRID_COLUMNS}"
            )
        if self.y + self.height > MAX_ROWS:
            raise ValueError(f"panel {self.panel_id} runs past the last row")
        return self

    def display_title(self) -> str:
        """The title to render, which is never empty."""
        if self.title:
            return self.title
        symbol = self.settings.get("symbol")
        if self.kind is PanelKind.CHART and symbol:
            timeframe = self.settings.get("timeframe", "")
            return f"{symbol} {timeframe}".strip()
        return self.kind.replace("_", " ").title()


class WorkspaceProfile(FrozenModel):
    """What the operator said they were here to do.

    Recorded so a layout can be rebuilt or adapted later ("make me one of these
    for ES"), and so the agent has something to reason about beyond the panels
    themselves. It configures a starting point. It never restricts anything:
    nothing reads this to decide whether a feature is allowed.
    """

    purpose: str = ""
    markets: tuple[str, ...] = ()
    style: str = ""
    sessions: tuple[str, ...] = ()
    research: tuple[str, ...] = ()
    risk: str = ""
    datasets: tuple[str, ...] = ()
    execution: Literal["paper_only", "live"] = "paper_only"
    preferred_export: str = ""


class WorkspaceKind(StrEnum):
    """Where a workspace came from.

    Provenance, not capability. A built-in workspace is editable exactly like a
    user-created one; what the kind buys is that "restore the default layout"
    has something to restore *to*, and that a listing can offer the four
    familiar environments above the operator's own.
    """

    BUILT_IN = "BUILT_IN"
    USER_CREATED = "USER_CREATED"
    CLONED = "CLONED"


class Workspace(FrozenModel):
    """A named arrangement of panels **and navigation**, owned by the operator.

    The sidebar is on here rather than on the mode for the reason
    `forge.workstation.sidebar` sets out at length: navigation used to be a
    constant of the mode, so wanting two features from two modes meant switching
    between them and losing the arrangement each time. A workspace is now a
    composition of capabilities — a chart, some accounts, a research campaign,
    an agent monitor — and the rail is part of the composition.
    """

    schema_version: Literal["1"] = "1"
    workspace_id: str
    name: str = Field(min_length=1, max_length=120)
    panels: tuple[Panel, ...] = ()
    profile: WorkspaceProfile | None = None
    #: The template this started from, kept for provenance only. Changing the
    #: layout does not detach it, and the template does not constrain it.
    template_key: str | None = None
    created_at: datetime
    updated_at: datetime
    #: What this workspace is for, in the operator's own words.
    description: str = Field(default="", max_length=600)
    #: One or two characters shown in the switcher. Deliberately free text: an
    #: emoji, an initial, a ticker.
    icon: str = Field(default="", max_length=8)
    kind: WorkspaceKind = WorkspaceKind.USER_CREATED
    #: The navigation rail. Empty means "fall back to the mode's sections",
    #: which is how a workspace saved before sidebars existed still opens.
    sidebar: Sidebar | None = None
    #: Pinned workspaces sort first in the switcher.
    pinned: bool = False
    #: Research campaigns and trading accounts this workspace is about. Links,
    #: not permissions: putting a campaign here does not start it and putting an
    #: account here does not connect it.
    campaign_ids: tuple[str, ...] = ()
    account_ids: tuple[str, ...] = ()
    #: The built-in mode this workspace descends from, when it descends from
    #: one. Used only to rebuild a default sidebar on request.
    mode: str = ""

    @field_validator("panels")
    @classmethod
    def _panels_are_sane(cls, panels: tuple[Panel, ...]) -> tuple[Panel, ...]:
        if len(panels) > MAX_PANELS:
            raise ValueError(f"a workspace holds at most {MAX_PANELS} panels")
        seen = [panel.panel_id for panel in panels]
        duplicates = {panel_id for panel_id in seen if seen.count(panel_id) > 1}
        if duplicates:
            raise ValueError(f"duplicate panel ids: {', '.join(sorted(duplicates))}")
        return panels

    # ── panel operations, all returning a new workspace ──────────────────────
    # Immutability is the point: a failed edit leaves the caller holding the
    # workspace it started with, so a rejected `move_panel` cannot half-apply.

    def panel(self, panel_id: str) -> Panel | None:
        return next((p for p in self.panels if p.panel_id == panel_id), None)

    def require(self, panel_id: str) -> Panel:
        found = self.panel(panel_id)
        if found is None:
            known = ", ".join(p.panel_id for p in self.panels) or "none"
            raise KeyError(f"no panel '{panel_id}' in this workspace. Panels: {known}")
        return found

    def with_panel(self, panel: Panel) -> Workspace:
        if self.panel(panel.panel_id) is not None:
            raise ValueError(f"panel '{panel.panel_id}' is already here")
        return self._touch(panels=(*self.panels, panel))

    def without_panel(self, panel_id: str) -> Workspace:
        self.require(panel_id)
        return self._touch(panels=tuple(p for p in self.panels if p.panel_id != panel_id))

    def replacing_panel(self, panel: Panel) -> Workspace:
        self.require(panel.panel_id)
        return self._touch(
            panels=tuple(panel if p.panel_id == panel.panel_id else p for p in self.panels)
        )

    def _touch(self, **changes: Any) -> Workspace:
        return self.model_copy(update={**changes, "updated_at": datetime.now(UTC)})

    def renamed(self, name: str) -> Workspace:
        return self._touch(name=name)

    def collapsing(self, panel_id: str, collapsed: bool) -> Workspace:
        """Fold a panel to its title bar, or unfold it.

        A separate operation from resizing because it is reversible without
        remembering anything: the panel keeps its geometry and the interface
        simply draws it as a bar, so unfolding restores exactly what was there.
        """
        panel = self.require(panel_id)
        return self.replacing_panel(panel.model_copy(update={"collapsed": bool(collapsed)}))

    def reordered(self, panel_id: str, position: int) -> Workspace:
        """Move a panel within the stacking order.

        Order in `panels` is draw order: later panels are drawn over earlier
        ones and appear later in tab order. Moving a panel to the end is what
        "bring to front" means, and there is no separate z-index to fall out of
        step with the list.
        """
        self.require(panel_id)
        remaining = [p for p in self.panels if p.panel_id != panel_id]
        moved = self.require(panel_id)
        index = max(0, min(len(remaining), int(position)))
        remaining.insert(index, moved)
        return self._touch(panels=tuple(remaining))

    # ── the sidebar ──────────────────────────────────────────────────────────
    # Each of these is a thin wrapper over the same operation on `Sidebar`, for
    # one reason: a caller holding a workspace should never have to take the
    # sidebar out, edit it and put it back, because the day somebody forgets the
    # last step is the day an edit silently does nothing.

    def rail(self) -> Sidebar:
        """This workspace's sidebar, falling back to its mode's default.

        A workspace saved before sidebars existed has ``None`` here and opens
        with the rail its mode always had, rather than with nothing.
        """
        if self.sidebar is not None:
            return self.sidebar
        if self.mode:
            try:
                return default_sidebar_for(self.mode)
            except (KeyError, ValueError):
                return Sidebar()
        return Sidebar()

    def with_sidebar(self, sidebar: Sidebar) -> Workspace:
        return self._touch(sidebar=sidebar)

    def _edit_rail(self, operation: str, *args: Any, **kwargs: Any) -> Workspace:
        """Apply one sidebar operation and keep the result.

        Errors propagate as `SidebarError`, which the HTTP layer turns into a
        refusal naming what was wrong. Nothing here is caught and ignored: a
        sidebar edit that quietly did nothing is worse than one that failed.
        """
        rail = getattr(self.rail(), operation)(*args, **kwargs)
        return self.with_sidebar(rail)

    def adding_sidebar_item(
        self, route: str, *, group_id: str | None = None, label: str = "",
        position: int | None = None,
    ) -> Workspace:
        return self._edit_rail(
            "with_item", route, group_id=group_id, label=label, position=position
        )

    def removing_sidebar_item(self, route: str) -> Workspace:
        return self._edit_rail("without_item", route)

    def moving_sidebar_item(
        self, route: str, *, group_id: str, position: int | None = None
    ) -> Workspace:
        return self._edit_rail("moved_item", route, group_id=group_id, position=position)

    def renaming_sidebar_item(self, route: str, label: str) -> Workspace:
        return self._edit_rail("renamed_item", route, label)

    def pinning_sidebar_item(self, route: str, pinned: bool) -> Workspace:
        return self._edit_rail("pinned_item", route, pinned)

    def hiding_sidebar_item(self, route: str, hidden: bool) -> Workspace:
        return self._edit_rail("hidden_item", route, hidden)

    def adding_sidebar_group(
        self, group_id: str, label: str, *, position: int | None = None
    ) -> Workspace:
        return self._edit_rail("with_group", group_id, label, position=position)

    def removing_sidebar_group(self, group_id: str) -> Workspace:
        return self._edit_rail("without_group", group_id)

    def renaming_sidebar_group(self, group_id: str, label: str) -> Workspace:
        return self._edit_rail("renamed_group", group_id, label)

    def collapsing_sidebar_group(self, group_id: str, collapsed: bool) -> Workspace:
        return self._edit_rail("collapsed_group", group_id, collapsed)

    def reordering_sidebar_groups(self, group_ids: Sequence[str]) -> Workspace:
        return self._edit_rail("reordered_groups", group_ids)

    def with_default_sidebar(self) -> Workspace:
        """Throw the rail away and rebuild it from the mode. Reversible only by undo."""
        return self.with_sidebar(default_sidebar_for(self.mode) if self.mode else Sidebar())

    # ── links ────────────────────────────────────────────────────────────────
    def linking_campaign(self, campaign_id: str, linked: bool = True) -> Workspace:
        """Associate a research campaign with this workspace.

        A link, never a grant: it puts the campaign on this screen and changes
        nothing about whether it runs or what it is allowed to do.
        """
        current = [c for c in self.campaign_ids if c != campaign_id]
        if linked:
            current.append(campaign_id)
        return self._touch(campaign_ids=tuple(current))

    def linking_account(self, account_id: str, linked: bool = True) -> Workspace:
        current = [a for a in self.account_ids if a != account_id]
        if linked:
            current.append(account_id)
        return self._touch(account_ids=tuple(current))

    def described(self, description: str = "", icon: str = "") -> Workspace:
        changes: dict[str, Any] = {}
        if description:
            changes["description"] = description[:600]
        if icon:
            changes["icon"] = icon[:8]
        return self._touch(**changes) if changes else self

    def pinning(self, pinned: bool) -> Workspace:
        return self._touch(pinned=bool(pinned))

    def linked(self, link_group: str | None, panel_ids: tuple[str, ...]) -> Workspace:
        for panel_id in panel_ids:
            self.require(panel_id)
        return self._touch(
            panels=tuple(
                p.model_copy(update={"link_group": link_group})
                if p.panel_id in panel_ids
                else p
                for p in self.panels
            )
        )


def new_panel_id(kind: PanelKind, existing: tuple[str, ...]) -> str:
    """A short, readable, unique id — `chart-1`, `chart-2`.

    Readable because these appear in agent transcripts: "add an indicator to
    chart-2" is a sentence a person can check, and a uuid is not.
    """
    index = 1
    while f"{kind}-{index}" in existing:
        index += 1
    return f"{kind}-{index}"


def new_workspace_id(name: str, created_at: datetime) -> str:
    return stable_id("workspace", {"name": name, "at": created_at.isoformat()})
