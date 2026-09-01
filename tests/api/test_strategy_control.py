from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from forge_api.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A client rooted in a temp directory so tests never touch the real library."""
    import forge_api.main as main

    monkeypatch.setattr(main, "ROOT", tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "rules").mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(database_path=tmp_path / "test.db")) as client:
        yield client


def _create(client, template="momentum_breakout"):
    response = client.post("/api/v1/strategies", json={"template": template})
    assert response.status_code == 201, response.text
    return response.json()["data"]["strategy_id"]


def test_templates_are_offered(client):
    body = client.get("/api/v1/templates").json()
    assert body["meta"]["total"] >= 3
    for item in body["data"]:
        assert item["hypothesis"] and item["falsifiable_prediction"]


def test_create_then_read_returns_executable_source(client):
    strategy_id = _create(client)
    detail = client.get(f"/api/v1/strategies/{strategy_id}").json()["data"]
    assert "def entry_signal" in detail["source"]
    assert "def exit_signal" in detail["source"]
    assert detail["code_hash"]


def test_backtest_produces_real_trades_with_clean_decision_indices(client):
    strategy_id = _create(client)
    response = client.post(f"/api/v1/strategies/{strategy_id}/backtest", json={"bar_count": 1500})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["lookahead_clean"] is True
    assert data["bar_count"] == 1500
    for trade in data["trades"]:
        assert trade["entry_decision_index"] == trade["entry_index"] - 1


def test_backtest_is_reproducible(client):
    strategy_id = _create(client)
    payload = {"bar_count": 1200, "seed": 7}
    first = client.post(f"/api/v1/strategies/{strategy_id}/backtest", json=payload).json()["data"]
    second = client.post(f"/api/v1/strategies/{strategy_id}/backtest", json=payload).json()["data"]
    assert first["backtest_id"] == second["backtest_id"]
    assert first["net_pnl"] == second["net_pnl"]


def test_judge_requires_a_backtest_first(client):
    strategy_id = _create(client)
    response = client.post(f"/api/v1/strategies/{strategy_id}/judge")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "no_backtest"


def test_judge_scores_the_real_backtest_and_fails_g0_on_synthetic_data(client):
    strategy_id = _create(client)
    client.post(f"/api/v1/strategies/{strategy_id}/backtest", json={"bar_count": 1500})
    body = client.post(f"/api/v1/strategies/{strategy_id}/judge").json()
    gates = {g["gate"]: g["status"] for g in body["data"]["gates"]}
    assert gates["G0"] == "FAIL", "synthetic data must never clear the data gate"
    assert body["data"]["decision"] in {"PASS", "FAIL", "INCONCLUSIVE"}


def test_guard_blocks_an_unsafe_source_edit(client):
    strategy_id = _create(client)
    unsafe = "\n".join(
        [
            "import os",
            "def entry_signal(w, p):",
            "    return 1",
            "def exit_signal(w, p, pos):",
            "    return None",
        ]
    )
    response = client.put(f"/api/v1/strategies/{strategy_id}/source", json={"source": unsafe})
    assert response.status_code == 422
    assert any("os" in problem for problem in response.json()["detail"]["problems"])


def test_sweep_counts_every_trial_and_refuses_to_promote(client):
    strategy_id = _create(client)
    body = client.post(
        f"/api/v1/strategies/{strategy_id}/sweep",
        json={"parameter": "lookback", "bar_count": 800},
    ).json()
    assert body["meta"]["promotable"] is False
    assert body["meta"]["trials_counted"] == len(body["data"]["points"])


def test_activity_records_what_actually_happened(client):
    strategy_id = _create(client)
    client.post(f"/api/v1/strategies/{strategy_id}/backtest", json={"bar_count": 800})
    events = client.get("/api/v1/activity").json()["data"]
    stages = {event["stage"] for event in events}
    assert {"STRATEGY", "BACKTEST"} <= stages


def test_delete_removes_the_strategy(client):
    strategy_id = _create(client)
    assert client.delete(f"/api/v1/strategies/{strategy_id}").status_code == 200
    assert client.get(f"/api/v1/strategies/{strategy_id}").status_code == 404
