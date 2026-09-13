"""The inbox over HTTP, and the one thing no route may do.

There is no route that creates an item. An arrival is caused by work finishing,
so a route able to manufacture one would let the record say something happened
that did not -- which is the whole value of the thing gone.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge_api.main import create_app


@pytest.fixture
def client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    with TestClient(create_app(database_path=tmp_path / "test.db")) as client:
        yield client


def test_a_new_inbox_is_empty_and_states_its_rule(client: TestClient) -> None:
    response = client.get("/api/v1/inbox")
    assert response.status_code == 200
    body = response.json()
    assert body["data"] == {"items": [], "unread": 0}
    rule = body["meta"]["arrival_rule"]
    assert "terminal state" in rule
    assert "judges" in rule, "the rule has to say that nothing decides what is worth surfacing"


def test_nothing_can_post_an_item_into_it(client: TestClient) -> None:
    """The absence is the point, so it is asserted rather than left implied."""
    for method, path in (
        ("POST", "/api/v1/inbox"),
        ("PUT", "/api/v1/inbox"),
        ("POST", "/api/v1/inbox/items"),
    ):
        response = client.request(method, path, json={"label": "invented"})
        assert response.status_code in (404, 405), f"{method} {path} was accepted"


def _finished_backtest(client: TestClient) -> dict[str, Any]:
    strategy_id = client.post("/api/v1/strategies", json={"template": "momentum_breakout"}).json()[
        "data"
    ]["strategy_id"]
    started = client.post(
        f"/api/v1/strategies/{strategy_id}/backtest/async",
        json={"dataset": "synthetic", "bar_count": 800},
    )
    if started.status_code != 200:
        pytest.skip(f"the job route refused this fixture: {started.status_code}")
    job_id = started.json()["data"]["job_id"]
    deadline = time.time() + 90
    while time.time() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()["data"]
        if job["status"] in {"DONE", "FAILED", "CANCELLED"}:
            return dict(job)
        time.sleep(0.05)
    pytest.fail("the backtest job never finished")


def test_a_real_finished_job_arrives_with_its_subject(client: TestClient) -> None:
    job = _finished_backtest(client)
    body = client.get("/api/v1/inbox").json()["data"]
    match = [item for item in body["items"] if item["job_id"] == job["job_id"]]
    assert match, "a job that reached a terminal state must have arrived"
    item = match[0]
    assert item["outcome"] in {"done", "failed", "cancelled"}
    assert item["refs"].get("strategy_id"), "the item has to name what the work was about"
    assert body["unread"] >= 1


def test_reading_then_dismissing_moves_the_count(client: TestClient) -> None:
    _finished_backtest(client)
    body = client.get("/api/v1/inbox").json()["data"]
    item_id = body["items"][0]["item_id"]
    before = body["unread"]
    assert before >= 1

    read = client.post(f"/api/v1/inbox/{item_id}/read").json()["data"]
    assert read["changed"] is True
    assert read["unread"] == before - 1

    again = client.post(f"/api/v1/inbox/{item_id}/read").json()["data"]
    assert again["changed"] is False, "reading twice is not a second change"

    dropped = client.delete(f"/api/v1/inbox/{item_id}").json()["data"]
    assert dropped["changed"] is True
    listed = client.get("/api/v1/inbox").json()["data"]["items"]
    assert all(item["item_id"] != item_id for item in listed)
    with_dismissed = client.get(
        "/api/v1/inbox", params={"include_dismissed": True}
    ).json()["data"]["items"]
    assert any(item["item_id"] == item_id for item in with_dismissed)


def test_mark_all_read_clears_the_count(client: TestClient) -> None:
    _finished_backtest(client)
    assert client.get("/api/v1/inbox").json()["data"]["unread"] >= 1
    cleared = client.post("/api/v1/inbox/read-all").json()["data"]
    assert cleared["changed"] >= 1
    assert cleared["unread"] == 0


def test_acting_on_something_that_is_not_there_is_not_an_error(client: TestClient) -> None:
    """A stale tab dismissing an item twice is ordinary, not a failure."""
    assert client.post("/api/v1/inbox/inb_nope/read").json()["data"]["changed"] is False
    assert client.delete("/api/v1/inbox/inb_nope").json()["data"]["changed"] is False
