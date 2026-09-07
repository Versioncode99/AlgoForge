from datetime import UTC, datetime

from forge.memory import DecisionMemory

# The debate moved to tests/agents/test_debate.py when it stopped being a
# fixture and started being derived from the verdict.


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
