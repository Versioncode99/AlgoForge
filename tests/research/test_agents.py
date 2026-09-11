"""Research agents: roles, states, leases and honest capacity.

Before this module, "worker" meant "thread index" — worker 3 did neighbourhood
search because it was worker 3 — and nothing about who was researching what
survived a restart. These tests pin the three properties that changes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from forge.research.agents import (
    MAX_AGENTS,
    AgentError,
    AgentRegistry,
    AgentRole,
    AgentState,
    capacity_for,
    default_roles,
)


@pytest.fixture
def registry(tmp_path: Path) -> AgentRegistry:
    return AgentRegistry(tmp_path / "agents.db")


# ── capacity ─────────────────────────────────────────────────────────────────
def test_capacity_grants_what_was_asked_for_when_it_can() -> None:
    granted, note = capacity_for(requested=4, cpu_count=8)
    assert granted == 4
    assert note == ""


def test_capacity_refuses_to_pretend_compute_exists() -> None:
    """An interface showing 32 idle rows is worse than one that says 8."""
    granted, note = capacity_for(requested=32, cpu_count=2)
    assert granted == 4
    assert "32 agents were requested" in note
    assert "can serve 4" in note


def test_capacity_has_a_hard_ceiling() -> None:
    granted, _ = capacity_for(requested=10_000, cpu_count=1024)
    assert granted == MAX_AGENTS


def test_the_default_crew_leads_with_discovery_and_falsification() -> None:
    """A crew that only confirms produces findings and no objections."""
    roles = default_roles(3)
    assert roles[0] is AgentRole.DISCOVERY
    assert AgentRole.FALSIFICATION in roles
    assert default_roles(0) == ()


# ── agents ───────────────────────────────────────────────────────────────────
def test_an_agent_is_created_with_a_role_and_a_purpose(registry: AgentRegistry) -> None:
    agent = registry.create(campaign_id="c1", role=AgentRole.FALSIFICATION)
    assert agent.role is AgentRole.FALSIFICATION
    assert agent.state is AgentState.CREATED
    assert agent.objective  # never blank: the role's own purpose is the default
    assert agent.as_dict()["role_purpose"]


def test_deploying_reports_any_shortfall(registry: AgentRegistry) -> None:
    agents, note = registry.deploy(campaign_id="c1", count=10_000)
    assert len(agents) <= MAX_AGENTS
    assert note
    assert registry.counts("c1")["total"] == len(agents)


def test_agents_survive_a_restart(tmp_path: Path) -> None:
    first = AgentRegistry(tmp_path / "agents.db")
    agent = first.create(campaign_id="c1", role=AgentRole.DISCOVERY)
    first.set_state(agent.agent_id, AgentState.RUNNING, task="proposing a family")
    second = AgentRegistry(tmp_path / "agents.db")
    restored = second.require(agent.agent_id)
    assert restored.state is AgentState.RUNNING
    assert restored.current_task == "proposing a family"


def test_only_real_work_moves_the_progress_mark(registry: AgentRegistry) -> None:
    agent = registry.create(campaign_id="c1", role=AgentRole.DISCOVERY)
    registry.beat(agent.agent_id, task="thinking")
    assert registry.require(agent.agent_id).progress_at == ""
    registry.record(agent.agent_id, experiments=1, result="judged one candidate")
    assert registry.require(agent.agent_id).progress_at != ""


def test_an_agent_that_spends_its_budget_completes(registry: AgentRegistry) -> None:
    agent = registry.create(campaign_id="c1", role=AgentRole.DISCOVERY, compute_budget=2.0)
    registry.record(agent.agent_id, experiments=1, compute=2.0)
    done = registry.require(agent.agent_id)
    assert done.state is AgentState.COMPLETED
    assert done.exhausted() is True
    assert done not in registry.eligible("c1")


def test_a_missing_agent_is_refused_loudly(registry: AgentRegistry) -> None:
    with pytest.raises(AgentError, match="No agent"):
        registry.require("nope")


# ── claims ───────────────────────────────────────────────────────────────────
def test_two_agents_cannot_claim_the_same_hypothesis(registry: AgentRegistry) -> None:
    a = registry.create(campaign_id="c1", role=AgentRole.DISCOVERY)
    b = registry.create(campaign_id="c1", role=AgentRole.FALSIFICATION)
    assert registry.claim(campaign_id="c1", subject="H1", agent_id=a.agent_id) is not None
    assert registry.claim(campaign_id="c1", subject="H1", agent_id=b.agent_id) is None
    assert registry.holder("c1", "H1") == a.agent_id


def test_the_same_hypothesis_is_free_in_another_campaign(registry: AgentRegistry) -> None:
    """A claim bounds one programme's work, not the question itself."""
    a = registry.create(campaign_id="c1", role=AgentRole.DISCOVERY)
    b = registry.create(campaign_id="c2", role=AgentRole.DISCOVERY)
    assert registry.claim(campaign_id="c1", subject="H1", agent_id=a.agent_id) is not None
    assert registry.claim(campaign_id="c2", subject="H1", agent_id=b.agent_id) is not None


def test_a_deliberate_replica_is_allowed_and_recorded_as_one(registry: AgentRegistry) -> None:
    """Replication stays possible; it just stops being accidental."""
    a = registry.create(campaign_id="c1", role=AgentRole.DISCOVERY)
    b = registry.create(campaign_id="c1", role=AgentRole.ROBUSTNESS)
    original = registry.claim(campaign_id="c1", subject="H1", agent_id=a.agent_id)
    replica = registry.claim(
        campaign_id="c1", subject="H1", agent_id=b.agent_id, replica_of=original.claim_id
    )
    assert replica is not None
    assert replica.replica_of == original.claim_id
    # And it is on the record as a replica, so "we ran it twice" can never be
    # mistaken for two independent findings.
    assert any(c["replica_of"] for c in registry.claims("c1"))


def test_reclaiming_your_own_subject_renews_the_lease(registry: AgentRegistry) -> None:
    a = registry.create(campaign_id="c1", role=AgentRole.DISCOVERY)
    first = registry.claim(campaign_id="c1", subject="H1", agent_id=a.agent_id, lease_seconds=10)
    second = registry.claim(campaign_id="c1", subject="H1", agent_id=a.agent_id, lease_seconds=600)
    assert second is not None
    assert second.expires_at > first.expires_at


def test_a_lapsed_lease_releases_the_work(registry: AgentRegistry) -> None:
    """The point of a lease: an agent that dies does not hold research forever."""
    a = registry.create(campaign_id="c1", role=AgentRole.DISCOVERY)
    b = registry.create(campaign_id="c1", role=AgentRole.DISCOVERY)
    held = registry.claim(campaign_id="c1", subject="H1", agent_id=a.agent_id, lease_seconds=1)
    assert held is not None
    # Reach into the row rather than sleeping: the expiry is a timestamp and the
    # behaviour under test is the comparison, not the passage of time.
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat(timespec="seconds")
    with registry._connect() as db:
        db.execute("UPDATE claims SET expires_at=? WHERE claim_id=?", (past, held.claim_id))
    assert registry.holder("c1", "H1") is None
    assert registry.claim(campaign_id="c1", subject="H1", agent_id=b.agent_id) is not None


def test_releasing_expired_claims_is_safe_to_call_at_any_time(registry: AgentRegistry) -> None:
    a = registry.create(campaign_id="c1", role=AgentRole.DISCOVERY)
    registry.claim(campaign_id="c1", subject="H1", agent_id=a.agent_id, lease_seconds=3600)
    assert registry.release_expired("c1") == 0
    assert registry.holder("c1", "H1") == a.agent_id


def test_stopping_a_campaign_stops_its_agents_and_frees_its_claims(
    registry: AgentRegistry,
) -> None:
    agents, _ = registry.deploy(campaign_id="c1", count=3)
    registry.set_state(agents[0].agent_id, AgentState.RUNNING)
    registry.claim(campaign_id="c1", subject="H1", agent_id=agents[0].agent_id)
    registry.stop_all("c1")
    assert all(a.state is AgentState.STOPPED for a in registry.list("c1"))
    assert registry.claims("c1") == []


def test_counts_report_every_state_and_role(registry: AgentRegistry) -> None:
    registry.deploy(campaign_id="c1", count=2)
    counts = registry.counts("c1")
    assert set(counts["by_state"]) == {str(s) for s in AgentState}
    assert set(counts["by_role"]) == {str(r) for r in AgentRole}
    assert counts["total"] == 2
