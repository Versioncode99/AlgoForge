"""Asking the lab a question in words.

The route exists so a researcher can type what they actually want to know. The
risk it introduces is the reason it refuses so readily: an analysis that runs is
computed correctly over real trades whatever question prompted it, so a
misrouted question comes back looking exactly like an answer.
"""

from __future__ import annotations

import pathlib
import shutil
from typing import Any

import pytest
from fastapi.testclient import TestClient
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
    run = client.post(
        f"/api/v1/strategies/{created['strategy_id']}/backtest",
        json={"dataset": "synthetic", "bar_count": 30000},
    )
    assert run.status_code == 200, run.text
    if len(run.json()["data"]["trades"]) < 40:
        pytest.skip("this sample took too few trades to analyse meaningfully")
    return str(created["strategy_id"])


def test_a_question_in_words_runs_the_analysis_that_answers_it(
    client: TestClient, strategy: str
) -> None:
    response = client.post(
        "/api/v1/lab/ask",
        json={
            "question": "Does this strategy's edge depend on the hour of day?",
            "strategy_id": strategy,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["analysis"] == "by_hour"
    assert body["data"]["cells"]
    assert body["meta"]["routed"]["decisive"] is True
    assert body["meta"]["routed"]["analysis"] == "by_hour"
    assert body["meta"]["is_evidence"] is False


def test_the_measure_is_taken_from_the_question(client: TestClient, strategy: str) -> None:
    body = client.post(
        "/api/v1/lab/ask",
        json={"question": "What is the win rate by hour of day?", "strategy_id": strategy},
    ).json()
    assert body["meta"]["routed"]["measure"] == "win_rate"
    assert body["data"]["measure"] == "win_rate"


def test_a_question_the_lab_cannot_answer_is_refused_with_what_it_can(
    client: TestClient, strategy: str
) -> None:
    """The important one. A route that guessed would return a real analysis of
    real trades, correctly computed, answering something else entirely."""
    response = client.post(
        "/api/v1/lab/ask",
        json={"question": "Would this work on ES instead?", "strategy_id": strategy},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "question_not_routed"
    assert detail["reason"]
    # It says what it *can* do rather than only what it cannot.
    assert {item["key"] for item in detail["available"]} >= {"by_hour", "worst_decile"}


def test_an_ambiguous_question_is_refused_with_the_candidates(
    client: TestClient, strategy: str
) -> None:
    response = client.post(
        "/api/v1/lab/ask",
        json={
            "question": "Does volatility or the hour of day matter more?",
            "strategy_id": strategy,
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "question_not_routed"
    names = {item["analysis"] for item in detail["candidates"]}
    assert {"by_hour", "by_volatility_percentile"} <= names
    for item in detail["candidates"]:
        assert item["matched"], "a candidate has to say what it heard"


def test_forcing_an_ambiguous_question_is_the_callers_decision(
    client: TestClient, strategy: str
) -> None:
    """`force` runs the top candidate, and the response says it was not clear."""
    response = client.post(
        "/api/v1/lab/ask",
        json={
            "question": "Does volatility or the hour of day matter more?",
            "strategy_id": strategy,
            "force": True,
        },
    )
    assert response.status_code == 200, response.text
    routed = response.json()["meta"]["routed"]
    assert routed["decisive"] is False
    assert routed["analysis"] in {"by_hour", "by_volatility_percentile"}


def test_the_question_is_kept_with_the_artifact(client: TestClient, strategy: str) -> None:
    question = "Has this edge decayed over time?"
    client.post("/api/v1/lab/ask", json={"question": question, "strategy_id": strategy})
    artifacts = client.get("/api/v1/lab/artifacts").json()["data"]
    assert any(row["note"] == question for row in artifacts)


def test_an_empty_question_is_rejected_by_the_schema(client: TestClient, strategy: str) -> None:
    response = client.post("/api/v1/lab/ask", json={"question": "", "strategy_id": strategy})
    assert response.status_code == 422


def test_a_routed_question_never_names_an_analysis_that_does_not_exist(
    client: TestClient, strategy: str
) -> None:
    keys = {item["key"] for item in client.get("/api/v1/lab/analyses").json()["data"]}
    for question in (
        "which hour is worst",
        "has the edge decayed",
        "what do the worst trades have in common",
        "are trades giving back gains",
    ):
        response = client.post(
            "/api/v1/lab/ask", json={"question": question, "strategy_id": strategy}
        )
        if response.status_code == 200:
            assert response.json()["meta"]["routed"]["analysis"] in keys
        else:
            for item in response.json()["detail"]["candidates"]:
                assert item["analysis"] in keys
