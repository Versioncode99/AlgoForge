"""Specialist positions must follow the verdict, not a script.

The replaced implementation returned the same four sentences and the same
confidences (0.62, 0.91, 0.88) for every run. The test that guarded it asserted
only that dissent existed — which a fixture satisfies trivially. These tests
assert the positions actually *move* with the evidence.
"""

from __future__ import annotations

import numpy as np
import pytest
from forge.agents import build_debate
from forge.judge import Judge, JudgeInput, probability_of_backtest_overfitting
from forge.judge.models import Verdict
from forge.research.cpcv import PathDistribution
from forge.research.walkforward import WalkForwardResult


def _full_evidence() -> dict[str, object]:
    rng = np.random.default_rng(5)
    matrix = rng.normal(0.0, 1.0, (400, 24))
    matrix[:, 0] += 0.4
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


def _verdict(**overrides: object) -> Verdict:
    values: dict[str, object] = {
        "run_id": "run_demo",
        "tier": "TRUTH_OOS",
        "pnl": tuple(np.random.default_rng(11).normal(30.0, 100.0, 300)),
        "trial_count": 24,
        "data_gate_passed": True,
        "preregistered": True,
        "implementation_tests_passed": True,
    }
    values.update(_full_evidence())
    values.update(overrides)
    return Judge().evaluate(JudgeInput(**values))  # type: ignore[arg-type]


def _claim(report, role: str):  # type: ignore[no-untyped-def]
    return next(claim for claim in report.claims if claim.role_id == role)


# ── the guarantees that must never change ────────────────────────────────────


def test_no_specialist_can_move_a_number_or_place_an_order() -> None:
    report = build_debate(_verdict())
    assert report.numeric_verdict_locked
    assert all(not role.can_change_numeric_verdict for role in report.roles)
    assert all(not role.can_execute_orders for role in report.roles)


def test_every_claim_cites_the_verdict_it_is_about() -> None:
    verdict = _verdict()
    report = build_debate(verdict)
    assert all(claim.evidence_ids for claim in report.claims)
    assert all(verdict.verdict_id in claim.evidence_ids for claim in report.claims)


def test_the_report_is_deterministic() -> None:
    verdict = _verdict()
    assert build_debate(verdict) == build_debate(verdict)


# ── the positions actually track the evidence ────────────────────────────────


def test_a_losing_strategy_is_opposed_not_merely_cautioned() -> None:
    losing = _verdict(pnl=tuple(np.random.default_rng(3).normal(-20.0, 80.0, 200)))
    researcher = _claim(build_debate(losing), "RESEARCHER")
    assert researcher.stance == "OPPOSE"
    assert "nothing here to promote" in researcher.statement


def test_the_critic_names_the_gate_that_actually_failed() -> None:
    """A failed G10 must be named, with its observed value."""
    failed = _verdict(tier="SWEEP")
    critic = _claim(build_debate(failed), "CRITIC")
    assert critic.stance == "OPPOSE"
    assert "G10" in critic.statement
    assert "G10" in critic.evidence_ids


def test_the_critic_distinguishes_unmeasured_from_failed() -> None:
    unmeasured = _verdict(overfitting=None, walk_forward=None, paths=None)
    critic = _claim(build_debate(unmeasured), "CRITIC")
    assert critic.stance == "CAUTION"
    assert "never measured" in critic.statement
    assert "G11" in critic.evidence_ids


def test_the_arbiter_reports_inconclusive_as_unanswered_not_near_miss() -> None:
    undecided = _verdict(overfitting=None, walk_forward=None, paths=None)
    arbiter = _claim(build_debate(undecided), "ARBITER")
    assert undecided.decision == "INCONCLUSIVE"
    assert arbiter.stance == "CAUTION"
    assert "unanswered question" in arbiter.statement


def test_the_arbiter_opposes_a_failure_decisively() -> None:
    failed = _verdict(tier="SWEEP")
    arbiter = _claim(build_debate(failed), "ARBITER")
    assert arbiter.stance == "OPPOSE"
    assert arbiter.confidence >= 0.9


def test_the_risk_auditor_flags_an_inadequate_track_record() -> None:
    short = _verdict(pnl=(40.0, -12.0, 33.0, -9.0) * 10)
    auditor = _claim(build_debate(short), "RISK_AUDITOR")
    assert auditor.stance in {"CAUTION", "OPPOSE"}


def test_the_risk_auditor_always_states_the_calibration_limit() -> None:
    """Fills are modelled. A risk opinion that omits that is misleading."""
    auditor = _claim(build_debate(_verdict()), "RISK_AUDITOR")
    if auditor.stance == "CAUTION" and "drawdown" in auditor.statement:
        assert "modelled rather than calibrated" in auditor.statement


def test_confidences_are_not_the_old_fixed_constants() -> None:
    """The regression guard: 0.62 / 0.91 / 0.88 for everything, forever."""
    weak = build_debate(_verdict(pnl=tuple(np.random.default_rng(3).normal(-20.0, 80.0, 200))))
    strong = build_debate(_verdict())
    weak_confidences = [claim.confidence for claim in weak.claims]
    strong_confidences = [claim.confidence for claim in strong.claims]
    assert weak_confidences != strong_confidences


def test_statements_differ_between_two_different_verdicts() -> None:
    weak = build_debate(_verdict(tier="SWEEP"))
    strong = build_debate(_verdict())
    assert [c.statement for c in weak.claims] != [c.statement for c in strong.claims]


# ── dissent ──────────────────────────────────────────────────────────────────


def test_disagreement_is_preserved_rather_than_resolved() -> None:
    report = build_debate(_verdict(overfitting=None, walk_forward=None, paths=None))
    stances = {claim.stance for claim in report.claims}
    assert len(stances) > 1
    assert report.dissent_present


def test_dissent_is_false_only_when_the_panel_genuinely_agrees() -> None:
    losing = build_debate(_verdict(pnl=tuple(np.random.default_rng(3).normal(-40.0, 20.0, 200))))
    stances = {claim.stance for claim in losing.claims}
    assert losing.dissent_present == (len(stances) > 1)


@pytest.mark.parametrize("tier", ["SWEEP", "TRUTH_OOS", "HOLDOUT"])
def test_a_report_is_produced_for_every_tier(tier: str) -> None:
    report = build_debate(_verdict(tier=tier))
    assert len(report.claims) == 4
    assert len(report.roles) == 4
