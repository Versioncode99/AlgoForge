"""The workspace engine, and the boundary that keeps it away from research.

The property that matters most here is not that panels move. It is that there is
exactly *one* way to move them. A person dragging a panel and an agent asked to
"put the DOM under the chart" go through the same action with the same
validation, because the alternative — a UI path and an AI path — is two
implementations that will disagree eventually, silently, and about state the
operator cares about.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from forge_api.actions import ActionError, ActionRisk
from forge_api.main import create_app


@pytest.fixture
def actions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    app = create_app(tmp_path / "workspaces.db")
    return app.state.actions


def _workspace(actions, template_key: str | None = "quant_researcher") -> str:
    created = actions.call(
        "create_workspace", {"name": "Desk", "template_key": template_key}
    )
    return str(created["workspace_id"])


# ── the layout itself ────────────────────────────────────────────────────────


def test_a_template_seeds_panels_and_then_stops_mattering(actions) -> None:
    """Templates are starting points. Nothing stays locked to one."""
    workspace_id = _workspace(actions, "quant_researcher")
    described = actions.call("describe_workspace", {"workspace_id": workspace_id})
    assert described["template_key"] == "quant_researcher"
    assert {p["kind"] for p in described["panels"]} == {
        "strategies",
        "evidence",
        "experiments",
        "research_memory",
    }

    # A panel from a completely different discipline, on a "researcher" desk.
    after = actions.call("add_panel", {"workspace_id": workspace_id, "kind": "dom"})
    assert "dom" in {p["kind"] for p in after["panels"]}
    # And the provenance is unchanged: it records where this started, not what
    # it is allowed to become.
    assert after["template_key"] == "quant_researcher"


def test_a_blank_workspace_starts_empty(actions) -> None:
    described = actions.call("describe_workspace", {"workspace_id": _workspace(actions, "blank")})
    assert described["panels"] == []


def test_panels_are_added_moved_resized_and_removed(actions) -> None:
    workspace_id = _workspace(actions, "blank")
    added = actions.call(
        "add_panel",
        {"workspace_id": workspace_id, "kind": "chart", "symbol": "nq", "timeframe": "5m"},
    )
    panel_id = added["panels"][0]["panel_id"]
    assert panel_id == "chart-1"
    assert added["panels"][0]["title"] == "NQ 5m"

    moved = actions.call(
        "move_panel", {"workspace_id": workspace_id, "panel_id": panel_id, "x": 2, "y": 3}
    )
    assert (moved["panels"][0]["x"], moved["panels"][0]["y"]) == (2, 3)

    sized = actions.call(
        "resize_panel",
        {"workspace_id": workspace_id, "panel_id": panel_id, "width": 4, "height": 5},
    )
    assert (sized["panels"][0]["width"], sized["panels"][0]["height"]) == (4, 5)

    assert actions.call(
        "remove_panel", {"workspace_id": workspace_id, "panel_id": panel_id}
    )["panels"] == []


def test_a_panel_cannot_be_pushed_off_the_grid(actions) -> None:
    """Geometry is validated on the way in, not discovered on the way out.

    A stored panel that overflows would load fine and fail at render, which is
    the worst place to find out — the operator sees a broken desk and no reason.
    """
    workspace_id = _workspace(actions, "blank")
    actions.call("add_panel", {"workspace_id": workspace_id, "kind": "chart", "width": 4})

    # Pushed right, it no longer fits at its current width.
    actions.call(
        "move_panel", {"workspace_id": workspace_id, "panel_id": "chart-1", "x": 8, "y": 0}
    )
    with pytest.raises(ActionError, match="grid"):
        actions.call(
            "resize_panel",
            {"workspace_id": workspace_id, "panel_id": "chart-1", "width": 8, "height": 4},
        )

    # And the rejection changed nothing: the panel is as it was.
    described = actions.call("describe_workspace", {"workspace_id": workspace_id})
    assert (described["panels"][0]["x"], described["panels"][0]["width"]) == (8, 4)


def test_a_new_panel_never_lands_on_top_of_an_existing_one(actions) -> None:
    workspace_id = _workspace(actions, "blank")
    actions.call(
        "add_panel", {"workspace_id": workspace_id, "kind": "chart", "height": 4}
    )
    second = actions.call("add_panel", {"workspace_id": workspace_id, "kind": "watchlist"})
    panels = {p["panel_id"]: p for p in second["panels"]}
    assert panels["watchlist-1"]["y"] >= panels["chart-1"]["y"] + panels["chart-1"]["height"]


def test_an_unknown_panel_kind_is_refused_with_the_list(actions) -> None:
    workspace_id = _workspace(actions, "blank")
    with pytest.raises(ActionError, match="not a panel kind"):
        actions.call("add_panel", {"workspace_id": workspace_id, "kind": "hologram"})


def test_an_indicator_is_refused_on_something_that_is_not_a_chart(actions) -> None:
    """Storing a setting nothing will ever read is a quiet lie about state."""
    workspace_id = _workspace(actions, "blank")
    actions.call("add_panel", {"workspace_id": workspace_id, "kind": "watchlist"})
    with pytest.raises(ActionError, match="not a chart"):
        actions.call(
            "add_indicator",
            {"workspace_id": workspace_id, "panel_id": "watchlist-1", "indicator": "vwap"},
        )


def test_indicators_accumulate_on_a_chart_without_duplicating(actions) -> None:
    workspace_id = _workspace(actions, "blank")
    actions.call("add_panel", {"workspace_id": workspace_id, "kind": "chart"})
    for indicator in ("vwap", "ema", "vwap"):
        result = actions.call(
            "add_indicator",
            {"workspace_id": workspace_id, "panel_id": "chart-1", "indicator": indicator},
        )
    assert result["panels"][0]["settings"]["indicators"] == ["vwap", "ema"]


def test_charts_can_be_linked_and_unlinked(actions) -> None:
    workspace_id = _workspace(actions, "systematic_trader")
    linked = actions.call(
        "link_panels",
        {"workspace_id": workspace_id, "panel_ids": ["chart-1", "chart-2"], "group": "pair"},
    )
    groups = {p["panel_id"]: p["link_group"] for p in linked["panels"]}
    assert groups["chart-1"] == groups["chart-2"] == "pair"

    unlinked = actions.call(
        "link_panels", {"workspace_id": workspace_id, "panel_ids": ["chart-1", "chart-2"]}
    )
    assert all(p["link_group"] is None for p in unlinked["panels"] if p["kind"] == "chart")


def test_a_missing_panel_is_refused_with_the_ones_that_exist(actions) -> None:
    workspace_id = _workspace(actions, "blank")
    actions.call("add_panel", {"workspace_id": workspace_id, "kind": "chart"})
    with pytest.raises(ActionError, match="chart-1"):
        actions.call(
            "move_panel", {"workspace_id": workspace_id, "panel_id": "chart-9", "x": 0, "y": 0}
        )


# ── persistence ──────────────────────────────────────────────────────────────


def test_a_layout_survives_a_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The workstation the operator built is still there tomorrow."""
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    first = create_app(tmp_path / "a.db").state.actions
    workspace_id = _workspace(first, "prop_trader")
    first.call(
        "add_panel", {"workspace_id": workspace_id, "kind": "chart", "symbol": "es"}
    )
    before = first.call("describe_workspace", {"workspace_id": workspace_id})

    # A second process against the same data root.
    second = create_app(tmp_path / "b.db").state.actions
    after = second.call("describe_workspace", {"workspace_id": workspace_id})
    assert after["panels"] == before["panels"]
    assert second.call("list_workspaces")["active"] == workspace_id


def test_cloning_leaves_the_original_alone(actions) -> None:
    """The "make me one of these for ES" move."""
    workspace_id = _workspace(actions, "systematic_trader")
    clone = actions.call("clone_workspace", {"workspace_id": workspace_id, "name": "ES desk"})
    assert clone["workspace_id"] != workspace_id
    assert clone["name"] == "ES desk"

    actions.call(
        "set_panel_setting",
        {
            "workspace_id": clone["workspace_id"],
            "panel_id": "chart-1",
            "key": "symbol",
            "value": "ES",
        },
    )
    original = actions.call("describe_workspace", {"workspace_id": workspace_id})
    assert original["panels"][0]["settings"]["symbol"] == "NQ"


def test_nothing_is_invented_when_no_workspace_is_open(actions) -> None:
    """A refusal that says what to do, rather than a desk nobody asked for."""
    with pytest.raises(ActionError, match="No workspace is open"):
        actions.call("add_panel", {"kind": "chart"})


# ── the safety boundary ──────────────────────────────────────────────────────


def test_deleting_a_workspace_needs_confirmation(actions) -> None:
    """An agent cannot decide on its own that a layout should stop existing."""
    workspace_id = _workspace(actions, "blank")

    with pytest.raises(ActionError, match="confirmation"):
        actions.call("delete_workspace", {"workspace_id": workspace_id})
    assert actions.call("describe_workspace", {"workspace_id": workspace_id})

    result = actions.call("delete_workspace", {"workspace_id": workspace_id}, confirmed=True)
    assert result["deleted"] is True


def test_every_action_declares_a_risk_tier(actions) -> None:
    """The tier is part of the contract, so a caller can decide what to prompt."""
    tiers = {str(tier) for tier in ActionRisk}
    for schema in actions.schemas():
        assert schema["risk"] in tiers, schema["name"]
        assert schema["requires_confirmation"] is (schema["risk"] != "safe")


def test_no_workspace_action_can_reach_research_state(actions) -> None:
    """Rearranging panels must not be able to touch evidence.

    Asserted structurally rather than by inspection: a layout is stored in its
    own database, and the workspace verbs only ever go through the workspace
    store. If one of them ever grew a path into the artifact store or the
    holdout ledger, this is where it should be noticed.
    """
    workspace_id = _workspace(actions, "quant_researcher")
    strategies_before = actions.call("list_strategies")
    actions.call("add_panel", {"workspace_id": workspace_id, "kind": "evidence"})
    actions.call("remove_panel", {"workspace_id": workspace_id, "panel_id": "strategies-1"})
    actions.call("delete_workspace", {"workspace_id": workspace_id}, confirmed=True)
    assert actions.call("list_strategies") == strategies_before
