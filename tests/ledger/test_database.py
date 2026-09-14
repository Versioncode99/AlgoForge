from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from forge.contracts.models import DecisionRecord, Preregistration, RunRecord
from forge.ledger import LedgerDatabase


def registration() -> Preregistration:
    return Preregistration.freeze(
        "A sufficiently detailed hypothesis exists for immutable test coverage.",
        "A documented sample mechanism.",
        "Reject when the required sample condition is absent.",
        datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_run_requires_existing_preregistration(tmp_path) -> None:
    ledger = LedgerDatabase(tmp_path / "ledger.db")
    pre = registration()
    run = RunRecord.create(
        pre, "TRUTH_OOS", "s", "d", "c", "fixture/1", pre.frozen_at + timedelta(seconds=1)
    )
    with pytest.raises(ValueError, match="REJECTED_NO_PREREG"):
        ledger.add_run(run)


def test_final_run_is_idempotently_addressed_and_immutable(tmp_path) -> None:
    ledger = LedgerDatabase(tmp_path / "ledger.db")
    pre = registration()
    ledger.add_preregistration(pre)
    run = RunRecord.create(
        pre, "TRUTH_OOS", "s", "d", "c", "fixture/1", pre.frozen_at + timedelta(seconds=1)
    )
    ledger.add_run(run)
    assert ledger.get_run(run.run_id) == run
    with pytest.raises(sqlite3.IntegrityError):
        ledger.add_run(run)


def test_decision_chain_rejects_wrong_previous_hash(tmp_path) -> None:
    ledger = LedgerDatabase(tmp_path / "ledger.db")
    when = datetime(2026, 1, 1, tzinfo=UTC)
    first = DecisionRecord.append("RUN", "run_1", {"ok": True}, None, when)
    ledger.append_decision(first)
    wrong = DecisionRecord.append("RUN", "run_2", {"ok": True}, "wrong", when)
    with pytest.raises(ValueError, match="chain"):
        ledger.append_decision(wrong)
    assert ledger.verify_decision_chain()


def test_concurrent_appends_cannot_fork_the_decision_chain(tmp_path) -> None:
    """The race the shared connection makes possible, and the lock closes.

    `LedgerDatabase` opens its connection with `check_same_thread=False`, which
    is what lets routes read it from request threads. `append_decision` reads the
    chain head and then writes against it, so two threads interleaving between
    those two statements both read the same head, both pass the continuity check
    and both insert -- a chain with two records claiming the same predecessor,
    which `verify_decision_chain` then reports as tampered forever on a ledger
    nobody tampered with.

    Every thread here builds its record against the head it last saw and retries
    when the chain moved, which is what a real caller does. Exactly one append
    per round may win; the assertion is that the chain still verifies and holds
    every record that reported success.
    """
    import threading

    ledger = LedgerDatabase(tmp_path / "ledger.db")
    when = datetime(2026, 1, 1, tzinfo=UTC)
    appended: list[str] = []
    guard = threading.Lock()
    start = threading.Barrier(8)

    def contend(index: int) -> None:
        start.wait()
        for attempt in range(40):
            head = ledger.connection.execute(
                "SELECT record_hash FROM decisions ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous = None if head is None else head["record_hash"]
            record = DecisionRecord.append(
                "RUN", f"run_{index}_{attempt}", {"worker": index}, previous, when
            )
            try:
                ledger.append_decision(record)
            except (ValueError, sqlite3.IntegrityError):
                continue  # the chain moved under us; rebuild against the new head
            with guard:
                appended.append(record.record_hash)
            return

    workers = [threading.Thread(target=contend, args=(index,)) for index in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)

    assert len(appended) == 8, "a contending append was lost"
    assert ledger.verify_decision_chain(), "the chain forked under concurrent appends"

    rows = ledger.connection.execute(
        "SELECT previous_hash FROM decisions ORDER BY sequence"
    ).fetchall()
    previous = [row["previous_hash"] for row in rows]
    assert len(previous) == len(set(previous)), (
        "two records claim the same predecessor, which is a forked chain"
    )
