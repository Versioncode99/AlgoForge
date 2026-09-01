from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest
from forge.prop import load_rules, simulate_prop_paths
from forge.prop.engine import replay_path

ROOT = Path(__file__).resolve().parents[2]


def rules():
    return {rule.rule_id: rule for rule in load_rules(ROOT / "rules")}


def test_unverified_rule_is_visible_but_locked() -> None:
    rule = rules()["topstep-50k-challenge-sample-v1"]
    assert not rule.runnable(date.today())
    with pytest.raises(ValueError, match="RULE_LOCKED"):
        simulate_prop_paths("run_demo", rule, (100.0, -50.0))


def test_maximum_loss_boundary_fails_at_equality() -> None:
    rule = rules()["lucid-50k-challenge-sample-v1"].model_copy(update={"daily_loss_limit": None})
    outcome, _ = replay_path(rule, np.array([-2000.0]))
    assert outcome.outcome == "FAIL"
    assert outcome.failure_reason == "MAXIMUM_LOSS"


def test_consistency_extends_target_instead_of_hard_failure() -> None:
    rule = rules()["topstep-50k-challenge-sample-v1"]
    outcome, _ = replay_path(rule, np.array([2500.0, 500.0]))
    assert outcome.outcome != "FAIL"
    assert outcome.outcome == "TIMEOUT"


def test_prop_simulation_is_deterministic_and_separate() -> None:
    rule = rules()["topstep-50k-challenge-sample-v1"]
    pnl = (450.0, -250.0, 600.0, -100.0, 300.0)
    first = simulate_prop_paths("run_demo", rule, pnl, seed=7, paths=50, allow_unverified=True)
    second = simulate_prop_paths("run_demo", rule, pnl, seed=7, paths=50, allow_unverified=True)
    assert first == second
    assert first.interval_low <= first.pass_rate <= first.interval_high
    assert "UNVERIFIED_RULES" in first.labels
