"""Research memory: what it prunes, and — more importantly — what it refuses to."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from forge.memory import (
    TEMPLATE_WIDE,
    FailureClass,
    ResearchMemory,
    classify_gate,
    classify_reason,
    normalise,
    reach_of,
)


@dataclass(frozen=True)
class Spec:
    """Stand-in for ParameterSpec: only name/low/high are needed here."""

    name: str
    low: float
    high: float


RANGES = (Spec("lookback", 10.0, 110.0), Spec("threshold", 0.0, 1.0))


@pytest.fixture
def memory(tmp_path: Path) -> ResearchMemory:
    return ResearchMemory(tmp_path / "research_memory.db")


def _record(
    memory: ResearchMemory,
    failure: FailureClass,
    params: dict[str, float],
    reason: str = "because",
) -> None:
    memory.record(
        scope="mnq-1m",
        template="momentum_breakout",
        failure_class=failure,
        reason=reason,
        parameters=params,
        ranges=RANGES,
    )


def _prune(memory: ResearchMemory, params: dict[str, float]):  # type: ignore[no-untyped-def]
    return memory.prune(
        scope="mnq-1m", template="momentum_breakout", parameters=params, ranges=RANGES
    )


# ── normalisation ────────────────────────────────────────────────────────────


def test_normalise_maps_declared_range_onto_unit_interval() -> None:
    scaled = normalise({"lookback": 60.0, "threshold": 0.25}, RANGES)
    assert scaled == {"lookback": 0.5, "threshold": 0.25}


def test_normalise_clamps_out_of_range_values() -> None:
    scaled = normalise({"lookback": 500.0, "threshold": -3.0}, RANGES)
    assert scaled == {"lookback": 1.0, "threshold": 0.0}


def test_normalise_ignores_undeclared_parameters() -> None:
    scaled = normalise({"lookback": 60.0, "mystery": 4.0}, RANGES)
    assert "mystery" not in scaled


def test_normalise_survives_a_zero_width_range() -> None:
    scaled = normalise({"fixed": 5.0}, (Spec("fixed", 5.0, 5.0),))
    assert scaled == {"fixed": 0.0}


# ── pruning ──────────────────────────────────────────────────────────────────


def test_a_near_neighbour_of_a_no_trades_failure_is_pruned(memory: ResearchMemory) -> None:
    _record(memory, FailureClass.NO_TRADES, {"lookback": 60.0, "threshold": 0.90})
    decision = _prune(memory, {"lookback": 62.0, "threshold": 0.92})
    assert decision is not None
    assert decision.failure_class is FailureClass.NO_TRADES
    assert decision.distance == pytest.approx(0.02)


def test_a_distant_candidate_is_not_pruned(memory: ResearchMemory) -> None:
    _record(memory, FailureClass.NO_TRADES, {"lookback": 60.0, "threshold": 0.90})
    assert _prune(memory, {"lookback": 60.0, "threshold": 0.20}) is None


def test_reach_differs_by_class(memory: ResearchMemory) -> None:
    """The same distance is inside NO_TRADES' reach and outside ROBUSTNESS'."""
    gap = {"lookback": 60.0, "threshold": 0.50}
    near = {"lookback": 66.0, "threshold": 0.50}  # 0.06 on the lookback axis

    _record(memory, FailureClass.NO_TRADES, gap)
    assert _prune(memory, near) is not None

    other = ResearchMemory(memory.path.parent / "other.db")
    other.record(
        scope="mnq-1m",
        template="momentum_breakout",
        failure_class=FailureClass.ROBUSTNESS,
        reason="fragile",
        parameters=gap,
        ranges=RANGES,
    )
    assert (
        other.prune(
            scope="mnq-1m",
            template="momentum_breakout",
            parameters=near,
            ranges=RANGES,
        )
        is None
    )


def test_chebyshev_means_one_far_axis_defeats_the_match(memory: ResearchMemory) -> None:
    _record(memory, FailureClass.NO_TRADES, {"lookback": 60.0, "threshold": 0.90})
    # Identical on threshold, far on lookback: not a neighbour.
    assert _prune(memory, {"lookback": 110.0, "threshold": 0.90}) is None


def test_lookahead_prunes_the_whole_template(memory: ResearchMemory) -> None:
    _record(memory, FailureClass.LOOKAHEAD, {"lookback": 60.0, "threshold": 0.90}, "peeks")
    decision = _prune(memory, {"lookback": 11.0, "threshold": 0.01})
    assert decision is not None
    assert decision.reach == TEMPLATE_WIDE
    assert "whole template" in decision.describe()


def test_bookkeeping_and_infrastructure_never_prune(memory: ResearchMemory) -> None:
    """A consumed holdout or a disk error says nothing about the parameters."""
    for failure in (
        FailureClass.BOOKKEEPING,
        FailureClass.INFRASTRUCTURE,
        FailureClass.DATA,
    ):
        store = ResearchMemory(memory.path.parent / f"{failure}.db")
        store.record(
            scope="mnq-1m",
            template="momentum_breakout",
            failure_class=failure,
            reason="not about the numbers",
            parameters={"lookback": 60.0, "threshold": 0.5},
            ranges=RANGES,
        )
        assert (
            store.prune(
                scope="mnq-1m",
                template="momentum_breakout",
                parameters={"lookback": 60.0, "threshold": 0.5},
                ranges=RANGES,
            )
            is None
        ), f"{failure} must never prune"
        assert reach_of(failure) is None


def test_scope_and_template_isolate_evidence(memory: ResearchMemory) -> None:
    _record(memory, FailureClass.NO_TRADES, {"lookback": 60.0, "threshold": 0.90})
    params = {"lookback": 60.0, "threshold": 0.90}
    assert (
        memory.prune(
            scope="other-scope",
            template="momentum_breakout",
            parameters=params,
            ranges=RANGES,
        )
        is None
    )
    assert (
        memory.prune(scope="mnq-1m", template="other_template", parameters=params, ranges=RANGES)
        is None
    )


def test_no_shared_axis_does_not_prune(memory: ResearchMemory) -> None:
    """Incomparable points must not collapse to distance zero."""
    _record(memory, FailureClass.NO_TRADES, {"lookback": 60.0})
    decision = memory.prune(
        scope="mnq-1m",
        template="momentum_breakout",
        parameters={"threshold": 0.5},
        ranges=(Spec("threshold", 0.0, 1.0),),
    )
    assert decision is None


def test_nearest_failure_wins_when_several_apply(memory: ResearchMemory) -> None:
    _record(memory, FailureClass.NO_TRADES, {"lookback": 60.0, "threshold": 0.50}, "far")
    _record(memory, FailureClass.NO_TRADES, {"lookback": 61.0, "threshold": 0.50}, "near")
    decision = _prune(memory, {"lookback": 61.5, "threshold": 0.50})
    assert decision is not None
    assert decision.reason == "near"


# ── durability ───────────────────────────────────────────────────────────────


def test_memory_survives_a_restart(tmp_path: Path) -> None:
    """The whole point: a new process must inherit what the last one learned."""
    path = tmp_path / "research_memory.db"
    first = ResearchMemory(path)
    first.record(
        scope="mnq-1m",
        template="momentum_breakout",
        failure_class=FailureClass.NO_TRADES,
        reason="threshold too high",
        parameters={"lookback": 60.0, "threshold": 0.90},
        ranges=RANGES,
    )

    reopened = ResearchMemory(path)
    decision = reopened.prune(
        scope="mnq-1m",
        template="momentum_breakout",
        parameters={"lookback": 61.0, "threshold": 0.91},
        ranges=RANGES,
    )
    assert decision is not None
    assert decision.reason == "threshold too high"


def test_recording_the_same_evidence_twice_is_idempotent(memory: ResearchMemory) -> None:
    for _ in range(3):
        _record(memory, FailureClass.NO_TRADES, {"lookback": 60.0, "threshold": 0.9})
    assert memory.total("mnq-1m") == 1


def test_counts_report_by_class(memory: ResearchMemory) -> None:
    _record(memory, FailureClass.NO_TRADES, {"lookback": 60.0, "threshold": 0.9})
    _record(memory, FailureClass.NO_TRADES, {"lookback": 70.0, "threshold": 0.9})
    _record(memory, FailureClass.RISK, {"lookback": 20.0, "threshold": 0.1})
    assert memory.counts("mnq-1m") == {"NO_TRADES": 2, "RISK": 1}


def test_recent_returns_newest_first(memory: ResearchMemory) -> None:
    _record(memory, FailureClass.NO_TRADES, {"lookback": 60.0, "threshold": 0.9}, "older")
    _record(memory, FailureClass.RISK, {"lookback": 20.0, "threshold": 0.1}, "newer")
    assert [r.reason for r in memory.recent("mnq-1m")] == ["newer", "older"]


# ── classification ───────────────────────────────────────────────────────────


def test_inconclusive_gates_are_never_learned_from() -> None:
    """Absence of evidence must not become evidence of absence."""
    for gate in ("G5", "G11", "G12", "G13"):
        assert classify_gate(gate, "INCONCLUSIVE") is None
    assert classify_gate("G4", "PASS") is None


def test_failed_gates_map_to_their_class() -> None:
    assert classify_gate("G3", "FAIL") is FailureClass.INSUFFICIENT_SAMPLE
    assert classify_gate("G4", "FAIL") is FailureClass.NEGATIVE_EXPECTANCY
    assert classify_gate("G8", "FAIL") is FailureClass.RISK
    assert classify_gate("G12", "FAIL") is FailureClass.WALK_FORWARD


def test_g2_splits_on_lookahead() -> None:
    assert classify_gate("G2", "FAIL", lookahead=True) is FailureClass.LOOKAHEAD
    assert classify_gate("G2", "FAIL", lookahead=False) is FailureClass.SAFETY


def test_unknown_gate_is_not_forced_into_a_class() -> None:
    assert classify_gate("G99", "FAIL") is None


def test_reason_fallback_classifies_engine_level_stops() -> None:
    assert classify_reason("produced no trades") is FailureClass.NO_TRADES
    assert classify_reason("burn-once holdout produced no trades") is FailureClass.NO_TRADES
    assert classify_reason("holdout already consumed for lineage") is FailureClass.BOOKKEEPING
    assert classify_reason("disk exploded") is FailureClass.INFRASTRUCTURE


# ── tamper evidence ──────────────────────────────────────────────────────────


def test_an_untouched_chain_verifies(memory: ResearchMemory) -> None:
    for value in (20.0, 40.0, 60.0):
        _record(memory, FailureClass.NO_TRADES, {"lookback": value, "threshold": 0.5})
    report = memory.verify_chain()
    assert report["intact"] is True
    assert report["entries"] == 3
    assert report["broken_at"] is None


def test_an_empty_chain_verifies(memory: ResearchMemory) -> None:
    assert memory.verify_chain() == {"intact": True, "entries": 0, "broken_at": None}


def test_editing_a_recorded_reason_is_detected(memory: ResearchMemory) -> None:
    """Research memory decides what is never searched again. Edits must show."""
    import sqlite3

    for value in (20.0, 40.0, 60.0):
        _record(memory, FailureClass.NO_TRADES, {"lookback": value, "threshold": 0.5})
    with sqlite3.connect(memory.path) as db:
        db.execute("UPDATE failures SET reason='rewritten' WHERE rowid=2")

    report = memory.verify_chain()
    assert report["intact"] is False
    assert report["broken_at"] == 1
    assert "does not match its recorded hash" in report["problem"]


def test_deleting_an_entry_from_the_middle_is_detected(memory: ResearchMemory) -> None:
    import sqlite3

    for value in (20.0, 40.0, 60.0):
        _record(memory, FailureClass.NO_TRADES, {"lookback": value, "threshold": 0.5})
    with sqlite3.connect(memory.path) as db:
        db.execute("DELETE FROM failures WHERE rowid=2")

    report = memory.verify_chain()
    assert report["intact"] is False
    assert "previous_hash does not match" in report["problem"]


def test_changing_a_failure_class_is_detected(memory: ResearchMemory) -> None:
    """The field that decides how far a failure prunes."""
    import sqlite3

    _record(memory, FailureClass.BOOKKEEPING, {"lookback": 20.0, "threshold": 0.5})
    with sqlite3.connect(memory.path) as db:
        db.execute("UPDATE failures SET failure_class='NO_TRADES' WHERE rowid=1")

    assert memory.verify_chain()["intact"] is False


def test_recording_the_same_finding_twice_leaves_the_chain_intact(
    memory: ResearchMemory,
) -> None:
    """Append-only: a repeat is a no-op, never a rewritten link."""
    for _ in range(3):
        _record(memory, FailureClass.NO_TRADES, {"lookback": 20.0, "threshold": 0.5})
    _record(memory, FailureClass.RISK, {"lookback": 90.0, "threshold": 0.1})
    report = memory.verify_chain()
    assert report["intact"] is True
    assert report["entries"] == 2


def test_a_database_written_before_the_chain_existed_still_opens(tmp_path: Path) -> None:
    """Unchained rows are not a broken chain; the workspace holds real research."""
    import sqlite3

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE failures ("
            "id TEXT PRIMARY KEY, scope TEXT NOT NULL, template TEXT NOT NULL, "
            "failure_class TEXT NOT NULL, reason TEXT NOT NULL, "
            "parameters TEXT NOT NULL, normalised TEXT NOT NULL, "
            "gate TEXT, strategy_id TEXT, created_at TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO failures VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("old_1", "mnq-1m", "momentum_breakout", "NO_TRADES", "legacy",
             '{"lookback": 60.0}', '{"lookback": 0.5}', None, None, "2026-09-01T00:00:00+00:00"),
        )

    memory = ResearchMemory(path)
    assert memory.total() == 1
    assert memory.verify_chain()["intact"] is True

    # And new rows chain from there without complaint.
    memory.record(
        scope="mnq-1m",
        template="momentum_breakout",
        failure_class=FailureClass.RISK,
        reason="new",
        parameters={"lookback": 20.0},
        ranges=RANGES,
    )
    assert memory.verify_chain()["intact"] is True
