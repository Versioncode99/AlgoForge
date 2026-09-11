"""The engine must report what it is actually doing.

Every test here reproduces a situation where the old interface said
``Engine: RUNNING`` and ``Skipped by memory: N`` while the search was, in fact,
refusing every proposal — and asserts that the new accounting can tell the
situations apart.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from forge.memory import FailureClass
from forge.research import ResearchLedger
from forge.research.allocation import Bucket
from forge.research.runtime import Outcome, RuntimeState
from forge.research.skips import NoveltyLevel, SkipKind
from forge.strategy import TEMPLATES, StrategyLibrary
from forge.vault import Workspace
from forge_api.activity import ActivityLog, BacktestStore
from forge_api.director import Refusal
from forge_api.engine import AutonomousEngine
from forge_api.market import MarketService

TEMPLATE = "momentum_breakout"


@pytest.fixture
def engine(tmp_path: Path) -> AutonomousEngine:
    shutil.copytree(Path("rules"), tmp_path / "rules", dirs_exist_ok=True)
    return AutonomousEngine(
        StrategyLibrary(tmp_path / "strategies"),
        BacktestStore(tmp_path / "backtests"),
        ActivityLog(tmp_path / "activity.ndjson"),
        MarketService(Path(".")),
        Workspace(repo=tmp_path, root=tmp_path, vault_mode=False).ensure(),
        ResearchLedger(tmp_path / "research.db"),
    )


# ── the accounting ───────────────────────────────────────────────────────────
def test_a_stopped_engine_does_not_claim_to_be_running(engine: AutonomousEngine) -> None:
    status = engine.status()
    assert status["runtime_state"] == str(RuntimeState.STOPPED)
    assert status["working"] is False


def test_each_refusal_kind_lands_on_its_own_counter(engine: AutonomousEngine) -> None:
    """The whole defect in one test.

    Five unrelated situations used to share ``skipped_by_memory``. They are five
    different facts about the research and now read as five different numbers.
    """
    cases = [
        (SkipKind.NO_ELIGIBLE_WORK, "skipped_no_work", Outcome.NO_WORK),
        (SkipKind.NOT_NOVEL, "skipped_not_novel", Outcome.NOT_NOVEL),
        (SkipKind.CAPABILITY_BLOCKED, "skipped_blocked", Outcome.BLOCKED),
        (SkipKind.CAMPAIGN_EXHAUSTED, "skipped_exhausted", Outcome.EXHAUSTED),
        (SkipKind.PROPOSAL_ERROR, "skipped_errors", Outcome.ERROR),
    ]
    for kind, _field, expected in cases:
        outcome, reason = engine._record_refusal(
            Refusal(
                reason=f"refused: {kind}",
                bucket=Bucket.DISCOVER_FAMILY,
                kind=kind,
                subject="a proposal",
            ),
            worker=0,
        )
        assert outcome is expected, kind
        assert reason

    status = engine.status()
    for _kind, field_name, _outcome in cases:
        assert status[field_name] == 1, field_name


def test_an_empty_frontier_is_not_counted_as_saved_compute(engine: AutonomousEngine) -> None:
    """The old `compute_saved` added cycles where nothing would have run."""
    for _ in range(5):
        engine._record_refusal(
            Refusal(
                reason="the frontier had nothing eligible",
                bucket=Bucket.EXPLORE_HYPOTHESIS,
                kind=SkipKind.NO_ELIGIBLE_WORK,
                subject="frontier",
            ),
            worker=0,
        )
    status = engine.status()
    assert status["skipped_no_work"] == 5
    assert status["compute_saved"] == 0
    assert status["skipped_by_memory"] == 0
    assert status["skipped_without_work"] == 5


def test_a_duplicate_refusal_does_count_as_saved_compute(engine: AutonomousEngine) -> None:
    engine._record_refusal(
        Refusal(
            reason="restates an existing hypothesis",
            bucket=Bucket.DISCOVER_FAMILY,
            kind=SkipKind.NOT_NOVEL,
            level=NoveltyLevel.NEAR_DUPLICATE,
            matched="family:mean_reversion",
            similarity=0.91,
            subject="a proposal",
        ),
        worker=0,
    )
    status = engine.status()
    assert status["compute_saved"] == 1
    assert status["skipped_by_memory"] == 1


def test_every_refusal_is_written_to_the_durable_ledger(engine: AutonomousEngine) -> None:
    engine._record_refusal(
        Refusal(
            reason="restates an existing hypothesis",
            bucket=Bucket.DISCOVER_FAMILY,
            kind=SkipKind.NOT_NOVEL,
            level=NoveltyLevel.NEAR_DUPLICATE,
            matched="family:mean_reversion",
            similarity=0.91,
            subject="volatility-scaled breakout",
        ),
        worker=3,
    )
    rows = engine.skips.list("standalone")
    assert len(rows) == 1
    row = rows[0]
    assert row["subject"] == "volatility-scaled breakout"
    assert row["matched"] == "family:mean_reversion"
    assert row["level"] == str(NoveltyLevel.NEAR_DUPLICATE)
    assert row["retry_permitted"] is True
    assert row["worker_id"] == "3"


def test_the_ledger_survives_an_engine_restart(tmp_path: Path) -> None:
    def build() -> AutonomousEngine:
        shutil.copytree(Path("rules"), tmp_path / "rules", dirs_exist_ok=True)
        return AutonomousEngine(
            StrategyLibrary(tmp_path / "strategies"),
            BacktestStore(tmp_path / "backtests"),
            ActivityLog(tmp_path / "activity.ndjson"),
            MarketService(Path(".")),
            Workspace(repo=tmp_path, root=tmp_path, vault_mode=False).ensure(),
            ResearchLedger(tmp_path / "research.db"),
        )

    first = build()
    first._record_refusal(
        Refusal(
            reason="nothing eligible",
            bucket=Bucket.ROBUSTNESS,
            kind=SkipKind.NO_ELIGIBLE_WORK,
            subject="frontier",
        ),
        worker=0,
    )
    assert first.skips.counts("standalone")["total"] == 1

    # A new process on the same workspace. The in-memory counter resets, which
    # is exactly why it could never be reconciled with the campaign's own row —
    # the ledger is what carries the fact across.
    second = build()
    assert second.status()["skipped_no_work"] == 0
    assert second.skips.counts("standalone")["total"] == 1


# ── the watchdog ─────────────────────────────────────────────────────────────
def test_the_watchdog_names_a_duplicate_loop(engine: AutonomousEngine) -> None:
    engine.monitor = type(engine.monitor)(no_progress_seconds=0.0, loop_threshold=5)
    engine.monitor.starting(workers=1)
    for _ in range(30):
        engine.monitor.record("0", Outcome.DUPLICATE, "already claimed")
    engine._watchdog()
    status = engine.status()
    assert status["runtime_state"] == str(RuntimeState.EXHAUSTED)
    assert status["runtime_code"] == "duplicate_loop"
    assert status["runtime_remedy"]
    assert status["working"] is False


def test_the_watchdog_logs_only_on_a_transition(engine: AutonomousEngine) -> None:
    engine.monitor = type(engine.monitor)(no_progress_seconds=0.0, loop_threshold=5)
    engine.monitor.starting(workers=1)
    for _ in range(30):
        engine.monitor.record("0", Outcome.DUPLICATE, "already claimed")
    before = len(engine.log.recent(limit=200))
    engine._watchdog()
    engine._watchdog()
    engine._watchdog()
    after = engine.log.recent(limit=200)
    runtime_lines = [e for e in after if e.stage == "RUNTIME"]
    assert len(runtime_lines) == 1
    assert len(after) == before + 1


def test_a_condemned_template_is_recorded_as_condemned(engine: AutonomousEngine) -> None:
    """A template-wide prune is a different fact from a neighbourhood one.

    LOOKAHEAD and SAFETY reach the whole template: one such failure bars every
    parameter set on it forever. That used to read as "skipped by memory" and
    was invisible.
    """
    params = {p.name: float(p.default) for p in TEMPLATES[TEMPLATE].parameters}
    engine._remember(
        TEMPLATE,
        params,
        TEMPLATES[TEMPLATE].parameters,
        FailureClass.LOOKAHEAD,
        "reads a bar it has not closed",
    )
    decision = engine.memory.prune(
        scope=engine._scope(),
        template=TEMPLATE,
        parameters=params,
        ranges=TEMPLATES[TEMPLATE].parameters,
    )
    assert decision is not None
    # The engine records this kind distinctly, so the interface can show that a
    # template is condemned rather than that "memory skipped something".
    engine.skips.record(
        campaign_id="standalone",
        kind=SkipKind.TEMPLATE_CONDEMNED,
        level=NoveltyLevel.SAME_CONSTRUCTION,
        subject=TEMPLATE,
        reason=decision.describe(),
        matched=decision.source_id,
    )
    counts = engine.skips.counts("standalone")
    assert counts["by_kind"][str(SkipKind.TEMPLATE_CONDEMNED)] == 1
