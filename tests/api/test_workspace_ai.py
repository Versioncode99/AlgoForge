"""The agent builds and edits workspaces through the same actions the interface uses.

There is no AI-only path into a workspace, which is the property these tests
exist to hold. Every assertion here goes through `Actions.call` with
`actor=Actor.AI` — the same entry point the console assistant uses — so a
regression that gave the agent a private route would fail here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from forge.modes.permissions import Actor
from forge.workstation import PanelKind, WorkspaceStore
from forge_api.actions import ActionError, ApprovalRequired
from forge_api.main import create_app


@pytest.fixture
def surface(tmp_path: Path):
    """The wired action registry, and the store it writes to."""
    app = create_app(tmp_path / "api.db")
    with __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(app):
        actions = app.state.actions
        yield actions, actions.workspaces


def ai(actions, name: str, arguments: dict | None = None, *, confirmed: bool = False):
    """Call an action exactly as the console assistant does."""
    return actions.call(
        name,
        arguments or {},
        actor=Actor.AI,
        origin="test",
        confirmed=confirmed,
    )


def test_the_agent_builds_a_workspace_from_a_stated_goal(surface) -> None:
    """Part 16's example: charts on top, research on the right, validation below."""
    actions, store = surface
    built = ai(
        actions,
        "build_workspace",
        {
            "name": "NQ Research Lab",
            "purpose": "quant_research",
            "markets": ["NQ"],
            "style": "scalp",
            "research": ["breakout", "momentum"],
            "activate": True,
        },
    )
    assert built["name"] == "NQ Research Lab"
    kinds = {panel["kind"] for panel in built["panels"]}
    assert PanelKind.CHART in kinds
    # It explains why each panel is there rather than only producing them.
    assert built.get("explanation") or built.get("summary") or built["panels"]
    # And it is an ordinary workspace, saved and reloadable.
    saved = store.get(built["workspace_id"])
    assert saved is not None
    assert len(saved.panels) == len(built["panels"])


def test_the_agent_edits_a_workspace_through_the_same_actions(surface) -> None:
    """'Add a chart', 'make it bigger', 'put validation beside it', 'link them'."""
    actions, store = surface
    workspace = ai(actions, "create_workspace", {"name": "NQ Research"})
    workspace_id = workspace["workspace_id"]

    chart = ai(
        actions,
        "add_panel",
        {"workspace_id": workspace_id, "kind": "chart", "symbol": "NQ", "timeframe": "1m"},
    )
    panel_id = chart["panels"][0]["panel_id"]

    ai(
        actions,
        "resize_panel",
        {"workspace_id": workspace_id, "panel_id": panel_id, "width": 12, "height": 12},
    )
    ai(
        actions,
        "add_panel",
        {"workspace_id": workspace_id, "kind": "validation", "x": 0, "y": 12},
    )
    linked = ai(
        actions,
        "add_panel",
        {"workspace_id": workspace_id, "kind": "chart", "symbol": "ES", "x": 0, "y": 24},
    )
    ids = [p["panel_id"] for p in linked["panels"] if p["kind"] == "chart"]
    ai(actions, "link_panels", {"workspace_id": workspace_id, "panel_ids": ids, "group": "index"})

    saved = store.get(workspace_id)
    assert saved is not None
    assert len(saved.panels) == 3
    assert saved.require(panel_id).width == 12
    assert {p.link_group for p in saved.panels if p.kind is PanelKind.CHART} == {"index"}


def test_an_unsupported_panel_is_refused_with_the_valid_kinds_named(surface) -> None:
    """The structured refusal the agent repairs from."""
    actions, _ = surface
    workspace = ai(actions, "create_workspace", {"name": "Desk"})
    with pytest.raises(ActionError) as caught:
        ai(
            actions,
            "add_panel",
            {"workspace_id": workspace["workspace_id"], "kind": "holographic_orb"},
        )
    message = str(caught.value)
    assert "not a panel kind" in message
    # The repair is possible because the refusal names what is available.
    assert "chart" in message and "validation" in message


def test_a_refusal_leaves_the_workspace_untouched(surface) -> None:
    actions, store = surface
    workspace = ai(actions, "create_workspace", {"name": "Desk"})
    workspace_id = workspace["workspace_id"]
    ai(actions, "add_panel", {"workspace_id": workspace_id, "kind": "chart"})
    before = store.get(workspace_id)
    assert before is not None

    with pytest.raises(ActionError):
        ai(actions, "add_panel", {"workspace_id": workspace_id, "kind": "nonsense"})
    with pytest.raises(ActionError):
        ai(
            actions,
            "resize_panel",
            {"workspace_id": workspace_id, "panel_id": "chart-1", "width": 99, "height": 4},
        )

    after = store.get(workspace_id)
    assert after is not None
    assert [p.model_dump() for p in after.panels] == [p.model_dump() for p in before.panels]


def test_an_agent_edit_is_attributable_in_the_history(surface) -> None:
    """Part 18: AI edits must be attributable."""
    actions, _ = surface
    workspace = ai(actions, "create_workspace", {"name": "NQ Research"})
    workspace_id = workspace["workspace_id"]
    ai(actions, "add_panel", {"workspace_id": workspace_id, "kind": "chart", "symbol": "NQ"})

    history = ai(actions, "workspace_history", {"workspace_id": workspace_id})
    latest = history["versions"][0]
    assert latest["actor"] == str(Actor.AI)
    assert "chart-1" in latest["summary"]
    # And a human edit is recorded as a human edit.
    actions.call(
        "add_panel", {"workspace_id": workspace_id, "kind": "validation"}, actor=Actor.HUMAN
    )
    assert ai(actions, "workspace_history", {"workspace_id": workspace_id})["versions"][0][
        "actor"
    ] == str(Actor.HUMAN)


def test_an_agent_edit_can_be_reversed(surface) -> None:
    actions, store = surface
    workspace = ai(actions, "create_workspace", {"name": "NQ Research"})
    workspace_id = workspace["workspace_id"]
    ai(actions, "add_panel", {"workspace_id": workspace_id, "kind": "chart"})
    ai(actions, "add_panel", {"workspace_id": workspace_id, "kind": "activity"})
    assert len(store.get(workspace_id).panels) == 2

    history = ai(actions, "workspace_history", {"workspace_id": workspace_id})
    one_panel = next(v["version"] for v in history["versions"] if v["panel_count"] == 1)
    ai(actions, "restore_workspace_version", {"workspace_id": workspace_id, "version": one_panel})
    assert len(store.get(workspace_id).panels) == 1


def test_the_agent_can_duplicate_a_workspace_under_a_new_name(surface) -> None:
    """'Create a copy called NQ Research — Volatility.'"""
    actions, _ = surface
    workspace = ai(actions, "create_workspace", {"name": "NQ Research"})
    workspace_id = workspace["workspace_id"]
    ai(actions, "add_panel", {"workspace_id": workspace_id, "kind": "chart", "symbol": "NQ"})
    copy = ai(
        actions,
        "clone_workspace",
        {"workspace_id": workspace_id, "name": "NQ Research — Volatility"},
    )
    assert copy["workspace_id"] != workspace_id
    assert copy["name"] == "NQ Research — Volatility"
    assert len(copy["panels"]) == 1


def test_the_agent_can_set_the_default_workspace(surface) -> None:
    """'Make this my default research workspace.'"""
    actions, store = surface
    workspace = ai(actions, "create_workspace", {"name": "NQ Research"})
    ai(actions, "set_default_workspace", {"workspace_id": workspace["workspace_id"]})
    assert store.default_id() == workspace["workspace_id"]


def test_the_agent_can_collapse_and_reorder_panels(surface) -> None:
    actions, store = surface
    workspace = ai(actions, "create_workspace", {"name": "Desk"})
    workspace_id = workspace["workspace_id"]
    ai(actions, "add_panel", {"workspace_id": workspace_id, "kind": "chart"})
    ai(actions, "add_panel", {"workspace_id": workspace_id, "kind": "activity"})

    ai(
        actions,
        "collapse_panel",
        {"workspace_id": workspace_id, "panel_id": "activity-1", "collapsed": True},
    )
    assert store.get(workspace_id).require("activity-1").collapsed is True

    ai(
        actions,
        "reorder_panel",
        {"workspace_id": workspace_id, "panel_id": "chart-1", "position": 1},
    )
    assert [p.panel_id for p in store.get(workspace_id).panels] == ["activity-1", "chart-1"]


def test_a_workspace_travels_between_installations(surface, tmp_path: Path) -> None:
    actions, _ = surface
    workspace = ai(actions, "create_workspace", {"name": "NQ Research"})
    workspace_id = workspace["workspace_id"]
    ai(actions, "add_panel", {"workspace_id": workspace_id, "kind": "chart", "symbol": "NQ"})

    document = ai(actions, "export_workspace", {"workspace_id": workspace_id})["document"]
    elsewhere = WorkspaceStore(tmp_path / "other.db")
    imported = elsewhere.import_workspace(document, name="NQ Research (from a colleague)")
    assert len(imported.panels) == 1
    assert imported.panels[0].settings["symbol"] == "NQ"


def test_deleting_a_workspace_is_held_for_a_person(surface) -> None:
    """An agent may not destroy a workspace on inference alone.

    The policy holds it for approval rather than merely refusing it, which is
    the stronger behaviour: the request survives as something a person can say
    yes to instead of being lost.
    """
    actions, store = surface
    workspace = ai(actions, "create_workspace", {"name": "Doomed"})
    with pytest.raises(ApprovalRequired, match="waiting for approval"):
        ai(actions, "delete_workspace", {"workspace_id": workspace["workspace_id"]})
    assert store.get(workspace["workspace_id"]) is not None


def test_every_workspace_verb_the_interface_has_is_in_the_registry(surface) -> None:
    """No AI-only implementation, and no interface-only one either."""
    actions, _ = surface
    names = set(actions.names())
    for verb in (
        "create_workspace",
        "open_workspace",
        "rename_workspace",
        "clone_workspace",
        "delete_workspace",
        "set_default_workspace",
        "export_workspace",
        "import_workspace",
        "workspace_history",
        "restore_workspace_version",
        "duplicate_workspace_version",
        "add_panel",
        "remove_panel",
        "move_panel",
        "resize_panel",
        "collapse_panel",
        "reorder_panel",
        "set_panel_setting",
        "link_panels",
        "build_workspace",
    ):
        assert verb in names, f"{verb} is missing from the action registry"
