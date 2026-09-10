"""The approval queue: what a held action can and cannot do while it waits.

The queue's whole value is that a request is *data* — an action name and some
arguments — and carries no capability. These tests say so directly: nothing here
runs until somebody supplies the runner, it runs exactly once, and it does not
run at all once the moment has passed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from forge.hedgefund import ApprovalError, ApprovalQueue, ApprovalStatus


@pytest.fixture
def queue(tmp_path: Path) -> ApprovalQueue:
    return ApprovalQueue(tmp_path / "approvals.db")


def submit(queue: ApprovalQueue, **overrides: object):
    base: dict[str, object] = {
        "action": "submit_orders",
        "arguments": {"order_ids": ["o1"]},
        "reason": "reaches the book",
        "mode": "hedge_fund",
        "stance": "human_in_the_loop",
    }
    return queue.submit(**{**base, **overrides})  # type: ignore[arg-type]


def test_a_submitted_request_is_pending_and_carries_the_reason_it_was_held(
    queue: ApprovalQueue,
) -> None:
    request = submit(queue)
    assert request.status is ApprovalStatus.PENDING
    assert request.reason == "reaches the book"
    assert request.requested_by == "ai"
    assert [pending.request_id for pending in queue.pending()] == [request.request_id]


def test_a_request_holds_the_arguments_it_would_have_used(queue: ApprovalQueue) -> None:
    """A person deciding must not have to reconstruct the request."""
    request = submit(queue, arguments={"order_ids": ["a", "b"], "note": "rebalance"})
    stored = queue.get(request.request_id)
    assert stored is not None
    assert stored.arguments == {"order_ids": ["a", "b"], "note": "rebalance"}


def test_approving_runs_the_action_exactly_once(queue: ApprovalQueue) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def run(name: str, arguments: dict[str, object]) -> dict[str, object]:
        calls.append((name, arguments))
        return {"accepted": 1}

    request = submit(queue)
    decided = queue.approve(request.request_id, run, decided_by="videen", note="checked")
    assert decided.status is ApprovalStatus.APPROVED
    assert decided.decided_by == "videen"
    assert calls == [("submit_orders", {"order_ids": ["o1"]})]

    with pytest.raises(ApprovalError, match="an approval runs once"):
        queue.approve(request.request_id, run)
    assert len(calls) == 1


def test_the_queue_does_not_hold_the_runner_itself(queue: ApprovalQueue) -> None:
    """It cannot become a second way to invoke an action.

    The caller supplies the runner at approval time, so this module has no
    reference to the action registry and no path to one.
    """
    assert not hasattr(queue, "actions")
    assert not hasattr(queue, "run")


def test_rejecting_does_not_run_anything(queue: ApprovalQueue) -> None:
    calls: list[str] = []
    request = submit(queue)
    decided = queue.reject(request.request_id, note="not now")
    assert decided.status is ApprovalStatus.REJECTED
    assert decided.note == "not now"
    assert calls == []
    assert queue.pending() == []


def test_a_rejected_request_cannot_then_be_approved(queue: ApprovalQueue) -> None:
    request = submit(queue)
    queue.reject(request.request_id)
    with pytest.raises(ApprovalError, match="already rejected"):
        queue.approve(request.request_id, lambda name, args: {})


def test_an_expired_request_is_refused_rather_than_run_late(tmp_path: Path) -> None:
    """A rebalance approved four hours later is priced on prices that have moved."""
    queue = ApprovalQueue(tmp_path / "approvals.db", ttl_seconds=30)
    request = submit(queue, ttl_seconds=30)
    stale = request.model_copy(
        update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    queue._write(stale)

    with pytest.raises(ApprovalError, match="expired"):
        queue.approve(request.request_id, lambda name, args: {"ran": True})
    assert queue.get(request.request_id).status is ApprovalStatus.EXPIRED  # type: ignore[union-attr]


def test_pending_does_not_show_an_expired_request_as_actionable(tmp_path: Path) -> None:
    queue = ApprovalQueue(tmp_path / "approvals.db")
    request = submit(queue)
    queue._write(
        request.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    )
    assert queue.pending() == []
    assert queue.get(request.request_id).status is ApprovalStatus.EXPIRED  # type: ignore[union-attr]


def test_an_action_that_fails_after_approval_is_recorded_as_failed_not_rejected(
    queue: ApprovalQueue,
) -> None:
    """The person said yes. What went wrong afterwards is a different fact."""

    def run(name: str, arguments: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("the gate blocked every order")

    request = submit(queue)
    decided = queue.approve(request.request_id, run)
    assert decided.status is ApprovalStatus.FAILED
    assert "the gate blocked every order" in decided.error


def test_deciding_something_that_does_not_exist_is_refused(queue: ApprovalQueue) -> None:
    with pytest.raises(ApprovalError, match="no approval request"):
        queue.approve("nothing", lambda name, args: {})
    with pytest.raises(ApprovalError, match="no approval request"):
        queue.reject("nothing")


def test_history_keeps_decided_requests(queue: ApprovalQueue) -> None:
    first = submit(queue)
    second = submit(queue, arguments={"order_ids": ["o2"]})
    queue.reject(first.request_id)
    queue.approve(second.request_id, lambda name, args: {"ok": True})
    statuses = {row.request_id: row.status for row in queue.history()}
    assert statuses[first.request_id] is ApprovalStatus.REJECTED
    assert statuses[second.request_id] is ApprovalStatus.APPROVED


def test_requests_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "approvals.db"
    request = submit(ApprovalQueue(path))
    assert ApprovalQueue(path).get(request.request_id) is not None
