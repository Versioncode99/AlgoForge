"""Docking through the action registry and over HTTP, which must be one path.

`tests/workstation/test_docking.py` pins the geometry. This pins the thing that
makes it a feature rather than a library: that an operator dragging a panel and
an agent asked to "put risk under the chart" reach the same validation, and that
a refusal arrives as a refusal rather than a 500.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge_api.actions import ActionError
from forge_api.main import create_app

#: Every route the interface calls is mounted here.
API = "/api/v1"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    return create_app()


@pytest.fixture
def actions(app: Any) -> Any:
    return app.state.actions


def workspace_with_one_panel(actions: Any) -> tuple[str, str]:
    created = actions.call("create_workspace", {"name": "Desk"})
    workspace_id = str(created["workspace_id"])
    actions.call("add_panel", {"workspace_id": workspace_id, "kind": "chart", "width": 12})
    described = actions.call("describe_workspace", {"workspace_id": workspace_id})
    return workspace_id, str(described["panels"][0]["panel_id"])


def panels(actions: Any, workspace_id: str) -> dict[str, dict[str, Any]]:
    described = actions.call("describe_workspace", {"workspace_id": workspace_id})
    return {str(p["panel_id"]): p for p in described["panels"]}


# ── through the registry, which is what an agent reaches ─────────────────────


def test_splitting_puts_a_new_panel_beside_the_one_split(actions: Any) -> None:
    workspace_id, chart = workspace_with_one_panel(actions)
    actions.call(
        "split_panel",
        {"workspace_id": workspace_id, "panel_id": chart, "kind": "risk", "along": "row"},
    )
    placed = panels(actions, workspace_id)
    assert len(placed) == 2
    added = next(p for pid, p in placed.items() if pid != chart)
    assert placed[chart]["width"] == added["width"] == 6
    assert added["x"] == 6


def test_splitting_downward_stacks_them_vertically(actions: Any) -> None:
    workspace_id, chart = workspace_with_one_panel(actions)
    actions.call(
        "split_panel",
        {"workspace_id": workspace_id, "panel_id": chart, "kind": "risk", "along": "column"},
    )
    placed = panels(actions, workspace_id)
    added = next(p for pid, p in placed.items() if pid != chart)
    assert added["y"] > 0
    assert added["x"] == placed[chart]["x"]


def test_a_direction_that_is_not_one_is_refused_by_name(actions: Any) -> None:
    """Defaulting a typo to 'row' would silently do the wrong thing."""
    workspace_id, chart = workspace_with_one_panel(actions)
    with pytest.raises(ActionError, match="not a direction"):
        actions.call(
            "split_panel",
            {
                "workspace_id": workspace_id,
                "panel_id": chart,
                "kind": "risk",
                "along": "diagonal",
            },
        )


def test_a_panel_kind_that_does_not_exist_lists_the_ones_that_do(actions: Any) -> None:
    workspace_id, chart = workspace_with_one_panel(actions)
    with pytest.raises(ActionError, match="not a panel kind"):
        actions.call(
            "split_panel", {"workspace_id": workspace_id, "panel_id": chart, "kind": "telepathy"}
        )


def test_stacking_makes_one_rectangle_with_the_arrival_showing(actions: Any) -> None:
    workspace_id, chart = workspace_with_one_panel(actions)
    actions.call(
        "split_panel", {"workspace_id": workspace_id, "panel_id": chart, "kind": "risk"}
    )
    risk = next(pid for pid in panels(actions, workspace_id) if pid != chart)

    actions.call("stack_panel", {"workspace_id": workspace_id, "panel_id": risk, "onto": chart})
    placed = panels(actions, workspace_id)
    assert placed[risk]["stack"] == placed[chart]["stack"] != ""
    assert placed[risk]["active"] is True
    assert placed[chart]["active"] is False
    assert placed[risk]["x"] == placed[chart]["x"]


def test_showing_a_tab_brings_it_forward(actions: Any) -> None:
    workspace_id, chart, risk = _stacked(actions)
    actions.call("show_panel_tab", {"workspace_id": workspace_id, "panel_id": chart})
    placed = panels(actions, workspace_id)
    assert placed[chart]["active"] is True
    assert placed[risk]["active"] is False


def test_detaching_dissolves_a_stack_of_two(actions: Any) -> None:
    workspace_id, chart, risk = _stacked(actions)
    actions.call("detach_panel", {"workspace_id": workspace_id, "panel_id": risk})
    placed = panels(actions, workspace_id)
    assert placed[chart]["stack"] == ""
    assert placed[risk]["stack"] == ""
    assert placed[chart]["active"] is True


def test_detaching_something_that_is_not_a_tab_is_refused(actions: Any) -> None:
    workspace_id, chart = workspace_with_one_panel(actions)
    with pytest.raises(ActionError, match="not stacked"):
        actions.call("detach_panel", {"workspace_id": workspace_id, "panel_id": chart})


def test_an_unknown_panel_names_the_ones_that_exist(actions: Any) -> None:
    workspace_id, _ = workspace_with_one_panel(actions)
    with pytest.raises(ActionError, match="No panel"):
        actions.call(
            "split_panel", {"workspace_id": workspace_id, "panel_id": "nope", "kind": "risk"}
        )


def test_docking_survives_a_reload(actions: Any) -> None:
    """A layout the operator arranged has to still be there next time."""
    workspace_id, _chart, _risk = _stacked(actions)
    described = actions.call("describe_workspace", {"workspace_id": workspace_id})
    stacks = {p["stack"] for p in described["panels"] if p["stack"]}
    assert len(stacks) == 1


# ── over HTTP, which is what the interface reaches ───────────────────────────


def test_the_same_operations_are_reachable_over_http(app: Any) -> None:
    with TestClient(app) as http:
        created = http.post(f"{API}/workspaces", json={"name": "Desk"})
        assert created.status_code in (200, 201), created.text
        workspace_id = created.json()["data"]["workspace_id"]

        added = http.post(
            f"{API}/workspaces/{workspace_id}/panels", json={"kind": "chart", "width": 12}
        )
        assert added.status_code == 200, added.text
        described = http.get(f"{API}/workspaces/{workspace_id}").json()["data"]
        chart = described["panels"][0]["panel_id"]

        split = http.post(
            f"{API}/workspaces/{workspace_id}/panels/{chart}/split",
            json={"kind": "risk", "along": "row"},
        )
        assert split.status_code == 200, split.text

        described = http.get(f"{API}/workspaces/{workspace_id}").json()["data"]
        assert len(described["panels"]) == 2
        risk = next(p["panel_id"] for p in described["panels"] if p["panel_id"] != chart)

        stacked = http.post(
            f"{API}/workspaces/{workspace_id}/panels/{risk}/stack", json={"onto": chart}
        )
        assert stacked.status_code == 200, stacked.text

        shown = http.post(f"{API}/workspaces/{workspace_id}/panels/{chart}/show")
        assert shown.status_code == 200, shown.text

        detached = http.post(
            f"{API}/workspaces/{workspace_id}/panels/{risk}/detach", json={"along": "row"}
        )
        assert detached.status_code == 200, detached.text

        final = http.get(f"{API}/workspaces/{workspace_id}").json()["data"]
        assert all(p["stack"] == "" for p in final["panels"])


def test_a_refused_split_is_a_refusal_not_a_crash(app: Any) -> None:
    """A 500 here would read as a broken server rather than an impossible layout."""
    with TestClient(app) as http:
        workspace_id = http.post(f"{API}/workspaces", json={"name": "Desk"}).json()["data"][
            "workspace_id"
        ]
        http.post(f"{API}/workspaces/{workspace_id}/panels", json={"kind": "chart", "width": 2})
        chart = http.get(f"{API}/workspaces/{workspace_id}").json()["data"]["panels"][0]["panel_id"]

        refused = http.post(
            f"{API}/workspaces/{workspace_id}/panels/{chart}/split", json={"kind": "risk"}
        )
        assert refused.status_code < 500, refused.text
        assert refused.status_code != 200


# ── helpers ──────────────────────────────────────────────────────────────────


def _stacked(actions: Any) -> tuple[str, str, str]:
    workspace_id, chart = workspace_with_one_panel(actions)
    actions.call(
        "split_panel", {"workspace_id": workspace_id, "panel_id": chart, "kind": "risk"}
    )
    risk = next(pid for pid in panels(actions, workspace_id) if pid != chart)
    actions.call("stack_panel", {"workspace_id": workspace_id, "panel_id": risk, "onto": chart})
    return workspace_id, chart, risk
