"""Missions: a plan the operator can read, and steps that actually chain.

Two properties matter more than the rest and both are easy to lose. A mission
may only call verbs that already exist — a planner that hallucinates one gets a
recorded refusal, not an execution — and a step that starts a background job has
to *wait* for it, or `{{stepN.net_pnl}}` silently resolves against a job handle
and the chain looks like it worked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge_api.jobs import REGISTRY
from forge_api.orchestrator import _resolve

pytest.importorskip("fastapi")


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """A real app whose entire workspace is a throwaway directory.

    The model is forced unreachable so planning is deterministic; the parts that
    depend on a live model are covered where they are used, not here.
    """
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path))
    import forge_api.orchestrator as orchestrator

    monkeypatch.setattr(
        orchestrator,
        "credential_for",
        lambda *a, **k: type("Absent", (), {"present": False})(),
    )
    from forge_api.main import create_app

    with TestClient(create_app()) as client:
        yield client


def wait(client: TestClient, mission_id: str, timeout: float = 60.0) -> dict[str, Any]:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        mission = client.get(f"/api/v1/missions/{mission_id}").json()["data"]
        if mission["status"] not in {"running", "planned"}:
            return dict(mission)
        time.sleep(0.15)
    raise AssertionError(f"mission did not finish: {mission}")


# ── placeholders ─────────────────────────────────────────────────────────────
def test_a_later_step_reads_an_earlier_result_by_path():
    results = [{"strategy_id": "abc_123"}, {"papers": [{"id": "p1"}]}]
    assert _resolve("{{step1.strategy_id}}", results) == "abc_123"
    assert _resolve("{{ step2.papers.0.id }}", results) == "p1"
    assert _resolve({"a": ["{{step1.strategy_id}}"]}, results) == {"a": ["abc_123"]}


def test_a_placeholder_that_cannot_be_resolved_is_left_alone():
    # Substituting an empty string would hand the action a plausible-looking
    # argument. Leaving the placeholder makes the action refuse it loudly.
    assert _resolve("{{step9.missing}}", [{"a": 1}]) == "{{step9.missing}}"
    assert _resolve("{{step1.nope}}", [{"a": 1}]) == "{{step1.nope}}"


# ── planning ─────────────────────────────────────────────────────────────────
def test_with_no_model_the_deterministic_playbook_plans_a_real_sequence(client: TestClient):
    response = client.post(
        "/api/v1/missions",
        json={"objective": "Look for intraday reversion evidence", "dry_run": True},
    )
    assert response.status_code == 201
    mission = response.json()["data"]
    assert mission["status"] == "planned"
    assert mission["plan_source"] == "deterministic"
    # The offline plan gathers evidence before it builds and measures before it
    # audits. It is a fallback, not a stub.
    assert [step["action"] for step in mission["steps"][:2]] == [
        "search_papers",
        "list_families",
    ]
    assert mission["steps"][3]["arguments"]["strategy_id"] == "{{step3.strategy_id}}"


def test_a_dry_run_executes_nothing(client: TestClient):
    before = client.get("/api/v1/strategies").json()["data"]
    client.post("/api/v1/missions", json={"objective": "Plan only, please", "dry_run": True})
    assert len(client.get("/api/v1/strategies").json()["data"]) == len(before)


def test_an_objective_needs_to_say_something(client: TestClient):
    assert client.post("/api/v1/missions", json={"objective": "hi"}).status_code == 422


# ── running ──────────────────────────────────────────────────────────────────
def test_steps_run_in_order_and_pass_results_forward(client: TestClient):
    mission = client.post(
        "/api/v1/missions",
        json={
            "objective": "Verify the executor chains its own results",
            "steps": [
                {"action": "list_families", "arguments": {}, "why": "read"},
                {
                    "action": "create_strategy",
                    "arguments": {"template": "momentum_breakout", "name": "Chained"},
                    "why": "build",
                },
                {
                    "action": "list_strategies",
                    "arguments": {"limit": 1},
                    "why": "confirm it landed",
                },
            ],
        },
    ).json()["data"]

    finished = wait(client, mission["id"])
    assert finished["status"] == "completed"
    assert [step["status"] for step in finished["steps"]] == ["completed"] * 3
    created = finished["steps"][1]["result"]["strategy_id"]
    assert finished["steps"][2]["result"]["strategies"][0]["strategy_id"] == created


def test_an_invented_action_fails_its_step_without_killing_the_mission(client: TestClient):
    mission = client.post(
        "/api/v1/missions",
        json={
            "objective": "A planner that made a verb up",
            "steps": [
                {"action": "delete_everything", "arguments": {}, "why": "should be refused"},
                {"action": "list_families", "arguments": {}, "why": "still runs"},
            ],
        },
    ).json()["data"]

    finished = wait(client, mission["id"])
    assert finished["steps"][0]["status"] == "failed"
    assert "No action named" in finished["steps"][0]["error"]
    assert finished["steps"][1]["status"] == "completed"
    # One bad step is a partial mission, not a successful one.
    assert finished["status"] == "partial"


def test_stop_on_failure_ends_the_mission_at_the_first_refusal(client: TestClient):
    mission = client.post(
        "/api/v1/missions",
        json={
            "objective": "Stop the moment something is wrong",
            "stop_on_failure": True,
            "steps": [
                {"action": "create_strategy", "arguments": {"template": "nope"}, "why": "bad"},
                {"action": "list_families", "arguments": {}, "why": "must not run"},
            ],
        },
    ).json()["data"]

    finished = wait(client, mission["id"])
    assert finished["status"] == "failed"
    assert finished["steps"][1]["status"] == "pending"


def test_a_mission_writes_a_note_into_the_vault(client: TestClient, tmp_path: Path):
    mission = client.post(
        "/api/v1/missions",
        json={
            "objective": "Leave a trail in the vault",
            "steps": [{"action": "list_families", "arguments": {}, "why": "cheap"}],
        },
    ).json()["data"]
    wait(client, mission["id"])
    notes = list((tmp_path / "10 AlgoForge" / "Missions").glob("*.md"))
    assert notes, "the mission should be mirrored as a note"
    assert "Leave a trail in the vault" in notes[0].read_text(encoding="utf-8")


# ── the action surface ───────────────────────────────────────────────────────
def test_every_action_declares_a_schema_and_says_whether_it_writes(client: TestClient):
    schemas = client.get("/api/v1/actions").json()["data"]
    names = {item["name"] for item in schemas}
    assert {"search_papers", "create_family", "create_strategy", "backtest_strategy"} <= names
    assert all("description" in item and "parameters" in item for item in schemas)
    # A caller deciding whether to ask for confirmation needs this to be explicit.
    assert {item["name"] for item in schemas if item["mutating"]} >= {"create_family"}


def test_an_action_refuses_an_argument_it_does_not_take(client: TestClient):
    response = client.post("/api/v1/actions/list_families", json={"arguments": {"unexpected": 1}})
    assert response.status_code == 422
    assert "does not take unexpected" in response.json()["detail"]["detail"]


def test_a_strategy_cannot_be_written_for_a_family_whose_data_is_missing(client: TestClient):
    client.post(
        "/api/v1/families",
        json={
            "key": "chain_depth",
            "label": "Chain depth",
            "mechanism": "M" * 60,
            "data_requirements": ["L2_MBP"],
        },
    )
    # Registering the family is allowed. Writing a strategy in it is not, because
    # nothing could honestly backtest one.
    templates = client.get("/api/v1/templates").json()["data"]
    assert all(t["family"] != "chain_depth" for t in templates)


@pytest.fixture(autouse=True)
def _drain_jobs():
    yield
    for job in REGISTRY.recent(50):
        REGISTRY.cancel(job.job_id)
