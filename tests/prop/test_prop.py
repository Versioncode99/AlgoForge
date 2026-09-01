from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest
from forge.prop import load_rules, simulate_prop_paths
from forge.prop.engine import MIN_TRADING_DAYS, _block_bootstrap, replay_path

RULES_DIR = Path(__file__).resolve().parents[2] / "rules"

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
    # 30+ days: below that the simulator refuses, because resampling a long
    # evaluation from a few days measures the sample rather than the strategy.
    rng = np.random.default_rng(3)
    pnl = tuple(float(v) for v in rng.normal(60.0, 300.0, 90))
    first = simulate_prop_paths("run_demo", rule, pnl, seed=7, paths=50, allow_unverified=True)
    second = simulate_prop_paths("run_demo", rule, pnl, seed=7, paths=50, allow_unverified=True)
    assert first == second
    assert first.interval_low <= first.pass_rate <= first.interval_high
    assert "UNVERIFIED_RULES" in first.labels


def test_simulator_refuses_a_track_record_that_is_too_short():
    """The 5-day case that produced a 99.5% headline must be impossible."""
    rule = next(r for r in load_rules(RULES_DIR) if r.phase == "CHALLENGE")
    with pytest.raises(ValueError, match="INSUFFICIENT_DAYS"):
        simulate_prop_paths(
            "short", rule, (180.0, 240.0, -60.0, 310.0, 150.0), paths=50, allow_unverified=True
        )
    assert MIN_TRADING_DAYS >= 30


def test_interval_widens_when_fewer_days_were_observed():
    """A short track record must produce a wide interval, not a confident one."""
    rule = next(r for r in load_rules(RULES_DIR) if r.phase == "CHALLENGE")
    rng = np.random.default_rng(11)
    full = rng.normal(90.0, 380.0, 800)

    short = simulate_prop_paths(
        "s", rule, tuple(full[:30]), paths=400, seed=5, allow_unverified=True
    )
    long = simulate_prop_paths(
        "l", rule, tuple(full[:800]), paths=400, seed=5, allow_unverified=True
    )
    assert (short.interval_high - short.interval_low) > (long.interval_high - long.interval_low)


def test_block_bootstrap_preserves_losing_streaks():
    """Independent day sampling erases clustering and understates ruin."""
    rng = np.random.default_rng(1)
    # Alternating regimes: five bad days then five good days, repeated.
    series = np.array([-300.0] * 5 + [300.0] * 5 + [-300.0] * 5 + [300.0] * 5)
    draws = [_block_bootstrap(series, 200, rng, block=5) for _ in range(30)]

    def longest_run(a):
        best = run = 0
        for value in a:
            run = run + 1 if value < 0 else 0
            best = max(best, run)
        return best

    blocked = np.mean([longest_run(d) for d in draws])
    independent = np.mean(
        [longest_run(rng.choice(series, size=200, replace=True)) for _ in range(30)]
    )
    assert blocked > independent
