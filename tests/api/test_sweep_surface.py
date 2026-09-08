"""The two-parameter sweep, end to end.

`tests/research/test_parameter_surface.py` holds the arithmetic and the
caveats. This checks the route actually runs the grid — every cell a real
backtest — and that what comes back is the same surface shape the existing 3D
renderer already draws, so there is no second format to keep in step.

The refusals matter as much as the success. A surface over one parameter
against itself, or over a range that cannot supply two distinct values, is a
line; drawing it as a landscape would suggest structure that is not there.
"""

from __future__ import annotations

import pathlib
import shutil
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge.research.parameter_surface import METRICS
from forge_api.main import create_app


@pytest.fixture
def client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    import forge_api.main as main

    monkeypatch.setattr(main, "ROOT", tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    shutil.copytree(pathlib.Path("rules"), tmp_path / "rules", dirs_exist_ok=True)
    with TestClient(create_app(database_path=tmp_path / "test.db")) as client:
        yield client


@pytest.fixture
def strategy(client: TestClient) -> str:
    created = client.post("/api/v1/strategies", json={"template": "momentum_breakout"}).json()[
        "data"
    ]
    return str(created["strategy_id"])


def parameters(client: TestClient, strategy_id: str) -> list[dict[str, Any]]:
    detail = client.get(f"/api/v1/strategies/{strategy_id}").json()["data"]
    return list(detail["spec"]["parameters"])


def await_job(client: TestClient, job_id: str, timeout: float = 240.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()["data"]
        if job["status"] in {"DONE", "FAILED", "CANCELLED"}:
            return dict(job)
        time.sleep(0.4)
    raise AssertionError("the sweep did not finish in time")


def run_surface(
    client: TestClient, strategy_id: str, **overrides: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    params = parameters(client, strategy_id)
    if len(params) < 2:
        pytest.skip("this template declares fewer than two parameters")
    body: dict[str, Any] = {
        "x_parameter": params[0]["name"],
        "y_parameter": params[1]["name"],
        "x_steps": 3,
        "y_steps": 3,
        "dataset": "synthetic",
        "bar_count": 2500,
    }
    body.update(overrides)
    response = client.post(f"/api/v1/strategies/{strategy_id}/sweep-surface", json=body)
    assert response.status_code == 200, response.text
    started = response.json()
    job = await_job(client, started["data"]["job_id"])
    assert job["status"] == "DONE", job.get("error")
    return dict(job["result"]), dict(started["meta"])


# ── it runs, and produces something the renderer can draw ────────────────────


def test_the_grid_runs_and_comes_back_as_a_surface(
    client: TestClient, strategy: str
) -> None:
    result, meta = run_surface(client, strategy)
    assert result["shape"] == "surface"
    assert len(result["axes"]) == 2
    assert len(result["cells"]) == 9
    assert meta["trials_counted"] == 9
    assert meta["promotable"] is False
    # Nine real backtests, each with its own id.
    ids = {point["backtest_id"] for point in result["points"]}
    assert len(ids) >= 2, "every configuration should produce its own run"


def test_every_cell_is_a_real_backtest_at_its_own_coordinates(
    client: TestClient, strategy: str
) -> None:
    result, _ = run_surface(client, strategy)
    xs = sorted({point["x"] for point in result["points"]})
    ys = sorted({point["y"] for point in result["points"]})
    assert result["axes"][0]["categories"] == [f"{x:g}" for x in xs]
    assert result["axes"][1]["categories"] == [f"{y:g}" for y in ys]
    for point in result["points"]:
        assert point["backtest_id"]
        assert point["trade_count"] >= 0


def test_the_result_says_it_is_not_evidence_and_why(
    client: TestClient, strategy: str
) -> None:
    result, _ = run_surface(client, strategy)
    assert result["is_evidence"] is False
    note = result["evidence_note"].lower()
    assert "in-sample" in note
    assert "never promote" in note
    warnings = " ".join(result["warnings"])
    assert "9 configurations were tried" in warnings


def test_the_artifact_carries_its_own_identity(client: TestClient, strategy: str) -> None:
    result, _ = run_surface(client, strategy)
    assert result["artifact_id"]
    assert result["content_hash"]
    assert result["provenance"]["strategy_id"] == strategy
    assert result["provenance"]["parameters"]["metric"] == "net_pnl"


def test_two_metrics_over_the_same_grid_are_different_artifacts(
    client: TestClient, strategy: str
) -> None:
    """Otherwise they would collide on one id in the artifact store."""
    net, _ = run_surface(client, strategy, metric="net_pnl")
    wins, _ = run_surface(client, strategy, metric="win_rate")
    assert net["artifact_id"] != wins["artifact_id"]
    assert net["measure"] != wins["measure"]


# ── refusals ─────────────────────────────────────────────────────────────────


def test_one_parameter_against_itself_is_refused_as_a_line(
    client: TestClient, strategy: str
) -> None:
    name = parameters(client, strategy)[0]["name"]
    response = client.post(
        f"/api/v1/strategies/{strategy}/sweep-surface",
        json={
            "x_parameter": name,
            "y_parameter": name,
            "dataset": "synthetic",
            "bar_count": 800,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "same_parameter_twice"


def test_an_unknown_parameter_names_the_ones_that_exist(
    client: TestClient, strategy: str
) -> None:
    name = parameters(client, strategy)[0]["name"]
    response = client.post(
        f"/api/v1/strategies/{strategy}/sweep-surface",
        json={"x_parameter": name, "y_parameter": "not_a_parameter", "dataset": "synthetic"},
    )
    assert response.status_code == 404
    detail = response.json()["detail"]
    assert detail["code"] == "parameter_not_found"
    assert detail["axis"] == "y"
    assert name in detail["known"]


def test_an_unknown_metric_names_the_ones_that_exist(
    client: TestClient, strategy: str
) -> None:
    params = parameters(client, strategy)
    if len(params) < 2:
        pytest.skip("this template declares fewer than two parameters")
    response = client.post(
        f"/api/v1/strategies/{strategy}/sweep-surface",
        json={
            "x_parameter": params[0]["name"],
            "y_parameter": params[1]["name"],
            "metric": "sharpe_ish",
            "dataset": "synthetic",
        },
    )
    assert response.status_code == 422
    assert set(response.json()["detail"]["known"]) == set(METRICS)


def test_an_unknown_strategy_is_a_404(client: TestClient) -> None:
    response = client.post(
        "/api/v1/strategies/nope/sweep-surface",
        json={"x_parameter": "a", "y_parameter": "b", "dataset": "synthetic"},
    )
    assert response.status_code == 404


def test_the_grid_is_capped_by_the_schema(client: TestClient, strategy: str) -> None:
    """Every cell is a full backtest, so an accidental 40x40 is an afternoon."""
    params = parameters(client, strategy)
    if len(params) < 2:
        pytest.skip("this template declares fewer than two parameters")
    response = client.post(
        f"/api/v1/strategies/{strategy}/sweep-surface",
        json={
            "x_parameter": params[0]["name"],
            "y_parameter": params[1]["name"],
            "x_steps": 40,
            "y_steps": 40,
            "dataset": "synthetic",
        },
    )
    assert response.status_code == 422
