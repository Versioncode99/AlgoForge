"""The conformance harness, and the gate that had been standing in for it.

`TEST_TEMPLATE` promised, in its own docstring, that "a suite without a
lookahead trap is rejected by the harness". There was no harness: `get_tests()`
served the text to the interface and nothing ever ran it, while G2 reported
"tests pass" for every strategy in the library.

These tests cover the three ways that promise can be broken — a suite that is
never run, a suite that is run and fails, and a suite that is green but asserts
nothing about reading the future — and assert that none of them produce a pass.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest
from forge.judge import Judge, JudgeInput
from forge.strategy import StrategyLibrary, run_conformance
from forge.strategy.guard import check_test_source

WORKING_SUITE = """
import numpy as np

from strategy import entry_signal, exit_signal  # noqa: F401

PARAMS = {"sma_period": 20.0}


def test_window_cannot_see_the_future():
    assert True


def test_signal_is_deterministic():
    assert np.isfinite(1.0)
"""


def fake_module(name: str = "strategy") -> types.ModuleType:
    module = types.ModuleType(name)
    module.entry_signal = lambda window, params: 1  # type: ignore[attr-defined]
    module.exit_signal = lambda window, params, position: None  # type: ignore[attr-defined]
    return module


def report_for(source: str, *, code_hash: str = "abc") -> object:
    return run_conformance("demo", code_hash=code_hash, test_source=source, module=fake_module())


# ── the three ways a suite fails to be evidence ──────────────────────────────


def test_a_suite_that_was_never_written_is_absent_not_passing() -> None:
    report = report_for("")
    assert report.passed is None
    assert report.reason.startswith("SUITE_ERROR")


def test_a_green_suite_with_a_lookahead_trap_passes() -> None:
    report = report_for(WORKING_SUITE)
    assert report.passed is True
    assert report.has_lookahead_trap
    assert report.reason == "2_PASSED"


def test_a_failing_case_fails_the_report() -> None:
    source = WORKING_SUITE.replace("assert np.isfinite(1.0)", "assert 1 == 2, 'drifted'")
    report = report_for(source)
    assert report.passed is False
    assert [case.name for case in report.failures] == ["test_signal_is_deterministic"]
    assert report.failures[0].detail.startswith("drifted")


def test_a_green_suite_without_a_lookahead_trap_is_not_evidence() -> None:
    """Otherwise G2 becomes satisfiable by deleting the one test that matters."""
    source = WORKING_SUITE.replace("test_window_cannot_see_the_future", "test_something_else")
    report = report_for(source)
    assert all(case.passed for case in report.cases)
    assert report.has_lookahead_trap is False
    assert report.passed is None
    assert report.reason == "NO_LOOKAHEAD_TRAP"


def test_a_suite_that_raises_at_import_is_absent_not_failing() -> None:
    report = report_for("import numpy as np\nraise ValueError('boom')\n")
    assert report.passed is None
    assert "ValueError" in report.reason


def test_a_case_that_raises_something_other_than_assertion_still_fails() -> None:
    source = WORKING_SUITE + "\n\ndef test_explodes():\n    raise KeyError('nope')\n"
    report = report_for(source)
    assert report.passed is False
    assert "KeyError" in report.failures[0].detail


# ── the guard applies to test code too ───────────────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        "import subprocess",
        "import os",
        "from pathlib import Path",
        "open('secrets.txt')",
        "eval('1+1')",
        "__import__('os')",
    ],
)
def test_a_suite_that_would_be_refused_is_never_run(line: str) -> None:
    source = WORKING_SUITE + f"\n\ndef test_escape():\n    {line}\n"
    report = report_for(source)
    assert report.passed is None
    assert report.reason.startswith("GUARD_REFUSED")
    assert report.cases == ()


def test_the_test_guard_allows_what_the_template_actually_uses() -> None:
    from forge.strategy.templates import TEST_TEMPLATE

    source = (
        TEST_TEMPLATE.format(name="Demo")
        + '\n\nPARAMS = {"sma_period": 20}\n'
        + "\nfrom strategy import entry_signal, exit_signal  # noqa: E402,F401\n"
    )
    assert check_test_source(source) == []


def test_the_strategy_guard_still_demands_the_required_functions() -> None:
    """Relaxing the guard for tests must not relax it for strategies."""
    from forge.strategy.guard import check_source

    assert any("missing required function" in problem for problem in check_source("x = 1\n"))


# ── staleness ────────────────────────────────────────────────────────────────


def test_the_report_records_the_code_it_was_produced_against() -> None:
    report = report_for(WORKING_SUITE, code_hash="hash-one")
    assert report.code_hash == "hash-one"
    assert report.as_dict()["code_hash"] == "hash-one"


def test_two_different_suites_hash_differently() -> None:
    first = report_for(WORKING_SUITE)
    second = report_for(WORKING_SUITE + "\n\ndef test_extra():\n    assert True\n")
    assert first.test_hash != second.test_hash


# ── G2 ───────────────────────────────────────────────────────────────────────


def judge_with(**overrides: object) -> object:
    base = {
        "run_id": "run-1",
        "tier": "TRUTH_OOS",
        "pnl": tuple(float(v) for v in (80, -25, 95, -30, 70, -20, 110, -35, 60, 45) * 4),
        "trial_count": 1,
        "data_gate_passed": True,
        "preregistered": True,
    }
    return Judge().evaluate(JudgeInput(**{**base, **overrides}))  # type: ignore[arg-type]


def gate(verdict: object, name: str) -> object:
    return next(item for item in verdict.gates if item.gate == name)  # type: ignore[attr-defined]


def test_g2_is_inconclusive_when_the_suite_was_never_run() -> None:
    """The defect this replaces: a bool cannot express 'nobody ran it'."""
    verdict = judge_with(implementation_tests_passed=None)
    assert gate(verdict, "G2").status == "INCONCLUSIVE"
    assert gate(verdict, "G2").observed == "CONFORMANCE_NOT_RUN"
    assert verdict.decision != "PASS"


def test_g2_passes_only_on_a_real_passing_report() -> None:
    assert gate(judge_with(implementation_tests_passed=True), "G2").status == "PASS"


def test_g2_fails_on_a_real_failing_report() -> None:
    verdict = judge_with(implementation_tests_passed=False)
    assert gate(verdict, "G2").status == "FAIL"
    assert verdict.decision == "FAIL"


def test_detected_lookahead_fails_g2_even_with_no_conformance_evidence() -> None:
    """Lookahead is evidence of failure, not absence of evidence."""
    verdict = judge_with(implementation_tests_passed=None, lookahead_detected=True)
    assert gate(verdict, "G2").status == "FAIL"
    assert gate(verdict, "G2").observed == "LOOKAHEAD"
    assert verdict.decision == "FAIL"


def test_detected_lookahead_outranks_a_passing_suite() -> None:
    verdict = judge_with(implementation_tests_passed=True, lookahead_detected=True)
    assert gate(verdict, "G2").status == "FAIL"


def test_implementation_tests_default_to_absent_not_to_true() -> None:
    """The regression guard. This defaulted to True for the whole project's life."""
    from dataclasses import fields

    field = next(f for f in fields(JudgeInput) if f.name == "implementation_tests_passed")
    assert field.default is None


# ── the real library ─────────────────────────────────────────────────────────


def test_a_freshly_created_strategy_passes_its_own_suite(tmp_path: Path) -> None:
    """End to end against the template the library actually writes."""
    library = StrategyLibrary(tmp_path / "strategies")
    spec = library.create_from_template("momentum_breakout", symbol="MNQ.CME")
    module = library.load_module(spec.strategy_id)
    report = run_conformance(
        spec.strategy_id,
        code_hash=library.code_hash(spec.strategy_id),
        test_source=library.get_tests(spec.strategy_id),
        module=module,
        test_path=library.test_path(spec.strategy_id),
    )
    assert report.passed is True, report.reason
    assert report.has_lookahead_trap
    library.unload_module(spec.strategy_id)
