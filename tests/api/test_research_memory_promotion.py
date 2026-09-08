"""Promoting a lab finding into durable memory, over HTTP.

The unit tests in `tests/research/test_knowledge.py` hold the store's rules.
These check the same rules survive the boundary — in particular that the route
refuses a claim nobody computed, because that is the one thing a caller could
attempt on purpose.

The other thing checked here is the direction of the arrow: promoting a finding
must leave the dossier's verdict byte-for-byte unchanged. Research memory is
knowledge; a verdict is what the evidence supports. If the first could move the
second, the system would start believing its own summaries.
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
def analysed(client: TestClient) -> tuple[str, dict[str, Any]]:
    """A strategy, a run, and one saved analysis that produced findings."""
    strategy_id = str(
        client.post("/api/v1/strategies", json={"template": "momentum_breakout"}).json()["data"][
            "strategy_id"
        ]
    )
    run = client.post(
        f"/api/v1/strategies/{strategy_id}/backtest",
        json={"dataset": "synthetic", "bar_count": 30000},
    )
    assert run.status_code == 200, run.text
    if len(run.json()["data"]["trades"]) < 40:
        pytest.skip("this sample took too few trades to analyse meaningfully")

    response = client.post(
        "/api/v1/lab/run",
        json={"analysis": "by_hour", "strategy_id": strategy_id, "save": True},
    )
    assert response.status_code == 200, response.text
    artifact = dict(response.json()["data"])
    if not artifact.get("findings"):
        pytest.skip("this analysis produced no findings to promote")
    return strategy_id, artifact


def test_a_finding_can_be_kept_with_the_whole_chain_behind_it(
    client: TestClient, analysed: tuple[str, dict[str, Any]]
) -> None:
    strategy_id, artifact = analysed
    statement = artifact["findings"][0]

    response = client.post(
        "/api/v1/lab/findings",
        json={
            "artifact_id": artifact["artifact_id"],
            "statement": statement,
            "note": "check against the 2024 window",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    finding = body["data"]

    assert finding["statement"] == statement
    assert finding["strategy_id"] == strategy_id
    assert finding["backtest_id"]
    assert finding["artifact_id"] == artifact["artifact_id"]
    assert finding["status"] == "STANDING"
    assert finding["is_evidence"] is False
    assert body["meta"]["is_evidence"] is False
    assert "not evidence" in body["meta"]["note"].lower()


def test_a_claim_nobody_computed_is_refused(
    client: TestClient, analysed: tuple[str, dict[str, Any]]
) -> None:
    """The route must not be a way to write research memory by hand.

    A typed sentence stored alongside a real strategy id, a real backtest id and
    a real code hash would be indistinguishable from a measured one, and the
    provenance chain would make it look *more* credible than it is.
    """
    _, artifact = analysed
    response = client.post(
        "/api/v1/lab/findings",
        json={
            "artifact_id": artifact["artifact_id"],
            "statement": "This strategy is robust across every regime.",
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "not_a_computed_finding"
    assert "never written next to it" in detail["reason"]
    assert client.get("/api/v1/lab/findings").json()["data"] == []


def test_an_unknown_artifact_is_a_404(client: TestClient) -> None:
    response = client.post(
        "/api/v1/lab/findings", json={"artifact_id": "art_nope", "statement": "anything"}
    )
    assert response.status_code == 404


def test_recall_returns_what_was_kept_and_can_be_searched(
    client: TestClient, analysed: tuple[str, dict[str, Any]]
) -> None:
    strategy_id, artifact = analysed
    statement = artifact["findings"][0]
    client.post(
        "/api/v1/lab/findings",
        json={"artifact_id": artifact["artifact_id"], "statement": statement},
    )

    listed = client.get("/api/v1/lab/findings", params={"strategy_id": strategy_id}).json()
    assert [item["statement"] for item in listed["data"]] == [statement]
    assert listed["meta"]["counts"]["STANDING"] == 1
    assert listed["meta"]["is_evidence"] is False

    word = statement.split()[0]
    assert client.get("/api/v1/lab/findings", params={"q": word}).json()["data"]
    assert client.get("/api/v1/lab/findings", params={"q": "zzzz-no-match"}).json()["data"] == []
    assert client.get("/api/v1/lab/findings", params={"strategy_id": "other"}).json()["data"] == []


def test_a_retracted_finding_stays_readable_but_leaves_the_default_recall(
    client: TestClient, analysed: tuple[str, dict[str, Any]]
) -> None:
    _, artifact = analysed
    created = client.post(
        "/api/v1/lab/findings",
        json={"artifact_id": artifact["artifact_id"], "statement": artifact["findings"][0]},
    ).json()["data"]

    retracted = client.post(
        f"/api/v1/lab/findings/{created['finding_id']}/retract",
        json={"reason": "the longer window contradicted it"},
    )
    assert retracted.status_code == 200
    assert retracted.json()["data"]["status"] == "RETRACTED"
    assert retracted.json()["data"]["retraction_reason"] == "the longer window contradicted it"

    assert client.get("/api/v1/lab/findings").json()["data"] == []
    audit = client.get("/api/v1/lab/findings", params={"include_withdrawn": True}).json()
    assert [item["status"] for item in audit["data"]] == ["RETRACTED"]


def test_a_retraction_without_a_reason_is_rejected_by_the_schema(
    client: TestClient, analysed: tuple[str, dict[str, Any]]
) -> None:
    _, artifact = analysed
    created = client.post(
        "/api/v1/lab/findings",
        json={"artifact_id": artifact["artifact_id"], "statement": artifact["findings"][0]},
    ).json()["data"]
    response = client.post(
        f"/api/v1/lab/findings/{created['finding_id']}/retract", json={"reason": ""}
    )
    assert response.status_code == 422


def test_remembering_something_does_not_move_the_verdict(
    client: TestClient, analysed: tuple[str, dict[str, Any]]
) -> None:
    """The whole point of keeping the two apart, asserted end to end."""
    strategy_id, artifact = analysed
    before = client.get(f"/api/v1/strategies/{strategy_id}/dossier").json()["data"]

    for statement in artifact["findings"]:
        client.post(
            "/api/v1/lab/findings",
            json={"artifact_id": artifact["artifact_id"], "statement": statement},
        )

    after = client.get(f"/api/v1/strategies/{strategy_id}/dossier").json()["data"]
    assert after["verdict"] == before["verdict"]
    assert after["findings"] == before["findings"]
    assert after["limitations"] == before["limitations"]
