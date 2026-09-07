"""The engine's memory gates, exercised through the engine rather than the store.

`tests/memory/test_research_memory.py` pins what ResearchMemory decides. These
tests pin that the *engine* actually asks it, persists to it, and survives a
restart with what it learned — the part a unit test of the store cannot show.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from forge.memory import FailureClass
from forge.research import ResearchLedger
from forge.strategy import TEMPLATES, StrategyLibrary
from forge.vault import Workspace
from forge_api.activity import ActivityLog, BacktestStore
from forge_api.engine import AutonomousEngine
from forge_api.market import MarketService

TEMPLATE = "momentum_breakout"


def _engine(root: Path) -> AutonomousEngine:
    shutil.copytree(Path("rules"), root / "rules", dirs_exist_ok=True)
    return AutonomousEngine(
        StrategyLibrary(root / "strategies"),
        BacktestStore(root / "backtests"),
        ActivityLog(root / "activity.ndjson"),
        MarketService(Path(".")),
        Workspace(repo=root, root=root, vault_mode=False).ensure(),
        ResearchLedger(root / "research.db"),
    )


@pytest.fixture
def engine(tmp_path: Path) -> AutonomousEngine:
    return _engine(tmp_path)


def _defaults() -> dict[str, float]:
    return {p.name: float(p.default) for p in TEMPLATES[TEMPLATE].parameters}


def _first_axis() -> str:
    return TEMPLATES[TEMPLATE].parameters[0].name


def test_the_engine_owns_a_durable_memory(engine: AutonomousEngine) -> None:
    assert engine.memory.path.exists()
    assert engine.memory.total() == 0


def test_a_recorded_failure_prunes_its_neighbourhood(engine: AutonomousEngine) -> None:
    params = _defaults()
    engine._remember(
        TEMPLATE,
        params,
        TEMPLATES[TEMPLATE].parameters,
        FailureClass.NO_TRADES,
        "threshold too high",
    )

    decision = engine.memory.prune(
        scope=engine._scope(),
        template=TEMPLATE,
        parameters=params,
        ranges=TEMPLATES[TEMPLATE].parameters,
    )
    assert decision is not None
    assert decision.failure_class is FailureClass.NO_TRADES


def test_a_bookkeeping_failure_never_prunes(engine: AutonomousEngine) -> None:
    """A consumed holdout must not delete the parameter region it happened in."""
    params = _defaults()
    engine._remember(
        TEMPLATE,
        params,
        TEMPLATES[TEMPLATE].parameters,
        FailureClass.BOOKKEEPING,
        "holdout already consumed for lineage",
    )

    assert (
        engine.memory.prune(
            scope=engine._scope(),
            template=TEMPLATE,
            parameters=params,
            ranges=TEMPLATES[TEMPLATE].parameters,
        )
        is None
    )


def test_constraints_are_served_from_disk_and_carry_their_class(
    engine: AutonomousEngine,
) -> None:
    engine._remember(
        TEMPLATE,
        _defaults(),
        TEMPLATES[TEMPLATE].parameters,
        FailureClass.RISK,
        "failed G8 (Risk)",
        gate="G8",
    )

    rows = engine.constraints()
    assert len(rows) == 1
    assert rows[0]["template"] == TEMPLATE
    assert rows[0]["failure_class"] == "RISK"
    assert rows[0]["gate"] == "G8"
    assert rows[0]["reason"] == "failed G8 (Risk)"


def test_what_the_engine_learned_survives_a_restart(tmp_path: Path) -> None:
    """The defect this whole change exists to fix.

    A second engine over the same workspace must inherit the first one's
    findings, rather than re-spending compute on a disproven region.
    """
    first = _engine(tmp_path)
    params = _defaults()
    first._remember(
        TEMPLATE,
        params,
        TEMPLATES[TEMPLATE].parameters,
        FailureClass.NO_TRADES,
        "threshold too high",
    )
    assert len(first.constraints()) == 1

    reborn = _engine(tmp_path)
    assert len(reborn.constraints()) == 1
    assert (
        reborn.memory.prune(
            scope=reborn._scope(),
            template=TEMPLATE,
            parameters=params,
            ranges=TEMPLATES[TEMPLATE].parameters,
        )
        is not None
    )


def test_a_distant_region_is_still_explorable(engine: AutonomousEngine) -> None:
    """Pruning must not quietly close the whole search space."""
    axis = _first_axis()
    spec = next(p for p in TEMPLATES[TEMPLATE].parameters if p.name == axis)
    low = dict(_defaults())
    low[axis] = float(spec.low)
    high = dict(_defaults())
    high[axis] = float(spec.high)

    engine._remember(
        TEMPLATE, low, TEMPLATES[TEMPLATE].parameters, FailureClass.NO_TRADES, "nothing fired"
    )

    assert (
        engine.memory.prune(
            scope=engine._scope(),
            template=TEMPLATE,
            parameters=high,
            ranges=TEMPLATES[TEMPLATE].parameters,
        )
        is None
    ), "the opposite end of the declared range must remain searchable"


def test_scope_separates_datasets(engine: AutonomousEngine) -> None:
    """A failure on one dataset must not prune the same idea on another."""
    params = _defaults()
    engine._remember(
        TEMPLATE, params, TEMPLATES[TEMPLATE].parameters, FailureClass.NO_TRADES, "flat"
    )

    assert (
        engine.memory.prune(
            scope="a-different-dataset",
            template=TEMPLATE,
            parameters=params,
            ranges=TEMPLATES[TEMPLATE].parameters,
        )
        is None
    )


# ── the spread the Deflated Sharpe deflates against ──────────────────────────


def test_the_search_spread_is_withheld_until_there_is_enough_of_it(
    engine: AutonomousEngine,
) -> None:
    """Early in a run the search has not produced enough trials to estimate a
    spread. Saying so makes G5 INCONCLUSIVE, which is the honest outcome."""
    from forge.judge import MINIMUM_TRIAL_CONFIGURATIONS

    scope = engine._scope()
    for index in range(MINIMUM_TRIAL_CONFIGURATIONS - 1):
        key = engine.experiments.reserve(scope, TEMPLATE, {"lookback": float(index)})
        engine.experiments.finish(str(key), status="backtested", development_sharpe=index * 0.1)

    assert engine._search_sharpes() is None


def test_the_search_spread_is_used_once_there_is_enough(
    engine: AutonomousEngine,
) -> None:
    from forge.judge import MINIMUM_TRIAL_CONFIGURATIONS

    scope = engine._scope()
    for index in range(MINIMUM_TRIAL_CONFIGURATIONS):
        key = engine.experiments.reserve(scope, TEMPLATE, {"lookback": float(index)})
        engine.experiments.finish(str(key), status="backtested", development_sharpe=index * 0.1)

    spread = engine._search_sharpes()
    assert spread is not None
    assert len(spread) == MINIMUM_TRIAL_CONFIGURATIONS
