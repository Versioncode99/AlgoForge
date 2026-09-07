"""Lineage and memory reachable through the HTTP API and the bounded actions.

The audit found 13 actions against 63 HTTP routes: an agent could start the
engine but could not ask what the engine had learned or where a candidate came
from. These are read-only additions, so they must also stay read-only when MCP
exposes them without --write.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge_api.main import create_app


@pytest.fixture
def client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # ALGOFORGE_VAULT is the documented first step of the workspace resolution
    # order. Without it create_app() resolves to the repository's own data/
    # directory, so these tests would share state with each other and with a
    # real workspace on the machine running them.
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    return TestClient(create_app(tmp_path / "api.db"))


def _engine(client: TestClient) -> Any:
    return client.app.state.engine  # type: ignore[attr-defined]


def _seed(client: TestClient) -> tuple[str, str]:
    """A parent and a child experiment in the engine's current scope."""
    engine = _engine(client)
    scope = engine._scope()
    root = engine.experiments.reserve(
        scope, "momentum_breakout", {"lookback": 10.0}, policy="exploration"
    )
    child = engine.experiments.reserve(
        scope,
        "momentum_breakout",
        {"lookback": 20.0},
        parent_id=root,
        policy="neighbourhood",
    )
    return str(root), str(child)


# ── HTTP ─────────────────────────────────────────────────────────────────────


def test_experiments_endpoint_lists_the_scope(client: TestClient) -> None:
    root, child = _seed(client)
    body = client.get("/api/v1/experiments").json()
    ids = [row["id"] for row in body["data"]]
    assert root in ids and child in ids
    assert body["meta"]["total"] == 2


def test_roots_only_excludes_derived_experiments(client: TestClient) -> None:
    root, child = _seed(client)
    body = client.get("/api/v1/experiments", params={"roots_only": True}).json()
    ids = [row["id"] for row in body["data"]]
    assert ids == [root]
    assert child not in ids


def test_lineage_endpoint_answers_where_it_came_from(client: TestClient) -> None:
    root, child = _seed(client)
    body = client.get(f"/api/v1/experiments/{child}/lineage").json()
    assert body["data"]["experiment"]["id"] == child
    assert [row["id"] for row in body["data"]["ancestors"]] == [root]
    assert body["meta"]["depth"] == 1


def test_lineage_endpoint_reports_descendants(client: TestClient) -> None:
    root, child = _seed(client)
    body = client.get(f"/api/v1/experiments/{root}/lineage").json()
    assert [row["id"] for row in body["data"]["descendants"]] == [child]


def test_an_unknown_experiment_is_a_404_not_an_empty_success(
    client: TestClient,
) -> None:
    assert client.get("/api/v1/experiments/nope").status_code == 404
    assert client.get("/api/v1/experiments/nope/lineage").status_code == 404


def test_memory_endpoint_reports_counts_by_class(client: TestClient) -> None:
    from forge.memory import FailureClass
    from forge.strategy import TEMPLATES

    engine = _engine(client)
    engine._remember(
        "momentum_breakout",
        {p.name: float(p.default) for p in TEMPLATES["momentum_breakout"].parameters},
        TEMPLATES["momentum_breakout"].parameters,
        FailureClass.NO_TRADES,
        "nothing fired",
    )
    body = client.get("/api/v1/memory").json()
    assert body["data"]["counts"] == {"NO_TRADES": 1}
    assert body["data"]["total"] == 1
    assert body["data"]["constraints"][0]["failure_class"] == "NO_TRADES"


# ── actions ──────────────────────────────────────────────────────────────────


def test_the_new_actions_are_registered_and_read_only(client: TestClient) -> None:
    actions = client.app.state.actions  # type: ignore[attr-defined]
    schemas = {schema["name"]: schema for schema in actions.schemas()}
    for name in ("list_experiments", "experiment_lineage", "research_memory"):
        assert name in schemas, f"{name} is not registered"
        assert schemas[name]["mutating"] is False, f"{name} must not be mutating"


def test_actions_return_the_same_lineage_as_the_endpoint(client: TestClient) -> None:
    root, child = _seed(client)
    actions = client.app.state.actions  # type: ignore[attr-defined]

    listed = actions.call("list_experiments", {})
    assert listed["total"] == 2

    line = actions.call("experiment_lineage", {"experiment_id": child})
    assert [row["id"] for row in line["ancestors"]] == [root]

    http = client.get(f"/api/v1/experiments/{child}/lineage").json()["data"]
    assert [row["id"] for row in http["ancestors"]] == [row["id"] for row in line["ancestors"]]


def test_an_unknown_experiment_refuses_with_a_reason(client: TestClient) -> None:
    """A refusal, not a plausible-looking empty result."""
    from forge_api.actions import ActionError

    actions = client.app.state.actions  # type: ignore[attr-defined]
    with pytest.raises(ActionError) as refusal:
        actions.call("experiment_lineage", {"experiment_id": "nope"})
    assert "list_experiments" in str(refusal.value)


def test_read_only_mcp_still_exposes_them(client: TestClient) -> None:
    """They are reads, so they must survive the absence of --write."""
    import asyncio

    from forge_api.mcp_server import build_server

    actions = client.app.state.actions  # type: ignore[attr-defined]
    server = build_server(actions, allow_write=False)
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert {"list_experiments", "experiment_lineage", "research_memory"} <= names
    # And the filter is doing something: mutating verbs are still absent.
    assert "start_engine" not in names
