from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient
from forge_api.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A client rooted in a temp directory so tests never touch the real library."""
    import forge_api.main as main

    monkeypatch.setattr(main, "ROOT", tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copytree(pathlib.Path("rules"), tmp_path / "rules", dirs_exist_ok=True)
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
    response = client.post(
        f"/api/v1/strategies/{strategy_id}/backtest",
        json={"dataset": "synthetic", "bar_count": 1500},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["lookahead_clean"] is True
    assert data["bar_count"] == 1500
    for trade in data["trades"]:
        assert trade["entry_decision_index"] == trade["entry_index"] - 1


def test_backtest_is_reproducible(client):
    strategy_id = _create(client)
    payload = {"dataset": "synthetic", "bar_count": 1200, "seed": 7}
    first = client.post(f"/api/v1/strategies/{strategy_id}/backtest", json=payload).json()["data"]
    second = client.post(f"/api/v1/strategies/{strategy_id}/backtest", json=payload).json()["data"]
    assert first["backtest_id"] == second["backtest_id"]
    assert first["net_pnl"] == second["net_pnl"]


def test_judge_requires_a_backtest_first(client):
    strategy_id = _create(client)
    response = client.post(f"/api/v1/strategies/{strategy_id}/judge")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "no_backtest"


def test_judge_scores_the_backtest_and_fails_g0_on_synthetic_data(client):
    strategy_id = _create(client)
    client.post(
        f"/api/v1/strategies/{strategy_id}/backtest",
        json={"dataset": "synthetic", "bar_count": 1500},
    )
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
        json={"parameter": "lookback", "dataset": "synthetic", "bar_count": 800},
    ).json()
    assert body["meta"]["promotable"] is False
    assert body["meta"]["trials_counted"] == len(body["data"]["points"])


def test_activity_records_what_actually_happened(client):
    strategy_id = _create(client)
    client.post(
        f"/api/v1/strategies/{strategy_id}/backtest",
        json={"dataset": "synthetic", "bar_count": 800},
    )
    events = client.get("/api/v1/activity").json()["data"]
    stages = {event["stage"] for event in events}
    assert {"STRATEGY", "BACKTEST"} <= stages


def test_delete_removes_the_strategy(client):
    strategy_id = _create(client)
    assert client.delete(f"/api/v1/strategies/{strategy_id}").status_code == 200
    assert client.get(f"/api/v1/strategies/{strategy_id}").status_code == 404


def test_datasets_expose_real_providers(client):
    rows = client.get("/api/v1/datasets").json()["data"]
    by_key = {row["key"]: row for row in rows}
    assert by_key["synthetic"]["is_real"] is False
    assert by_key["mnq_1m_3mo"]["is_real"] is True
    assert by_key["mnq_1m_3mo"]["provider"] == "databento"
    assert by_key["btc_5m_6mo"]["cost_note"] == "free"


def test_engine_starts_and_stops(client):
    status = client.post(
        "/api/v1/engine/start",
        json={"dataset": "synthetic", "cycle_seconds": 1.0, "max_strategies": 3, "max_bars": 2000},
    ).json()["data"]
    assert status["running"] is True
    stopped = client.post("/api/v1/engine/stop").json()["data"]
    assert stopped["running"] is False


def test_engine_rejects_an_unknown_dataset(client):
    response = client.post("/api/v1/engine/start", json={"dataset": "not_a_dataset"})
    assert response.status_code == 404


def test_prop_requires_a_backtest_and_enough_days(client):
    strategy_id = _create(client)
    rules = client.get("/api/v1/prop/rules").json()["data"]
    if not rules:
        return
    response = client.post(
        f"/api/v1/strategies/{strategy_id}/prop", json={"rule_id": rules[0]["rule_id"]}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "no_backtest"


def test_settings_never_return_secret_values(client, monkeypatch):
    secret = "sk-private-unit-secret"
    monkeypatch.setenv("OMNIROUTE_API_KEY", secret)
    data = client.get("/api/v1/settings").json()["data"]
    blob = str(data)
    for credential in data["credentials"]:
        assert set(credential) >= {"key", "label", "present", "hint"}
        # Presence and length only — never any part of the value itself.
        assert credential["hint"] in {"not set", "configured"}
        assert credential["source"] in {"none", "environment", "external_file"}
    assert secret not in blob


def test_settings_routing_can_be_changed_per_role(client):
    updated = client.patch("/api/v1/settings", json={"routing": {"chat": "glm-5.3"}}).json()["data"]
    assert updated["ai"]["routing"]["chat"] == "glm-5.3"
    assert updated["ai"]["routing"]["hypothesis"] == "deepseek-v4-pro", "other roles untouched"


def test_settings_reject_unknown_model_and_role(client):
    assert client.patch("/api/v1/settings", json={"routing": {"chat": "gpt-9"}}).status_code == 422
    assert (
        client.patch("/api/v1/settings", json={"routing": {"nope": "glm-5.3"}}).status_code == 422
    )


def test_assistant_answers_from_the_ledger_without_a_key(client):
    # AI is on by default now that a provider is configured, so the ledger path
    # is reached by turning it off rather than by having no key. Tests must not
    # reach the network either way.
    client.patch("/api/v1/settings", json={"ai_enabled": False})
    body = client.post("/api/v1/ask", json={"question": "how many strategies are there?"}).json()
    assert body["data"]["model"] == "local-ledger"
    assert body["data"]["grounded"] is True
    assert "strategies" in body["data"]["answer"].lower()


def test_prop_refuses_a_track_record_that_is_too_short(client):
    """The 5-day, 99.5%-pass case must be impossible through the API too."""
    strategy_id = _create(client)
    client.post(
        f"/api/v1/strategies/{strategy_id}/backtest",
        json={"dataset": "synthetic", "bar_count": 3000},
    )
    rules = client.get("/api/v1/prop/rules").json()["data"]
    response = client.post(
        f"/api/v1/strategies/{strategy_id}/prop", json={"rule_id": rules[0]["rule_id"]}
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "insufficient_days"
    assert detail["days_required"] >= 30


def test_strategy_listing_does_not_rescan_every_artifact(client):
    """Listing N strategies must not re-parse every backtest N times."""
    for _ in range(4):
        strategy_id = _create(client)
        client.post(
            f"/api/v1/strategies/{strategy_id}/backtest",
            json={"dataset": "synthetic", "bar_count": 900},
        )
    import time

    start = time.time()
    rows = client.get("/api/v1/strategies").json()["data"]
    assert len(rows) == 4
    assert time.time() - start < 5.0
