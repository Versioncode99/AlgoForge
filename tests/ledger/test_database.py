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
