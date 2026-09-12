"""The whole loop, end to end, against the real stores.

    question -> hypothesis -> scope -> plan -> gate -> pilot -> experiment
      -> failure -> derived hypothesis -> a *different* scope -> second plan

This is the test that answers "does this actually happen in the code", and it
deliberately uses the real `CampaignStore`, `HypothesisGraph`, `ResearchFrontier`
and `ResearchJournal` rather than doubles. A lifecycle proven against doubles
proves the doubles.

It does **not** assert that any hypothesis is true, or that validation passes.
The claim under test is that the machinery carries research from a question to
a materially different follow-up and keeps the lineage — which is a claim about
the engine, not about the market.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from forge.memory import FailureClass
from forge.research.campaign import CampaignStore
from forge.research.followup import Observation, derive
from forge.research.frontier import FrontierState, ResearchFrontier, SearchKind
from forge.research.hypotheses import HypothesisGraph, HypothesisStatus
from forge.research.journal import EventKind, ResearchJournal
from forge.research.plan import (
    Direction,
    PilotDesign,
    PilotOutcome,
    PlanStatus,
    ResearchPlan,
    promotion,
    review,
)
from forge.research.timescope import fixed_range, full_history, recent_years

A0, A1 = datetime(2009, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)
DS = "nq_1m_16y"

QUESTION = "Does unusually compressed overnight range predict directional expansion at the US open?"
HYPOTHESIS = (
    "Compressed overnight range precedes directional expansion in the first hour of the "
    "US cash session."
)
MECHANISM = (
    "Liquidity provision withdraws into the open, so resting size thins and the same order "
    "flow moves price further than it would in a normal book."
)
WHY_RECENT = (
    "The mechanism is about current market microstructure: book depth and execution "
    "conditions have changed materially since 2020, so older bars describe a different book."
)


@pytest.fixture
def stores(tmp_path: Path):
    return (
        CampaignStore(tmp_path / "campaigns.db"),
        HypothesisGraph(tmp_path / "hypotheses.db"),
        ResearchFrontier(tmp_path / "frontier.db"),
        ResearchJournal(tmp_path / "journal.db"),
    )


def _plan(scope, **overrides) -> ResearchPlan:
    base = dict(
        research_question=QUESTION,
        hypothesis=HYPOTHESIS,
        mechanism=MECHANISM,
        falsifiable_prediction=(
            "Expectancy after costs is not greater than zero at the 5% level over the "
            "selected window."
        ),
        expected_direction=Direction.POSITIVE,
        invalidation_condition="Expectancy is negative in two consecutive folds.",
        dataset=DS,
        scope=scope,
        success_criteria="Positive net expectancy over at least 200 trades.",
        failure_criteria="Non-positive net expectancy, or fewer than 200 trades.",
        transaction_costs="One tick round turn plus commission.",
        created_at=datetime.now(UTC),
    )
    base.update(overrides)
    return ResearchPlan(**base)


def test_the_research_lifecycle_runs_and_keeps_its_lineage(stores) -> None:
    campaigns, hypotheses, frontier, journal = stores

    # ── a campaign, and a question it does not yet know the answer to ────────
    campaign = campaigns.create(
        name="NQ opening expansion",
        objective=(
            "Investigate whether overnight range compression conditions the opening "
            "session's directional behaviour, stated falsifiably."
        ),
        dataset=DS,
    )
    cid = campaign.campaign_id

    item = frontier.admit(
        campaign_id=cid,
        question=QUESTION,
        family="momentum",
        mechanism=MECHANISM,
        search_kind=SearchKind.HYPOTHESIS,
    )
    assert item.state in {FrontierState.UNTESTED, FrontierState.UNKNOWN}

    hypothesis = hypotheses.propose(
        campaign_id=cid, statement=HYPOTHESIS, mechanism=MECHANISM,
        prediction="Opening-hour expansion is larger after compressed overnight sessions.",
        family="momentum", search_kind=SearchKind.HYPOTHESIS,
        frontier_item_id=item.item_id,
    )
    assert hypothesis.status is HypothesisStatus.UNTESTED

    # ── a window chosen for *this* hypothesis, not a default ────────────────
    scope = recent_years(
        dataset=DS, available_start=A0, available_end=A1, years=2, rationale=WHY_RECENT,
    )
    assert scope.coverage < 0.15, "a microstructure claim took the whole reservoir"

    # ── a plan, gated before anything runs ──────────────────────────────────
    pilot = PilotDesign(
        scope=fixed_range(
            dataset=DS, available_start=A0, available_end=A1,
            start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 1, tzinfo=UTC),
            rationale=(
                "One recent year is enough to see whether the effect is present at all "
                "before spending the full window on it."
            ),
        ),
        promote_if=(
            "opening-hour expansion after compressed sessions exceeds the unconditional mean"
        ),
    )
    first = _plan(scope, campaign_id=cid, pilot=pilot)
    verdict = review(first, campaign=campaign)
    assert verdict.status is PlanStatus.ACCEPTED, verdict.reasons("block")

    # The claim, frozen with its window, before any number exists.
    claim = first.preregister()
    assert claim.scope_fingerprint == scope.fingerprint()

    journal.record(
        cid,
        EventKind.EXPERIMENT_STARTED,
        f"plan {first.plan_id} accepted over {scope.describe()}",
        detail={"scope": scope.fingerprint(), "plan": first.plan_id},
    )

    # ── a pilot that says "keep going" ──────────────────────────────────────
    allowed, why = promotion(PilotOutcome.PROMISING)
    assert allowed and why
    journal.record(cid, EventKind.PILOT_RUN, f"pilot promoted: {why}")

    # ── the experiment fails, in a *named* way ──────────────────────────────
    hypotheses.set_status(hypothesis.hypothesis_id, HypothesisStatus.REFUTED)
    frontier.transition(
        item.item_id,
        FrontierState.FAILED,
        reason="G4 failed with negative continuation expectancy after costs",
    )

    observation = Observation(
        family="momentum",
        mechanism=MECHANISM,
        hypothesis=HYPOTHESIS,
        template="opening_expansion",
        failure_class=FailureClass.NEGATIVE_EXPECTANCY,
        gate="G4",
        concentration="the effect reversed: expansion was smaller after compressed sessions",
    )
    followups = derive(observation)
    assert followups, "a named failure suggested nothing at all"

    # ── a derived hypothesis, materially different from its parent ──────────
    derived_statement = followups[0].statement
    assert derived_statement != HYPOTHESIS
    derived = hypotheses.propose(
        campaign_id=cid,
        statement=derived_statement,
        mechanism=followups[0].mechanism,
        prediction=followups[0].prediction,
        family="momentum",
        search_kind=followups[0].search_kind,
        parent_id=hypothesis.hypothesis_id,
    )
    assert derived.parent_id == hypothesis.hypothesis_id, "the lineage was not kept"

    # ── and a *different* window, because the question changed ──────────────
    second_scope = full_history(
        dataset=DS, available_start=A0, available_end=A1,
        rationale=(
            "The reversed reading is a claim about a persistent structural relationship "
            "rather than a current one, so it is tested over the whole record."
        ),
    )
    assert second_scope.fingerprint() != scope.fingerprint()

    second = _plan(
        second_scope,
        campaign_id=cid,
        hypothesis=derived_statement[:990],
        derived_from_failure="G4 with reversed conditional expansion",
        pilot=pilot,
    )
    assert review(second, campaign=campaign).accepted

    # The two experiments are distinct claims, so G1 can tell them apart.
    assert second.preregister().content_hash != claim.content_hash

    # ── the lineage survives ────────────────────────────────────────────────
    assert hypotheses.get(derived.hypothesis_id).parent_id == hypothesis.hypothesis_id
    assert frontier.get(item.item_id).state is FrontierState.FAILED
    assert journal.counts(cid)


def test_the_lineage_survives_a_restart(tmp_path: Path) -> None:
    """Agent memory is not system state.

    Everything the loop produced is re-read from freshly opened stores, so a
    campaign interrupted between the failure and the follow-up resumes with the
    parent, the state and the window intact.
    """
    paths = (
        tmp_path / "campaigns.db", tmp_path / "hypotheses.db",
        tmp_path / "frontier.db", tmp_path / "journal.db",
    )
    campaigns, hypotheses, frontier, journal = (
        CampaignStore(paths[0]), HypothesisGraph(paths[1]),
        ResearchFrontier(paths[2]), ResearchJournal(paths[3]),
    )
    campaign = campaigns.create(
        name="Restart", objective="Investigate opening expansion after compressed sessions",
        dataset=DS, start_date="2024-01-01", end_date="2026-01-01",
    )
    parent = hypotheses.propose(
        campaign_id=campaign.campaign_id, statement=HYPOTHESIS, mechanism=MECHANISM,
        prediction="Opening-hour expansion is larger after compressed overnight sessions.",
        family="momentum", search_kind=SearchKind.HYPOTHESIS,
    )
    child = hypotheses.propose(
        campaign_id=campaign.campaign_id,
        statement=(
            "The detector identifies exhaustion rather than initiation, so the sign of "
            "the expansion reverses after compressed sessions."
        ),
        mechanism=MECHANISM, prediction="Expectancy is positive with the sign reversed.",
        family="momentum", search_kind=SearchKind.HYPOTHESIS,
        parent_id=parent.hypothesis_id,
    )
    item = frontier.admit(
        campaign_id=campaign.campaign_id, question=QUESTION, family="momentum",
        mechanism=MECHANISM, search_kind=SearchKind.HYPOTHESIS,
    )
    frontier.transition(
        item.item_id, FrontierState.FAILED, reason="G4 failed with negative expectancy"
    )

    # Everything is dropped and re-opened, as a process restart would.
    del campaigns, hypotheses, frontier, journal
    reopened_campaigns = CampaignStore(paths[0])
    reopened_hypotheses = HypothesisGraph(paths[1])
    reopened_frontier = ResearchFrontier(paths[2])

    survivor = reopened_campaigns.get(campaign.campaign_id)
    assert survivor is not None
    assert reopened_hypotheses.get(child.hypothesis_id).parent_id == parent.hypothesis_id
    assert reopened_frontier.get(item.item_id).state is FrontierState.FAILED

    # And the window survives, because it is on the campaign rather than in a
    # worker's memory.
    scope = survivor.time_scope(A0, A1)
    assert scope is not None
    assert scope.selected_start.year == 2024
