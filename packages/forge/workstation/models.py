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

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel

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


class Workspace(FrozenModel):
    """A named arrangement of panels, owned by the operator."""

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
