"""The validation endpoint, and the judge's refusal to pass without its output."""

from __future__ import annotations

import json
import pathlib
import shutil

import pytest
from fastapi.testclient import TestClient
from forge_api.main import create_app
from forge_api.strategies import per_bar_pnl, validation_grid


@pytest.fixture
def client(tmp_path, monkeypatch):
    import forge_api.main as main

    monkeypatch.setattr(main, "ROOT", tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    shutil.copytree(pathlib.Path("rules"), tmp_path / "rules", dirs_exist_ok=True)
    with TestClient(create_app(database_path=tmp_path / "test.db")) as client:
        yield client


def _create(client, template="momentum_breakout"):
    response = client.post("/api/v1/strategies", json={"template": template})
    assert response.status_code == 201, response.text
    return response.json()["data"]["strategy_id"]


class _Spec:
    def __init__(self, name: str, low: float, high: float, step: float) -> None:
        self.name, self.low, self.high, self.step = name, low, high, step


class _Trade:
    def __init__(self, exit_index: int, net_pnl: float) -> None:
        self.exit_index, self.net_pnl = exit_index, net_pnl


class _Result:
    def __init__(self, trades):
        self.trades = trades


def test_grid_never_exceeds_the_trial_budget() -> None:
    params = (
        _Spec("a", 1.0, 20.0, 1.0),
        _Spec("b", 0.1, 5.0, 0.1),
        _Spec("c", 2.0, 40.0, 2.0),
    )
    grid = validation_grid(params, max_trials=16)
    total = 1
    for values in grid.values():
        total *= len(values)
    assert total <= 16
    assert all(len(values) >= 2 for values in grid.values())


def test_grid_keeps_the_endpoints_of_each_range() -> None:
    grid = validation_grid((_Spec("a", 1.0, 10.0, 1.0),), max_trials=4)
    assert grid["a"][0] == 1.0
    assert grid["a"][-1] == 10.0


def test_grid_ignores_parameters_with_no_usable_range() -> None:
    assert validation_grid((_Spec("fixed", 5.0, 5.0, 1.0),), max_trials=16) == {}
    assert validation_grid((_Spec("bad_step", 1.0, 5.0, 0.0),), max_trials=16) == {}


def test_per_bar_pnl_lands_each_trade_on_its_exit_bar() -> None:
    result = _Result([_Trade(2, 10.0), _Trade(2, -4.0), _Trade(5, 7.0), _Trade(99, 1.0)])
    series = per_bar_pnl(result, 6)
    assert series == (0.0, 0.0, 6.0, 0.0, 0.0, 7.0)
    # A trade closing beyond the slice is dropped, not folded into the last bar.
    assert sum(series) == 13.0


def test_validation_refuses_synthetic_data(client) -> None:
    strategy_id = _create(client)
    response = client.post(
        f"/api/v1/strategies/{strategy_id}/validate",
        json={"dataset": "synthetic", "bar_count": 4000},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "real_data_required"


def test_validation_404s_for_an_unknown_strategy(client) -> None:
    response = client.post("/api/v1/strategies/does-not-exist/validate", json={})
    assert response.status_code == 404


def test_judge_withholds_a_pass_until_validation_has_run(client, tmp_path) -> None:
    """The whole point of the upgrade, asserted end to end."""
    strategy_id = _create(client)
    client.post(
        f"/api/v1/strategies/{strategy_id}/backtest",
        json={"dataset": "synthetic", "bar_count": 4000},
    )
    verdict = client.post(f"/api/v1/strategies/{strategy_id}/judge")
    assert verdict.status_code == 200, verdict.text
    gates = {gate["gate"]: gate["status"] for gate in verdict.json()["data"]["gates"]}
    for unmeasured in ("G11", "G12", "G13"):
        assert gates[unmeasured] == "INCONCLUSIVE"
    assert verdict.json()["data"]["decision"] != "PASS"


def test_corrupt_evidence_reads_as_absent_not_favourable(client, tmp_path) -> None:
    strategy_id = _create(client)
    directory = tmp_path / "data" / "validation"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{strategy_id}.json").write_text("{ not json", encoding="utf-8")
    client.post(
        f"/api/v1/strategies/{strategy_id}/backtest",
        json={"dataset": "synthetic", "bar_count": 4000},
    )
    verdict = client.post(f"/api/v1/strategies/{strategy_id}/judge")
    gates = {gate["gate"]: gate["status"] for gate in verdict.json()["data"]["gates"]}
    assert gates["G11"] == "INCONCLUSIVE"


def test_evidence_missing_a_field_reads_as_absent(client, tmp_path) -> None:
    strategy_id = _create(client)
    directory = tmp_path / "data" / "validation"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{strategy_id}.json").write_text(
        json.dumps({"trial_sharpes": [0.1, 0.2]}), encoding="utf-8"
    )
    client.post(
        f"/api/v1/strategies/{strategy_id}/backtest",
        json={"dataset": "synthetic", "bar_count": 4000},
    )
    verdict = client.post(f"/api/v1/strategies/{strategy_id}/judge")
    gates = {gate["gate"]: gate["status"] for gate in verdict.json()["data"]["gates"]}
    assert gates["G12"] == "INCONCLUSIVE"
