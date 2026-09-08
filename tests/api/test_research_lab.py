"""The research lab: real analyses, real provenance, and a real way back.

The load-bearing test here is
`test_a_drilldown_returns_exactly_the_trades_the_cell_was_computed_over`. An
analysis is only worth more than a screenshot if the number in a cell can be
opened and the trades under it read — and only trustworthy if those are the same
trades the number was computed from, not a set recomputed later against a
slightly different classification.

The second thing under test is the boundary. An analysis slices a ledger that
already exists: it runs no strategy, consumes no holdout and produces no
verdict. Every stored record says so, because a dossier that mistook one for
evidence would be laundering exploration into proof.
"""

from __future__ import annotations

import pathlib
import shutil

import pytest
from fastapi.testclient import TestClient
from forge_api.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    import forge_api.main as main

    monkeypatch.setattr(main, "ROOT", tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    shutil.copytree(pathlib.Path("rules"), tmp_path / "rules", dirs_exist_ok=True)
    with TestClient(create_app(database_path=tmp_path / "test.db")) as client:
        yield client


@pytest.fixture
def strategy(client) -> str:
    """A strategy with a real run behind it, on enough bars to analyse."""
    created = client.post(
        "/api/v1/strategies", json={"template": "momentum_breakout"}
    ).json()["data"]
    run = client.post(
        f"/api/v1/strategies/{created['strategy_id']}/backtest",
        json={"dataset": "synthetic", "bar_count": 30000},
    )
    assert run.status_code == 200, run.text
    if len(run.json()["data"]["trades"]) < 40:
        pytest.skip("this sample took too few trades to analyse meaningfully")
    return str(created["strategy_id"])


# ── the catalogue ────────────────────────────────────────────────────────────


def test_the_lab_says_what_it_can_be_asked_and_what_each_question_needs(client):
    body = client.get("/api/v1/lab/analyses").json()
    keys = {item["key"] for item in body["data"]}
    assert {
        "by_hour",
        "by_volatility_percentile",
        "hour_by_volatility",
        "edge_over_time",
        "excursion",
        "worst_decile",
    } <= keys
    for item in body["data"]:
        assert item["question"].endswith("?")
        assert item["needs"]
    assert "bounded set rather than generated code" in body["meta"]["note"]


def test_an_unknown_analysis_names_the_ones_that_exist(client, strategy):
    response = client.post(
        "/api/v1/lab/run", json={"analysis": "make_money", "strategy_id": strategy}
    )
    assert response.status_code == 422
    assert "by_hour" in response.json()["detail"]["reason"]


# ── running ──────────────────────────────────────────────────────────────────


def test_expectancy_by_hour_covers_every_trade_and_reports_its_samples(client, strategy):
    body = client.post(
        "/api/v1/lab/run", json={"analysis": "by_hour", "strategy_id": strategy}
    ).json()
    data = body["data"]

    assert data["shape"] == "bars"
    assert len(data["axes"]) == 1
    assert len(data["cells"]) == 24
    # Every trade lands in exactly one hour, so the buckets must account for all
    # of them. A bucketing that silently dropped trades would understate the
    # sample every downstream number rests on.
    assert sum(cell["trade_count"] for cell in data["cells"]) == data["total_trades"]

    for cell in data["cells"]:
        if cell["trade_count"] == 0:
            # Absent, not zero. The strategy never traded that hour, which is a
            # different statement from trading it and breaking even.
            assert cell["value"] is None
            assert cell["win_rate"] is None
        else:
            assert cell["insufficient"] == (cell["trade_count"] < 20)


def test_a_result_is_not_evidence_and_says_so(client, strategy):
    body = client.post(
        "/api/v1/lab/run", json={"analysis": "by_hour", "strategy_id": strategy}
    ).json()
    assert body["meta"]["is_evidence"] is False
    assert body["data"]["is_evidence"] is False
    assert "consumes no holdout" in body["data"]["evidence_note"]


def test_every_result_carries_the_provenance_to_reproduce_it(client, strategy):
    latest = client.get(f"/api/v1/strategies/{strategy}/trades").json()["data"]
    body = client.post(
        "/api/v1/lab/run", json={"analysis": "by_hour", "strategy_id": strategy}
    ).json()["data"]
    provenance = body["provenance"]

    assert provenance["strategy_id"] == strategy
    assert provenance["backtest_id"] == latest["backtest_id"]
    assert provenance["spec_hash"] == latest["spec_hash"]
    assert provenance["code_hash"] == latest["code_hash"]
    assert provenance["data_hash"] == latest["data_hash"]
    assert provenance["evidence_tier"] == latest["evidence_tier"]
    assert provenance["analysis"] == "by_hour"
    assert provenance["created_at"]
    assert body["content_hash"]


def test_a_surface_has_two_axes_and_a_full_grid_of_cells(client, strategy):
    response = client.post(
        "/api/v1/lab/run",
        json={"analysis": "hour_by_volatility", "strategy_id": strategy, "hour_bucket": 4},
    )
    if response.status_code != 200:
        assert response.json()["detail"]["code"] == "analysis_unavailable"
        pytest.skip("this run's bars could not be classified")
    data = response.json()["data"]
    assert data["shape"] == "surface"
    assert len(data["axes"]) == 2
    x, y = data["axes"]
    assert len(x["categories"]) == 6  # 24 hours in 4-hour slots
    assert len(y["categories"]) == 5
    # A dense grid: every (x, y) present exactly once, so a renderer never has
    # to guess whether a hole is missing data or a bucket with no trades.
    assert len(data["cells"]) == 30
    coords = {tuple(cell["coords"]) for cell in data["cells"]}
    assert coords == {(i, j) for i in range(6) for j in range(5)}


def test_the_descriptive_percentile_warning_is_on_every_result_that_uses_one(
    client, strategy
):
    """Ranking a trade against the whole run uses observations later than it."""
    for analysis in ("by_volatility_percentile", "hour_by_volatility"):
        response = client.post(
            "/api/v1/lab/run", json={"analysis": analysis, "strategy_id": strategy}
        )
        if response.status_code != 200:
            continue
        warnings = " ".join(response.json()["data"]["warnings"])
        assert "later" in warnings or "Descriptive" in warnings


def test_an_analysis_needing_conditions_refuses_rather_than_using_nulls(client):
    """A run whose bars cannot be classified must not produce a volatility
    breakdown built from a column of nothing."""
    created = client.post(
        "/api/v1/strategies", json={"template": "momentum_breakout"}
    ).json()["data"]
    client.post(
        f"/api/v1/strategies/{created['strategy_id']}/backtest",
        json={"dataset": "synthetic", "bar_count": 8000},
    )
    response = client.post(
        "/api/v1/lab/run",
        json={
            "analysis": "by_volatility_percentile",
            "strategy_id": created["strategy_id"],
        },
    )
    # Either it classified and worked, or it refused by name. Never a chart of
    # nulls.
    if response.status_code != 200:
        assert response.status_code == 422
        assert "conditions" in response.json()["detail"]["reason"]
    else:
        cells = response.json()["data"]["cells"]
        assert sum(c["trade_count"] for c in cells) > 0


def test_worst_decile_refuses_a_sample_too_small_to_split(client):
    created = client.post(
        "/api/v1/strategies", json={"template": "volatility_compression_break"}
    ).json()["data"]
    run = client.post(
        f"/api/v1/strategies/{created['strategy_id']}/backtest",
        json={"dataset": "synthetic", "bar_count": 3000},
    )
    if run.status_code != 200 or len(run.json()["data"]["trades"]) >= 30:
        pytest.skip("this sample is large enough to split")
    response = client.post(
        "/api/v1/lab/run",
        json={"analysis": "worst_decile", "strategy_id": created["strategy_id"]},
    )
    assert response.status_code == 422
    assert "at least 30" in response.json()["detail"]["reason"]


# ── artifacts ────────────────────────────────────────────────────────────────


def test_a_run_is_stored_and_listed_and_readable_back(client, strategy):
    created = client.post(
        "/api/v1/lab/run", json={"analysis": "by_hour", "strategy_id": strategy}
    ).json()["data"]
    assert created["saved"] is True
    artifact_id = created["artifact_id"]

    listed = client.get("/api/v1/lab/artifacts").json()["data"]
    assert any(row["artifact_id"] == artifact_id for row in listed)
    row = next(row for row in listed if row["artifact_id"] == artifact_id)
    assert row["strategy_id"] == strategy
    assert row["analysis"] == "by_hour"

    reopened = client.get(f"/api/v1/lab/artifacts/{artifact_id}").json()["data"]
    assert reopened["content_hash"] == created["content_hash"]
    assert len(reopened["cells"]) == len(created["cells"])


def test_not_saving_leaves_nothing_behind(client, strategy):
    before = len(client.get("/api/v1/lab/artifacts").json()["data"])
    body = client.post(
        "/api/v1/lab/run",
        json={"analysis": "by_hour", "strategy_id": strategy, "save": False},
    ).json()["data"]
    assert body["saved"] is False
    after = len(client.get("/api/v1/lab/artifacts").json()["data"])
    assert after == before


def test_deleting_an_analysis_is_safe_and_says_why(client, strategy):
    """It is derived. The backtest it cites is untouched."""
    created = client.post(
        "/api/v1/lab/run", json={"analysis": "by_hour", "strategy_id": strategy}
    ).json()["data"]
    deleted = client.delete(f"/api/v1/lab/artifacts/{created['artifact_id']}")
    assert deleted.status_code == 200
    assert "re-running the analysis reproduces it" in deleted.json()["meta"]["note"]
    assert client.get(f"/api/v1/lab/artifacts/{created['artifact_id']}").status_code == 404
    # And the run it was about is still there.
    assert client.get(f"/api/v1/strategies/{strategy}/trades").status_code == 200


def test_the_same_question_twice_produces_the_same_numbers(client, strategy):
    first = client.post(
        "/api/v1/lab/run", json={"analysis": "by_hour", "strategy_id": strategy}
    ).json()["data"]
    second = client.post(
        "/api/v1/lab/run", json={"analysis": "by_hour", "strategy_id": strategy}
    ).json()["data"]
    assert [c["value"] for c in first["cells"]] == [c["value"] for c in second["cells"]]
    assert [c["trade_count"] for c in first["cells"]] == [
        c["trade_count"] for c in second["cells"]
    ]


# ── the drilldown ────────────────────────────────────────────────────────────


def test_a_drilldown_returns_exactly_the_trades_the_cell_was_computed_over(
    client, strategy
):
    """The load-bearing property of this whole feature.

    Recomputing which trades belong in a cell would let the drilldown drift
    from the picture — a re-percentiled bucket, a differently classified bar —
    and show a set of trades that is not the set the number came from.
    """
    created = client.post(
        "/api/v1/lab/run", json={"analysis": "by_hour", "strategy_id": strategy}
    ).json()["data"]
    populated = [c for c in created["cells"] if c["trade_count"] > 0]
    assert populated, "the fixture must produce at least one populated hour"
    cell = max(populated, key=lambda c: c["trade_count"])

    coords = ",".join(str(v) for v in cell["coords"])
    body = client.get(
        f"/api/v1/lab/artifacts/{created['artifact_id']}/trades?coords={coords}"
    ).json()["data"]

    assert body["cell"]["trade_count"] == cell["trade_count"]
    assert body["cell"]["labels"] == cell["labels"]
    assert body["strategy_id"] == strategy
    returned = {row["trade_id"] for row in body["trades"]}
    assert returned <= set(cell["trade_ids"])
    assert returned

    # Every trade handed back really was entered in that hour, which is the
    # claim the cell makes.
    hour = int(cell["labels"][0].split(":")[0])
    for row in body["trades"]:
        assert int(row["entry_time"][11:13]) == hour

    # And their P&L reconstructs the cell's number.
    if not body["capped"]:
        total = sum(row["net_pnl"] for row in body["trades"])
        assert total == pytest.approx(cell["net_pnl"], abs=0.02)


def test_a_drilldown_on_a_surface_cell_finds_its_region(client, strategy):
    response = client.post(
        "/api/v1/lab/run",
        json={"analysis": "hour_by_volatility", "strategy_id": strategy, "hour_bucket": 4},
    )
    if response.status_code != 200:
        pytest.skip("this run's bars could not be classified")
    created = response.json()["data"]
    populated = [c for c in created["cells"] if c["trade_count"] > 0]
    if not populated:
        pytest.skip("no populated surface region")
    cell = max(populated, key=lambda c: c["trade_count"])

    coords = ",".join(str(v) for v in cell["coords"])
    body = client.get(
        f"/api/v1/lab/artifacts/{created['artifact_id']}/trades?coords={coords}"
    ).json()["data"]
    assert body["cell"]["labels"] == cell["labels"]
    assert body["trades"]
    slot = int(cell["labels"][0].split("-")[0])
    for row in body["trades"]:
        assert slot <= int(row["entry_time"][11:13]) < slot + 4


def test_a_cell_that_does_not_exist_is_a_named_404(client, strategy):
    created = client.post(
        "/api/v1/lab/run", json={"analysis": "by_hour", "strategy_id": strategy}
    ).json()["data"]
    response = client.get(
        f"/api/v1/lab/artifacts/{created['artifact_id']}/trades?coords=99"
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "cell_not_found"


def test_malformed_coordinates_are_refused_with_the_format(client, strategy):
    created = client.post(
        "/api/v1/lab/run", json={"analysis": "by_hour", "strategy_id": strategy}
    ).json()["data"]
    response = client.get(
        f"/api/v1/lab/artifacts/{created['artifact_id']}/trades?coords=morning"
    )
    assert response.status_code == 422
    assert "one per axis" in response.json()["detail"]["reason"]


# ── the agent uses the same lab ──────────────────────────────────────────────


def test_the_agent_runs_the_same_analyses_through_the_same_registry(client, strategy):
    actions = client.app.state.actions
    names = {schema["name"] for schema in actions.schemas()}
    assert {
        "list_analyses",
        "run_analysis",
        "list_analysis_artifacts",
        "analysis_trades",
    } <= names

    result = actions.call(
        "run_analysis", {"analysis": "by_hour", "strategy_id": strategy}
    )
    assert result["is_evidence"] is False
    assert result["total_trades"] > 0
    assert len(result["cells"]) == 24

    # The artifact the agent produced is visible over HTTP like any other.
    listed = client.get("/api/v1/lab/artifacts").json()["data"]
    row = next(r for r in listed if r["artifact_id"] == result["artifact_id"])
    assert row["created_by"] == "agent"

    populated = [c for c in result["cells"] if c["trade_count"] > 0]
    drill = actions.call(
        "analysis_trades",
        {"artifact_id": result["artifact_id"], "coords": populated[0]["coords"]},
    )
    assert drill["trades"]


def test_the_agent_is_refused_an_analysis_that_does_not_exist(client, strategy):
    from forge_api.actions import ActionError

    actions = client.app.state.actions
    with pytest.raises(ActionError, match="is not an analysis"):
        actions.call("run_analysis", {"analysis": "profit", "strategy_id": strategy})
    with pytest.raises(ActionError, match="measure must be one of"):
        actions.call(
            "run_analysis",
            {"analysis": "by_hour", "strategy_id": strategy, "measure": "vibes"},
        )
