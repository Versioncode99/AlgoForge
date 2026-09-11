"""The scheduler: which campaign a worker serves, and why.

The engine was `Engine → Campaign → Loop`. One campaign, and a worker's research
policy was its thread index. These tests pin what replaces that — and, as
importantly, what the scheduler is still not allowed to do.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from forge.research.agents import AgentRegistry, AgentRole
from forge.research.campaign import CampaignStore
from forge.research.orchestration import (
    MIN_HEALTH,
    STALL_THRESHOLD,
    ResearchOrchestrator,
)
from forge.research.runtime import Outcome

OBJECTIVE = "discover intraday alpha on NQ using falsifiable mechanisms"


@pytest.fixture
def fabric(tmp_path: Path) -> tuple[ResearchOrchestrator, CampaignStore, AgentRegistry]:
    campaigns = CampaignStore(tmp_path / "campaigns.db")
    agents = AgentRegistry(tmp_path / "agents.db")
    return ResearchOrchestrator(campaigns=campaigns, agents=agents), campaigns, agents


def _campaign(store: CampaignStore, name: str, *, priority: int = 50, running: bool = True):
    campaign = store.create(name=name, objective=OBJECTIVE, dataset="nq_1m_16y")
    store.prioritise(campaign.campaign_id, priority)
    if running:
        store.set_status(campaign.campaign_id, "running")
    return store.get(campaign.campaign_id)


def _deal(orchestrator: ResearchOrchestrator, workers: int = 8) -> Counter:
    return Counter(
        orchestrator.assign(str(index), workers=workers).campaign_id for index in range(workers)
    )


def test_no_campaign_means_no_assignment(fabric) -> None:
    """The mode the engine had before campaigns existed, and still a valid one."""
    orchestrator, _, _ = fabric
    assignment = orchestrator.assign("0", workers=4)
    assert assignment.campaign_id is None
    assert "no campaign" in assignment.reason


def test_workers_are_shared_across_every_running_campaign(fabric) -> None:
    orchestrator, campaigns, _ = fabric
    _campaign(campaigns, "NQ")
    _campaign(campaigns, "ES")
    _campaign(campaigns, "Crypto")
    deal = _deal(orchestrator, workers=8)
    assert len(deal) == 3
    assert sum(deal.values()) == 8


def test_priority_buys_workers(fabric) -> None:
    orchestrator, campaigns, _ = fabric
    high = _campaign(campaigns, "High", priority=90)
    low = _campaign(campaigns, "Low", priority=10)
    deal = _deal(orchestrator, workers=8)
    assert deal[high.campaign_id] > deal[low.campaign_id]


def test_no_running_campaign_is_ever_starved_to_zero(fabric) -> None:
    """A campaign at zero workers can never demonstrate that it recovered."""
    orchestrator, campaigns, _ = fabric
    high = _campaign(campaigns, "High", priority=100)
    low = _campaign(campaigns, "Low", priority=1)
    for _ in range(200):
        orchestrator.observe(low.campaign_id, Outcome.DUPLICATE)
    deal = _deal(orchestrator, workers=8)
    assert deal[low.campaign_id] >= 1
    assert deal[high.campaign_id] >= 1


def test_a_campaign_that_stops_producing_loses_share_to_one_that_is(fabric) -> None:
    orchestrator, campaigns, _ = fabric
    stalled = _campaign(campaigns, "Stalled", priority=80)
    healthy = _campaign(campaigns, "Healthy", priority=20)
    before = _deal(orchestrator, workers=8)
    assert before[stalled.campaign_id] > before[healthy.campaign_id]

    for _ in range(60):
        orchestrator.observe(stalled.campaign_id, Outcome.DUPLICATE)
        orchestrator.observe(healthy.campaign_id, Outcome.PROGRESS)

    after = _deal(orchestrator, workers=8)
    assert after[healthy.campaign_id] > before[healthy.campaign_id]
    assert after[stalled.campaign_id] < before[stalled.campaign_id]


def test_health_recovers_when_a_campaign_starts_producing_again(fabric) -> None:
    orchestrator, campaigns, _ = fabric
    campaign = _campaign(campaigns, "Recovering")
    for _ in range(100):
        orchestrator.observe(campaign.campaign_id, Outcome.DUPLICATE)
    assert orchestrator._runtime(campaign.campaign_id).health() == pytest.approx(MIN_HEALTH)
    for _ in range(100):
        orchestrator.observe(campaign.campaign_id, Outcome.PROGRESS)
    assert orchestrator._runtime(campaign.campaign_id).health() == pytest.approx(1.0)


def test_an_unproven_campaign_gets_the_benefit_of_the_doubt(fabric) -> None:
    orchestrator, campaigns, _ = fabric
    campaign = _campaign(campaigns, "Brand new")
    assert orchestrator._runtime(campaign.campaign_id).health() == 1.0


def test_a_stalled_campaign_is_named(fabric) -> None:
    orchestrator, campaigns, _ = fabric
    campaign = _campaign(campaigns, "Stuck")
    for _ in range(STALL_THRESHOLD):
        orchestrator.observe(campaign.campaign_id, Outcome.NO_WORK)
    assert orchestrator.stalled_campaigns() == [campaign.campaign_id]
    # And one result clears it: a stall is a current condition, not a mark.
    orchestrator.observe(campaign.campaign_id, Outcome.PROGRESS)
    assert orchestrator.stalled_campaigns() == []


def test_an_assignment_carries_the_agent_and_its_role(fabric) -> None:
    orchestrator, campaigns, agents = fabric
    campaign = _campaign(campaigns, "Crewed")
    agents.deploy(campaign_id=campaign.campaign_id, count=2)
    for agent in agents.list(campaign.campaign_id):
        agents.set_state(agent.agent_id, agent.state.__class__.RUNNING)
    assignment = orchestrator.assign("0", workers=1)
    assert assignment.agent_id is not None
    assert assignment.role in set(AgentRole)


def test_work_goes_to_the_least_loaded_agent(fabric) -> None:
    orchestrator, campaigns, agents = fabric
    campaign = _campaign(campaigns, "Crewed")
    created, _ = agents.deploy(campaign_id=campaign.campaign_id, count=2)
    for agent in created:
        agents.set_state(agent.agent_id, agent.state.__class__.RUNNING)
    agents.record(created[0].agent_id, experiments=10)
    assignment = orchestrator.assign("0", workers=1)
    assert assignment.agent_id == created[1].agent_id


def test_a_campaign_with_no_agents_still_gets_workers(fabric) -> None:
    """Agents refine *what* is proposed. They are not a precondition for work."""
    orchestrator, campaigns, _ = fabric
    campaign = _campaign(campaigns, "Crewless")
    assignment = orchestrator.assign("0", workers=1)
    assert assignment.campaign_id == campaign.campaign_id
    assert assignment.agent_id is None


def test_stopping_a_campaign_removes_it_from_the_deal(fabric) -> None:
    orchestrator, campaigns, _ = fabric
    first = _campaign(campaigns, "First")
    second = _campaign(campaigns, "Second")
    campaigns.set_status(second.campaign_id, "stopped")
    orchestrator.forget(second.campaign_id)
    deal = _deal(orchestrator, workers=4)
    assert set(deal) == {first.campaign_id}


def test_the_snapshot_reports_allocation_without_inventing_anything(fabric) -> None:
    orchestrator, campaigns, agents = fabric
    campaign = _campaign(campaigns, "Reported")
    agents.deploy(campaign_id=campaign.campaign_id, count=1)
    orchestrator.assign("0", workers=2)
    snapshot = orchestrator.snapshot()
    assert len(snapshot["running_campaigns"]) == 1
    row = snapshot["running_campaigns"][0]
    assert row["name"] == "Reported"
    assert row["runtime"]["workers"] >= 1
    assert row["agents"]["total"] == 1
    assert snapshot["worker_plan"]
