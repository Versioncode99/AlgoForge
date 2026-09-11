"""Starting points, not modes.

A template produces a set of panels and then stops existing. It does not gate
features, it is not remembered as a restriction, and a workspace built from one
can be changed into anything else — the `template_key` a workspace carries is
provenance, not a category. The difference matters: a mode tells the operator
what they are allowed to do, and this product's position is that the workspace
belongs to them.

Every template here uses panels that actually render. A template offering a
panel with nothing behind it would be a fake, and the empty-state rule applies
to layouts as much as to data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forge.workstation.models import GRID_COLUMNS, Panel, PanelKind


@dataclass(frozen=True)
class WorkspaceTemplate:
    key: str
    name: str
    summary: str
    panels: tuple[Panel, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "summary": self.summary,
            "panel_count": len(self.panels),
            "panels": [p.kind.value for p in self.panels],
        }


def _panel(
    kind: PanelKind,
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    index: int = 1,
    **settings: Any,
) -> Panel:
    return Panel(
        panel_id=f"{kind.value}-{index}",
        kind=kind,
        x=x,
        y=y,
        width=width,
        height=height,
        settings=settings,
    )


HALF = GRID_COLUMNS // 2

_TEMPLATES: tuple[WorkspaceTemplate, ...] = (
    WorkspaceTemplate(
        key="blank",
        name="Blank",
        summary="Nothing at all. Build it yourself.",
        panels=(),
    ),
    WorkspaceTemplate(
        key="quant_researcher",
        name="Quant Researcher",
        summary="The evidence trail: strategies, their experiments, and what the judge said.",
        panels=(
            _panel(PanelKind.STRATEGIES, 0, 0, 7, 8),
            _panel(PanelKind.EVIDENCE, 7, 0, 5, 8),
            _panel(PanelKind.EXPERIMENTS, 0, 8, 6, 6),
            _panel(PanelKind.RESEARCH_MEMORY, 6, 8, 6, 6),
        ),
    ),
    WorkspaceTemplate(
        key="strategy_developer",
        name="Strategy Developer",
        summary="One strategy at a time, on the bars it traded, with its validation beside it.",
        panels=(
            _panel(PanelKind.CHART, 0, 0, 8, 9, symbol="NQ", timeframe="1m"),
            _panel(PanelKind.STRATEGIES, 8, 0, 4, 9),
            _panel(PanelKind.VALIDATION, 0, 9, 6, 5),
            _panel(PanelKind.RUNS, 6, 9, 6, 5),
        ),
    ),
    WorkspaceTemplate(
        key="prop_trader",
        name="Prop Trader",
        summary="Chart, watchlist and the prop rules that decide whether an account survives.",
        panels=(
            _panel(PanelKind.CHART, 0, 0, 8, 9, symbol="MNQ", timeframe="1m"),
            _panel(PanelKind.WATCHLIST, 8, 0, 4, 5),
            _panel(PanelKind.RISK, 8, 5, 4, 4),
            _panel(PanelKind.PROP, 0, 9, 7, 5),
            _panel(PanelKind.ACTIVITY, 7, 9, 5, 5),
        ),
    ),
    WorkspaceTemplate(
        key="systematic_trader",
        name="Systematic Trader",
        summary="Two linked charts over the index pair, with the run log underneath.",
        panels=(
            _panel(
                PanelKind.CHART, 0, 0, HALF, 8, index=1, symbol="NQ", timeframe="5m"
            ).model_copy(update={"link_group": "index"}),
            _panel(
                PanelKind.CHART, HALF, 0, HALF, 8, index=2, symbol="ES", timeframe="5m"
            ).model_copy(update={"link_group": "index"}),
            _panel(PanelKind.WATCHLIST, 0, 8, 4, 6),
            _panel(PanelKind.RUNS, 4, 8, 8, 6),
        ),
    ),
    WorkspaceTemplate(
        key="data_steward",
        name="Data Steward",
        summary="What the datasets actually contain, and where the gaps are.",
        panels=(
            _panel(PanelKind.DATA_HEALTH, 0, 0, 12, 7),
            _panel(PanelKind.CHART, 0, 7, 7, 7, symbol="NQ", timeframe="1m"),
            _panel(PanelKind.LOGS, 7, 7, 5, 7),
        ),
    ),
)

#: The default layout each operating mode is seeded with the first time it is
#: entered (§22). Kept beside the hand-picked templates above rather than in
#: `forge.modes`, because they are the same kind of object and a second registry
#: of layouts is a second place for a panel kind to go stale.
#:
#: These four are still templates, not modes: a workspace built from one can be
#: rearranged into anything, and `template_key` remains provenance. What the
#: mode contributes is only *which one is seeded*.
_MODE_TEMPLATES: tuple[WorkspaceTemplate, ...] = (
    WorkspaceTemplate(
        key="normal_desk",
        name="Trading Desk",
        summary="A chart, what you are watching, your book, and the strategies behind it.",
        panels=(
            _panel(PanelKind.CHART, 0, 0, 8, 9, symbol="NQ", timeframe="5m"),
            _panel(PanelKind.WATCHLIST, 8, 0, 4, 4, symbols="NQ, ES, GC"),
            _panel(PanelKind.POSITIONS, 8, 4, 4, 5),
            _panel(PanelKind.STRATEGIES, 0, 9, 7, 5),
            _panel(PanelKind.ACTIVITY, 7, 9, 5, 5),
        ),
    ),
    WorkspaceTemplate(
        key="prop_desk",
        name="Prop Desk",
        summary="The account's rule status first, then the chart it is being traded on.",
        panels=(
            # The prop panel is widest and first because the mode exists to answer
            # one question, and the answer should not be something the operator
            # scrolls to.
            _panel(PanelKind.PROP, 0, 0, 8, 8),
            _panel(PanelKind.RISK, 8, 0, 4, 8),
            _panel(PanelKind.CHART, 0, 8, 7, 6, symbol="MNQ", timeframe="1m"),
            _panel(PanelKind.POSITIONS, 7, 8, 5, 6),
        ),
    ),
    WorkspaceTemplate(
        key="multi_account_desk",
        name="Multi-Account Desk",
        summary=(
            "Every connected account, what each is running, and every order the "
            "desk refused and why."
        ),
        panels=(
            # Accounts first and widest: the question this layout exists for is
            # "what is every account doing", and an answer that has to be
            # scrolled to is not one. The activity panel is beside it rather than
            # below because a refusal is only useful next to the account it
            # refused for.
            _panel(PanelKind.DESK_ACCOUNTS, 0, 0, 7, 7),
            _panel(PanelKind.DESK_ACTIVITY, 7, 0, 5, 7),
            _panel(PanelKind.DESK_ALLOCATION, 0, 7, 4, 6),
            _panel(PanelKind.DESK_COPY, 4, 7, 4, 6),
            _panel(PanelKind.DESK_NEWS, 8, 7, 4, 6),
        ),
    ),
    WorkspaceTemplate(
        key="ai_desk",
        name="AI Workspace",
        summary="The specialists, what they have done, and the work they have done it to.",
        panels=(
            _panel(PanelKind.AGENT, 0, 0, 7, 8),
            _panel(PanelKind.ACTIVITY, 7, 0, 5, 8),
            _panel(PanelKind.STRATEGIES, 0, 8, 6, 6),
            _panel(PanelKind.EXPERIMENTS, 6, 8, 6, 6),
        ),
    ),
    WorkspaceTemplate(
        key="fund_command",
        name="Fund Command",
        summary="NAV and the loop across the top, then portfolio, risk and the gate.",
        panels=(
            _panel(PanelKind.FUND_SUMMARY, 0, 0, 12, 5),
            _panel(PanelKind.PORTFOLIO, 0, 5, 5, 6),
            _panel(PanelKind.RISK, 5, 5, 4, 6),
            _panel(PanelKind.PRETRADE_GATE, 9, 5, 3, 6),
            _panel(PanelKind.APPROVALS, 0, 11, 4, 5),
            _panel(PanelKind.AUDIT, 4, 11, 5, 5),
            _panel(PanelKind.ACTIVITY, 9, 11, 3, 5),
        ),
    ),
)

TEMPLATES: dict[str, WorkspaceTemplate] = {
    t.key: t for t in (*_TEMPLATES, *_MODE_TEMPLATES)
}


def template(key: str) -> WorkspaceTemplate:
    found = TEMPLATES.get(key)
    if found is None:
        raise KeyError(f"no template '{key}'. Available: {', '.join(sorted(TEMPLATES))}")
    return found


def catalogue() -> list[dict[str, Any]]:
    return [t.as_dict() for t in (*_TEMPLATES, *_MODE_TEMPLATES)]
