"""The run protocol over HTTP, which is the surface the interface actually reads.

`tests/api/test_chat_runs.py` drives `ChatRunner` directly. These drive the
routes: a question is accepted with a run id before the model is called, the
event stream is real server-sent events a browser can parse, a cancel is
acknowledged, and a reloaded client can find the run it lost.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge_api.main import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    app = create_app(tmp_path / "runs.db")
    with TestClient(app) as running:
        running.app_state = app.state  # type: ignore[attr-defined]
        yield running


def conversation(client: Any) -> str:
    made = client.post("/api/v1/conversations", json={"title": "Run"})
    assert made.status_code in (200, 201), made.text
    return str(made.json()["data"]["conversation_id"])


def frames(body: str) -> list[dict[str, Any]]:
    """Parse an SSE body the way a browser does: blank-line-separated frames."""
    out: list[dict[str, Any]] = []
    for block in body.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                out.append(json.loads(line[5:].strip()))
    return out


def test_a_question_is_accepted_with_a_run_id(client: Any) -> None:
    started = client.post(
        f"/api/v1/conversations/{conversation(client)}/runs", json={"message": "what is here?"}
    )
    assert started.status_code == 202, started.text
    data = started.json()["data"]
    assert data["run_id"].startswith("run_")
    assert data["status"] in {"accepted", "running", "completed"}


def test_an_empty_message_is_refused_rather_than_started(client: Any) -> None:
    refused = client.post(
        f"/api/v1/conversations/{conversation(client)}/runs", json={"message": "   "}
    )
    assert refused.status_code == 422
    assert "empty" in refused.text


def test_a_run_on_a_conversation_that_does_not_exist_is_a_404(client: Any) -> None:
    missing = client.post("/api/v1/conversations/nope/runs", json={"message": "hello"})
    assert missing.status_code == 404


def test_the_stream_is_server_sent_events_ending_in_a_terminal_frame(client: Any) -> None:
    run = client.post(
        f"/api/v1/conversations/{conversation(client)}/runs", json={"message": "what is here?"}
    ).json()["data"]["run_id"]
    stream = client.get(f"/api/v1/chat/runs/{run}/events")
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    events = [e for e in frames(stream.text) if e["event"] != "keep-alive"]
    assert events[0]["event"] == "accepted"
    assert events[-1]["event"] in {"completed", "cancelled", "failed"}
    assert [e["index"] for e in events] == sorted(e["index"] for e in events)


def test_the_stream_can_be_resumed_from_a_cursor(client: Any) -> None:
    """What makes a reload cost nothing: the buffer is replayable by index."""
    run = client.post(
        f"/api/v1/conversations/{conversation(client)}/runs", json={"message": "what is here?"}
    ).json()["data"]["run_id"]
    whole = [e for e in frames(client.get(f"/api/v1/chat/runs/{run}/events").text)
             if e["event"] != "keep-alive"]
    tail = [e for e in frames(client.get(f"/api/v1/chat/runs/{run}/events?cursor=2").text)
            if e["event"] != "keep-alive"]
    assert tail == whole[2:]


def test_a_finished_run_reports_that_it_cannot_be_stopped(client: Any) -> None:
    run = client.post(
        f"/api/v1/conversations/{conversation(client)}/runs", json={"message": "quick"}
    ).json()["data"]["run_id"]
    list(frames(client.get(f"/api/v1/chat/runs/{run}/events").text))
    stopped = client.post(f"/api/v1/chat/runs/{run}/cancel")
    assert stopped.status_code == 200
    assert stopped.json()["data"]["cancelling"] is False
    # And it is honest about what a stop can do even when one is possible.
    assert "cannot be withdrawn" in stopped.json()["meta"]["note"]


def test_cancelling_a_run_that_does_not_exist_is_a_404(client: Any) -> None:
    assert client.post("/api/v1/chat/runs/run_nope/cancel").status_code == 404


def test_a_reloaded_client_can_find_the_conversations_latest_run(client: Any) -> None:
    thread = conversation(client)
    assert client.get(f"/api/v1/conversations/{thread}/runs/latest").json()["data"] is None
    run = client.post(
        f"/api/v1/conversations/{thread}/runs", json={"message": "what is here?"}
    ).json()["data"]["run_id"]
    list(frames(client.get(f"/api/v1/chat/runs/{run}/events").text))
    latest = client.get(f"/api/v1/conversations/{thread}/runs/latest").json()["data"]
    assert latest["run_id"] == run
    assert latest["status"] in {"completed", "cancelled", "failed"}


def test_the_question_is_in_the_transcript_whatever_the_run_did(client: Any) -> None:
    """Written before the model is called, so a failure loses the answer only.

    A transcript that develops holes exactly where something went wrong is the
    one shape a research record must not have.
    """
    thread = conversation(client)
    run = client.post(
        f"/api/v1/conversations/{thread}/runs", json={"message": "a question worth keeping"}
    ).json()["data"]["run_id"]
    list(frames(client.get(f"/api/v1/chat/runs/{run}/events").text))
    turns = client.get(f"/api/v1/conversations/{thread}").json()["data"]["turns"]
    assert any(t["role"] == "user" and t["text"] == "a question worth keeping" for t in turns)
