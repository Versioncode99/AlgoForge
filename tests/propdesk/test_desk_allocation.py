"""Allocation: deterministic feasibility, and advice that can only narrow.

The adversarial tests are the point. An advisory layer that could introduce a
pairing or raise a size would be a path around the judge, the firm policy and
the account's own rule engine all at once, so those are tested against
deliberately hostile input rather than against a well-behaved recommender.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from forge.propdesk import (
    AccountSnapshot,
    Advice,
    Allocation,
    AllocationConstraints,
    Allocator,
    FeasibilityCheck,
    HealthGrade,
    Permission,
    PropProgramPolicy,
    StrategyHealth,
    UseCase,
    diff_allocations,
    evaluate_compatibility,
    grade_health,
    use_case_for,
)

NOW = datetime(2026, 9, 11, 15, 30, tzinfo=UTC)


def health(strategy_id: str = "s1", **overrides) -> StrategyHealth:
    base: dict = {
        "strategy_id": strategy_id,
        "verdict": "PASS",
        "verdict_id": f"verdict-{strategy_id}",
        "oos_sharpe": 1.2,
        "oos_trades": 120,
        "expected_drawdown_p95": 400.0,
        "as_of": NOW,
        "as_of_basis": "out-of-sample",
    }
    return StrategyHealth(**{**base, **overrides})


def account(account_uid: str = "a1", **overrides) -> AccountSnapshot:
    base: dict = {
        "account_uid": account_uid,
        "display_name": account_uid,
        "can_trade": True,
        "buffer": 8_000.0,
        "rules_id": "rules-1",
        "rules_level": "ok",
    }
    return AccountSnapshot(**{**base, **overrides})


def compatible(account_uid: str = "a1", strategy_id: str = "s1"):
    policy = PropProgramPolicy(
        automation=Permission.ALLOWED, algorithmic_allocation=Permission.ALLOWED
    )
    report = evaluate_compatibility(
        account_uid=account_uid,
        use_case=UseCase.ALGORITHMIC_ALLOCATION,
        policy=policy,
        at=NOW,
    )
    return {(account_uid, strategy_id): report}


@pytest.fixture
def allocator() -> Allocator:
    return Allocator(now=lambda: NOW)


# ── health grading ───────────────────────────────────────────────────────────


def test_an_unjudged_strategy_is_unproven_not_neutral() -> None:
    assert grade_health(StrategyHealth(strategy_id="s")) is HealthGrade.UNPROVEN


def test_a_failed_verdict_grades_failing() -> None:
    assert grade_health(health(verdict="FAIL")) is HealthGrade.FAILING


def test_too_few_out_of_sample_trades_is_unproven() -> None:
    """A Sharpe over a handful of outcomes is an artefact, not evidence."""
    assert grade_health(health(oos_trades=5)) is HealthGrade.UNPROVEN


def test_a_non_positive_out_of_sample_sharpe_grades_failing() -> None:
    assert grade_health(health(oos_sharpe=-0.1)) is HealthGrade.FAILING


def test_live_expectancy_drifting_below_the_baseline_moves_the_grade() -> None:
    assert (
        grade_health(health(baseline_expectancy=100.0, live_expectancy=70.0))
        is HealthGrade.WATCH
    )
    assert (
        grade_health(health(baseline_expectancy=100.0, live_expectancy=40.0))
        is HealthGrade.DEGRADED
    )
    assert (
        grade_health(health(baseline_expectancy=100.0, live_expectancy=110.0))
        is HealthGrade.HEALTHY
    )


def test_regime_fit_distinguishes_unclassified_from_uncovered() -> None:
    assert health().regime_fit == "unclassified"
    assert health(current_regime="BULL_LOW").regime_fit == "unknown"
    assert (
        health(current_regime="BULL_LOW", regimes_covered=("BEAR_HIGH",)).regime_fit
        == "uncovered"
    )
    assert (
        health(current_regime="BULL_LOW", regimes_covered=("BULL_LOW",)).regime_fit
        == "covered"
    )


# ── feasibility ──────────────────────────────────────────────────────────────


def test_a_feasible_pairing_reports_a_size_and_the_reason_for_it(allocator) -> None:
    candidate = allocator.feasible(
        account=account(),
        health=health(),
        compatibility=compatible()[("a1", "s1")],
    )
    assert candidate.feasible is True
    # 25% of an 8,000 buffer is 2,000; a 400 drawdown per contract buys five.
    assert candidate.max_contracts == 5
    assert "the risk budget" in candidate.detail


def test_an_unjudged_strategy_is_refused_and_says_so(allocator) -> None:
    candidate = allocator.feasible(
        account=account(),
        health=StrategyHealth(strategy_id="s1", expected_drawdown_p95=100.0),
        compatibility=compatible()[("a1", "s1")],
    )
    assert candidate.feasible is False
    assert any("never been judged" in reason for reason in candidate.blocking)


def test_no_compatibility_evaluation_refuses(allocator) -> None:
    candidate = allocator.feasible(
        account=account(), health=health(), compatibility=None
    )
    assert candidate.feasible is False
    assert any(
        r.check is FeasibilityCheck.COMPATIBILITY and r.unknown for r in candidate.reasons
    )


def test_an_account_with_no_recorded_state_is_refused(allocator) -> None:
    """A rule engine with nothing to evaluate cannot clear anything."""
    candidate = allocator.feasible(
        account=account(can_trade=None, buffer=None),
        health=health(),
        compatibility=compatible()[("a1", "s1")],
    )
    assert candidate.feasible is False
    assert any("no state has been recorded" in reason for reason in candidate.blocking)


def test_a_strategy_with_no_drawdown_estimate_cannot_be_sized(allocator) -> None:
    candidate = allocator.feasible(
        account=account(),
        health=health(expected_drawdown_p95=None),
        compatibility=compatible()[("a1", "s1")],
    )
    assert candidate.feasible is False
    assert any("cannot be bounded" in reason for reason in candidate.blocking)


def test_an_account_with_no_buffer_left_is_refused(allocator) -> None:
    candidate = allocator.feasible(
        account=account(buffer=0.0),
        health=health(),
        compatibility=compatible()[("a1", "s1")],
    )
    assert candidate.feasible is False
    assert any("no buffer left" in reason for reason in candidate.blocking)


def test_a_budget_that_does_not_cover_one_contract_refuses(allocator) -> None:
    candidate = allocator.feasible(
        account=account(buffer=1_000.0),
        health=health(expected_drawdown_p95=5_000.0),
        compatibility=compatible()[("a1", "s1")],
    )
    assert candidate.feasible is False
    assert any("does not cover one contract" in reason for reason in candidate.blocking)


def test_the_accounts_own_contract_cap_can_bind_before_the_budget(allocator) -> None:
    candidate = allocator.feasible(
        account=account(max_contracts=2),
        health=health(),
        compatibility=compatible()[("a1", "s1")],
    )
    assert candidate.max_contracts == 2
    assert "contract cap" in candidate.detail


def test_a_degraded_strategy_is_feasible_only_with_a_confirmation(allocator) -> None:
    candidate = allocator.feasible(
        account=account(),
        health=health(baseline_expectancy=100.0, live_expectancy=40.0),
        compatibility=compatible()[("a1", "s1")],
    )
    assert candidate.feasible is True
    assert candidate.requires_confirmation is True


def test_a_failing_strategy_is_not_feasible_at_all(allocator) -> None:
    candidate = allocator.feasible(
        account=account(),
        health=health(verdict="FAIL"),
        compatibility=compatible()[("a1", "s1")],
    )
    assert candidate.feasible is False


def test_the_churn_limit_refuses_a_further_change_today(allocator) -> None:
    candidate = allocator.feasible(
        account=account(),
        health=health(),
        compatibility=compatible()[("a1", "s1")],
        changes_today=5,
    )
    assert candidate.feasible is False
    assert any("change(s) already today" in reason for reason in candidate.blocking)


def test_continuing_an_existing_allocation_is_not_a_change(allocator) -> None:
    candidate = allocator.feasible(
        account=account(current_strategy_id="s1"),
        health=health(),
        compatibility=compatible()[("a1", "s1")],
        changes_today=5,
    )
    assert candidate.feasible is True


def test_requiring_regime_fit_refuses_an_uncovered_regime() -> None:
    allocator = Allocator(
        AllocationConstraints(require_regime_fit=True), now=lambda: NOW
    )
    candidate = allocator.feasible(
        account=account(),
        health=health(current_regime="BEAR_HIGH", regimes_covered=("BULL_LOW",)),
        compatibility=compatible()[("a1", "s1")],
    )
    assert candidate.feasible is False


# ── the plan ─────────────────────────────────────────────────────────────────


def test_a_plan_allocates_the_feasible_pairing(allocator) -> None:
    plan = allocator.plan(
        accounts=[account()], healths=[health()], compatibility=compatible()
    )
    assert len(plan.allocations) == 1
    allocation = plan.allocations[0]
    assert allocation.strategy_id == "s1"
    assert allocation.source == "deterministic"
    assert allocation.verdict_id == "verdict-s1"
    assert allocation.rules_id == "rules-1"


def test_a_plan_keeps_every_refusal_so_the_screen_can_explain_itself(allocator) -> None:
    plan = allocator.plan(
        accounts=[account()],
        healths=[health(), health("s2", verdict="FAIL")],
        compatibility={
            **compatible(),
            ("a1", "s2"): compatible("a1", "s2")[("a1", "s2")],
        },
    )
    assert len(plan.candidates) == 2
    refused = [c for c in plan.candidates if not c.feasible]
    assert refused and refused[0].blocking


def test_one_account_runs_one_strategy_by_default(allocator) -> None:
    plan = allocator.plan(
        accounts=[account()],
        healths=[health(), health("s2")],
        compatibility={
            **compatible(),
            ("a1", "s2"): compatible("a1", "s2")[("a1", "s2")],
        },
    )
    assert len(plan.allocations) == 1


def test_a_strategy_can_be_capped_across_accounts() -> None:
    """Twenty accounts running one strategy is one position, not twenty."""
    allocator = Allocator(
        AllocationConstraints(max_accounts_per_strategy=1), now=lambda: NOW
    )
    plan = allocator.plan(
        accounts=[account("a1"), account("a2")],
        healths=[health()],
        compatibility={
            **compatible("a1"),
            **compatible("a2"),
        },
    )
    assert len(plan.allocations) == 1


def test_a_plan_states_that_allocation_is_a_proposal(allocator) -> None:
    plan = allocator.plan(
        accounts=[account()], healths=[health()], compatibility=compatible()
    )
    assert any("pre-trade gate" in item for item in plan.limitations)


# ── advice: narrowing only ───────────────────────────────────────────────────


def test_advice_cannot_introduce_a_pairing_the_deterministic_layer_refused(
    allocator,
) -> None:
    """The adversarial case: a recommender inventing an allocation."""
    plan = allocator.plan(
        accounts=[account()],
        healths=[health(verdict="FAIL")],
        compatibility=compatible(),
        advice=[Advice(account_uid="a1", strategy_id="s1", preference=10.0, contracts=9)],
    )
    assert plan.allocations == ()
    assert any("cannot create a pairing" in item for item in plan.advice_rejected)


def test_advice_cannot_name_an_account_or_strategy_that_does_not_exist(
    allocator,
) -> None:
    plan = allocator.plan(
        accounts=[account()],
        healths=[health()],
        compatibility=compatible(),
        advice=[Advice(account_uid="ghost", strategy_id="phantom", contracts=50)],
    )
    assert [a.account_uid for a in plan.allocations] == ["a1"]
    assert len(plan.advice_rejected) == 1


def test_advice_cannot_raise_a_size_above_the_deterministic_maximum(
    allocator,
) -> None:
    plan = allocator.plan(
        accounts=[account()],
        healths=[health()],
        compatibility=compatible(),
        advice=[Advice(account_uid="a1", strategy_id="s1", contracts=999)],
    )
    assert plan.allocations[0].contracts == 5
    assert any("reduced to 5" in item for item in plan.advice_clamped)


def test_advice_can_reduce_a_size(allocator) -> None:
    plan = allocator.plan(
        accounts=[account()],
        healths=[health()],
        compatibility=compatible(),
        advice=[Advice(account_uid="a1", strategy_id="s1", contracts=2)],
    )
    assert plan.allocations[0].contracts == 2
    assert plan.allocations[0].source == "advisory"


def test_advice_can_reorder_within_the_feasible_set(allocator) -> None:
    """Preference decides which of two equally feasible strategies runs."""
    compat = {
        **compatible("a1", "s1"),
        ("a1", "s2"): compatible("a1", "s2")[("a1", "s2")],
    }
    plan = allocator.plan(
        accounts=[account()],
        healths=[health("s1", oos_sharpe=1.9), health("s2", oos_sharpe=0.4)],
        compatibility=compat,
        advice=[Advice(account_uid="a1", strategy_id="s2", preference=5.0)],
    )
    assert plan.allocations[0].strategy_id == "s2"


def test_a_confirmation_requirement_survives_advice(allocator) -> None:
    """Advice must not turn a pairing that needs a person into one that does not."""
    plan = allocator.plan(
        accounts=[account()],
        healths=[health(baseline_expectancy=100.0, live_expectancy=40.0)],
        compatibility=compatible(),
        advice=[Advice(account_uid="a1", strategy_id="s1", preference=99.0, contracts=5)],
    )
    assert plan.allocations[0].requires_confirmation is True
    assert plan.allocations[0].actionable is False


def test_an_allocation_needing_confirmation_becomes_actionable_when_confirmed() -> None:
    allocation = Allocation(
        account_uid="a1",
        strategy_id="s1",
        contracts=2,
        requires_confirmation=True,
        decided_at=NOW,
    )
    assert allocation.actionable is False
    assert allocation.model_copy(update={"confirmed_by": "operator"}).actionable is True


# ── history ──────────────────────────────────────────────────────────────────


def test_a_diff_names_what_kind_of_change_each_row_is() -> None:
    previous = (
        Allocation(account_uid="a1", strategy_id="s1", contracts=2, decided_at=NOW),
        Allocation(account_uid="a2", strategy_id="s2", contracts=1, decided_at=NOW),
    )
    proposed = (
        Allocation(account_uid="a1", strategy_id="s1", contracts=4, decided_at=NOW),
        Allocation(account_uid="a3", strategy_id="s3", contracts=1, decided_at=NOW),
    )
    changes = {c.account_uid: c.kind for c in diff_allocations(
        previous=previous, proposed=proposed, at=NOW, actor="tester"
    )}
    assert changes == {"a1": "resized", "a2": "stopped", "a3": "started"}


def test_an_unchanged_allocation_produces_no_history_row() -> None:
    same = (Allocation(account_uid="a1", strategy_id="s1", contracts=2, decided_at=NOW),)
    assert diff_allocations(previous=same, proposed=same, at=NOW) == ()


def test_a_switch_is_named_as_one() -> None:
    changes = diff_allocations(
        previous=(
            Allocation(account_uid="a1", strategy_id="s1", contracts=2, decided_at=NOW),
        ),
        proposed=(
            Allocation(account_uid="a1", strategy_id="s2", contracts=2, decided_at=NOW),
        ),
        at=NOW,
    )
    assert changes[0].kind == "switched"


# ── which permission question an allocation asks ─────────────────────────────


def test_an_advisory_allocation_asks_the_stricter_permission_question() -> None:
    """At least one researched firm answers these two differently."""
    assert use_case_for("advisory") is UseCase.ALGORITHMIC_ALLOCATION
    assert use_case_for("deterministic") is UseCase.STRATEGY_AUTOMATION
    assert use_case_for("manual") is UseCase.STRATEGY_AUTOMATION
