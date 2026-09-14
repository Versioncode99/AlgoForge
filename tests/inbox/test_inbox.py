"""The arrival rule, and what an inbox must never do.

The rule is the design: an item arrives when a job reaches a terminal state and
at no other time. Everything worth asserting here follows from that -- nothing
judges what is interesting, nothing arrives twice, and nothing that has not
finished arrives at all.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from forge_api.inbox import MAX_ITEMS, Inbox
from forge_api.jobs import JobRegistry


class Finished:
    """A job-shaped object. The store reads attributes, not a class."""

    def __init__(
        self,
        job_id: str = "job_1",
        status: str = "DONE",
        kind: str = "backtest",
        label: str = "momentum on NQ",
        error: str | None = None,
        refs: dict[str, str] | None = None,
    ) -> None:
        self.job_id = job_id
        self.status = status
        self.kind = kind
        self.label = label
        self.error = error
        self.refs = refs or {}
        self.started_at = 1000.0
        self.finished_at = 1012.5


@pytest.fixture
def inbox(tmp_path: Path) -> Inbox:
    return Inbox(tmp_path / "inbox.db")


def test_a_finished_job_arrives(inbox: Inbox) -> None:
    item = inbox.arrive(Finished())
    assert item is not None
    assert item["outcome"] == "done"
    assert item["label"] == "momentum on NQ"
    assert item["seconds"] == pytest.approx(12.5)
    assert item["read_at"] == ""
    assert inbox.unread() == 1


@pytest.mark.parametrize("status", ["QUEUED", "RUNNING", "", "SOMETHING_ELSE"])
def test_work_that_has_not_finished_does_not_arrive(inbox: Inbox, status: str) -> None:
    """An inbox that fills with running work is a job list with a worse name."""
    assert inbox.arrive(Finished(status=status)) is None
    assert inbox.items() == []


@pytest.mark.parametrize(
    ("status", "outcome"), [("DONE", "done"), ("FAILED", "failed"), ("CANCELLED", "cancelled")]
)
def test_every_terminal_state_arrives_and_keeps_its_outcome(
    inbox: Inbox, status: str, outcome: str
) -> None:
    """A failure is news. Recording only the successes would be the worse inbox."""
    item = inbox.arrive(Finished(job_id=f"job_{status}", status=status))
    assert item is not None and item["outcome"] == outcome


def test_a_job_cannot_arrive_twice(inbox: Inbox) -> None:
    first = inbox.arrive(Finished())
    second = inbox.arrive(Finished())
    assert first is not None
    assert second is None, "arrival is keyed by job id and must be idempotent"
    assert len(inbox.items()) == 1


def test_the_subject_travels_with_the_item(inbox: Inbox) -> None:
    item = inbox.arrive(Finished(refs={"strategy_id": "s1"}))
    assert item is not None
    assert item["refs"] == {"strategy_id": "s1"}


def test_an_explicit_subject_overrides_the_job_s_own(inbox: Inbox) -> None:
    item = inbox.arrive(Finished(refs={"strategy_id": "s1"}), refs={"account_id": "a1"})
    assert item is not None
    assert item["refs"] == {"account_id": "a1"}


def test_a_failure_keeps_the_reason(inbox: Inbox) -> None:
    item = inbox.arrive(Finished(status="FAILED", error="ValueError: no bars"))
    assert item is not None
    assert item["error"] == "ValueError: no bars"


def test_reading_and_dismissing(inbox: Inbox) -> None:
    item = inbox.arrive(Finished())
    assert item is not None
    assert inbox.unread() == 1

    assert inbox.mark_read(item["item_id"]) is True
    assert inbox.unread() == 0
    # Reading twice is not an error, and is not a second change either.
    assert inbox.mark_read(item["item_id"]) is False

    assert inbox.dismiss(item["item_id"]) is True
    assert inbox.items() == []
    assert inbox.items(include_dismissed=True) != []
    assert inbox.dismiss(item["item_id"]) is False


def test_dismissing_something_unread_marks_it_read_too(inbox: Inbox) -> None:
    """Acting on an item is seeing it; leaving it counted as unread is a lie."""
    item = inbox.arrive(Finished())
    assert item is not None
    assert inbox.dismiss(item["item_id"]) is True
    assert inbox.unread() == 0


def test_mark_all_read_reports_what_moved(inbox: Inbox) -> None:
    for index in range(3):
        inbox.arrive(Finished(job_id=f"job_{index}"))
    assert inbox.mark_all_read() == 3
    assert inbox.unread() == 0
    assert inbox.mark_all_read() == 0


def test_the_newest_item_is_first(inbox: Inbox) -> None:
    for index in range(3):
        inbox.arrive(Finished(job_id=f"job_{index}", label=f"run {index}"))
        time.sleep(0.01)
    assert next(item["label"] for item in inbox.items()) == "run 2"


def test_it_survives_being_reopened(tmp_path: Path) -> None:
    """The whole point: the registry loses jobs on restart and this must not."""
    path = tmp_path / "inbox.db"
    Inbox(path).arrive(Finished())
    reopened = Inbox(path)
    assert len(reopened.items()) == 1
    assert reopened.unread() == 1


def test_the_cap_never_drops_something_unread(tmp_path: Path) -> None:
    """The one thing an inbox must not do is quietly lose what nobody has seen."""
    inbox = Inbox(tmp_path / "inbox.db")
    for index in range(MAX_ITEMS + 5):
        inbox.arrive(Finished(job_id=f"job_{index}"))
    assert inbox.unread() == MAX_ITEMS + 5
    assert len(inbox.items(limit=MAX_ITEMS)) == MAX_ITEMS


def test_the_cap_drops_the_oldest_read_items_first(tmp_path: Path) -> None:
    inbox = Inbox(tmp_path / "inbox.db")
    inbox.arrive(Finished(job_id="job_oldest"))
    inbox.mark_all_read()
    for index in range(MAX_ITEMS):
        inbox.arrive(Finished(job_id=f"job_{index}"))
    ids = {item["job_id"] for item in inbox.items(limit=MAX_ITEMS)}
    assert "job_oldest" not in ids
    assert len(ids) == MAX_ITEMS


def test_the_registry_announces_a_finished_job(tmp_path: Path) -> None:
    """The wiring, end to end, without an application around it."""
    inbox = Inbox(tmp_path / "inbox.db")
    registry = JobRegistry()
    registry.on_finish(inbox.arrive)

    job = registry.submit("backtest", "a short one", 1, lambda handle: "ok", refs={"s": "1"})
    deadline = time.time() + 5
    while time.time() < deadline and not inbox.items():
        time.sleep(0.01)

    items = inbox.items()
    assert len(items) == 1
    assert items[0]["job_id"] == job.job_id
    assert items[0]["outcome"] == "done"
    assert items[0]["refs"] == {"s": "1"}


def test_a_job_that_raised_arrives_as_failed(tmp_path: Path) -> None:
    inbox = Inbox(tmp_path / "inbox.db")
    registry = JobRegistry()
    registry.on_finish(inbox.arrive)

    def explode(handle: Any) -> None:
        raise ValueError("no bars in that window")

    registry.submit("backtest", "a doomed one", 1, explode)
    deadline = time.time() + 5
    while time.time() < deadline and not inbox.items():
        time.sleep(0.01)

    items = inbox.items()
    assert len(items) == 1
    assert items[0]["outcome"] == "failed"
    assert "no bars in that window" in items[0]["error"]


def test_an_observer_that_throws_cannot_fail_the_job(tmp_path: Path) -> None:
    """A closed database must not turn completed work into a failure."""
    registry = JobRegistry()

    def broken(job: Any) -> None:
        raise RuntimeError("the inbox is gone")

    registry.on_finish(broken)
    job = registry.submit("backtest", "fine really", 1, lambda handle: "result")
    deadline = time.time() + 5
    while time.time() < deadline and job.status not in {"DONE", "FAILED", "CANCELLED"}:
        time.sleep(0.01)
    assert job.status == "DONE"
    assert job.result == "result"
