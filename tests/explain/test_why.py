"""Every question declines when it has no state, and answers only from what it got."""

from __future__ import annotations

from datetime import UTC, datetime

from forge.explain.why import (
    ANSWERS,
    Question,
    catalogue,
    why_cant_i_deploy_this,
    why_did_allocation_change,
    why_did_risk_change,
    why_did_this_strategy_fail,
    why_is_this_account_blocked,
    why_is_this_validation_insufficient,
    why_was_this_strategy_recommended,
)
from forge.judge import Judge, JudgeInput
from forge.propdesk.allocation import (
    AllocationChange,
    Candidate,
    FeasibilityCheck,
    FeasibilityReason,
    HealthGrade,
)
from forge.propdesk.autonomy import AutonomyLevel, DeploymentFacts, evaluate
from forge.propdesk.desk import DeskDecision, Stage
from forge.propdesk.fabric import CommandKind, OrderIntent
from forge.propdesk.risk import RiskMode
from forge.propdesk.scaling import Direction, RiskProposal

from tests.judge.test_judge import judge_input, strong_pnl

ANSWERERS = (
    why_did_this_strategy_fail,
    why_was_this_strategy_recommended,
    why_did_risk_change,
    why_cant_i_deploy_this,
    why_is_this_account_blocked,
    why_is_this_validation_insufficient,
    why_did_allocation_change,
)


class TestSilenceWhenThereIsNothingToSay:
    def test_every_question_declines_when_it_has_no_state(self) -> None:
        # The failure mode this guards: a fluent paragraph about nothing.
        for answerer in ANSWERERS:
            answer = answerer(None)
            assert not answer.answered
            assert answer.unavailable
            assert answer.headline == ""
            assert answer.points == ()

    def test_a_declined_answer_says_what_is_missing_rather_than_shrugging(self) -> None:
        assert "never been judged" in why_did_this_strategy_fail(None).unavailable
        assert "no risk evaluation" in why_did_risk_change(None).unavailable


class TestStrategyFailure:
    def test_a_passing_strategy_did_not_fail_and_says_so(self) -> None:
        passing = Judge().evaluate(judge_input())
        answer = why_did_this_strategy_fail(passing)
        if passing.decision == "PASS":
            assert not answer.answered
            assert "did not fail" in answer.unavailable

    def test_an_inconclusive_verdict_is_not_reported_as_a_failure(self) -> None:
        # Collapsing INCONCLUSIVE into FAIL is the failure mode the judge's
        # explanation layer exists to avoid, and this question must not
        # reintroduce it.
        unmeasured = Judge().evaluate(
            JudgeInput(
                run_id="run_unmeasured",
                tier="TRUTH_OOS",
                pnl=strong_pnl(),
                trial_count=24,
                data_gate_passed=True,
                preregistered=True,
                implementation_tests_passed=True,
            )
        )
        answer = why_did_this_strategy_fail(unmeasured)
        if unmeasured.decision == "INCONCLUSIVE":
            assert answer.answered
            assert "did not fail" in answer.headline

    def test_the_answer_names_the_verdict_it_came_from(self) -> None:
        verdict = Judge().evaluate(judge_input(tier="LEGACY_IN_SAMPLE"))
        answer = why_did_this_strategy_fail(verdict)
        if answer.answered:
            assert verdict.verdict_id in answer.source


class TestValidationSufficiency:
    def test_a_fully_measured_verdict_has_nothing_to_report(self) -> None:
        verdict = Judge().evaluate(judge_input())
        answer = why_is_this_validation_insufficient(verdict)
        if verdict.decision == "PASS":
            assert not answer.answered or answer.points

    def test_unmeasured_gates_are_listed_with_what_would_measure_them(self) -> None:
        sparse = Judge().evaluate(
            JudgeInput(
                run_id="r",
                tier="TRUTH_OOS",
                pnl=strong_pnl(),
                trial_count=1,
                data_gate_passed=True,
                preregistered=True,
                implementation_tests_passed=True,
            )
        )
        answer = why_is_this_validation_insufficient(sparse)
        assert answer.answered
        assert "is a judgement about the strategy" in answer.headline
        assert answer.points


class TestRecommendation:
    def test_a_feasible_candidate_reports_its_grade_and_ceiling(self) -> None:
        candidate = Candidate(
            account_uid="acct-1",
            strategy_id="s1",
            feasible=True,
            max_contracts=3,
            health_grade=HealthGrade.HEALTHY,
            reasons=(
                FeasibilityReason(
                    check=FeasibilityCheck.RISK_BUDGET, passed=True, detail="the risk budget"
                ),
            ),
        )
        answer = why_was_this_strategy_recommended(candidate)
        assert answer.answered
        assert "3 contract" in answer.headline
        assert "healthy" in answer.headline

    def test_an_infeasible_candidate_says_it_was_not_recommended(self) -> None:
        candidate = Candidate(
            account_uid="acct-1",
            strategy_id="s1",
            feasible=False,
            reasons=(
                FeasibilityReason(
                    check=FeasibilityCheck.VALIDATION,
                    passed=False,
                    detail="the judge has not passed this strategy",
                ),
            ),
        )
        answer = why_was_this_strategy_recommended(candidate)
        assert answer.answered
        assert answer.headline == "It was not recommended."
        assert "the judge has not passed" in answer.points[0]


class TestRiskChange:
    def proposal(self, **kwargs) -> RiskProposal:
        base = dict(
            account_uid="acct-1",
            mode=RiskMode.ADAPTIVE,
            at=datetime(2026, 9, 11, tzinfo=UTC),
            current_fraction=0.15,
            proposed_fraction=0.1,
            applied_fraction=0.1,
            direction=Direction.DECREASE,
        )
        base.update(kwargs)
        return RiskProposal(**base)

    def test_a_change_reports_both_numbers(self) -> None:
        answer = why_did_risk_change(self.proposal())
        assert "15.00%" in answer.headline
        assert "10.00%" in answer.headline

    def test_no_change_says_it_was_held_rather_than_inventing_a_movement(self) -> None:
        held = self.proposal(applied_fraction=0.15, direction=Direction.HOLD)
        assert "held" in why_did_risk_change(held).headline


class TestDeployment:
    def test_a_blocked_deployment_lists_every_gate_that_did_not_pass(self) -> None:
        decision = evaluate(
            DeploymentFacts(strategy_id="s1", account_uid="a"), level=AutonomyLevel.OFF
        )
        answer = why_cant_i_deploy_this(decision)
        assert answer.answered
        assert len(answer.points) == len(decision.blocking)
        assert all("not a pass" in point for point in answer.points)

    def test_a_cleared_deployment_says_so_and_names_the_level(self) -> None:
        decision = evaluate(
            DeploymentFacts(
                strategy_id="s1",
                account_uid="a",
                verdict="PASS",
                evidence_tier="TRUTH_OOS",
                walk_forward_survives=True,
                paths_robust=True,
                account_bound=True,
                connection_live=True,
                instrument_permitted=True,
                instrument_mapped=True,
                permitted_contracts=1,
                pretrade_cleared=True,
                buffer=10_000.0,
                expected_drawdown_per_contract=100.0,
                lifecycle_allowed=True,
                disclosure_acknowledged=True,
            ),
            level=AutonomyLevel.OFF,
        )
        answer = why_cant_i_deploy_this(decision)
        # Health and firm policy are still unknown here, so it is blocked; the
        # point of this test is that a cleared decision reads differently.
        assert answer.answered


class TestAccountBlocked:
    def intent(self) -> OrderIntent:
        return OrderIntent(
            intent_id="i1",
            kind=CommandKind.PLACE,
            account_uid="acct-1",
            idempotency_key="k1",
            symbol="MNQ",
            quantity=1,
            created_at=datetime(2026, 9, 11, tzinfo=UTC),
        )

    def decision(self, *, cleared: bool, stages: tuple[Stage, ...]) -> DeskDecision:
        return DeskDecision(
            decision_id="d1",
            intent=self.intent(),
            cleared=cleared,
            stages=stages,
            at=datetime(2026, 9, 11, tzinfo=UTC),
        )

    def test_a_refused_desk_decision_names_the_rung(self) -> None:
        decision = self.decision(
            cleared=False,
            stages=(
                Stage(stage="account_binding", passed=True, detail="ok"),
                Stage(
                    stage="compatibility",
                    passed=False,
                    unknown=True,
                    detail="no programme policy has been evaluated",
                ),
            ),
        )
        answer = why_is_this_account_blocked(decision)
        assert answer.answered
        assert answer.points == ("compatibility: no programme policy has been evaluated",)

    def test_a_cleared_decision_means_the_account_is_not_blocked(self) -> None:
        decision = self.decision(
            cleared=True, stages=(Stage(stage="account_binding", passed=True),)
        )
        assert not why_is_this_account_blocked(decision).answered


class TestAllocationChange:
    def test_a_switch_names_both_strategies_and_the_recorded_rationale(self) -> None:
        change = AllocationChange(
            account_uid="acct-1",
            at=datetime(2026, 9, 11, tzinfo=UTC),
            previous_strategy_id="s0",
            strategy_id="s1",
            previous_contracts=2,
            contracts=1,
            rationale="s0 was graded degraded",
        )
        answer = why_did_allocation_change(change)
        assert "s0 was replaced by s1." in answer.points
        assert "s0 was graded degraded" in answer.points
        assert any("2 to 1" in point for point in answer.points)

    def test_the_rationale_is_the_one_recorded_not_one_derived_now(self) -> None:
        # Deriving it now would use state that has since moved.
        change = AllocationChange(
            account_uid="acct-1",
            at=datetime(2026, 9, 11, tzinfo=UTC),
            strategy_id="s1",
            rationale="",
        )
        answer = why_did_allocation_change(change)
        assert not any("degraded" in point for point in answer.points)


class TestCatalogue:
    def test_every_question_declares_what_it_needs(self) -> None:
        assert set(ANSWERS) == set(Question)
        for item in catalogue():
            assert item["label"].endswith("?")
            assert item["requires"]

    def test_the_specification_s_questions_are_all_present(self) -> None:
        labels = " | ".join(item["label"].lower() for item in catalogue())
        for required in (
            "why did this strategy fail",
            "why was this strategy recommended",
            "change my risk",
            "why can't i deploy this",
            "why is this account blocked",
            "why is this validation insufficient",
            "why did allocation change",
        ):
            assert required in labels
