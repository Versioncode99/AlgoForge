"""The scaler: what moves the number, what refuses to, and what paces it.

Four properties are what this layer is for, and each has its own section:

- a driver can only cut, so risk never rises because a driver fired;
- an increase needs every driver measured, a decrease needs none;
- de-risking is never delayed, and increases always are;
- the explanation is read off the record rather than generated.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from forge.propdesk.allocation import StrategyHealth
from forge.propdesk.consent import AI_RISK_DISCLOSURE
from forge.propdesk.risk import AiCapability, ManualRisk, RiskBoundaries, RiskMode, RiskSettings
from forge.propdesk.scaling import (
    Clamp,
    Direction,
    Driver,
    DriverKind,
    RiskObservation,
    ScalingState,
    apply,
    drivers_for,
    explain,
    propose,
)

NOW = datetime(2026, 9, 11, 15, 30, tzinfo=UTC)


def series(seed: int = 7, days: int = 200, scale: float = 100.0) -> tuple[float, ...]:
    rng = np.random.default_rng(seed)
    return tuple(float(x) for x in rng.normal(15.0, scale, days))


def healthy() -> StrategyHealth:
    return StrategyHealth(
        strategy_id="s1",
        verdict="PASS",
        oos_sharpe=1.2,
        oos_trades=120,
        expected_drawdown_p95=400.0,
        regimes_covered=("trending",),
        current_regime="trending",
    )


def observation(**kwargs) -> RiskObservation:
    base = dict(
        account_uid="acct-1",
        strategy_id="s1",
        buffer=20_000.0,
        starting_buffer=20_000.0,
        realised_daily_volatility=100.0,
        modelled_daily_volatility=100.0,
        max_pairwise_correlation=0.1,
        rules_level="OK",
        news_restricted=False,
        oos_daily_pnl=series(),
        health=healthy(),
    )
    base.update(kwargs)
    return RiskObservation(**base)


def adaptive(**kwargs) -> RiskSettings:
    base = dict(account_uid="acct-1", mode=RiskMode.ADAPTIVE, appetite=50)
    base.update(kwargs)
    return RiskSettings(**base)


def state(fraction: float = 0.1, **kwargs) -> ScalingState:
    return ScalingState(account_uid="acct-1", current_fraction=fraction, **kwargs)


class TestDriversOnlyCut:
    def test_every_driver_effect_is_at_most_one(self) -> None:
        # The containment property: no driver can raise anything, in any state.
        # Sampled across the states the drivers actually branch on.
        boundaries = RiskBoundaries()
        cases = [
            observation(),
            observation(buffer=1_000.0),
            observation(rules_level="BREACH"),
            observation(news_restricted=True),
            observation(max_pairwise_correlation=0.95),
            observation(realised_daily_volatility=300.0),
            observation(health=healthy().model_copy(update={"current_regime": "ranging"})),
            observation(health=healthy().model_copy(update={"oos_trades": 3})),
            observation(health=None),
        ]
        for case in cases:
            for driver in drivers_for(case, boundaries):
                assert 0.0 <= driver.effect <= 1.0

    def test_an_unmeasured_driver_may_not_carry_an_effect(self) -> None:
        with pytest.raises(ValueError, match="must not move the number"):
            Driver(kind=DriverKind.BUFFER, measured=False, effect=0.5)

    def test_a_perfect_state_leaves_every_driver_at_one(self) -> None:
        for driver in drivers_for(observation(), RiskBoundaries()):
            assert driver.measured
            assert driver.effect == 1.0

    def test_balance_alone_is_not_a_driver(self) -> None:
        # §11's headline refusal. A larger balance with the same buffer and the
        # same evidence changes nothing.
        boundaries = RiskBoundaries()
        poorer = drivers_for(observation(), boundaries)
        richer = drivers_for(observation(), boundaries)
        assert [d.effect for d in poorer] == [d.effect for d in richer]


class TestEachDriver:
    def test_a_shrinking_buffer_cuts_proportionally(self) -> None:
        boundaries = RiskBoundaries(emergency_buffer_ratio=0.3)
        full = drivers_for(observation(), boundaries)[0]
        half = drivers_for(observation(buffer=10_000.0), boundaries)[0]
        gone = drivers_for(observation(buffer=5_000.0), boundaries)[0]
        assert full.effect == 1.0
        assert 0.0 < half.effect < 1.0
        assert gone.effect == 0.0

    def test_an_unknown_starting_buffer_makes_the_buffer_driver_unmeasured(self) -> None:
        driver = drivers_for(observation(starting_buffer=None), RiskBoundaries())[0]
        assert not driver.measured
        assert driver.effect == 1.0

    def test_a_strategy_the_judge_did_not_pass_zeroes_the_health_driver(self) -> None:
        unproven = observation(health=healthy().model_copy(update={"verdict": None}))
        driver = drivers_for(unproven, RiskBoundaries())[1]
        assert driver.effect == 0.0
        assert "judge" in driver.detail

    def test_realised_volatility_above_the_model_cuts_by_the_ratio(self) -> None:
        driver = drivers_for(observation(realised_daily_volatility=200.0), RiskBoundaries())[2]
        assert driver.effect == pytest.approx(0.5)
        assert "above what the evidence modelled" in driver.detail

    def test_realised_volatility_below_the_model_does_not_raise_anything(self) -> None:
        driver = drivers_for(observation(realised_daily_volatility=50.0), RiskBoundaries())[2]
        assert driver.effect == 1.0

    def test_an_unclassified_regime_is_unmeasured_rather_than_uncovered(self) -> None:
        # The distinction matters: "we did not look" and "we looked and it does
        # not fit" lead to different decisions.
        blank = observation(health=healthy().model_copy(update={"current_regime": ""}))
        driver = drivers_for(blank, RiskBoundaries())[3]
        assert not driver.measured

    def test_an_uncovered_regime_cuts_without_disqualifying(self) -> None:
        off = observation(health=healthy().model_copy(update={"current_regime": "ranging"}))
        driver = drivers_for(off, RiskBoundaries())[3]
        assert 0.0 < driver.effect < 1.0

    def test_correlation_below_a_half_does_not_cut(self) -> None:
        low = observation(max_pairwise_correlation=0.4)
        assert drivers_for(low, RiskBoundaries())[4].effect == 1.0

    def test_perfect_correlation_zeroes_the_driver(self) -> None:
        perfect = observation(max_pairwise_correlation=1.0)
        assert drivers_for(perfect, RiskBoundaries())[4].effect == 0.0

    def test_a_breached_account_rule_zeroes_the_rules_driver(self) -> None:
        assert drivers_for(observation(rules_level="breach"), RiskBoundaries())[5].effect == 0.0

    def test_every_level_the_rule_engine_can_report_is_handled(self) -> None:
        # An earlier version of this map knew three level names, none of which
        # the rule engine actually uses for its middle states — so a `caution`
        # or `warning` account read as unmeasured, which is the wrong answer
        # twice over. Running the application is what surfaced it, and this is
        # what stops it coming back.
        from forge.prop.account import Level

        for level in Level:
            driver = drivers_for(observation(rules_level=level.value), RiskBoundaries())[5]
            if level is Level.NOT_ASSESSED:
                assert not driver.measured, "not_assessed is not a level, it is an absence"
            else:
                assert driver.measured, f"{level.value} is a measurement and must read as one"

    def test_the_middle_levels_reduce_without_zeroing(self) -> None:
        for level in ("caution", "warning"):
            effect = drivers_for(observation(rules_level=level), RiskBoundaries())[5].effect
            assert 0.0 < effect < 1.0

    def test_severity_orders_the_effects(self) -> None:
        effects = [
            drivers_for(observation(rules_level=level), RiskBoundaries())[5].effect
            for level in ("ok", "caution", "warning", "breach")
        ]
        assert effects == sorted(effects, reverse=True)

    def test_an_unassessed_account_is_unmeasured_not_ok(self) -> None:
        for value in ("", "not_assessed", "something_else"):
            driver = drivers_for(observation(rules_level=value), RiskBoundaries())[5]
            assert not driver.measured

    def test_a_news_blackout_zeroes_the_news_driver(self) -> None:
        assert drivers_for(observation(news_restricted=True), RiskBoundaries())[6].effect == 0.0

    def test_too_few_out_of_sample_trades_zeroes_the_evidence_driver(self) -> None:
        thin = observation(health=healthy().model_copy(update={"oos_trades": 4}))
        assert drivers_for(thin, RiskBoundaries())[7].effect == 0.0


class TestTheGovernor:
    def test_a_change_inside_the_hysteresis_band_does_not_fire(self) -> None:
        applied, clamp = apply(
            proposed=0.1005,
            state=state(0.1),
            boundaries=RiskBoundaries(hysteresis=0.005),
            all_measured=True,
            emergency=False,
            now=NOW,
        )
        assert applied == 0.1
        assert clamp is Clamp.HYSTERESIS

    def test_an_increase_is_refused_when_any_driver_was_unmeasured(self) -> None:
        applied, clamp = apply(
            proposed=0.2,
            state=state(0.1),
            boundaries=RiskBoundaries(),
            all_measured=False,
            emergency=False,
            now=NOW,
        )
        assert applied == 0.1
        assert clamp is Clamp.UNMEASURED

    def test_a_decrease_is_permitted_with_nothing_measured(self) -> None:
        applied, _ = apply(
            proposed=0.06,
            state=state(0.1),
            boundaries=RiskBoundaries(max_step=0.05),
            all_measured=False,
            emergency=False,
            now=NOW,
        )
        assert applied == 0.06

    def test_an_increase_inside_the_cooldown_is_held(self) -> None:
        applied, clamp = apply(
            proposed=0.2,
            state=state(0.1, last_change_at=NOW - timedelta(minutes=5)),
            boundaries=RiskBoundaries(cooldown_minutes=240),
            all_measured=True,
            emergency=False,
            now=NOW,
        )
        assert applied == 0.1
        assert clamp is Clamp.COOLDOWN

    def test_a_decrease_inside_the_cooldown_is_not_held(self) -> None:
        # The asymmetry that matters most: a governor that delays de-risking
        # causes the loss it was installed to prevent.
        applied, clamp = apply(
            proposed=0.08,
            state=state(0.1, last_change_at=NOW - timedelta(minutes=1)),
            boundaries=RiskBoundaries(cooldown_minutes=240, max_step=0.05),
            all_measured=True,
            emergency=False,
            now=NOW,
        )
        assert applied == 0.08
        assert clamp is not Clamp.COOLDOWN

    def test_an_increase_is_paced_by_the_step(self) -> None:
        applied, clamp = apply(
            proposed=0.25,
            state=state(0.1),
            boundaries=RiskBoundaries(max_step=0.02, maximum_fraction=0.4, max_daily_change=0.1),
            all_measured=True,
            emergency=False,
            now=NOW,
        )
        assert applied == pytest.approx(0.12)
        assert clamp is Clamp.STEP

    def test_the_daily_limit_stops_further_increases(self) -> None:
        used = state(0.1, change_today=0.05, change_day=NOW.date().isoformat())
        applied, clamp = apply(
            proposed=0.2,
            state=used,
            boundaries=RiskBoundaries(max_daily_change=0.05, max_step=0.02),
            all_measured=True,
            emergency=False,
            now=NOW,
        )
        assert applied == 0.1
        assert clamp is Clamp.DAILY_LIMIT

    def test_yesterdays_movement_does_not_count_against_today(self) -> None:
        stale = state(0.1, change_today=0.05, change_day="2026-09-01")
        assert stale.today(NOW) == 0.0

    def test_a_proposal_outside_the_boundaries_is_clamped_to_them(self) -> None:
        applied, clamp = apply(
            proposed=0.9,
            state=state(0.1),
            boundaries=RiskBoundaries(maximum_fraction=0.2, max_step=0.5, max_daily_change=0.5),
            all_measured=True,
            emergency=False,
            now=NOW,
        )
        assert applied == 0.2
        assert clamp is Clamp.BOUNDARY

    def test_an_emergency_decrease_skips_cooldown_and_step(self) -> None:
        applied, _ = apply(
            proposed=0.05,
            state=state(0.2, last_change_at=NOW - timedelta(seconds=30)),
            boundaries=RiskBoundaries(
                minimum_fraction=0.05, cooldown_minutes=600, max_step=0.01
            ),
            all_measured=False,
            emergency=True,
            now=NOW,
        )
        assert applied == 0.05

    def test_an_emergency_cannot_turn_into_an_increase(self) -> None:
        applied, _ = apply(
            proposed=0.3,
            state=state(0.1),
            boundaries=RiskBoundaries(maximum_fraction=0.4, max_step=0.02, max_daily_change=0.1),
            all_measured=True,
            emergency=True,
            now=NOW,
        )
        assert applied == pytest.approx(0.12)


class TestProposals:
    def test_manual_mode_proposes_exactly_what_was_configured(self) -> None:
        manual = RiskSettings(
            account_uid="acct-1",
            mode=RiskMode.MANUAL,
            manual=ManualRisk(risk_fraction=0.12, max_contracts=2),
            boundaries=RiskBoundaries(minimum_fraction=0.05, maximum_fraction=0.25),
        )
        proposal = propose(settings=manual, observation=observation(), state=state(), now=NOW)
        assert proposal.applied_fraction == 0.12
        assert proposal.direction is Direction.HOLD
        assert proposal.drivers == ()

    def test_an_adaptive_proposal_reports_every_driver_measured_or_not(self) -> None:
        proposal = propose(settings=adaptive(), observation=observation(), state=state(), now=NOW)
        assert {d.kind for d in proposal.drivers} == set(DriverKind) - {DriverKind.ADVISORY}

    def test_without_a_measurable_band_risk_falls_to_the_operators_minimum(self) -> None:
        bare = observation(oos_daily_pnl=())
        boundaries = RiskBoundaries(minimum_fraction=0.05, maximum_fraction=0.25, max_step=0.05)
        proposal = propose(
            settings=adaptive(boundaries=boundaries),
            observation=bare,
            state=state(0.2),
            now=NOW,
        )
        assert proposal.unmeasurable is not None
        assert proposal.applied_fraction < 0.2
        assert proposal.band is None

    def test_a_breach_de_risks_immediately_and_says_it_was_an_emergency(self) -> None:
        proposal = propose(
            settings=adaptive(),
            observation=observation(rules_level="BREACH"),
            state=state(0.2, last_change_at=NOW - timedelta(seconds=10)),
            now=NOW,
        )
        assert proposal.emergency
        assert proposal.direction is Direction.DECREASE

    def test_the_binding_driver_is_the_one_that_cut_most(self) -> None:
        proposal = propose(
            settings=adaptive(),
            observation=observation(news_restricted=True, realised_daily_volatility=150.0),
            state=state(0.2),
            now=NOW,
        )
        assert proposal.binding_driver is DriverKind.NEWS

    def test_contract_counts_are_reported_on_both_sides_of_the_change(self) -> None:
        proposal = propose(settings=adaptive(), observation=observation(), state=state(), now=NOW)
        assert proposal.contracts_before is not None
        assert proposal.contracts_after is not None

    def test_contract_counts_are_absent_rather_than_zero_when_unmeasurable(self) -> None:
        # Zero would read as "flatten", which is a different instruction.
        proposal = propose(
            settings=adaptive(),
            observation=observation(oos_daily_pnl=()),
            state=state(),
            now=NOW,
        )
        assert proposal.contracts_before is None
        assert proposal.contracts_after is None


class TestAdvisory:
    def ai(self, **kwargs) -> RiskSettings:
        base = dict(
            account_uid="acct-1",
            mode=RiskMode.AI_MANAGED,
            appetite=50,
            disclosure_version=AI_RISK_DISCLOSURE.version,
            ai_capabilities=(AiCapability.POSITION_SIZING,),
        )
        base.update(kwargs)
        return RiskSettings(**base)

    def test_an_advisory_factor_above_one_is_refused_rather_than_clamped(self) -> None:
        # A caller passing 1.4 has misunderstood the parameter and should be
        # told, not quietly obeyed at 1.0.
        with pytest.raises(ValueError, match="may narrow a proposal and may never widen"):
            propose(
                settings=self.ai(),
                observation=observation(),
                state=state(),
                now=NOW,
                advisory=1.4,
            )

    def test_an_advisory_factor_narrows_the_proposal(self) -> None:
        without = propose(settings=self.ai(), observation=observation(), state=state(), now=NOW)
        with_advice = propose(
            settings=self.ai(),
            observation=observation(),
            state=state(),
            now=NOW,
            advisory=0.5,
        )
        assert with_advice.proposed_fraction < without.proposed_fraction

    def test_advice_is_ignored_when_the_capability_was_not_granted(self) -> None:
        without = self.ai(ai_capabilities=(AiCapability.RISK_REDUCTION,))
        plain = propose(settings=without, observation=observation(), state=state(), now=NOW)
        advised = propose(
            settings=without, observation=observation(), state=state(), now=NOW, advisory=0.1
        )
        assert plain.proposed_fraction == advised.proposed_fraction

    def test_advice_is_ignored_entirely_in_adaptive_mode(self) -> None:
        plain = propose(settings=adaptive(), observation=observation(), state=state(), now=NOW)
        advised = propose(
            settings=adaptive(), observation=observation(), state=state(), now=NOW, advisory=0.1
        )
        assert plain.proposed_fraction == advised.proposed_fraction

    def test_advice_is_marked_as_a_recommendation_not_a_measurement(self) -> None:
        proposal = propose(
            settings=self.ai(),
            observation=observation(),
            state=state(),
            now=NOW,
            advisory=0.6,
        )
        advisory = [d for d in proposal.drivers if d.kind is DriverKind.ADVISORY]
        assert len(advisory) == 1
        assert advisory[0].label == "AI recommendation"

    def test_advice_cannot_unlock_an_increase_that_measurement_would_not(self) -> None:
        # The advisory driver is appended after `all_measured` is computed, so
        # a recommendation can never substitute for a missing measurement.
        blind = observation(rules_level="")
        proposal = propose(
            settings=self.ai(),
            observation=blind,
            state=state(0.06),
            now=NOW,
            advisory=1.0,
        )
        assert proposal.direction is not Direction.INCREASE


class TestExplanation:
    def test_manual_mode_says_nothing_adjusts_it(self) -> None:
        manual = RiskSettings(
            account_uid="acct-1",
            mode=RiskMode.MANUAL,
            manual=ManualRisk(risk_fraction=0.1, max_contracts=2),
        )
        proposal = propose(settings=manual, observation=observation(), state=state(), now=NOW)
        assert explain(proposal) == ["Risk is set manually. Nothing adjusts it."]

    def test_every_cutting_driver_appears_in_the_explanation(self) -> None:
        proposal = propose(
            settings=adaptive(),
            observation=observation(realised_daily_volatility=200.0, max_pairwise_correlation=0.9),
            state=state(),
            now=NOW,
        )
        text = " ".join(explain(proposal))
        assert "Realised volatility" in text
        assert "Portfolio correlation" in text

    def test_every_unmeasured_driver_is_named_as_unmeasured(self) -> None:
        proposal = propose(
            settings=adaptive(),
            observation=observation(max_pairwise_correlation=None),
            state=state(),
            now=NOW,
        )
        assert any("not measured" in line for line in explain(proposal))

    def test_a_clamp_is_explained_with_both_numbers(self) -> None:
        proposal = propose(
            settings=adaptive(boundaries=RiskBoundaries(max_step=0.001, max_daily_change=0.05)),
            observation=observation(),
            state=state(0.05),
            now=NOW,
        )
        assert any("Proposed" in line and "applied" in line for line in explain(proposal))

    def test_no_explanation_line_exists_without_a_field_behind_it(self) -> None:
        # A proposal with everything measured and nothing cutting has exactly
        # one substantive thing to say, and says it rather than inventing a
        # narrative to fill the panel.
        proposal = propose(settings=adaptive(), observation=observation(), state=state(), now=NOW)
        lines = explain(proposal)
        assert all(line.strip() for line in lines)
        assert not any("likely" in line or "probably" in line for line in lines)

    def test_the_dictionary_form_carries_the_explanation(self) -> None:
        proposal = propose(settings=adaptive(), observation=observation(), state=state(), now=NOW)
        payload = proposal.as_dict()
        assert payload["why"] == explain(proposal)
        assert payload["clamp_detail"]
