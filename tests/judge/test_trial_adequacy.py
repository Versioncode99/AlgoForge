"""G5 and G11 must refuse evidence that is present but too thin to support them.

The judge already refuses *absent* evidence. This file covers the harder case:
a trial matrix that exists, produces a number from a correct estimator, and is
nonetheless not an estimate of anything — two configurations give a variance
from two samples and a rank that is nearly a coin flip.

Before this, the engine handed the judge exactly two configurations and G5/G11
reported confident-looking values built on that.
"""

from __future__ import annotations

import numpy as np
from forge.judge import Judge, probability_of_backtest_overfitting
from forge.judge.engine import MINIMUM_TRIAL_CONFIGURATIONS

from tests.judge.test_judge import judge_input, strong_pnl


def gate(verdict, name: str):  # type: ignore[no-untyped-def]
    return next(item for item in verdict.gates if item.gate == name)


def _matrix(configurations: int, seed: int = 5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    matrix = rng.normal(0.0, 1.0, (400, configurations))
    matrix[:, 0] += 0.4
    return matrix


# ── G5: deflated Sharpe ──────────────────────────────────────────────────────


def test_too_few_trial_sharpes_is_inconclusive_not_a_number() -> None:
    verdict = Judge().evaluate(
        judge_input(trial_count=1200, trial_sharpes=(0.10, 0.12))
    )
    g5 = gate(verdict, "G5")
    assert g5.status == "INCONCLUSIVE"
    assert g5.observed == "ONLY_2_TRIAL_SHARPES"
    assert verdict.decision != "PASS"


def test_absent_and_inadequate_are_reported_differently() -> None:
    """A researcher needs to know which of the two happened."""
    absent = gate(Judge().evaluate(judge_input(trial_sharpes=None)), "G5")
    thin = gate(Judge().evaluate(judge_input(trial_sharpes=(0.1, 0.2, 0.3))), "G5")
    assert absent.observed == "V[SR]_NOT_MEASURED"
    assert thin.observed == "ONLY_3_TRIAL_SHARPES"


def test_exactly_the_minimum_is_measurable() -> None:
    rng = np.random.default_rng(3)
    sharpes = tuple(rng.normal(0.0, 0.06, MINIMUM_TRIAL_CONFIGURATIONS))
    g5 = gate(Judge().evaluate(judge_input(trial_sharpes=sharpes)), "G5")
    assert g5.status in {"PASS", "FAIL"}
    assert isinstance(g5.observed, float)


def test_a_single_preregistered_hypothesis_still_needs_no_deflation() -> None:
    """One trial is not a search, so the thinness rule must not catch it."""
    g5 = gate(
        Judge().evaluate(judge_input(trial_count=1, trial_sharpes=None)),
        "G5",
    )
    assert g5.status in {"PASS", "FAIL"}


def test_the_limitation_names_the_shortfall() -> None:
    verdict = Judge().evaluate(judge_input(trial_sharpes=(0.1, 0.2)))
    trace = next(t for t in verdict.traces if t.metric == "deflated_sharpe")
    assert any("only 2 trial Sharpes" in note for note in trace.limitations)


# ── G11: probability of backtest overfitting ─────────────────────────────────


def test_pbo_over_two_configurations_is_inconclusive() -> None:
    verdict = Judge().evaluate(
        judge_input(overfitting=probability_of_backtest_overfitting(_matrix(2), blocks=8))
    )
    g11 = gate(verdict, "G11")
    assert g11.status == "INCONCLUSIVE"
    assert g11.observed == "ONLY_2_CONFIGURATIONS"
    assert verdict.decision != "PASS"


def test_pbo_over_enough_configurations_is_judged() -> None:
    verdict = Judge().evaluate(
        judge_input(
            overfitting=probability_of_backtest_overfitting(
                _matrix(MINIMUM_TRIAL_CONFIGURATIONS), blocks=8
            )
        )
    )
    g11 = gate(verdict, "G11")
    assert g11.status in {"PASS", "FAIL"}
    assert isinstance(g11.observed, float)


def test_thin_pbo_earns_no_robustness_credit() -> None:
    """An inadequate PBO must not quietly inflate the score."""
    thin = Judge().evaluate(
        judge_input(overfitting=probability_of_backtest_overfitting(_matrix(2), blocks=8))
    )
    assert thin.dimensions["robustness"] == 0


def test_thin_pbo_says_so_in_its_limitations() -> None:
    verdict = Judge().evaluate(
        judge_input(overfitting=probability_of_backtest_overfitting(_matrix(3), blocks=8))
    )
    trace = next(t for t in verdict.traces if t.metric == "probability_of_overfitting")
    assert any("only 3 configurations" in note for note in trace.limitations)


# ── the two together ─────────────────────────────────────────────────────────


def test_a_thin_search_cannot_pass_however_good_the_curve_looks() -> None:
    """The regression this guards: a great equity curve plus a 2-point grid."""
    verdict = Judge().evaluate(
        judge_input(
            pnl=strong_pnl(seed=4),
            trial_count=5000,
            trial_sharpes=(0.31, 0.29),
            overfitting=probability_of_backtest_overfitting(_matrix(2), blocks=8),
        )
    )
    assert verdict.decision == "INCONCLUSIVE"
    assert gate(verdict, "G5").status == "INCONCLUSIVE"
    assert gate(verdict, "G11").status == "INCONCLUSIVE"
    # Not a failure — nothing was disproven. The search was simply too narrow.
    assert all(g.status != "FAIL" for g in verdict.gates)
