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

TEMPLATES: dict[str, WorkspaceTemplate] = {t.key: t for t in _TEMPLATES}


def template(key: str) -> WorkspaceTemplate:
    found = TEMPLATES.get(key)
    if found is None:
        raise KeyError(f"no template '{key}'. Available: {', '.join(sorted(TEMPLATES))}")
    return found


def catalogue() -> list[dict[str, Any]]:
    return [t.as_dict() for t in _TEMPLATES]
