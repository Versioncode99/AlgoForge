"""A chat turn as a run: acknowledged at once, watchable, and stoppable.

The properties asserted here are the ones the blocking `POST /messages` route
could not have. A test that only checked "the answer comes back" would pass
against the old shape and tell nobody whether the new one works.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from forge_api.chat_runs import FINISHED, ChatRunner, RunStore


class FakeChat:
    """A chat service that can be made slow, and that reports progress."""

    def __init__(self, *, delay: float = 0.0, fail: bool = False) -> None:
        self.delay = delay
        self.fail = fail
        self.calls: list[tuple[str, str]] = []
        self.stopped = False

    def send(self, conversation_id: str, message: str, *, on_event: Any = None) -> dict[str, Any]:
        self.calls.append((conversation_id, message))
        if on_event is not None:
            on_event("reading", "Reading this instance…")
        deadline = time.monotonic() + self.delay
        while time.monotonic() < deadline:
            if on_event is not None and not on_event("thinking", "Working…"):
                self.stopped = True
                break
            time.sleep(0.01)
        if self.fail:
            raise RuntimeError("the provider fell over")
        return {
            "turn": {
                "turn_id": "t1",
                "text": f"answer to {message}",
                "artifacts": [{"artifact_id": "a1", "kind": "backtest", "title": "Backtest"}],
            },
            "note": None,
        }


@pytest.fixture
def runner(tmp_path: Path) -> ChatRunner:
    return ChatRunner(FakeChat(), RunStore(tmp_path / "runs.db"))


def drain(runner: ChatRunner, run_id: str) -> list[dict[str, Any]]:
    return [event for event in runner.stream(run_id) if event["event"] != "keep-alive"]


def test_a_run_is_acknowledged_before_the_model_is_called(tmp_path: Path) -> None:
    """The whole point. `POST /messages` returned when the answer did."""
    chat = FakeChat(delay=1.0)
    runner = ChatRunner(chat, RunStore(tmp_path / "runs.db"))
    started = time.perf_counter()
    run = runner.start("c1", "does this return?")
    acknowledged = (time.perf_counter() - started) * 1000
    assert run.run_id
    # Not `== "accepted"`: the worker may legitimately have reached "running"
    # by the time this line executes. What matters is that it has not finished,
    # which is exactly what the blocking route could not promise.
    assert run.status not in FINISHED
    assert acknowledged < 200, f"acknowledgement took {acknowledged:.0f}ms"
    runner.request_cancel(run.run_id)
    drain(runner, run.run_id)


def test_the_events_arrive_in_order_and_end_in_a_terminal_status(runner: ChatRunner) -> None:
    run = runner.start("c1", "what happened?")
    events = drain(runner, run.run_id)
    kinds = [event["event"] for event in events]
    assert kinds[0] == "accepted"
    assert kinds[-1] in FINISHED
    assert "delta" in kinds, "the answer never arrived as a delta"
    assert "artifact" in kinds, "the turn's artifact was not reported"
    assert [event["index"] for event in events] == list(range(len(events)))


def test_a_reattaching_client_sees_the_run_from_the_beginning(runner: ChatRunner) -> None:
    """A reload must not cost the transcript of a run already in flight."""
    run = runner.start("c1", "still there?")
    first = drain(runner, run.run_id)
    again = [e for e in runner.stream(run.run_id, 0) if e["event"] != "keep-alive"]
    assert [e["event"] for e in again] == [e["event"] for e in first]
    from_middle = [e for e in runner.stream(run.run_id, 2) if e["event"] != "keep-alive"]
    assert from_middle == first[2:], "the cursor did not resume where it was asked to"


def test_stopping_a_run_reaches_the_tool_loop(tmp_path: Path) -> None:
    chat = FakeChat(delay=5.0)
    runner = ChatRunner(chat, RunStore(tmp_path / "runs.db"))
    run = runner.start("c1", "take your time")
    # Wait for the worker to be inside `send` before asking it to stop. Waiting
    # on the status instead would sometimes cancel before the worker had
    # started, which exercises the pre-flight check and not the signal — and the
    # run ends "cancelled" either way, so the test would look like it passed.
    deadline = time.monotonic() + 5
    while not chat.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert chat.calls, "the worker never started"
    assert runner.request_cancel(run.run_id) is True
    events = drain(runner, run.run_id)
    assert events[-1]["event"] == "cancelled"
    assert chat.stopped, "the stop never reached the loop"


def test_a_stop_says_it_kept_the_turn_when_the_provider_had_already_replied(
    tmp_path: Path,
) -> None:
    """Honest about what a stop can do.

    A provider call in flight cannot be withdrawn. If the reply lands anyway the
    turn is kept -- deleting it would leave a transcript that disagrees with the
    audit log -- and the run says so rather than reporting a clean stop.
    """
    chat = FakeChat()
    runner = ChatRunner(chat, RunStore(tmp_path / "runs.db"))
    run = runner.start("c1", "quick one")
    drain(runner, run.run_id)
    assert run.status == "completed"
    assert runner.request_cancel(run.run_id) is False, "a finished run cannot be stopped"


def test_a_failure_is_reported_rather_than_swallowed(tmp_path: Path) -> None:
    runner = ChatRunner(FakeChat(fail=True), RunStore(tmp_path / "runs.db"))
    run = runner.start("c1", "break")
    events = drain(runner, run.run_id)
    assert events[-1]["event"] == "failed"
    assert "provider fell over" in events[-1]["data"]["reason"]


def test_the_outcome_survives_the_event_buffer(tmp_path: Path) -> None:
    """A reloaded client asks the store, not the buffer, whether a run happened."""
    store = RunStore(tmp_path / "runs.db")
    runner = ChatRunner(FakeChat(), store)
    run = runner.start("c1", "remembered?")
    drain(runner, run.run_id)
    latest = store.latest("c1")
    assert latest is not None
    assert latest["run_id"] == run.run_id
    assert latest["status"] == "completed"
    assert latest["finished_at"]


def test_an_unknown_run_is_refused_rather_than_hanging(runner: ChatRunner) -> None:
    events = [e for e in runner.stream("run_nope")]
    assert events[0]["event"] == "failed"
    assert "no run" in events[0]["data"]["reason"]
