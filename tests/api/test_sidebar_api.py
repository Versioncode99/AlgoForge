"""The sidebar over HTTP, and through the same registry the assistant uses.

The point being pinned: there is exactly one implementation. A destination added
by clicking and one added by asking the assistant go through the same action, so
a capability the interface does not have is not one an agent can invent — and
neither can drift from the other.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("ALGOFORGE_HOME", str(tmp_path))
    from forge_api.main import create_app

    return TestClient(create_app())


def _data(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _create(client: TestClient, **body) -> dict:
    payload = {"name": "My Quant Desk", **body}
    return _data(client.post("/api/v1/workspaces", json=payload))


# ── the catalogue ────────────────────────────────────────────────────────────
def test_the_catalogue_spans_every_mode(client: TestClient) -> None:
    payload = _data(client.get("/api/v1/sidebar/destinations"))
    routes = {row["route"] for row in payload["destinations"]}
    # One from each built-in environment, all reachable from one workspace.
    assert {"charts", "desk", "campaigns", "portfolio"} <= routes
    assert payload["count"] == len(payload["destinations"])


# ── composition ──────────────────────────────────────────────────────────────
def test_a_workspace_can_be_created_with_its_rail_already_composed(
    client: TestClient,
) -> None:
    """One call, because "a workspace for NQ research and prop trading" is one
    intention rather than nine."""
    workspace = _create(
        client,
        name="My Prop Research",
        description="Accounts and research on one screen",
        icon="NQ",
        sidebar_items=["desk", "copy", "risk", "campaigns", "agents", "charts"],
    )
    routes = {
        item["route"]
        for group in workspace["sidebar"]["groups"]
        for item in group["items"]
    }
    assert routes == {"desk", "copy", "risk", "campaigns", "agents", "charts"}
    assert workspace["sidebar_is_custom"] is True
    assert workspace["description"] == "Accounts and research on one screen"
    assert workspace["icon"] == "NQ"


def test_a_workspace_created_from_a_mode_gets_that_modes_rail(
    client: TestClient,
) -> None:
    workspace = _create(client, name="Prop desk", mode="prop_firm")
    routes = {
        item["route"]
        for group in workspace["sidebar"]["groups"]
        for item in group["items"]
    }
    assert {"desk", "drawdown", "rules"} <= routes


def test_an_unknown_destination_is_refused_with_the_valid_ones_named(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/workspaces", json={"name": "Broken", "sidebar_items": ["no_such_screen"]}
    )
    assert response.status_code >= 400
    assert "no_such_screen" in response.text


# ── editing ──────────────────────────────────────────────────────────────────
def test_the_full_edit_cycle_over_http(client: TestClient) -> None:
    workspace = _create(client, sidebar_items=["charts"])
    ws = workspace["workspace_id"]
    base = f"/api/v1/workspaces/{ws}/sidebar"

    _data(client.post(f"{base}/groups", json={"group_id": "research", "label": "RESEARCH"}))
    _data(client.post(f"{base}/items", json={"route": "campaigns", "group_id": "research"}))
    _data(client.post(f"{base}/items", json={"route": "agents", "group_id": "research"}))

    rail = _data(client.get(f"{base}"))
    research = next(g for g in rail["groups"] if g["group_id"] == "research")
    assert [i["route"] for i in research["items"]] == ["campaigns", "agents"]

    _data(client.post(f"{base}/items/campaigns/rename", json={"name": "NQ Alpha"}))
    _data(client.post(f"{base}/items/campaigns/pin", json={"value": True}))
    _data(client.post(f"{base}/items/agents/hide", json={"value": True}))
    _data(client.post(f"{base}/groups/research/rename", json={"name": "NQ RESEARCH"}))
    _data(client.post(f"{base}/groups/research/collapse", json={"value": True}))

    rail = _data(client.get(f"{base}"))
    research = next(g for g in rail["groups"] if g["group_id"] == "research")
    assert research["label"] == "NQ RESEARCH"
    assert research["collapsed"] is True
    campaigns = next(i for i in research["items"] if i["route"] == "campaigns")
    assert campaigns["label"] == "NQ Alpha"
    assert campaigns["renamed"] is True
    assert campaigns["pinned"] is True
    assert next(i for i in research["items"] if i["route"] == "agents")["hidden"] is True

    _data(client.delete(f"{base}/items/agents"))
    rail = _data(client.get(f"{base}"))
    research = next(g for g in rail["groups"] if g["group_id"] == "research")
    assert [i["route"] for i in research["items"]] == ["campaigns"]


def test_moving_an_item_between_groups_over_http(client: TestClient) -> None:
    workspace = _create(client, sidebar_items=["charts"])
    ws = workspace["workspace_id"]
    base = f"/api/v1/workspaces/{ws}/sidebar"
    _data(client.post(f"{base}/groups", json={"group_id": "mine", "label": "MINE"}))
    _data(client.post(f"{base}/items/charts/move", json={"group_id": "mine"}))
    rail = _data(client.get(f"{base}"))
    mine = next(g for g in rail["groups"] if g["group_id"] == "mine")
    assert [i["route"] for i in mine["items"]] == ["charts"]


def test_reordering_groups_over_http(client: TestClient) -> None:
    workspace = _create(client, sidebar_items=["charts", "desk"])
    ws = workspace["workspace_id"]
    base = f"/api/v1/workspaces/{ws}/sidebar"
    rail = _data(client.get(f"{base}"))
    order = [g["group_id"] for g in rail["groups"]]
    assert len(order) >= 2
    _data(client.post(f"{base}/groups/order", json={"group_ids": list(reversed(order))}))
    rail = _data(client.get(f"{base}"))
    assert [g["group_id"] for g in rail["groups"]] == list(reversed(order))


def test_resetting_restores_the_modes_rail(client: TestClient) -> None:
    workspace = _create(client, name="Prop", mode="prop_firm", sidebar_items=["charts"])
    ws = workspace["workspace_id"]
    reset = _data(client.post(f"/api/v1/workspaces/{ws}/sidebar/reset"))
    routes = {
        item["route"] for group in reset["sidebar"]["groups"] for item in group["items"]
    }
    assert {"desk", "drawdown"} <= routes
    assert "charts" not in routes


# ── persistence ──────────────────────────────────────────────────────────────
def test_a_rail_survives_a_reload(client: TestClient) -> None:
    workspace = _create(client, sidebar_items=["charts", "campaigns"])
    ws = workspace["workspace_id"]
    _data(client.post(f"/api/v1/workspaces/{ws}/open"))
    reloaded = _data(client.get(f"/api/v1/workspaces/{ws}"))
    routes = {
        item["route"] for group in reloaded["sidebar"]["groups"] for item in group["items"]
    }
    assert routes == {"charts", "campaigns"}


def test_cloning_copies_the_rail_and_marks_the_copy(client: TestClient) -> None:
    workspace = _create(client, sidebar_items=["charts", "campaigns"])
    ws = workspace["workspace_id"]
    clone = _data(
        client.post(f"/api/v1/workspaces/{ws}/clone", json={"name": "ES version"})
    )
    routes = {
        item["route"] for group in clone["sidebar"]["groups"] for item in group["items"]
    }
    assert routes == {"charts", "campaigns"}
    assert clone["kind"] == "CLONED"
    assert clone["name"] == "ES version"


def test_pinning_and_links_are_persisted(client: TestClient) -> None:
    workspace = _create(client)
    ws = workspace["workspace_id"]
    _data(client.post(f"/api/v1/workspaces/{ws}/pin", json={"value": True}))
    _data(
        client.post(f"/api/v1/workspaces/{ws}/campaigns", json={"target_id": "camp_1"})
    )
    _data(client.post(f"/api/v1/workspaces/{ws}/accounts", json={"target_id": "acct_1"}))
    reloaded = _data(client.get(f"/api/v1/workspaces/{ws}"))
    assert reloaded["pinned"] is True
    assert reloaded["campaign_ids"] == ["camp_1"]
    assert reloaded["account_ids"] == ["acct_1"]
    # And the listing carries enough to render a switcher without loading panels.
    listing = _data(client.get("/api/v1/workspaces"))
    row = next(r for r in listing["workspaces"] if r["workspace_id"] == ws)
    assert row["pinned"] is True
    assert row["campaign_ids"] == ["camp_1"]


# ── the registry is shared ───────────────────────────────────────────────────
def test_the_sidebar_actions_are_in_the_shared_registry(client: TestClient) -> None:
    """No AI-only path. The agent composes workspaces through these or not at all."""
    actions = _data(client.get("/api/v1/actions"))
    names = {a["name"] for a in actions}
    assert {
        "list_sidebar_destinations",
        "add_sidebar_item",
        "remove_sidebar_item",
        "move_sidebar_item",
        "add_sidebar_group",
        "reset_sidebar",
        "link_campaign_to_workspace",
    } <= names
