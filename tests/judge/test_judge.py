from __future__ import annotations

from pathlib import Path

import numpy as np
from forge.judge import Judge, JudgeInput
from forge.sweep import ArraySweepEngine


def profitable_input(**overrides: object) -> JudgeInput:
    values: dict[str, object] = {
        "run_id": "run_demo",
        "tier": "TRUTH_OOS",
        "pnl": (80, -25, 95, -30, 70, -20, 110, -35, 60, 45, -15, 85) * 3,
        "trial_count": 4,
        "data_gate_passed": True,
        "preregistered": True,
        "implementation_tests_passed": True,
    }
    values.update(overrides)
    return JudgeInput(**values)  # type: ignore[arg-type]


def test_judge_is_deterministic_and_traced() -> None:
    first = Judge().evaluate(profitable_input())
    second = Judge().evaluate(profitable_input())
    assert first == second
    assert first.decision == "PASS"
    assert len(first.gates) == 10
    assert {trace.metric for trace in first.traces} == set(first.metrics)


def test_lookahead_and_sweep_tier_fail_closed() -> None:
    verdict = Judge().evaluate(profitable_input(lookahead_detected=True, tier="SWEEP"))
    assert verdict.decision == "FAIL"
    assert next(gate for gate in verdict.gates if gate.gate == "G2").status == "FAIL"
    assert next(gate for gate in verdict.gates if gate.gate == "G9").status == "FAIL"


def test_sweep_counts_every_combination_and_cannot_promote() -> None:
    result = ArraySweepEngine().run(
        np.array([0.01, -0.005, 0.02]),
        {"threshold": [0.0, 0.01], "size": [1.0, 2.0]},
        cost_per_trade=0.001,
    )
    assert len(result.trials) == 4
    assert result.tier == "SWEEP"
    assert result.promotable is False


def test_judge_source_has_no_agent_or_model_imports() -> None:
    import forge.judge.engine as engine

    source = Path(engine.__file__).read_text(encoding="utf-8")
    forbidden = ("openai", "anthropic", "forge.agents", "forge.prop", "fastapi")
    assert not any(token in source for token in forbidden)
