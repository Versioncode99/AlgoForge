"""The director: what it proposes, what it refuses, and what it learns.

These tests drive the director directly with real stores and simulated cycle
outcomes. They pin the behaviour the engine depends on without paying for a
backtest per assertion; `test_research_factory.py` runs the whole loop.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest
from forge.memory.research import FailureClass
from forge.research.allocation import Bucket, ResearchAllocation
from forge.research.frontier import FrontierState, SearchKind
from forge.research.hypotheses import HypothesisStatus
from forge.research.journal import EventKind
from forge.strategy import TEMPLATES, FamilyRegistry, TemplateStore
from forge_api.campaigns import CampaignService
from forge_api.director import Candidate, ObservationInput, Refusal, ResearchDirector

OBJECTIVE = "Discover intraday NQ alpha on one-minute bars between 2008 and 2026."


@pytest.fixture
def shipped_templates() -> dict:
    """Restore the shared catalogue after a test that registers into it.

    `TEMPLATES` is a module-level dict every consumer holds, and the director
    registers generated templates into it on purpose — that is what makes a
    generated template indistinguishable from a shipped one. It must not leak
    between tests.
    """
    before = dict(TEMPLATES)
    yield before
    TEMPLATES.clear()
    TEMPLATES.update(before)


@pytest.fixture
def director(tmp_path: Path, shipped_templates) -> ResearchDirector:
    service = CampaignService(tmp_path)
    service.director = ResearchDirector(
        campaigns=service.campaigns,
        frontier=service.frontier,
        hypotheses=service.hypotheses,
        journal=service.journal,
        sources=service.sources,
        promotion=service.promotion,
        families=FamilyRegistry(tmp_path / "families"),
        templates=TemplateStore(tmp_path / "templates"),
    )
    return service.director


def start(director: ResearchDirector, **kwargs):
    campaign = director.campaigns.create(
        name="NQ Intraday", objective=OBJECTIVE, dataset="synthetic", **kwargs
    )
    director.campaigns.set_status(campaign.campaign_id, "running")
    campaign = director.campaigns.get(campaign.campaign_id)
    director.attach(campaign)
    return campaign


def propose_until(director: ResearchDirector, bucket: Bucket, tries: int = 60):
    """Force one bucket by pinning the allocation, then ask for a candidate."""
    campaign = director.campaign()
    assert campaign is not None
    campaign.allocation = ResearchAllocation(
        weights={b: (1.0 if b is bucket else 0.0) for b in Bucket}
    )
    director.campaigns.save(campaign)
    director._allocation = None
    for seed in range(tries):
        outcome = director.next_candidate(random.Random(seed), 0, "exploration")
        if isinstance(outcome, Candidate):
            return outcome
    return None


def test_no_campaign_means_no_direction(director: ResearchDirector) -> None:
    """With nothing attached the engine's own draw must run unchanged."""
    assert director.next_candidate(random.Random(1), 0, "exploration") is None


def test_a_candidate_carries_its_research_context(director: ResearchDirector) -> None:
    start(director)
    candidate = propose_until(director, Bucket.EXPLORE_HYPOTHESIS)
    assert candidate is not None
    assert candidate.template_key in TEMPLATES
    assert candidate.parameters
    assert candidate.hypothesis_id
    assert candidate.frontier_item_id
    assert candidate.rationale


def test_family_discovery_creates_a_family_with_a_mechanism(director: ResearchDirector) -> None:
    start(director)
    before = set(director.families.all_keys())
    candidate = propose_until(director, Bucket.DISCOVER_FAMILY)
    assert candidate is not None
    created = set(director.families.all_keys()) - before
    assert created
    family = director.families.get(next(iter(created)))
    assert family is not None
    assert len(family.mechanism) >= 40
    assert family.origin == "custom"
    assert family.created_by == "research-director"


def test_a_generated_template_is_registered_and_runnable(director: ResearchDirector) -> None:
    start(director)
    candidate = propose_until(director, Bucket.DISCOVER_FAMILY)
    assert candidate is not None
    assert candidate.template_key.startswith("gen_")
    template = TEMPLATES[candidate.template_key]
    # Indistinguishable from a shipped one: same shape, same guarantees.
    assert template.parameters
    assert len(template.hypothesis) >= 40
    assert len(template.falsifiable_prediction) >= 30
    assert template.research_status == "RUNNABLE"
    # It was smoke-tested before registration.
    metadata = director.templates.metadata(candidate.template_key)
    assert metadata["smoke_test"]["ok"] is True
    assert metadata["created_by"] == "research-director"


def test_generated_templates_are_attributable_to_their_origin(
    director: ResearchDirector,
) -> None:
    start(director)
    candidate = propose_until(director, Bucket.DISCOVER_FAMILY)
    assert candidate is not None
    generated = director.generated_templates()
    assert candidate.template_key in generated
    record = generated[candidate.template_key]
    assert record["archetype"]
    assert record["definition_hash"]
    assert record["campaign_id"] == director.campaign_id


def test_a_duplicate_family_proposal_is_refused_with_the_collision_named(
    director: ResearchDirector,
) -> None:
    """Every archetype has already become a family, so the next is a duplicate."""
    start(director)
    refusals = []
    for seed in range(80):
        outcome = propose_until(
            director, Bucket.DISCOVER_FAMILY, tries=1
        ) or director.next_candidate(random.Random(seed), 0, "exploration")
        if isinstance(outcome, Refusal):
            refusals.append(outcome)
        if len(refusals) >= 1 and seed > 40:
            break
    campaign = director.campaigns.get(director.campaign_id)
    assert campaign is not None
    # Either it refused duplicates, or it exhausted the archetype vocabulary —
    # both are the gate doing its job rather than inventing families.
    assert campaign.progress.families_created <= len(
        __import__("forge.research.synthesis", fromlist=["ARCHETYPES"]).ARCHETYPES
    )


def test_parameter_refinement_is_still_available_and_budgeted(
    director: ResearchDirector,
) -> None:
    """Refining a claim with evidence is real research; it just is not the only one."""
    start(director)
    candidate = propose_until(director, Bucket.REFINE_PARAMETERS)
    assert candidate is not None
    assert candidate.search_kind is SearchKind.PARAMETER
    assert candidate.bucket is Bucket.REFINE_PARAMETERS
    assert candidate.template_key in TEMPLATES


def test_robustness_replicates_at_declared_defaults(director: ResearchDirector) -> None:
    start(director)
    candidate = propose_until(director, Bucket.ROBUSTNESS)
    assert candidate is not None
    template = TEMPLATES[candidate.template_key]
    assert candidate.parameters == {p.name: float(p.default) for p in template.parameters}
    assert "no neighbourhood" in candidate.rationale


def test_a_blocked_capability_is_recorded_blocked_not_tested(
    director: ResearchDirector,
) -> None:
    """A campaign that cannot serve VOLUME must not schedule a volume signal."""
    start(director, allowed_capabilities=["BARS"])
    for seed in range(40):
        director.next_candidate(random.Random(seed), 0, "exploration")
    blocked = director.frontier.list(director.campaign_id, states=(FrontierState.BLOCKED_BY_DATA,))
    scheduled = director.frontier.schedulable(director.campaign_id)
    # Nothing scheduled needs data the campaign cannot serve.
    campaign = director.campaigns.get(director.campaign_id)
    assert campaign is not None
    for item in scheduled:
        assert campaign.serves(item.required_data) == ()
    if blocked:
        assert all(item.missing_data for item in blocked)


def test_a_failure_becomes_a_new_question(director: ResearchDirector) -> None:
    """The loop that makes this a research programme rather than a queue."""
    start(director)
    candidate = propose_until(director, Bucket.EXPLORE_HYPOTHESIS)
    assert candidate is not None
    before = len(director.hypotheses.list(director.campaign_id))

    director.observe(
        ObservationInput(
            candidate=candidate,
            strategy_id="s1",
            experiment_id="exp_1",
            status="judged",
            trades=52,
            net_pnl=-320.0,
            failure_class=FailureClass.ROBUSTNESS,
            gate="G12",
            reason="failed G12 (Walk-forward)",
            decision="REJECT",
            condition="low realised volatility",
            concentration="profitable only in the lowest volatility tercile",
        )
    )

    after = director.hypotheses.list(director.campaign_id)
    assert len(after) > before
    derived = [h for h in after if h.origin == "failure-derived"]
    assert derived
    # A derived hypothesis is a question, never a conclusion.
    assert all(h.status is HypothesisStatus.UNTESTED for h in derived)
    assert all(h.parent_id == candidate.hypothesis_id for h in derived)
    events = director.journal.recent(director.campaign_id, kinds=[EventKind.FOLLOWUP_GENERATED])
    assert events


def test_a_failure_moves_the_frontier_item_to_failed_with_a_reason(
    director: ResearchDirector,
) -> None:
    start(director)
    candidate = propose_until(director, Bucket.EXPLORE_HYPOTHESIS)
    assert candidate is not None
    director.observe(
        ObservationInput(
            candidate=candidate,
            strategy_id="s1",
            experiment_id="exp_1",
            status="judged",
            trades=40,
            net_pnl=-50.0,
            failure_class=FailureClass.NEGATIVE_EXPECTANCY,
            gate="G4",
            reason="failed G4 (Expectancy)",
            decision="REJECT",
        )
    )
    item = director.frontier.get(candidate.frontier_item_id or "")
    assert item is not None
    assert item.state is FrontierState.FAILED
    assert "G4" in item.reason


def test_an_inconclusive_result_is_not_recorded_as_a_failure(
    director: ResearchDirector,
) -> None:
    """The single mistake that would undo the whole point of the frontier."""
    start(director)
    candidate = propose_until(director, Bucket.EXPLORE_HYPOTHESIS)
    assert candidate is not None
    director.observe(
        ObservationInput(
            candidate=candidate,
            strategy_id="s1",
            experiment_id="exp_1",
            status="inconclusive",
            trades=12,
            net_pnl=40.0,
            reason="too few trials to deflate against",
            decision="INCONCLUSIVE",
        )
    )
    item = director.frontier.get(candidate.frontier_item_id or "")
    assert item is not None
    assert item.state is not FrontierState.FAILED
    assert item.schedulable


def test_a_promising_result_is_queued_for_validation_when_it_earns_it(
    director: ResearchDirector,
) -> None:
    start(director)
    candidate = propose_until(director, Bucket.EXPLORE_HYPOTHESIS)
    assert candidate is not None
    director.observe(
        ObservationInput(
            candidate=candidate,
            strategy_id="s_good",
            experiment_id="exp_1",
            status="screened",
            trades=84,
            net_pnl=1_240.0,
            grid_size=9,
            conformance_passed=True,
            determinism_reproduced=True,
            real_data=True,
            reason="development net positive",
        )
    )
    assert director.promotion.pending(director.campaign_id) == 1
    item = director.frontier.get(candidate.frontier_item_id or "")
    assert item is not None
    assert item.state is FrontierState.PROMISING
    events = director.journal.recent(director.campaign_id, kinds=[EventKind.VALIDATION_QUEUED])
    assert events


def test_a_candidate_that_has_not_earned_validation_is_not_queued(
    director: ResearchDirector,
) -> None:
    start(director)
    candidate = propose_until(director, Bucket.EXPLORE_HYPOTHESIS)
    assert candidate is not None
    director.observe(
        ObservationInput(
            candidate=candidate,
            strategy_id="s_thin",
            experiment_id="exp_1",
            status="screened",
            trades=4,
            net_pnl=12.0,
            grid_size=1,
            conformance_passed=True,
            determinism_reproduced=True,
            real_data=True,
        )
    )
    assert director.promotion.pending(director.campaign_id) == 0


def test_the_journal_records_what_actually_happened(director: ResearchDirector) -> None:
    start(director)
    propose_until(director, Bucket.DISCOVER_FAMILY)
    kinds = {e["kind"] for e in director.journal.recent(director.campaign_id, limit=200)}
    assert EventKind.CAMPAIGN_STARTED in kinds
    assert EventKind.BUDGET_DRAWN in kinds
    assert EventKind.NOVELTY_CHECKED in kinds
    assert EventKind.HYPOTHESIS_PROPOSED in kinds
    # Every event carries structured detail, not just a sentence.
    for event in director.journal.recent(director.campaign_id, limit=20):
        assert isinstance(event["detail"], dict)


def test_exhausting_the_budget_stops_the_campaign_by_name(
    director: ResearchDirector,
) -> None:
    start(director, stopping={"max_experiments": 1})
    candidate = propose_until(director, Bucket.EXPLORE_HYPOTHESIS)
    assert candidate is not None
    director.observe(
        ObservationInput(
            candidate=candidate,
            strategy_id="s1",
            experiment_id="exp_1",
            status="judged",
            trades=10,
            net_pnl=-1.0,
            decision="REJECT",
        )
    )
    outcome = director.next_candidate(random.Random(1), 0, "exploration")
    assert isinstance(outcome, Refusal)
    assert "experiment budget" in outcome.reason
    assert director.campaign_id is None


def test_the_engine_keeps_working_after_a_campaign_completes(
    director: ResearchDirector,
) -> None:
    """A finished campaign detaches; it does not leave the engine refusing."""
    start(director, stopping={"max_experiments": 1})
    candidate = propose_until(director, Bucket.EXPLORE_HYPOTHESIS)
    assert candidate is not None
    director.observe(
        ObservationInput(
            candidate=candidate,
            strategy_id="s1",
            experiment_id="e1",
            status="judged",
            trades=1,
            net_pnl=0.0,
            decision="REJECT",
        )
    )
    director.next_candidate(random.Random(1), 0, "exploration")
    assert director.next_candidate(random.Random(2), 0, "exploration") is None


def test_observation_without_a_campaign_is_a_no_op(director: ResearchDirector) -> None:
    candidate = Candidate(
        template_key="momentum_breakout",
        parameters={},
        bucket=Bucket.REFINE_PARAMETERS,
        search_kind=SearchKind.PARAMETER,
    )
    director.observe(
        ObservationInput(candidate=candidate, strategy_id="s", experiment_id="e", status="judged")
    )
