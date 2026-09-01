from datetime import UTC, datetime

from forge.agents import build_demo_debate
from forge.memory import DecisionMemory


def test_debate_contains_cited_dissent_and_cannot_change_numbers() -> None:
    report = build_demo_debate("run_1", "verdict_1")
    assert report.dissent_present
    assert report.numeric_verdict_locked
    assert all(claim.evidence_ids for claim in report.claims)
    assert all(not role.can_change_numeric_verdict for role in report.roles)
    assert all(not role.can_execute_orders for role in report.roles)


def test_memory_partitions_and_hash_chain_are_enforced() -> None:
    memory = DecisionMemory()
    memory.append(
        "SEMANTIC",
        "run_1",
        "Positive sample expectancy.",
        ("trace_1",),
        0.6,
        datetime(2026, 9, 1, tzinfo=UTC),
    )
    memory.append(
        "ADVERSARIAL",
        "run_1",
        "Synthetic data blocks promotion.",
        ("gate_0",),
        1.0,
        datetime(2026, 9, 1, 0, 0, 1, tzinfo=UTC),
    )
    visible = memory.retrieve("run_1", {"ADVERSARIAL"})
    assert len(visible) == 1
    assert visible[0].partition == "ADVERSARIAL"
    assert memory.verify_chain()
