"""Autonomous deployment: one gate list, and what each level does after it."""

from __future__ import annotations

import pytest
from forge.propdesk.allocation import StrategyHealth
from forge.propdesk.autonomy import (
    LEVEL_DETAIL,
    LEVEL_LABEL,
    MANDATORY_GATES,
    AutonomyLevel,
    DeploymentFacts,
    GateKind,
    GateOutcome,
    Outcome,
    describe_gates,
    describe_levels,
    evaluate,
)
from forge.propdesk.policy import Permission
from forge.propdesk.risk import RiskMode


def healthy() -> StrategyHealth:
    return StrategyHealth(
        strategy_id="s1",
        verdict="PASS",
        oos_sharpe=1.4,
        oos_trades=140,
        expected_drawdown_p95=400.0,
    )


def facts(**kwargs) -> DeploymentFacts:
    base = dict(
        strategy_id="s1",
        account_uid="acct-1",
        symbol="MNQ",
        verdict="PASS",
        verdict_id="v-1",
        evidence_tier="TRUTH_OOS",
        walk_forward_survives=True,
        paths_robust=True,
        health=healthy(),
        account_bound=True,
        connection_live=True,
        automation_permission=Permission.ALLOWED,
        instrument_permitted=True,
        instrument_mapped=True,
        risk_mode=RiskMode.ADAPTIVE,
        permitted_contracts=2,
        pretrade_cleared=True,
        buffer=20_000.0,
        expected_drawdown_per_contract=400.0,
        lifecycle_allowed=True,
        disclosure_acknowledged=True,
        disclosure_version="abc123",
    )
    base.update(kwargs)
    return DeploymentFacts(**base)


class TestTheGateListIsTheSameEverywhere:
    def test_full_autonomy_does_not_shorten_the_gate_list(self) -> None:
        # Identity, not equality: a future edit that builds a filtered copy for
        # the autonomous path fails here rather than shipping.
        kinds = [gate.kind for gate in MANDATORY_GATES]
        for level in AutonomyLevel:
            decision = evaluate(facts(), level=level)
            assert [gate.kind for gate in decision.gates] == kinds

    def test_a_failed_gate_blocks_at_every_level(self) -> None:
        for level in AutonomyLevel:
            decision = evaluate(facts(verdict="FAIL"), level=level)
            assert decision.outcome is Outcome.BLOCKED

    def test_an_unknown_gate_blocks_at_every_level(self) -> None:
        for level in AutonomyLevel:
            decision = evaluate(facts(automation_permission=None), level=level)
            assert decision.outcome is Outcome.BLOCKED

    def test_the_level_only_changes_what_happens_after_every_gate_passes(self) -> None:
        assert evaluate(facts(), level=AutonomyLevel.OFF).outcome is Outcome.RECORDED
        assert (
            evaluate(facts(), level=AutonomyLevel.APPROVAL_REQUIRED).outcome
            is Outcome.AWAITING_APPROVAL
        )
        assert evaluate(facts(), level=AutonomyLevel.FULLY_AUTONOMOUS).outcome is Outcome.PROCEED


class TestGates:
    def test_an_unjudged_strategy_is_unknown_rather_than_failed(self) -> None:
        decision = evaluate(facts(verdict=None), level=AutonomyLevel.OFF)
        gate = next(g for g in decision.gates if g.kind is GateKind.VALIDATION)
        assert gate.unknown and not gate.passed

    def test_inconclusive_is_not_a_pass(self) -> None:
        decision = evaluate(facts(verdict="INCONCLUSIVE"), level=AutonomyLevel.OFF)
        gate = next(g for g in decision.gates if g.kind is GateKind.VALIDATION)
        assert not gate.passed
        assert "not a pass" in gate.detail

    def test_in_sample_evidence_is_refused(self) -> None:
        decision = evaluate(facts(evidence_tier="SWEEP"), level=AutonomyLevel.OFF)
        gate = next(g for g in decision.gates if g.kind is GateKind.EVIDENCE_TIER)
        assert not gate.passed
        assert "in-sample" in gate.detail

    def test_unmeasured_robustness_is_unknown_and_names_which_half(self) -> None:
        decision = evaluate(facts(paths_robust=None), level=AutonomyLevel.OFF)
        gate = next(g for g in decision.gates if g.kind is GateKind.ROBUSTNESS)
        assert gate.unknown
        assert "path robustness" in gate.detail

    def test_a_strategy_that_is_merely_watched_does_not_deploy_automatically(self) -> None:
        drifting = healthy().model_copy(
            update={"baseline_expectancy": 100.0, "live_expectancy": 70.0}
        )
        decision = evaluate(facts(health=drifting), level=AutonomyLevel.FULLY_AUTONOMOUS)
        gate = next(g for g in decision.gates if g.kind is GateKind.STRATEGY_HEALTH)
        assert not gate.passed
        assert decision.outcome is Outcome.BLOCKED

    def test_an_unrecorded_firm_permission_blocks_and_says_why(self) -> None:
        decision = evaluate(
            facts(automation_permission=Permission.UNKNOWN), level=AutonomyLevel.FULLY_AUTONOMOUS
        )
        gate = next(g for g in decision.gates if g.kind is GateKind.PROP_POLICY)
        assert gate.unknown
        assert "not permission" in gate.detail

    def test_requires_confirmation_is_not_allowed(self) -> None:
        decision = evaluate(
            facts(automation_permission=Permission.REQUIRES_CONFIRMATION),
            level=AutonomyLevel.FULLY_AUTONOMOUS,
        )
        gate = next(g for g in decision.gates if g.kind is GateKind.PROP_POLICY)
        assert not gate.passed

    def test_a_risk_layer_permitting_no_contracts_blocks(self) -> None:
        decision = evaluate(facts(permitted_contracts=0), level=AutonomyLevel.OFF)
        gate = next(g for g in decision.gates if g.kind is GateKind.RISK_ENVELOPE)
        assert not gate.passed
        assert "no contracts" in gate.detail

    def test_a_buffer_too_small_for_one_contract_blocks(self) -> None:
        decision = evaluate(facts(buffer=100.0), level=AutonomyLevel.OFF)
        gate = next(g for g in decision.gates if g.kind is GateKind.ACCOUNT_BUFFER)
        assert not gate.passed
        assert "does not cover" in gate.detail

    def test_a_refused_pre_trade_screen_blocks_with_the_gates_own_words(self) -> None:
        decision = evaluate(
            facts(pretrade_cleared=False, pretrade_detail="notional weight exceeded"),
            level=AutonomyLevel.OFF,
        )
        gate = next(g for g in decision.gates if g.kind is GateKind.PRETRADE)
        assert gate.detail == "notional weight exceeded"

    def test_a_refused_lifecycle_transition_blocks(self) -> None:
        decision = evaluate(
            facts(lifecycle_allowed=False, lifecycle_detail="live execution is not available"),
            level=AutonomyLevel.FULLY_AUTONOMOUS,
        )
        assert decision.outcome is Outcome.BLOCKED
        assert any("live execution is not available" in reason for reason in decision.reasons)

    def test_an_unacknowledged_disclosure_blocks_autonomy(self) -> None:
        decision = evaluate(
            facts(disclosure_acknowledged=False), level=AutonomyLevel.FULLY_AUTONOMOUS
        )
        assert decision.outcome is Outcome.BLOCKED

    def test_every_field_absent_produces_all_unknown_and_no_passes(self) -> None:
        # A caller that could not reach its sources produces a blocked
        # deployment, never an unvalidated one.
        decision = evaluate(
            DeploymentFacts(strategy_id="s1", account_uid="a"), level=AutonomyLevel.FULLY_AUTONOMOUS
        )
        assert all(gate.unknown for gate in decision.gates)
        assert decision.outcome is Outcome.BLOCKED


class TestOutcomes:
    def test_unknown_and_passed_together_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="not a pass"):
            GateOutcome(kind=GateKind.PRETRADE, passed=True, unknown=True)

    def test_a_blocked_decision_lists_only_the_gates_that_blocked(self) -> None:
        decision = evaluate(facts(verdict="FAIL", connection_live=False), level=AutonomyLevel.OFF)
        blocking = {gate.kind for gate in decision.blocking}
        assert blocking == {GateKind.VALIDATION, GateKind.CONNECTION}

    def test_reasons_are_readable_and_name_the_gate(self) -> None:
        decision = evaluate(facts(connection_live=False), level=AutonomyLevel.OFF)
        assert decision.reasons == ("Connection: the execution connection is not live",)

    def test_the_dictionary_form_carries_the_question_each_gate_asked(self) -> None:
        payload = evaluate(facts(), level=AutonomyLevel.OFF).as_dict()
        assert all(gate["question"] for gate in payload["gates"])
        assert payload["cleared"] is True
        assert payload["level_label"] == LEVEL_LABEL[AutonomyLevel.OFF]


class TestDescription:
    def test_every_level_is_described(self) -> None:
        described = describe_levels()
        assert [item["level"] for item in described] == [level.value for level in AutonomyLevel]
        assert all(LEVEL_DETAIL[AutonomyLevel(item["level"])] for item in described)

    def test_the_gate_list_is_published_so_the_panel_can_show_it(self) -> None:
        published = describe_gates()
        assert len(published) == len(MANDATORY_GATES)
        assert all(item["name"] and item["question"] for item in published)

    def test_the_specification_s_mandatory_conditions_are_all_present(self) -> None:
        kinds = {gate.kind for gate in MANDATORY_GATES}
        assert {
            GateKind.VALIDATION,
            GateKind.PROP_POLICY,
            GateKind.RISK_ENVELOPE,
            GateKind.INSTRUMENT,
            GateKind.CONNECTION,
            GateKind.PRETRADE,
            GateKind.ACCOUNT_BUFFER,
        } <= kinds
