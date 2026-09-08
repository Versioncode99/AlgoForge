"""The findings layer must explain a verdict without ever becoming one.

Three properties are load-bearing and each has a test that would fail loudly if
the layer drifted:

1. It is a pure function of a verdict. Decision, grade, every gate status and
   every metric come through untouched.
2. INCONCLUSIVE stays INCONCLUSIVE. A gate that did not measure something is
   reported as an absence, never ranked or worded as a shortfall.
3. No suggested fix proposes weakening the standard. This is the one that
   matters most: the obvious "fix" for a deflated-Sharpe shortfall is to declare
   fewer trials, and a layer that said so would be teaching users to launder a
   failure into a pass.
"""

from __future__ import annotations

import pytest
from forge.judge import Judge, JudgeInput, Severity, explain
from forge.judge.explain import _FIX, _TO_MEASURE, _WHY, DIMENSION, FOUNDATIONAL
from forge.judge.models import Verdict

from tests.judge.test_judge import judge_input, strong_pnl


def verdict(**overrides: object) -> Verdict:
    return Judge().evaluate(judge_input(**overrides))


def bare_verdict() -> Verdict:
    """A run with nothing behind it: no evidence, no receipts, in-sample tier."""
    return Judge().evaluate(
        JudgeInput(
            run_id="run_bare",
            tier="LEGACY_IN_SAMPLE",
            pnl=strong_pnl(),
            trial_count=1,
            data_gate_passed=None,
            preregistered=False,
            implementation_tests_passed=None,
        )
    )


def inconclusive_verdict() -> Verdict:
    """A run where nothing failed and several gates simply never ran.

    This is the state the whole architecture turns on, and the one a legibility
    layer is most likely to damage, so it gets its own fixture rather than being
    hoped for as a side effect of another one.
    """
    return Judge().evaluate(
        JudgeInput(
            run_id="run_unmeasured",
            tier="TRUTH_OOS",
            pnl=strong_pnl(),
            trial_count=24,
            data_gate_passed=True,
            preregistered=True,
            implementation_tests_passed=True,
        )
    )


def test_every_gate_is_accounted_for_exactly_once() -> None:
    result = explain(verdict())
    seen = [item.gate for item in (*result.findings, *result.strengths)]
    assert sorted(seen) == sorted(gate.gate for gate in verdict().gates)
    assert len(seen) == len(set(seen))
    assert result.passed + result.failed + result.unmeasured == len(seen)


def test_the_decision_and_grade_are_copied_never_recomputed() -> None:
    sources = (verdict(), bare_verdict(), inconclusive_verdict(), verdict(lookahead_detected=True))
    for source in sources:
        result = explain(source)
        assert result.decision == source.decision
        assert result.grade == source.grade
        assert result.verdict_id == source.verdict_id
        assert result.run_id == source.run_id
        assert result.dimensions == dict(source.dimensions)
        assert result.limitations == source.limitations
        assert result.labels == source.labels


def test_explaining_a_verdict_does_not_mutate_it() -> None:
    source = bare_verdict()
    before = source.model_dump(mode="json")
    explain(source)
    assert source.model_dump(mode="json") == before


def test_inconclusive_gates_are_absences_not_shortfalls() -> None:
    result = explain(bare_verdict())
    unmeasured = [item for item in result.findings if item.status == "INCONCLUSIVE"]
    assert unmeasured, "a bare run must leave gates unmeasured"
    for item in unmeasured:
        assert item.severity is Severity.NOT_MEASURED
        # Never CRITICAL or HIGH: a gate that measured nothing has found nothing
        # wrong, and ranking it alongside real failures is how INCONCLUSIVE
        # starts reading as FAIL.
        assert item.severity not in {Severity.CRITICAL, Severity.HIGH}
        assert "not measured" in item.headline
        assert item.what_would_help, f"{item.gate} says nothing about how to measure it"


def test_a_failed_foundational_gate_outranks_every_other_finding() -> None:
    result = explain(verdict(lookahead_detected=True))
    assert result.findings[0].severity is Severity.CRITICAL
    assert result.findings[0].gate in FOUNDATIONAL
    order = [item.severity for item in result.findings]
    ranks = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.NOT_MEASURED: 2, Severity.INFO: 3}
    assert order == sorted(order, key=lambda severity: ranks[severity])


def test_passing_gates_are_reported_apart_from_findings() -> None:
    result = explain(verdict())
    assert all(item.status != "PASS" for item in result.findings)
    assert all(item.status == "PASS" for item in result.strengths)
    # Strengths are never a counterweight: a verdict with a failed gate still
    # leads with the failure.
    failing = explain(verdict(lookahead_detected=True))
    assert failing.findings and failing.strengths
    assert failing.decision == "FAIL"


@pytest.mark.parametrize("gate_id", sorted(DIMENSION))
def test_every_gate_has_a_dimension_and_a_reason(gate_id: str) -> None:
    assert DIMENSION[gate_id]
    assert _WHY[gate_id], f"{gate_id} has no stated reason it matters"


#: Language that would move a threshold, shrink a declared search, or re-run
#: until the answer changes. A suggestion containing any of these is teaching a
#: user to defeat the gate rather than to satisfy it.
FORBIDDEN = (
    "lower the threshold",
    "lower threshold",
    "raise the threshold",
    "relax",
    "loosen",
    "disable",
    "skip the gate",
    "bypass",
    "ignore the gate",
    "declare fewer",
    "report fewer trials",
    "fewer trials",
    "reduce the trial count",
    "re-run until",
    "rerun until",
    "try again until",
    "override",
    "waive",
    "mark it as pass",
    "force a pass",
    "turn off",
)


@pytest.mark.parametrize("gate_id", sorted(set(_FIX) | set(_TO_MEASURE)))
def test_no_suggested_fix_proposes_weakening_the_standard(gate_id: str) -> None:
    for text in (_FIX.get(gate_id, ""), _TO_MEASURE.get(gate_id, "")):
        lowered = text.lower()
        for phrase in FORBIDDEN:
            assert phrase not in lowered, f"{gate_id} suggests: {phrase!r}"


def test_no_suggestion_reaching_a_caller_proposes_weakening_the_standard() -> None:
    """The same guard applied to the rendered output rather than the tables."""
    sources = (verdict(), bare_verdict(), inconclusive_verdict(), verdict(lookahead_detected=True))
    for source in sources:
        for item in explain(source).findings:
            lowered = item.what_would_help.lower()
            for phrase in FORBIDDEN:
                assert phrase not in lowered, f"{item.gate} suggests: {phrase!r}"


def test_the_headline_never_calls_an_inconclusive_verdict_a_failure() -> None:
    source = inconclusive_verdict()
    assert source.decision == "INCONCLUSIVE", "the fixture stopped being inconclusive"
    result = explain(source)
    assert result.decision == "INCONCLUSIVE"
    assert "INCONCLUSIVE" in result.headline
    assert "absence of evidence" in result.headline
    # The word FAIL must not appear anywhere else in the sentence, and the
    # headline must not imply that waiting turns it into a pass.
    assert "FAIL" not in result.headline.replace("INCONCLUSIVE", "")
    assert all(item.severity is not Severity.CRITICAL for item in result.findings)


def test_the_layer_adds_no_second_grade() -> None:
    """Two grades from one body of evidence invites quoting the kinder one."""
    result = explain(bare_verdict())
    fields = set(type(result).model_fields)
    assert "grade" in fields
    assert not (fields - {"grade"}) & {"score", "rating", "confidence", "overall"}


def test_a_finding_carries_the_observation_that_produced_it() -> None:
    source = verdict(lookahead_detected=True)
    by_gate = {gate.gate: gate for gate in source.gates}
    for item in explain(source).findings:
        assert item.observed == by_gate[item.gate].observed
        assert item.rule == by_gate[item.gate].rule
        assert item.status == by_gate[item.gate].status
