"""The findings layer explains the verdict; it must never become a second one.

The hazard a legibility layer introduces is not that it is wrong — it is that it
is *nicer*. A readable summary sitting next to a gate ladder is the natural
thing to quote, so if it can drift from the ladder by even one status, the
softer of the two documents becomes the one people act on.

Every test here is a form of the same assertion: the findings route and the
dossier's gate ladder are the same facts, and no route reachable over HTTP can
turn INCONCLUSIVE into anything else.
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


def _judged_strategy(client: TestClient) -> str:
    """A strategy with a real backtest behind it, so a real verdict exists."""
    strategy_id = str(
        client.post("/api/v1/strategies", json={"template": "momentum_breakout"}).json()["data"][
            "strategy_id"
        ]
    )
    response = client.post(
        f"/api/v1/strategies/{strategy_id}/backtest",
        json={"dataset": "synthetic", "bar_count": 2000, "seed": 3},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["trades"], "the fixture needs trades to judge"
    return strategy_id


def _findings(client: TestClient, strategy_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/strategies/{strategy_id}/findings")
    assert response.status_code == 200, response.text
    return dict(response.json()["data"])


def _dossier(client: TestClient, strategy_id: str) -> dict[str, Any]:
    return dict(client.get(f"/api/v1/strategies/{strategy_id}/dossier").json()["data"])


def test_findings_and_the_gate_ladder_never_disagree(client: TestClient) -> None:
    strategy_id = _judged_strategy(client)
    dossier = _dossier(client, strategy_id)
    ladder = {gate["gate"]: gate for gate in dossier["verdict"]["gates"]}
    explained = dossier["findings"]

    assert explained["decision"] == dossier["verdict"]["decision"]
    assert explained["grade"] == dossier["verdict"]["grade"]
    assert explained["verdict_id"] == dossier["verdict"]["verdict_id"]

    for item in (*explained["findings"], *explained["strengths"]):
        gate = ladder[item["gate"]]
        assert item["status"] == gate["status"], f"{item['gate']} status moved"
        assert item["observed"] == gate["observed"], f"{item['gate']} observation moved"
        assert item["rule"] == gate["rule"]
    assert len(explained["findings"]) + len(explained["strengths"]) == len(ladder)


def test_the_route_returns_the_same_document_as_the_dossier_section(
    client: TestClient,
) -> None:
    strategy_id = _judged_strategy(client)
    section = _findings(client, strategy_id)
    embedded = _dossier(client, strategy_id)["findings"]
    assert section == embedded


def test_no_unmeasured_gate_is_reported_as_a_failure(client: TestClient) -> None:
    strategy_id = _judged_strategy(client)
    explained = _findings(client, strategy_id)
    unmeasured = [item for item in explained["findings"] if item["status"] == "INCONCLUSIVE"]
    assert unmeasured, "a fresh synthetic backtest leaves evidence gates unmeasured"
    for item in unmeasured:
        assert item["severity"] == "NOT_MEASURED"
        assert item["what_would_help"]
        assert "fail" not in item["headline"].lower()


def test_a_strategy_without_a_backtest_reports_an_absence(client: TestClient) -> None:
    strategy_id = str(
        client.post("/api/v1/strategies", json={"template": "momentum_breakout"}).json()["data"][
            "strategy_id"
        ]
    )
    section = _findings(client, strategy_id)
    assert section["available"] is False
    assert section["reason"]
    # No zeroes: "nothing failed" and "nothing was measured" are different facts.
    assert set(section) == {"available", "reason"}


def test_an_unknown_strategy_is_a_404(client: TestClient) -> None:
    assert client.get("/api/v1/strategies/nope/findings").status_code == 404


def test_meta_counts_but_never_scores(client: TestClient) -> None:
    strategy_id = _judged_strategy(client)
    meta = client.get(f"/api/v1/strategies/{strategy_id}/findings").json()["meta"]
    assert set(meta) == {"critical", "unmeasured"}
    # A second grade computed here could be quoted instead of the judge's.
    assert "grade" not in meta and "score" not in meta


def test_every_suggestion_served_over_http_improves_the_strategy(
    client: TestClient,
) -> None:
    """The route-level form of the guard in `tests/judge/test_explain.py`.

    A suggestion that reached a user telling them to declare fewer trials or
    move a threshold would be the application teaching people to defeat its own
    standard, so it is checked again at the boundary where a user would read it.
    """
    from tests.judge.test_explain import FORBIDDEN

    explained = _findings(client, _judged_strategy(client))
    for item in explained["findings"]:
        lowered = str(item["what_would_help"]).lower()
        for phrase in FORBIDDEN:
            assert phrase not in lowered, f"{item['gate']} suggests: {phrase!r}"
