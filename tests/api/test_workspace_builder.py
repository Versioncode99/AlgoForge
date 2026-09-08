"""Building a workspace from a stated purpose.

The product promise is that "build me an NQ London breakout research
workstation" and building the same thing by hand end in the same place. That
only holds if construction is deterministic and separable from language: the
model turns a sentence into a profile, and this turns a profile into panels.

So the properties here are about the second half. It must be reproducible, it
must never produce a layout that cannot be rendered, and it must never turn the
profile into a restriction.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from forge.workstation import Panel, WorkspaceProfile, layout_for, markets_for, timeframe_for
from forge.workstation.templates import TEMPLATES
from forge_api.actions import ActionError
from forge_api.main import create_app


@pytest.fixture
def actions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    return create_app(tmp_path / "builder.db").state.actions


def _overlaps(panels: tuple[Panel, ...]) -> list[tuple[str, str]]:
    clashes = []
    for index, a in enumerate(panels):
        for b in panels[index + 1 :]:
            if (
                a.x < b.x + b.width
                and b.x < a.x + a.width
                and a.y < b.y + b.height
                and b.y < a.y + a.height
            ):
                clashes.append((a.panel_id, b.panel_id))
    return clashes


# ── the layout must be renderable ────────────────────────────────────────────


@pytest.mark.parametrize(
    "profile",
    [
        WorkspaceProfile(
            purpose="prop_trading",
            markets=("NQ", "ES"),
            style="scalp",
            sessions=("London", "New York"),
            research=("breakout",),
            risk="prop_firm",
            datasets=("nq_1m_16y",),
        ),
        WorkspaceProfile(purpose="quant_research", research=("momentum",)),
        WorkspaceProfile(markets=("NQ",), style="swing"),
        WorkspaceProfile(markets=("NQ", "ES", "GC", "MNQ", "MES"), style="intraday"),
        WorkspaceProfile(),
    ],
    ids=["prop-scalper", "quant", "single-market", "many-markets", "empty"],
)
def test_no_built_layout_places_one_panel_on_top_of_another(
    profile: WorkspaceProfile,
) -> None:
    """Regression: two panels landed at x=8 and x=9, both three columns wide.

    On a CSS grid the later one simply covers the earlier, so a panel the
    operator asked for is silently invisible. It is not a rendering curiosity,
    it is a missing feature that looks like nothing at all.
    """
    panels = layout_for(profile)
    assert _overlaps(panels) == []
    assert panels, "every profile produces somewhere to start"


def test_the_shipped_templates_do_not_overlap_either() -> None:
    for key, template in TEMPLATES.items():
        assert _overlaps(template.panels) == [], key


def test_building_is_reproducible() -> None:
    """"Build me another of these for ES" needs a checkable answer."""
    profile = WorkspaceProfile(markets=("NQ",), style="scalp", research=("breakout",))
    first = layout_for(profile)
    second = layout_for(profile)
    assert [p.model_dump() for p in first] == [p.model_dump() for p in second]


# ── the profile is read, not obeyed ──────────────────────────────────────────


def test_style_picks_the_timeframe() -> None:
    assert timeframe_for(WorkspaceProfile(style="scalp")) == "1m"
    assert timeframe_for(WorkspaceProfile(style="swing")) == "1h"
    assert timeframe_for(WorkspaceProfile(style="position")) == "1d"
    # Not a failure and not a guess dressed up as knowledge.
    assert timeframe_for(WorkspaceProfile(style="whatever")) == "5m"


def test_a_market_with_no_archive_gets_no_chart() -> None:
    """A chart for an instrument with no bars would have nothing to draw."""
    assert markets_for(WorkspaceProfile(markets=("NQ", "WHEAT"))) == ("NQ",)


def test_two_markets_are_linked_and_one_is_not() -> None:
    """A link group of one is a label with nothing to synchronise."""
    pair = layout_for(WorkspaceProfile(markets=("NQ", "ES")))
    charts = [p for p in pair if p.kind == "chart"]
    assert len(charts) == 2
    assert {c.link_group for c in charts} == {"linked"}

    single = layout_for(WorkspaceProfile(markets=("NQ",)))
    assert [p.link_group for p in single if p.kind == "chart"] == [None]


# ── through the action, the way the agent reaches it ─────────────────────────


def test_the_action_builds_and_explains(actions) -> None:
    built = actions.call(
        "build_workspace",
        {
            "name": "NQ London breakout",
            "purpose": "prop_trading",
            "markets": ["NQ", "ES"],
            "style": "scalp",
            "sessions": ["London"],
            "research": ["breakout"],
            "risk": "prop_firm",
        },
    )

    kinds = {p["kind"] for p in built["panels"]}
    assert "chart" in kinds
    assert "prop" in kinds, "a prop-firm profile gets the prop surface"
    assert built["explanation"]
    assert built["profile"]["markets"] == ["NQ", "ES"]
    assert built["panels"][0]["settings"]["timeframe"] == "1m", "scalp is a fast chart"


def test_the_action_names_markets_it_could_not_chart(actions) -> None:
    """Dropped in silence would be the dishonest version."""
    built = actions.call(
        "build_workspace", {"name": "Mixed", "markets": ["NQ", "WHEAT", "COCOA"]}
    )
    assert built["markets_without_archives"] == ["WHEAT", "COCOA"]


def test_a_built_workspace_is_an_ordinary_workspace(actions) -> None:
    """The profile records what was asked for. It never decides what is allowed."""
    built = actions.call(
        "build_workspace",
        {"name": "Research desk", "purpose": "quant_research", "research": ["momentum"]},
    )
    workspace_id = built["workspace_id"]

    # A panel the profile never asked for, on a desk built from a profile.
    after = actions.call("add_panel", {"workspace_id": workspace_id, "kind": "dom"})
    assert "dom" in {p["kind"] for p in after["panels"]}

    # And one it did ask for can go.
    first = built["panels"][0]["panel_id"]
    stripped = actions.call(
        "remove_panel", {"workspace_id": workspace_id, "panel_id": first}
    )
    assert first not in {p["panel_id"] for p in stripped["panels"]}


def test_the_builder_needs_a_name(actions) -> None:
    with pytest.raises(ActionError, match="name"):
        actions.call("build_workspace", {"name": "  "})
