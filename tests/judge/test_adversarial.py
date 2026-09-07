"""Adversarial tests: can a gate be made to say something it should not?

Every test here started as a probe that succeeded against the code at
`17f4d22`. They are kept as regressions because each one is a way the ladder
could be made to misrepresent evidence, and none of them are exotic — a
parameter grid whose settings do not bite produces identical trial Sharpes, a
short holdout produces no drawdown, and a strategy that never loses produces
zero variance.

See `docs/2026-09-08-opus-deep-bug-audit.md` for how each was found.
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pytest
from forge.judge import Judge, JudgeInput
from forge.judge.statistics import deflated_sharpe_ratio

STRONG = tuple(float(v) for v in (80, -25, 95, -30, 70, -20, 110, -35, 60, 45) * 4)


def judge_with(**overrides: object) -> Any:
    base: dict[str, Any] = {
        "run_id": "probe",
        "tier": "HOLDOUT",
        "pnl": STRONG,
        "trial_count": 1,
        "data_gate_passed": True,
        "preregistered": True,
        "implementation_tests_passed": True,
        "engine_consistent": True,
        "mechanism_aligned": True,
    }
    return Judge().evaluate(JudgeInput(**{**base, **overrides}))


def gate(verdict: Any, name: str) -> Any:
    return next(item for item in verdict.gates if item.gate == name)


# ── G5: the deflation hurdle can silently collapse ───────────────────────────


def test_a_search_whose_trials_all_scored_the_same_cannot_deflate() -> None:
    """The most dangerous failure available: it fails *permissively*.

    `V[SR] = 0` makes `expected_max_sharpe` return 0.0, so a thousand-trial
    search is deflated against a hurdle of zero and the Deflated Sharpe silently
    becomes a plain Probabilistic Sharpe. It looks identical to a well-deflated
    result. Counting configurations does not catch it — forty identical Sharpes
    clears every count.
    """
    identical = tuple([0.5] * 40)
    verdict = judge_with(trial_count=1000, trial_sharpes=identical)
    assert gate(verdict, "G5").status == "INCONCLUSIVE"
    assert gate(verdict, "G5").observed == "TRIAL_SHARPES_HAVE_NO_SPREAD"

    result = deflated_sharpe_ratio(STRONG, 1000, identical)
    assert result.spread_is_measured is False
    assert result.credible is False, "a zero hurdle must never read as credible"


def test_a_spread_that_is_only_floating_point_noise_is_still_no_spread() -> None:
    """Relative, not absolute: agreement to twelve digits is agreement."""
    almost = tuple(0.5 + index * 1e-15 for index in range(40))
    assert gate(judge_with(trial_count=1000, trial_sharpes=almost), "G5").status == "INCONCLUSIVE"


def test_a_real_spread_still_deflates() -> None:
    """The fix must not disable deflation wherever it was working."""
    spread = tuple(0.1 + index * 0.05 for index in range(40))
    verdict = judge_with(trial_count=1000, trial_sharpes=spread)
    assert gate(verdict, "G5").status in {"PASS", "FAIL"}
    assert deflated_sharpe_ratio(STRONG, 1000, spread).spread_is_measured is True


@pytest.mark.parametrize("poison", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_trial_sharpes_do_not_reach_the_verdict(poison: float) -> None:
    """These propagated through np.var into a NaN DSR, and `nan >= 0.95` is
    False — so it failed the gate for the wrong reason while emitting a bare
    `NaN` token that no strict JSON parser accepts."""
    verdict = judge_with(trial_count=1000, trial_sharpes=(0.1, poison, 0.3) * 4)
    assert gate(verdict, "G5").status == "INCONCLUSIVE"
    assert gate(verdict, "G5").observed == "TRIAL_SHARPES_NON_FINITE"
    assert math.isfinite(float(verdict.metrics["deflated_sharpe"]))


def test_no_metric_is_ever_non_finite() -> None:
    """Every metric is serialised. A NaN breaks JSON.parse in the browser."""
    verdict = judge_with(trial_count=1000, trial_sharpes=(0.1, float("nan"), 0.3) * 4)
    for name, value in verdict.metrics.items():
        assert math.isfinite(float(value)), f"{name} is non-finite"
    blob = json.dumps(verdict.model_dump(mode="json"))
    assert "NaN" not in blob
    assert "Infinity" not in blob


def test_the_payload_survives_a_strict_json_parser() -> None:
    def refuse(token: str) -> None:
        raise AssertionError(f"non-JSON constant in payload: {token}")

    blob = json.dumps(judge_with(trial_count=1000, trial_sharpes=(0.1, float("inf"))).model_dump(
        mode="json"
    ))
    json.loads(blob, parse_constant=refuse)


# ── G8: no drawdown is not bad risk ──────────────────────────────────────────


def test_a_run_with_no_drawdown_reports_risk_as_unmeasured() -> None:
    """`calmar_ratio` returns 0.0 with no drawdown to divide by — the same
    number a genuinely terrible strategy gets. Reporting that as a risk FAIL
    says "poor return per unit of drawdown" about a run that had none."""
    verdict = judge_with(pnl=tuple([10.0] * 60))
    assert gate(verdict, "G8").status == "INCONCLUSIVE"
    assert gate(verdict, "G8").observed == "NO_DRAWDOWN_TO_MEASURE"


def test_a_real_drawdown_is_still_judged() -> None:
    assert gate(judge_with(pnl=STRONG), "G8").status in {"PASS", "FAIL"}
    assert isinstance(gate(judge_with(pnl=STRONG), "G8").observed, float)


def test_a_losing_run_with_a_real_drawdown_still_fails_risk() -> None:
    losing = tuple([-5.0, 2.0] * 30)
    assert gate(judge_with(pnl=losing), "G8").status == "FAIL"


# ── refusing pathological input outright ─────────────────────────────────────


@pytest.mark.parametrize(
    "pnl",
    [
        (1.0, float("nan"), 2.0),
        (1.0, float("inf"), 2.0),
        (1.0, float("-inf")),
        (),
    ],
)
def test_non_finite_or_empty_pnl_is_refused(pnl: tuple[float, ...]) -> None:
    with pytest.raises(ValueError, match="finite non-empty pnl"):
        judge_with(pnl=pnl)


def test_a_series_that_overflows_when_summed_is_refused() -> None:
    """Individually finite values can still overflow the cumulative curve, and
    every drawdown metric runs on that curve."""
    overflowing = tuple([1e308] * 40)
    assert all(math.isfinite(value) for value in overflowing)
    with pytest.raises(ValueError, match="overflows when summed"):
        judge_with(pnl=overflowing)


def test_extreme_but_summable_magnitudes_are_still_judged() -> None:
    """The overflow guard must not refuse merely large numbers."""
    verdict = judge_with(pnl=tuple([1e100, -5e99] * 30))
    assert verdict.decision in {"PASS", "FAIL", "INCONCLUSIVE"}
    for value in verdict.metrics.values():
        assert math.isfinite(float(value))


# ── degenerate series must not look like edges ───────────────────────────────


@pytest.mark.parametrize(
    ("name", "pnl"),
    [
        ("constant positive", tuple([10.0] * 60)),
        ("all zeros", tuple([0.0] * 60)),
        ("alternating, zero sum", tuple([1.0, -1.0] * 30)),
        ("denormals", tuple([1e-300] * 30 + [2e-300] * 30)),
        ("near-constant with one hair of noise", tuple([10.0] * 59 + [10.000001])),
    ],
)
def test_no_degenerate_series_reaches_pass(name: str, pnl: tuple[float, ...]) -> None:
    """Even with every non-statistical gate handed a pass."""
    assert judge_with(pnl=pnl).decision != "PASS", name


def test_a_single_enormous_win_does_not_carry_the_verdict() -> None:
    """One trade doing all the work is not an edge; G6 is what catches it."""
    lopsided = tuple([-0.01] * 59 + [1_000_000.0])
    verdict = judge_with(pnl=lopsided)
    assert verdict.decision != "PASS"
    assert gate(verdict, "G6").status == "FAIL"


def test_zero_variance_does_not_produce_an_infinite_sharpe() -> None:
    from forge.judge.statistics import per_period_sharpe

    assert per_period_sharpe(np.full(60, 7.0)) == 0.0
    assert math.isfinite(per_period_sharpe(np.full(60, 7.0)))
