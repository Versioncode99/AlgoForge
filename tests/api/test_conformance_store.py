"""Stored conformance evidence, and the staleness rule that keeps it honest.

A report produced against source that has since been edited is not evidence
about the current code. The store treats it as absent, which the judge reports
as INCONCLUSIVE — the same rule `judge_evidence` applies to validation evidence,
for the same reason.
"""

from __future__ import annotations

import json
from pathlib import Path

from forge.strategy import StrategyLibrary
from forge_api.conformance_store import (
    conformance_verdict,
    ensure_conformance,
    load_conformance,
    refresh_conformance,
)


def library_with_one(tmp_path: Path) -> tuple[StrategyLibrary, str]:
    library = StrategyLibrary(tmp_path / "strategies")
    spec = library.create_from_template("momentum_breakout", symbol="MNQ.CME")
    return library, spec.strategy_id


def test_a_report_round_trips(tmp_path: Path) -> None:
    library, strategy_id = library_with_one(tmp_path)
    module = library.load_module(strategy_id)
    code_hash = library.code_hash(strategy_id)

    report = refresh_conformance(tmp_path, library, strategy_id, module=module, code_hash=code_hash)
    assert report.passed is True

    stored = load_conformance(tmp_path, strategy_id)
    assert stored is not None
    assert stored["passed"] is True
    assert stored["has_lookahead_trap"] is True
    assert "recorded_at" in stored
    library.unload_module(strategy_id)


def test_evidence_for_different_source_reads_as_absent(tmp_path: Path) -> None:
    """The rule that stops a gate quietly measuring the wrong code."""
    library, strategy_id = library_with_one(tmp_path)
    module = library.load_module(strategy_id)
    refresh_conformance(
        tmp_path, library, strategy_id, module=module, code_hash=library.code_hash(strategy_id)
    )
    assert conformance_verdict(tmp_path, strategy_id, code_hash=library.code_hash(strategy_id))

    library.write_source(strategy_id, library.get_source(strategy_id) + "\n# edited\n")
    assert (
        conformance_verdict(tmp_path, strategy_id, code_hash=library.code_hash(strategy_id)) is None
    )
    library.unload_module(strategy_id)


def test_never_run_reads_as_absent(tmp_path: Path) -> None:
    assert conformance_verdict(tmp_path, "never_seen", code_hash="abc") is None


def test_unreadable_evidence_reads_as_absent_not_favourable(tmp_path: Path) -> None:
    path = tmp_path / "data" / "conformance" / "demo.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ truncated", encoding="utf-8")
    assert load_conformance(tmp_path, "demo") is None
    assert conformance_verdict(tmp_path, "demo", code_hash="abc") is None


def test_a_report_that_says_null_reads_as_absent(tmp_path: Path) -> None:
    """`passed: null` means the suite was not evidence. It is not a pass."""
    path = tmp_path / "data" / "conformance" / "demo.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"code_hash": "abc", "passed": None, "reason": "NO_LOOKAHEAD_TRAP"}),
        encoding="utf-8",
    )
    assert conformance_verdict(tmp_path, "demo", code_hash="abc") is None


def test_a_traversing_strategy_id_is_refused(tmp_path: Path) -> None:
    assert load_conformance(tmp_path, "../../etc/passwd") is None
    assert conformance_verdict(tmp_path, "../../etc/passwd", code_hash="abc") is None


def test_ensure_runs_the_suite_when_nothing_is_stored(tmp_path: Path) -> None:
    library, strategy_id = library_with_one(tmp_path)
    module = library.load_module(strategy_id)
    assert load_conformance(tmp_path, strategy_id) is None

    verdict = ensure_conformance(
        tmp_path, library, strategy_id, module=module, code_hash=library.code_hash(strategy_id)
    )
    assert verdict is True
    assert load_conformance(tmp_path, strategy_id) is not None
    library.unload_module(strategy_id)


def test_ensure_reuses_a_matching_report(tmp_path: Path) -> None:
    library, strategy_id = library_with_one(tmp_path)
    module = library.load_module(strategy_id)
    code_hash = library.code_hash(strategy_id)
    refresh_conformance(tmp_path, library, strategy_id, module=module, code_hash=code_hash)

    path = tmp_path / "data" / "conformance" / f"{strategy_id}.json"
    stamp = path.stat().st_mtime_ns
    assert (
        ensure_conformance(tmp_path, library, strategy_id, module=module, code_hash=code_hash)
        is True
    )
    assert path.stat().st_mtime_ns == stamp
    library.unload_module(strategy_id)


def test_a_broken_suite_is_recorded_as_a_failure_not_an_absence(tmp_path: Path) -> None:
    library, strategy_id = library_with_one(tmp_path)
    library.test_path(strategy_id).write_text(
        "def test_window_cannot_see_the_future():\n    assert False, 'trap tripped'\n",
        encoding="utf-8",
    )
    module = library.load_module(strategy_id)
    report = refresh_conformance(
        tmp_path, library, strategy_id, module=module, code_hash=library.code_hash(strategy_id)
    )
    assert report.passed is False
    assert (
        conformance_verdict(tmp_path, strategy_id, code_hash=library.code_hash(strategy_id))
        is False
    )
    library.unload_module(strategy_id)


def test_a_deleted_suite_makes_the_gate_inconclusive_not_passing(tmp_path: Path) -> None:
    library, strategy_id = library_with_one(tmp_path)
    library.test_path(strategy_id).unlink()
    module = library.load_module(strategy_id)
    report = refresh_conformance(
        tmp_path, library, strategy_id, module=module, code_hash=library.code_hash(strategy_id)
    )
    assert report.passed is None
    library.unload_module(strategy_id)
