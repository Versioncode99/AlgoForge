"""The audit log: who did what, and — the half that matters — what was refused.

A log holding only successes would present an agent that tried forty times to
raise its own limits as an agent that did nothing. So refusals are records, the
ruling is stored even when it was "allow", and nothing here can update or delete
a row.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from forge.hedgefund import AuditLog, Outcome
from forge.hedgefund.audit import MAX_PAYLOAD


@pytest.fixture
def audit(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "audit.db")


def test_a_record_answers_who_what_and_under_which_policy(audit: AuditLog) -> None:
    entry = audit.record(
        actor="ai",
        origin="orchestrator",
        mode="hedge_fund",
        stance="autonomous",
        action="construct_portfolio",
        arguments={"capital": 1_000_000},
        ruling="allow",
        ruling_reason="preparatory: it proposes or measures",
        outcome=Outcome.OK,
        result={"holdings": 3},
    )
    stored = audit.get(entry.entry_id)
    assert stored is not None
    assert (stored.actor, stored.origin, stored.mode, stored.stance) == (
        "ai", "orchestrator", "hedge_fund", "autonomous",
    )
    assert stored.ruling == "allow"
    assert "preparatory" in stored.ruling_reason


def test_the_ruling_is_stored_even_when_the_call_was_allowed(audit: AuditLog) -> None:
    """"The policy permitted this" is what makes an autonomous run auditable."""
    audit.record(actor="ai", action="list_strategies", outcome=Outcome.OK,
                 ruling="allow", ruling_reason="read-only")
    assert audit.recent()[0].ruling == "allow"


def test_refusals_are_records(audit: AuditLog) -> None:
    audit.record(actor="ai", action="set_fund_config", outcome=Outcome.DENIED,
                 ruling="deny", ruling_reason="changes a protected control",
                 error="changes a protected control")
    audit.record(actor="ai", action="submit_orders", outcome=Outcome.PENDING_APPROVAL,
                 ruling="require_approval", approval_id="approval_1")
    summary = audit.summary()
    assert summary["denied"] == 1
    assert summary["pending_approval"] == 1


def test_the_log_can_be_filtered_to_one_actor_or_one_outcome(audit: AuditLog) -> None:
    audit.record(actor="human", action="create_strategy", outcome=Outcome.OK)
    audit.record(actor="ai", action="create_strategy", outcome=Outcome.OK)
    audit.record(actor="ai", action="set_stance", outcome=Outcome.DENIED)
    assert len(audit.recent(actor="ai")) == 2
    assert len(audit.recent(outcome=Outcome.DENIED)) == 1
    assert len(audit.recent(action="create_strategy")) == 2


def test_entries_come_back_newest_first(audit: AuditLog) -> None:
    for index in range(5):
        audit.record(actor="ai", action=f"a{index}", outcome=Outcome.OK)
    assert [entry.action for entry in audit.recent(3)] == ["a4", "a3", "a2"]


def test_an_approval_links_the_proposal_to_the_authorisation(audit: AuditLog) -> None:
    """The chain from proposal to decision to result has to be followable."""
    audit.record(actor="ai", action="submit_orders", outcome=Outcome.PENDING_APPROVAL,
                 approval_id="approval_7")
    audit.record(actor="human", action="submit_orders", outcome=Outcome.OK,
                 approval_id="approval_7")
    linked = [entry for entry in audit.recent() if entry.approval_id == "approval_7"]
    assert {entry.actor for entry in linked} == {"ai", "human"}


def test_a_large_payload_is_truncated_rather_than_stored_whole(audit: AuditLog) -> None:
    """A log that keeps a megabyte of backtest output stops being readable."""
    entry = audit.record(actor="ai", action="backtest_strategy", outcome=Outcome.OK,
                         result={"trades": ["x" * 100] * 500})
    assert len(entry.result) <= MAX_PAYLOAD


def test_an_unserialisable_payload_is_recorded_rather_than_raising(audit: AuditLog) -> None:
    entry = audit.record(actor="ai", action="x", outcome=Outcome.OK, result=object())
    assert entry.result


def test_the_log_offers_no_update_or_delete(audit: AuditLog) -> None:
    """A history that can be edited is not a history."""
    for forbidden in ("update", "delete", "clear", "purge", "edit"):
        assert not hasattr(audit, forbidden), forbidden


def test_records_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "audit.db"
    AuditLog(path).record(actor="ai", action="x", outcome=Outcome.OK)
    assert AuditLog(path).count() == 1
