from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from forge.judge import Judge, JudgeInput, probability_of_backtest_overfitting
from forge.research.cpcv import PathDistribution
from forge.research.walkforward import WalkForwardResult


def strong_pnl(seed: int = 11, size: int = 300) -> tuple[float, ...]:
    """A series with a genuine, large edge — the only kind that should pass."""
    rng = np.random.default_rng(seed)
    return tuple(rng.normal(30.0, 100.0, size))


def full_evidence() -> dict[str, object]:
    """Every validation artifact the ladder demands, all favourable."""
    rng = np.random.default_rng(5)
    matrix = rng.normal(0.0, 1.0, (400, 24))
    matrix[:, 0] += 0.4  # one configuration carries a real edge
    return {
        "trial_sharpes": tuple(rng.normal(0.0, 0.06, 24)),
        "overfitting": probability_of_backtest_overfitting(matrix, blocks=8),
        "walk_forward": WalkForwardResult(
            fold_count=6,
            in_sample_sharpe=0.30,
            out_of_sample_sharpe=0.24,
            efficiency=0.80,
            positive_folds=5,
            consistency=0.833,
            degradation=0.06,
        ),
        "paths": PathDistribution(
            paths=5,
            mean_sharpe=0.25,
            median_sharpe=0.26,
            sharpe_p05=0.12,
            sharpe_p95=0.34,
            positive_share=1.0,
            dispersion=0.05,
        ),
    }


def judge_input(**overrides: object) -> JudgeInput:
    values: dict[str, object] = {
        "run_id": "run_demo",
        "tier": "TRUTH_OOS",
        "pnl": strong_pnl(),
        "trial_count": 24,
        "data_gate_passed": True,
        "preregistered": True,
        "implementation_tests_passed": True,
    }
    values.update(full_evidence())
    values.update(overrides)
    return JudgeInput(**values)  # type: ignore[arg-type]


def gate(verdict: object, name: str) -> object:
    return next(g for g in verdict.gates if g.gate == name)  # type: ignore[attr-defined]


def test_judge_is_deterministic_and_traced() -> None:
    first = Judge().evaluate(judge_input())
    second = Judge().evaluate(judge_input())
    assert first == second
    assert first.decision == "PASS"
    assert len(first.gates) == 14
    assert {trace.metric for trace in first.traces} == set(first.metrics)


def test_lookahead_and_sweep_tier_fail_closed() -> None:
    verdict = Judge().evaluate(judge_input(lookahead_detected=True, tier="SWEEP"))
    assert verdict.decision == "FAIL"
    assert gate(verdict, "G2").status == "FAIL"
    assert gate(verdict, "G10").status == "FAIL"


def test_missing_validation_evidence_is_inconclusive_not_a_pass() -> None:
    """The central rule: an unmeasured gate withholds the pass, never grants it."""
    verdict = Judge().evaluate(
        JudgeInput(
            run_id="run_bare",
            tier="HOLDOUT",
            pnl=strong_pnl(),
            trial_count=24,
            data_gate_passed=True,
            preregistered=True,
            implementation_tests_passed=True,
        )
    )
    assert verdict.decision == "INCONCLUSIVE"
    for name in ("G5", "G11", "G12", "G13"):
        assert gate(verdict, name).status == "INCONCLUSIVE"


def test_unrecorded_search_cannot_be_deflated() -> None:
    """Without the trial Sharpes, V[SR] is unknown and G5 must say so."""
    verdict = Judge().evaluate(judge_input(trial_sharpes=None))
    assert gate(verdict, "G5").status == "INCONCLUSIVE"
    assert gate(verdict, "G5").observed == "V[SR]_NOT_MEASURED"
    assert verdict.decision == "INCONCLUSIVE"


def test_single_preregistered_hypothesis_needs_no_deflation() -> None:
    verdict = Judge().evaluate(judge_input(trial_count=1, trial_sharpes=None))
    assert gate(verdict, "G5").status == "PASS"


def test_overfit_selection_fails_even_with_a_profitable_curve() -> None:
    """PBO at chance level fails the run however good the equity curve looks."""
    rng = np.random.default_rng(19)
    noise = rng.normal(0.0, 1.0, (400, 24))  # no configuration has an edge
    verdict = Judge().evaluate(
        judge_input(overfitting=probability_of_backtest_overfitting(noise, blocks=8))
    )
    assert gate(verdict, "G11").status == "FAIL"
    assert verdict.decision == "FAIL"


def test_walk_forward_collapse_fails_on_efficiency_alone() -> None:
    """Positive out of sample is not enough if most of the edge evaporated."""
    collapsed = WalkForwardResult(
        fold_count=6,
        in_sample_sharpe=0.90,
        out_of_sample_sharpe=0.05,
        efficiency=0.055,
        positive_folds=4,
        consistency=0.667,
        degradation=0.85,
    )
    verdict = Judge().evaluate(judge_input(walk_forward=collapsed))
    assert gate(verdict, "G12").status == "FAIL"


def test_thin_sample_fails_before_any_ratio_is_believed() -> None:
    verdict = Judge().evaluate(judge_input(pnl=strong_pnl(size=12)))
    assert gate(verdict, "G3").status == "FAIL"


def test_risk_gate_is_not_circular() -> None:
    """A losing strategy must never earn a wider drawdown allowance."""
    losing = tuple(-abs(value) for value in strong_pnl(size=60))
    verdict = Judge().evaluate(judge_input(pnl=losing))
    assert gate(verdict, "G8").status == "FAIL"
    assert gate(verdict, "G4").status == "FAIL"


def test_empty_pnl_is_rejected() -> None:
    with pytest.raises(ValueError):
        Judge().evaluate(judge_input(pnl=()))


def test_judge_source_has_no_agent_or_model_imports() -> None:
    import forge.judge.engine as engine

    source = Path(engine.__file__).read_text(encoding="utf-8")
    forbidden = ("openai", "anthropic", "forge.agents", "forge.prop", "fastapi")
    assert not any(token in source for token in forbidden)
