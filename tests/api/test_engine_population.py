"""The autonomous engine's population policy.

The engine used to stop the moment the library reached its ceiling, which meant
a full library produced exactly one cycle and a halt — the opposite of a search
you can leave running for days. These tests pin the replacement behaviour.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from forge.research import ResearchLedger
from forge.strategy import StrategyLibrary
from forge.vault import Workspace
from forge_api.activity import ActivityLog, BacktestStore
from forge_api.engine import AutonomousEngine, EngineConfig
from forge_api.market import MarketService


@pytest.fixture
def engine(tmp_path: Path) -> AutonomousEngine:
    shutil.copytree(Path("rules"), tmp_path / "rules", dirs_exist_ok=True)
    return AutonomousEngine(
        StrategyLibrary(tmp_path / "strategies"),
        BacktestStore(tmp_path / "backtests"),
        ActivityLog(tmp_path / "activity.ndjson"),
        MarketService(Path(".")),
        # A flat workspace rooted at tmp_path: store, notes and repo all resolve
        # under it, which is what the pre-vault layout does.
        Workspace(repo=tmp_path, root=tmp_path, vault_mode=False).ensure(),
        ResearchLedger(tmp_path / "research.db"),
    )


def test_the_cap_is_a_population_limit_not_a_stop_condition(engine: AutonomousEngine):
    """A full library must make room, not end the run."""
    engine.state.config = EngineConfig(max_strategies=3)
    for _ in range(3):
        engine.library.create_from_template("momentum_breakout", symbol="NQ")
    assert len(engine.library.list_specs()) == 3

    engine._make_room()

    assert len(engine.library.list_specs()) == 2
    assert engine.state.pruned == 1
    assert engine.state.running is False  # never started; _make_room must not stop anything


def test_room_is_only_made_once_the_cap_is_reached(engine: AutonomousEngine):
    engine.state.config = EngineConfig(max_strategies=5)
    engine.library.create_from_template("momentum_breakout", symbol="NQ")
    engine._make_room()
    assert len(engine.library.list_specs()) == 1
    assert engine.state.pruned == 0


def test_the_never_run_candidate_is_retired_before_a_losing_one(engine: AutonomousEngine):
    """A strategy with no result holds a slot while carrying no evidence."""
    engine.state.config = EngineConfig(max_strategies=2)
    kept = engine.library.create_from_template("momentum_breakout", symbol="NQ")
    untested = engine.library.create_from_template("mean_reversion_band", symbol="NQ")

    class _Store:
        # Population pruning reads a projection, never the trade ledger: it
        # compares one scalar across the whole library.
        def latest_projection(self, strategy_id: str):
            return {"net_pnl": -500.0} if strategy_id == kept.strategy_id else None

    engine.store = _Store()  # type: ignore[assignment]
    engine._make_room()

    remaining = {spec.strategy_id for spec in engine.library.list_specs()}
    assert untested.strategy_id not in remaining
    assert kept.strategy_id in remaining


def test_default_window_can_reach_the_prop_gate():
    """30k bars is ~21 sessions and only 20% of it is validation, so the old
    default could never produce the 30 trading days the prop gate needs."""
    assert EngineConfig().max_bars >= 250_000


def test_the_default_population_leaves_room_to_search():
    assert EngineConfig().max_strategies >= 100
    assert EngineConfig().workers >= 2


def test_counter_increments_go_through_the_lock(engine: AutonomousEngine):
    """`+=` on a shared int is not atomic once several workers run."""
    engine._bump("created", 3)
    engine._bump("created", -1)
    assert engine.state.created == 2


def test_worker_stages_are_tracked_separately(engine: AutonomousEngine):
    engine._stage(0, "backtesting")
    engine._stage(1, "judging")
    status = engine.status()
    assert status["worker_stages"] == {"0": "backtesting", "1": "judging"}


def test_prop_scoring_refuses_a_track_record_that_is_too_short(engine: AutonomousEngine):
    """Fewer than 30 days is reported, not silently scored."""

    class _Trade:
        def __init__(self, day: str, pnl: float) -> None:
            self.exit_time = f"2026-01-{day}T15:00:00+00:00"
            self.net_pnl = pnl

    engine._score_against_prop_firms("s1", [_Trade("01", 10.0), _Trade("02", -5.0)])
    assert engine.state.prop_tested == 0
