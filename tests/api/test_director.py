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
from forge.research.skips import SkipKind
from forge.strategy import TEMPLATES, FamilyRegistry, TemplateStore
from forge_api.campaigns import CampaignService
from forge_api.director import Candidate, ObservationInput, Refusal, ResearchDirector

OBJECTIVE = "Discover intraday NQ alpha on one-minute bars between 2008 and 2026."


@pytest.fixture
def director(tmp_path: Path) -> ResearchDirector:
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
    """Ask for a candidate from one specific bucket.

    The allocation is pinned to that bucket, but `MIN_WEIGHT` keeps every other
    bucket reachable on purpose — a campaign that stopped exploring entirely
    because exploration was going badly would never find out it had stopped
    being right. So roughly one draw in nine lands elsewhere, and the returned
    candidate is filtered rather than assumed: a helper that accepted whatever
    came back would assert against a bucket the test did not ask for.
    """
    campaign = director.campaign()
    assert campaign is not None
    campaign.allocation = ResearchAllocation(
        weights={b: (1.0 if b is bucket else 0.0) for b in Bucket}
    )
    director.campaigns.save(campaign)
    director._allocation = None
    for seed in range(tries):
        outcome = director.next_candidate(random.Random(seed), 0, "exploration")
        if isinstance(outcome, Candidate) and outcome.bucket is bucket:
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


def test_family_discovery_never_creates_more_families_than_it_has_mechanisms(
    director: ResearchDirector,
) -> None:
    """The gate's job: a restatement is refused rather than becoming a family.

    Run the discovery bucket far past the number of distinct constructions
    available. Every family beyond that would have to be a re-proposal of one
    already registered, so the count staying inside the vocabulary is the gate
    working.
    """
    from forge.research.synthesis import ARCHETYPES

    start(director)
    for seed in range(60):
        director.next_candidate(random.Random(seed), 0, "exploration")
    campaign = director.campaigns.get(director.campaign_id)
    assert campaign is not None
    created = [f for f in director.families.all() if f.origin != "builtin"]
    assert len(created) <= len(ARCHETYPES)
    # And no family is a numeric restatement of an existing name.
    assert not [f for f in created if f.key.endswith(("_2", "_v2", "_new"))]


def test_an_open_question_can_actually_be_tested(director: ResearchDirector) -> None:
    """The exploration bucket must not refuse a question it just admitted.

    Picking up an existing frontier item is a second *construction* of a claim
    already on the frontier, not a new claim. Running the novelty gate on it
    refused it as a duplicate of its own hypothesis, so every attempt to answer
    an open question produced a refusal instead of an experiment and the bucket
    stalled the moment the frontier had anything in it.
    """
    start(director)
    first = propose_until(director, Bucket.EXPLORE_HYPOTHESIS)
    assert first is not None
    assert first.hypothesis_id
    open_items = director.frontier.schedulable(director.campaign_id)
    assert open_items, "the first proposal admitted nothing to the frontier"

    # With the frontier now non-empty, the bucket has to keep producing
    # candidates rather than refusals.
    produced = [
        propose_until(director, Bucket.EXPLORE_HYPOTHESIS, tries=30) for _ in range(3)
    ]
    assert all(candidate is not None for candidate in produced)
    assert all(candidate.hypothesis_id for candidate in produced if candidate)
    # And they are recorded against an admitted question, not as new claims.
    assert all(candidate.frontier_item_id for candidate in produced if candidate)


def test_a_second_construction_of_a_promising_question_is_structural(
    director: ResearchDirector,
) -> None:
    """If the effect is real it should survive being measured a different way."""
    start(director)
    first = propose_until(director, Bucket.EXPLORE_HYPOTHESIS)
    assert first is not None
    director.observe(
        ObservationInput(
            candidate=first,
            strategy_id="s1",
            experiment_id="exp_1",
            status="screened",
            trades=84,
            net_pnl=900.0,
            grid_size=9,
            conformance_passed=True,
            determinism_reproduced=True,
            real_data=True,
        )
    )
    second = propose_until(director, Bucket.ADVANCE_PROMISING, tries=40)
    assert second is not None
    assert second.hypothesis_id == first.hypothesis_id
    assert second.frontier_item_id == first.frontier_item_id
    assert second.search_kind is SearchKind.STRUCTURAL
    # A different construction, not the same template again.
    assert second.template_key != first.template_key


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


def test_a_validated_candidate_gets_its_outcome_recorded(director: ResearchDirector) -> None:
    """The queue is a record, not a list of intentions.

    The engine validates inline, so by the time a judged observation arrives the
    answer exists. It has to land on the entry, or "queued for validation" and
    "validated" become indistinguishable.
    """
    start(director)
    candidate = propose_until(director, Bucket.EXPLORE_HYPOTHESIS)
    assert candidate is not None
    director.observe(
        ObservationInput(
            candidate=candidate,
            strategy_id="s_judged",
            experiment_id="exp_1",
            status="passed",
            trades=96,
            net_pnl=2_400.0,
            grid_size=9,
            conformance_passed=True,
            determinism_reproduced=True,
            real_data=True,
            decision="PASS",
            gates=(("G0", "PASS", "Data integrity"),),
            verdict_id="vd_1",
        )
    )
    outcomes = director.promotion.outcomes(director.campaign_id)
    assert outcomes["PASS"] == 1
    assert director.promotion.pending(director.campaign_id) == 0
    entry = director.promotion.list(director.campaign_id)[0]
    assert entry["state"] == "DECIDED"
    assert entry["detail"]["verdict_id"] == "vd_1"


def test_an_unmeasured_gate_settles_as_inconclusive_not_failed(
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
            status="judged",
            trades=96,
            net_pnl=1_400.0,
            grid_size=9,
            conformance_passed=True,
            determinism_reproduced=True,
            real_data=True,
            decision="INCONCLUSIVE",
            gates=(("G5", "INCONCLUSIVE", "Deflated Sharpe"),),
        )
    )
    outcomes = director.promotion.outcomes(director.campaign_id)
    assert outcomes["INCONCLUSIVE"] == 1
    assert outcomes["FAIL"] == 0


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


def test_a_completed_campaign_keeps_saying_it_is_finished(
    director: ResearchDirector,
) -> None:
    """It used to go quiet, and quiet was read as "no campaign was ever here".

    A campaign that reached a stopping criterion detached itself and
    ``next_candidate`` began returning ``None``. The engine reads ``None`` as
    "nothing is directing me" and answers with the uniform random template draw
    it used before campaigns existed — so the programme ended, the engine went
    on spending compute on undirected candidates against a scope that had
    already claimed most of the catalogue, and the interface said RUNNING
    throughout. The refusal has to keep coming until somebody attaches
    something else.
    """
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
    first = director.next_candidate(random.Random(1), 0, "exploration")
    assert isinstance(first, Refusal)
    assert "experiment budget" in first.reason

    second = director.next_candidate(random.Random(2), 0, "exploration")
    assert isinstance(second, Refusal)
    assert second.kind is SkipKind.CAMPAIGN_EXHAUSTED
    assert "experiment budget" in second.reason


def test_the_engine_runs_undirected_only_when_nothing_was_ever_attached(
    director: ResearchDirector,
) -> None:
    """``None`` still means "no campaign", which is a legitimate mode."""
    assert director.next_candidate(random.Random(1), 0, "exploration") is None


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

def test_a_family_proposal_that_is_not_new_is_still_researched(
    director: ResearchDirector,
) -> None:
    """Deduplication must prevent waste without preventing discovery.

    The novelty gate refuses a *family* proposal that restates a known
    explanation — correctly, because `mean_reversion_2` should never exist. But
    the verdict it returns carries a downgrade in its own words: "this is a new
    hypothesis within a known explanation, which is worth testing". The director
    used to discard that and spend the cycle on nothing, which is exactly the
    shape of a memory that stops research rather than focusing it.

    Driven directly at the builder so the assertion is about the decision and
    not about which bucket the budget happened to draw.
    """
    start(director)
    campaign = director.campaign()
    assert campaign is not None
    rng = random.Random(11)

    # Every archetype is drawn repeatedly, so collisions with the families
    # registered by earlier draws are guaranteed within a few dozen attempts.
    outcomes = [director._discover_family(campaign, rng, 0) for _ in range(40)]
    refusals = [o for o in outcomes if isinstance(o, Refusal)]
    candidates = [o for o in outcomes if isinstance(o, Candidate)]

    assert candidates, "every family proposal was refused; nothing was researched"

    # Whatever was refused was refused as a restatement, with the collision
    # named — never as a bare "duplicate".
    for refusal in refusals:
        assert refusal.kind is SkipKind.NOT_NOVEL
        assert refusal.reason
        assert refusal.matched or "archetype" in refusal.reason

    # And at least one candidate came through the downgrade path rather than as
    # an outright new family: the gate said "not a family" and research still
    # happened.
    downgraded = [c for c in candidates if "downgraded from a family proposal" in c.rationale]
    assert downgraded, "no proposal was pursued at the level the gate assigned it"
    assert all(c.search_kind is not SearchKind.PARAMETER for c in downgraded)
