"""G1 must be a gate, not a rubber stamp.

`preregistered=True` used to be a literal at all nine JudgeInput call sites, so
G1 could never fail. Meanwhile `forge.contracts.models.Preregistration` — a
frozen, content-hashed claim with a `freeze()` classmethod — existed and was
never constructed anywhere in the codebase, including tests.

The engine now freezes the claim before the candidate is backtested and
re-derives it at judge time. These tests pin the property that matters: the
check must fail when the claim changes after the fact, which is the "find a
good result, then rewrite the hypothesis" move §11 is about.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from forge.research import ResearchLedger
from forge.strategy import TEMPLATES, StrategyLibrary
from forge.vault import Workspace
from forge_api.activity import ActivityLog, BacktestStore
from forge_api.engine import AutonomousEngine, _freeze_preregistration
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


def _params() -> dict[str, float]:
    return {p.name: float(p.default) for p in TEMPLATES[TEMPLATE].parameters}


# ── the frozen claim ─────────────────────────────────────────────────────────


def test_freezing_produces_a_real_preregistration() -> None:
    prereg = _freeze_preregistration(TEMPLATES[TEMPLATE], _params())
    assert prereg.preregistration_id
    assert prereg.content_hash
    assert prereg.hypothesis == TEMPLATES[TEMPLATE].hypothesis
    assert TEMPLATE in prereg.mechanism


def test_the_claim_names_the_settings_it_is_about() -> None:
    """'This template has an edge' is not falsifiable by one parameterised run."""
    params = _params()
    prereg = _freeze_preregistration(TEMPLATES[TEMPLATE], params)
    first = sorted(params)[0]
    assert f"{first}={params[first]}" in prereg.falsification


def test_freezing_is_deterministic_for_the_same_claim() -> None:
    """Re-derivation at judge time must hash identically, or nothing can pass."""
    a = _freeze_preregistration(TEMPLATES[TEMPLATE], _params())
    b = _freeze_preregistration(TEMPLATES[TEMPLATE], _params())
    assert a.content_hash == b.content_hash
    assert a.preregistration_id == b.preregistration_id


def test_changing_a_parameter_changes_the_hash() -> None:
    params = _params()
    moved = dict(params)
    axis = sorted(params)[0]
    moved[axis] = params[axis] + 1.0
    assert (
        _freeze_preregistration(TEMPLATES[TEMPLATE], params).content_hash
        != _freeze_preregistration(TEMPLATES[TEMPLATE], moved).content_hash
    )


def test_two_templates_do_not_share_a_claim() -> None:
    keys = [k for k in sorted(TEMPLATES) if TEMPLATES[k].parameters][:2]
    hashes = {
        _freeze_preregistration(
            TEMPLATES[key], {p.name: float(p.default) for p in TEMPLATES[key].parameters}
        ).content_hash
        for key in keys
    }
    assert len(hashes) == 2


# ── the gate ─────────────────────────────────────────────────────────────────


def test_a_reserved_experiment_stores_its_frozen_hash(engine: AutonomousEngine) -> None:
    params = _params()
    prereg = _freeze_preregistration(TEMPLATES[TEMPLATE], params)
    key = engine.experiments.reserve(
        engine._scope(),
        TEMPLATE,
        params,
        preregistration_id=prereg.preregistration_id,
        preregistration_hash=prereg.content_hash,
    )
    row = engine.experiments.get(str(key))
    assert row is not None
    assert row["preregistration_hash"] == prereg.content_hash


def test_the_gate_holds_when_nothing_changed(engine: AutonomousEngine) -> None:
    params = _params()
    prereg = _freeze_preregistration(TEMPLATES[TEMPLATE], params)
    key = str(
        engine.experiments.reserve(
            engine._scope(),
            TEMPLATE,
            params,
            preregistration_id=prereg.preregistration_id,
            preregistration_hash=prereg.content_hash,
        )
    )
    assert engine._preregistration_holds(key, TEMPLATES[TEMPLATE], params) is True


def test_the_gate_fails_when_the_parameters_moved_after_freezing(
    engine: AutonomousEngine,
) -> None:
    """The move the gate exists to catch."""
    params = _params()
    prereg = _freeze_preregistration(TEMPLATES[TEMPLATE], params)
    key = str(
        engine.experiments.reserve(
            engine._scope(),
            TEMPLATE,
            params,
            preregistration_id=prereg.preregistration_id,
            preregistration_hash=prereg.content_hash,
        )
    )
    axis = sorted(params)[0]
    rewritten = {**params, axis: params[axis] + 1.0}
    assert engine._preregistration_holds(key, TEMPLATES[TEMPLATE], rewritten) is False


def test_an_unknown_experiment_fails_closed(engine: AutonomousEngine) -> None:
    assert engine._preregistration_holds("nope", TEMPLATES[TEMPLATE], _params()) is False


def test_an_experiment_with_no_frozen_claim_fails_closed(
    engine: AutonomousEngine,
) -> None:
    """A row from before this existed was not pre-registered, whatever else it is."""
    params = _params()
    key = str(engine.experiments.reserve(engine._scope(), TEMPLATE, params))
    assert engine._preregistration_holds(key, TEMPLATES[TEMPLATE], params) is False


def test_the_judge_fails_g1_on_a_broken_preregistration() -> None:
    """End to end: a false `preregistered` must actually fail the ladder."""
    import numpy as np
    from forge.judge import Judge, JudgeInput

    verdict = Judge().evaluate(
        JudgeInput(
            run_id="run_x",
            tier="TRUTH_OOS",
            pnl=tuple(np.random.default_rng(11).normal(30.0, 100.0, 300)),
            trial_count=1,
            data_gate_passed=True,
            preregistered=False,
            implementation_tests_passed=True,
        )
    )
    g1 = next(gate for gate in verdict.gates if gate.gate == "G1")
    assert g1.status == "FAIL"
    assert verdict.decision == "FAIL"
