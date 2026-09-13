from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest
from forge.prop import load_rules, simulate_prop_journey, simulate_prop_paths
from forge.prop.engine import (
    MIN_TRADING_DAYS,
    OUTCOME_SAMPLE,
    _block_bootstrap,
    replay_path,
)

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
    assert first.risk_of_ruin == first.fail_count / first.path_count
    assert first.target_reach_curve[-1].probability <= 1
    assert sum(item.count for item in first.terminal_histogram) == first.path_count
    assert len(first.return_drawdown_map) == first.path_count
    assert first.tail_risk.terminal_p05 <= first.tail_risk.terminal_median
    assert first.tail_risk.terminal_median <= first.tail_risk.terminal_p95


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


# ── labels have to mean something ────────────────────────────────────────────


def _series(seed: int = 3) -> tuple[float, ...]:
    rng = np.random.default_rng(seed)
    return tuple(float(v) for v in rng.normal(60.0, 300.0, 90))


def test_a_label_that_is_always_present_carries_no_information() -> None:
    """`SAMPLE_DATA` and `UNVERIFIED_RULES` used to be unconditional defaults.

    A simulation over a real strategy's real trade ledger, against a rule set
    marked verified, came back saying both. The cost of a label that is always
    true is not zero: it teaches the reader to skip the row, and the labels that
    do mean something go with it.
    """
    rule = rules()["topstep-50k-challenge-sample-v1"]
    verified = rule.model_copy(update={"verified": True})

    unverified_run = simulate_prop_paths(
        "run_demo", rule, _series(), paths=40, allow_unverified=True
    )
    verified_run = simulate_prop_paths(
        "run_demo", verified, _series(), paths=40, allow_unverified=True
    )
    assert "UNVERIFIED_RULES" in unverified_run.labels
    assert "UNVERIFIED_RULES" not in verified_run.labels


def test_the_caller_states_what_the_series_is() -> None:
    rule = rules()["topstep-50k-challenge-sample-v1"]
    real = simulate_prop_paths(
        "run_demo",
        rule,
        _series(),
        paths=40,
        allow_unverified=True,
        source_labels=("REAL_DATA", "EVIDENCE_TIER:VALIDATION_OOS"),
    )
    assert "REAL_DATA" in real.labels
    assert "EVIDENCE_TIER:VALIDATION_OOS" in real.labels
    assert "SAMPLE_DATA" not in real.labels

    # A caller that says nothing is recorded as having said nothing, rather than
    # being credited with real data by omission.
    silent = simulate_prop_paths("run_demo", rule, _series(), paths=40, allow_unverified=True)
    assert "UNDECLARED_SOURCE" in silent.labels
    assert "REAL_DATA" not in silent.labels


def test_the_method_assumptions_are_stated_on_every_simulation() -> None:
    """These three hold whatever the simulator is fed, so they are never omitted."""
    rule = rules()["topstep-50k-challenge-sample-v1"]
    simulation = simulate_prop_paths(
        "run_demo", rule, _series(), paths=40, allow_unverified=True, source_labels=("REAL_DATA",)
    )
    for label in ("RESEARCH_ONLY", "BLOCK_BOOTSTRAP", "DAILY_SETTLEMENT_APPROXIMATION"):
        assert label in simulation.labels


def test_reason_counts_cover_every_path_not_just_the_sample() -> None:
    """The tally has to be over the population, because `outcomes` is not.

    A thousand accounts all failing on maximum loss were displayed as
    "maximum loss - 100 - 10.0%", because the caller counted reasons from the
    hundred paths kept for inspection and printed the result under the full
    failure count. Summarising in the engine is what makes that impossible.
    """
    rule = rules()["topstep-50k-challenge-sample-v1"]
    # A losing series, so every path fails and the total is unambiguous.
    losing = tuple(-400.0 for _ in range(40))
    simulation = simulate_prop_paths(
        "run_demo", rule, losing, paths=500, allow_unverified=True, source_labels=("REAL_DATA",)
    )
    assert simulation.fail_count == 500
    assert sum(simulation.failure_reasons.values()) == simulation.fail_count
    assert len(simulation.outcomes) < simulation.fail_count, "the sample is meant to be smaller"
    assert simulation.outcome_sample_size == len(simulation.outcomes)


def test_the_sample_is_labelled_as_one() -> None:
    rule = rules()["topstep-50k-challenge-sample-v1"]
    simulation = simulate_prop_paths(
        "run_demo", rule, _series(), paths=250, allow_unverified=True, source_labels=("REAL_DATA",)
    )
    assert simulation.outcome_sample_size == len(simulation.outcomes) <= simulation.path_count
    assert len(simulation.equity_paths) == simulation.outcome_sample_size
    # Every statistic is over the population, so none of them is bounded by it.
    assert simulation.pass_count + simulation.fail_count + simulation.timeout_count == 250


def _fan_fixture(paths: int = 120):
    """A challenge simulation with a spread wide enough that accounts close early."""
    rule = rules()["topstep-50k-challenge-sample-v1"]
    rng = np.random.default_rng(11)
    pnl = tuple(float(v) for v in rng.normal(40.0, 700.0, 90))
    return rule, simulate_prop_paths(
        "run_fan", rule, pnl, seed=19, paths=paths, allow_unverified=True
    )


def test_equity_fan_counts_every_account_on_every_day() -> None:
    _, result = _fan_fixture()
    assert result.equity_fan, "a simulation over 90 observed days must produce a fan"
    assert result.equity_fan[0].day == 0
    for point in result.equity_fan:
        assert point.live + point.resolved == result.path_count
        assert point.p05 <= point.p25 <= point.median <= point.p75 <= point.p95


def test_equity_fan_day_zero_is_the_starting_balance() -> None:
    rule, result = _fan_fixture()
    first = result.equity_fan[0]
    assert first.live == result.path_count
    assert first.resolved == 0
    assert first.p05 == first.p95 == rule.starting_balance


def test_accounts_close_and_the_fan_says_so() -> None:
    _, result = _fan_fixture()
    live = [point.live for point in result.equity_fan]
    assert live == sorted(live, reverse=True), "an account cannot re-open once resolved"
    assert live[-1] < live[0], "this fixture must actually close some accounts"


def test_the_fan_does_not_drop_the_accounts_that_failed() -> None:
    """The survivorship check, which is the whole reason the fan is computed here.

    `paths` is `OUTCOME_SAMPLE` exactly, so `equity_paths` is the entire
    population rather than a sample of it and the shipped fan can be checked
    against both readings of the last day. It must equal the one that keeps
    closed accounts at their final balance, and must *not* equal the one
    computed over survivors -- if those two agreed the carry-forward would be
    doing nothing and the guarantee would be untested.
    """
    rule = rules()["topstep-50k-challenge-sample-v1"]
    rng = np.random.default_rng(5)
    pnl = tuple(float(v) for v in rng.normal(-30.0, 900.0, 90))
    result = simulate_prop_paths(
        "run_survivor", rule, pnl, seed=23, paths=OUTCOME_SAMPLE, allow_unverified=True
    )
    assert len(result.equity_paths) == result.path_count
    last = result.equity_fan[-1]
    assert last.resolved > 0, "the fixture must close accounts before the last day"

    day = last.day
    survivors = [path[day] for path in result.equity_paths if day < len(path)]
    everyone = [path[day] if day < len(path) else path[-1] for path in result.equity_paths]
    assert survivors, "some accounts must still be live, or the comparison is empty"
    assert last.median == round(float(np.percentile(everyone, 50)), 2)
    assert last.median != round(float(np.percentile(survivors, 50)), 2)
    assert last.live == len(survivors)


def test_a_challenge_with_no_payout_terms_reports_no_payouts() -> None:
    _, result = _fan_fixture()
    assert result.payout.any_probability == 0.0
    assert result.payout.mean == result.payout.median == result.payout.best == 0.0
    assert result.payout.mean == result.mean_payout


def test_payout_distribution_sits_beside_its_mean() -> None:
    """A funded rule pays out, and the mean alone cannot say how unevenly."""
    funded = next(
        rule for rule in load_rules(RULES_DIR) if rule.phase == "FUNDED" and rule.payout_amount
    )
    rng = np.random.default_rng(2)
    pnl = tuple(float(v) for v in rng.normal(120.0, 400.0, 90))
    result = simulate_prop_paths(
        "run_payout", funded, pnl, seed=31, paths=120, allow_unverified=True
    )
    assert result.payout.mean == result.mean_payout
    assert 0.0 < result.payout.any_probability <= 1.0
    assert result.payout.p05 <= result.payout.median <= result.payout.p95
    assert result.payout.best >= result.payout.p95


def _journey_rules(provider: str = "Topstep"):
    loaded = load_rules(RULES_DIR)
    challenge = next(r for r in loaded if r.provider == provider and r.phase == "CHALLENGE")
    funded = next(r for r in loaded if r.provider == provider and r.phase == "FUNDED")
    return challenge, funded


def _journey_pnl(mean: float = 90.0, sd: float = 450.0, seed: int = 13):
    rng = np.random.default_rng(seed)
    return tuple(float(v) for v in rng.normal(mean, sd, 90))


def test_the_journey_refuses_to_join_two_providers() -> None:
    challenge, _ = _journey_rules("Topstep")
    _, funded = _journey_rules("Lucid Trading")
    with pytest.raises(ValueError, match="PROVIDER_MISMATCH"):
        simulate_prop_journey(
            "run_x", challenge, funded, _journey_pnl(), paths=40, allow_unverified=True
        )


def test_the_journey_refuses_the_phases_the_wrong_way_round() -> None:
    challenge, funded = _journey_rules()
    with pytest.raises(ValueError, match="NOT_A_CHALLENGE"):
        simulate_prop_journey(
            "run_x", funded, funded, _journey_pnl(), paths=40, allow_unverified=True
        )
    with pytest.raises(ValueError, match="NOT_A_FUNDED_RULE"):
        simulate_prop_journey(
            "run_x", challenge, challenge, _journey_pnl(), paths=40, allow_unverified=True
        )


def test_only_the_accounts_that_passed_reach_the_funded_leg() -> None:
    challenge, funded = _journey_rules()
    journey = simulate_prop_journey(
        "run_j", challenge, funded, _journey_pnl(), seed=3, paths=200, allow_unverified=True
    )
    assert journey.challenge.reached == journey.path_count
    assert journey.funded.reached == journey.challenge.cleared
    assert journey.funded.reached <= journey.path_count
    assert journey.funded.cleared <= journey.funded.reached
    # Every leg's outcomes must account for everyone who reached it.
    assert journey.challenge.cleared + journey.challenge.failed + journey.challenge.timed_out == (
        journey.path_count
    )


def test_the_journey_is_deterministic_for_a_seed() -> None:
    challenge, funded = _journey_rules()
    args = dict(seed=17, paths=120, allow_unverified=True)
    first = simulate_prop_journey("run_j", challenge, funded, _journey_pnl(), **args)
    second = simulate_prop_journey("run_j", challenge, funded, _journey_pnl(), **args)
    assert first == second


def test_the_payout_rate_is_not_the_product_of_the_two_pass_rates() -> None:
    """The reason the journey is simulated rather than multiplied.

    Multiplying the legs assumes the accounts reaching the funded stage are a
    fair sample of all accounts. They are not: they are the ones that passed.
    If the simulated figure ever equalled the product exactly, the conditioning
    would not be happening and this feature would be an expensive way to do
    arithmetic.
    """
    challenge, funded = _journey_rules()
    pnl = _journey_pnl()
    journey = simulate_prop_journey(
        "run_j", challenge, funded, pnl, seed=29, paths=400, allow_unverified=True
    )
    assert journey.challenge.cleared > 0, "the fixture must pass some challenges"
    assert journey.funded.cleared > 0, "the fixture must reach some payouts"

    standalone = simulate_prop_paths(
        "run_s", funded, pnl, seed=29, paths=400, allow_unverified=True
    )
    naive = (journey.challenge.cleared / journey.path_count) * standalone.payout.any_probability
    assert journey.payout_probability != pytest.approx(naive, abs=1e-9)


def test_days_to_payout_spans_both_legs() -> None:
    challenge, funded = _journey_rules()
    journey = simulate_prop_journey(
        "run_j", challenge, funded, _journey_pnl(), seed=41, paths=300, allow_unverified=True
    )
    assert journey.days_to_payout_median is not None
    assert journey.challenge.days_median is not None
    # A payout cannot arrive before the challenge it is downstream of was passed.
    assert journey.days_to_payout_median > journey.challenge.days_median
    assert journey.days_to_payout_p10 <= journey.days_to_payout_median
    assert journey.days_to_payout_median <= journey.days_to_payout_p90


def test_the_journey_states_the_assumption_it_adds() -> None:
    challenge, funded = _journey_rules()
    journey = simulate_prop_journey(
        "run_j", challenge, funded, _journey_pnl(), paths=60, allow_unverified=True
    )
    assert "TWO_STAGE_RESAMPLE" in journey.labels
    assert "FUNDED_LEG_RESAMPLES_THE_SAME_DAYS" in journey.labels
    assert "UNVERIFIED_RULES" in journey.labels


def test_the_payout_interval_widens_with_fewer_observed_days() -> None:
    """Same guarantee as the single leg: more paths cannot buy more certainty.

    Both runs draw from one generator, so the only difference between them is
    how much of it was observed -- 30 days against 800. Comparing two unrelated
    series would compare their P&L shapes rather than their sample sizes.
    """
    challenge, funded = _journey_rules()
    rng = np.random.default_rng(11)
    full = rng.normal(90.0, 380.0, 800)

    short = simulate_prop_journey(
        "run_short", challenge, funded, tuple(full[:30]), seed=7, paths=60, allow_unverified=True
    )
    long = simulate_prop_journey(
        "run_long", challenge, funded, tuple(full), seed=7, paths=60, allow_unverified=True
    )
    assert (short.payout_interval_high - short.payout_interval_low) > (
        long.payout_interval_high - long.payout_interval_low
    )
