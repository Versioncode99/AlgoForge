from __future__ import annotations

from forge.analytics import build_normal_analysis
from forge.judge import Judge, JudgeInput

PNL = (80, -25, 95, -30, 70, -20, 110, -35, 60, 45, -15, 85) * 3


def demo_verdict():
    return Judge().evaluate(
        JudgeInput(
            run_id="run_demo",
            tier="TRUTH_OOS",
            pnl=PNL,
            trial_count=4,
            data_gate_passed=True,
            preregistered=True,
            implementation_tests_passed=True,
        )
    )


def test_normal_analysis_is_seed_deterministic() -> None:
    first = build_normal_analysis("run_demo", demo_verdict(), PNL, paths=120, seed=42)
    second = build_normal_analysis("run_demo", demo_verdict(), PNL, paths=120, seed=42)
    assert first == second
    assert first.risk.p05_path[-1] <= first.risk.median_path[-1] <= first.risk.p95_path[-1]
    assert "EVT_SUPPRESSED_INADEQUATE_EXCEEDANCES" in first.risk.warnings


def test_regimes_never_hide_sparse_samples() -> None:
    analysis = build_normal_analysis("run_demo", demo_verdict(), PNL, paths=20)
    assert len(analysis.regimes) == 4
    assert all(regime.confidence == "LOW" for regime in analysis.regimes)
    assert sum(regime.trade_count for regime in analysis.regimes) == len(PNL)
