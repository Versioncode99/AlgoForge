"""A plan, frozen before it runs, and the gate that refuses hopeless ones.

The engine's unit of work was a strategy specification -- a template, some
parameters, a backtest. That is a thing to *run*, not a thing to *ask*, and a
proposal with no stated prediction cannot be wrong, so a result cannot teach
anything, so a campaign generates candidates instead of learning.

These tests pin the gate's refusals, and the two boundaries that make it
trustworthy: it is deterministic, and it distinguishes research that could
never work from research whose data is merely absent.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from forge.research.campaign import CampaignStore
from forge.research.plan import (
    Direction,
    PilotDesign,
    PilotOutcome,
    PlanStatus,
    ResearchPlan,
    promotion,
    review,
)
from forge.research.timescope import recent_years
from pydantic import ValidationError

A0, A1 = datetime(2009, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)
DS = "nq_1m_16y"

WHY = (
    "Recent intraday microstructure: execution conditions and the volatility regime "
    "have both changed since 2020, so older bars describe a different market."
)


def _scope(years: float = 2.0, dataset: str = DS):
    return recent_years(
        dataset=dataset, available_start=A0, available_end=A1, years=years, rationale=WHY
    )


def _plan(**overrides) -> ResearchPlan:
    base = dict(
        research_question="Does compressed overnight range precede directional expansion?",
        hypothesis="Compressed overnight range precedes directional expansion in the US session.",
        mechanism=(
            "Liquidity provision withdraws into the open, so resting size thins and "
            "the same flow moves price further."
        ),
        falsifiable_prediction=(
            "Expectancy after costs is not greater than zero at the 5% level over "
            "the selected window."
        ),
        expected_direction=Direction.POSITIVE,
        invalidation_condition="Expectancy is negative in two consecutive folds.",
        dataset=DS,
        scope=_scope(),
        success_criteria="Positive net expectancy with at least 200 trades.",
        failure_criteria="Non-positive net expectancy, or fewer than 200 trades.",
        transaction_costs="1 tick round turn plus commission.",
        parameters_frozen=True,
        created_at=datetime.now(UTC),
    )
    base.update(overrides)
    return ResearchPlan(**base)


# ── the gate accepts sound research ──────────────────────────────────────────


def test_a_complete_plan_is_accepted(tmp_path) -> None:
    verdict = review(_plan())
    assert verdict.status is PlanStatus.ACCEPTED, verdict.reasons("block")
    assert verdict.accepted


def test_the_gate_is_deterministic(tmp_path) -> None:
    """No model is consulted, so the same plan decides the same way every time."""
    plan = _plan()
    assert len({review(plan).status for _ in range(5)}) == 1


# ── could a result be wrong? ─────────────────────────────────────────────────


def test_a_prediction_naming_no_observation_is_rejected() -> None:
    verdict = review(
        _plan(falsifiable_prediction="The strategy will work well in most market conditions.")
    )
    assert verdict.status is PlanStatus.REJECTED
    assert any("no result can disagree" in r for r in verdict.reasons("block"))


def test_undefined_success_criteria_are_rejected() -> None:
    """Deciding what counts as success after the run is deciding from the answer."""
    assert review(_plan(success_criteria="")).status is PlanStatus.REJECTED
    assert review(_plan(failure_criteria="")).status is PlanStatus.REJECTED


def test_unfrozen_parameters_are_rejected() -> None:
    verdict = review(_plan(parameters={"lookback": 20.0}, parameters_frozen=False))
    assert verdict.status is PlanStatus.REJECTED
    assert any("search" in r for r in verdict.reasons("block"))


def test_frozen_parameters_are_accepted() -> None:
    assert review(_plan(parameters={"lookback": 20.0}, parameters_frozen=True)).accepted


def test_a_plan_with_no_parameters_needs_no_freeze() -> None:
    assert review(_plan(parameters={}, parameters_frozen=False)).accepted


# ── blocked by data is not rejected ──────────────────────────────────────────


def test_missing_data_blocks_rather_than_rejects(tmp_path) -> None:
    """Nothing is wrong with the research. It becomes runnable when data arrives."""
    store = CampaignStore(tmp_path / "c.db")
    campaign = store.create(
        name="NQ", objective="Discover intraday alpha on NQ one-minute bars", dataset=DS
    )
    verdict = review(_plan(data_requirements=("L2_MBP",)), campaign=campaign)
    assert verdict.status is PlanStatus.BLOCKED_DATA
    assert any("cannot serve" in r for r in verdict.reasons("block"))
    assert any("Nothing is substituted" in f.remedy for f in verdict.findings)


def test_data_the_campaign_serves_does_not_block(tmp_path) -> None:
    store = CampaignStore(tmp_path / "c.db")
    campaign = store.create(
        name="NQ", objective="Discover intraday alpha on NQ one-minute bars", dataset=DS
    )
    assert review(_plan(data_requirements=("BARS", "VOLUME")), campaign=campaign).accepted


def test_unsound_research_outranks_missing_data() -> None:
    """A plan that is both unfalsifiable and blocked is rejected, not shelved."""
    verdict = review(
        _plan(
            falsifiable_prediction=(
                "The strategy should do well in most market conditions and be robust."
            ),
            data_requirements=("L2_MBP",),
        ),
        available_capabilities=frozenset({"BARS"}),
    )
    assert verdict.status is PlanStatus.REJECTED


# ── already settled ──────────────────────────────────────────────────────────


def test_an_identical_plan_that_was_already_settled_is_refused() -> None:
    plan = _plan()
    verdict = review(plan, exhausted_fingerprints=frozenset({plan.fingerprint()}))
    assert verdict.status is PlanStatus.REJECTED
    assert any("already been run and settled" in r for r in verdict.reasons("block"))


def test_the_same_claim_over_a_different_window_is_not_the_settled_plan() -> None:
    """Changing the window makes a new experiment -- which is the whole point."""
    two_year = _plan()
    five_year = _plan(scope=_scope(5))
    verdict = review(five_year, exhausted_fingerprints=frozenset({two_year.fingerprint()}))
    assert verdict.accepted
    assert two_year.fingerprint() != five_year.fingerprint()


# ── consequential, not wrong ─────────────────────────────────────────────────


def test_consuming_the_whole_reservoir_with_no_pilot_wants_a_person() -> None:
    """Sound for a seasonal claim. Worth confirming it is not a default."""
    from forge.research.timescope import full_history

    whole = full_history(
        dataset=DS, available_start=A0, available_end=A1,
        rationale="A day-of-week seasonal effect needs many observations of each weekday.",
    )
    verdict = review(_plan(scope=whole, pilot=None))
    assert verdict.status is PlanStatus.REQUIRES_REVIEW
    assert any("with no pilot" in r for r in verdict.reasons("review"))


def test_the_whole_reservoir_with_a_pilot_is_accepted() -> None:
    from forge.research.timescope import full_history

    whole = full_history(
        dataset=DS, available_start=A0, available_end=A1,
        rationale="A day-of-week seasonal effect needs many observations of each weekday.",
    )
    pilot = PilotDesign(
        scope=_scope(2),
        promote_if="a weekday effect of the predicted sign appears at all",
    )
    assert review(_plan(scope=whole, pilot=pilot)).accepted


def test_an_exhausted_campaign_wants_a_person_rather_than_a_refusal(tmp_path) -> None:
    store = CampaignStore(tmp_path / "c.db")
    campaign = store.create(
        name="NQ", objective="Discover intraday alpha on NQ one-minute bars",
        dataset=DS, stopping={"max_experiments": 1},
    )
    store.record(campaign.campaign_id, experiments=2)
    verdict = review(_plan(), campaign=store.get(campaign.campaign_id))
    assert verdict.status is PlanStatus.REQUIRES_REVIEW


# ── notes ────────────────────────────────────────────────────────────────────


def test_unstated_costs_are_a_note_not_a_refusal() -> None:
    verdict = review(_plan(transaction_costs=""))
    assert verdict.accepted
    assert "costs_unstated" in {f.code for f in verdict.findings}


def test_a_failure_derived_plan_says_so_in_its_findings() -> None:
    verdict = review(
        _plan(derived_from_failure="G4 failed with negative continuation expectancy")
    )
    assert verdict.accepted
    assert any("because an earlier one failed" in f.summary for f in verdict.findings)


# ── shape ────────────────────────────────────────────────────────────────────


def test_a_scope_over_a_different_archive_is_refused() -> None:
    with pytest.raises(ValidationError) as exc:
        _plan(scope=_scope(2, dataset="es_1m_10y"))
    assert "not a window" in str(exc.value)


def test_a_pilot_over_a_different_archive_is_refused() -> None:
    with pytest.raises(ValidationError):
        _plan(
            pilot=PilotDesign(
                scope=_scope(2, dataset="es_1m_10y"), promote_if="anything at all"
            )
        )


# ── preregistration ──────────────────────────────────────────────────────────


def test_the_plan_preregisters_its_claim_with_its_window() -> None:
    plan = _plan(parameters={"lookback": 20.0}, parameters_frozen=True)
    claim = plan.preregister()
    assert claim.scope_fingerprint == plan.scope.fingerprint()
    assert "lookback=20.0" in claim.falsification


def test_two_plans_differing_only_in_window_preregister_differently() -> None:
    """The G1 integration, from the plan's side."""
    assert _plan().preregister().content_hash != _plan(scope=_scope(5)).preregister().content_hash


def test_the_plan_fingerprint_ignores_who_wrote_it_and_when() -> None:
    early = _plan(created_at=datetime(2026, 1, 1, tzinfo=UTC), created_by="agent-1")
    late = _plan(created_at=datetime(2026, 6, 1, tzinfo=UTC), created_by="operator")
    assert early.fingerprint() == late.fingerprint()


# ── pilots ───────────────────────────────────────────────────────────────────


def test_a_promising_pilot_promotes() -> None:
    allowed, why = promotion(PilotOutcome.PROMISING)
    assert allowed and why


def test_an_inconclusive_pilot_promotes() -> None:
    """A pilot is small by construction.

    "We could not tell" is the expected answer for a real effect measured on a
    short window, so treating it as a refusal would reject exactly the
    hypotheses a pilot is too small to see.
    """
    assert promotion(PilotOutcome.INCONCLUSIVE)[0] is True


def test_no_signal_does_not_promote() -> None:
    allowed, why = promotion(PilotOutcome.NO_SIGNAL)
    assert not allowed
    assert "predicted an effect and measured none" in why


def test_an_implementation_failure_says_nothing_about_the_hypothesis() -> None:
    """The distinction that stops a broken strategy being read as a dead idea."""
    allowed, why = promotion(PilotOutcome.IMPLEMENTATION_FAILURE)
    assert not allowed
    assert "not a negative result" in why


def test_a_blocked_pilot_does_not_promote() -> None:
    assert promotion(PilotOutcome.BLOCKED)[0] is False


def test_a_pilot_may_not_search() -> None:
    """A pilot that searches is a cheap experiment with an inflated trial count."""
    with pytest.raises(ValidationError):
        PilotDesign(scope=_scope(2), promote_if="anything at all", max_configurations=50)
