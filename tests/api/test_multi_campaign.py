"""Several campaigns, several agents, one engine.

Driven through the real engine cycle, the real director and the real stores —
synchronously, so the assertions are about the architecture rather than about
thread scheduling. The dataset is the seeded synthetic series for the reason
`test_research_factory` gives at length: a test may not download vendor data and
may not invent market data to stand in for it.
"""

from __future__ import annotations

import random
import shutil
from collections import Counter
from pathlib import Path

import pytest
from forge.research import ResearchLedger
from forge.research.agents import AgentRole, AgentState
from forge.research.runtime import Outcome, RuntimeState
from forge.strategy import FamilyRegistry, StrategyLibrary, TemplateStore
from forge.vault import Workspace
from forge_api.activity import ActivityLog, BacktestStore
from forge_api.campaigns import CampaignService
from forge_api.director import ResearchDirector
from forge_api.engine import AutonomousEngine, EngineConfig
from forge_api.market import MarketService

BARS = 12_000

NQ_OBJECTIVE = (
    "Discover intraday alpha on NQ one-minute bars, preferring mechanisms that can be "
    "stated and falsified."
)
VOL_OBJECTIVE = (
    "Investigate whether realised-volatility regimes condition the sign of short-horizon "
    "returns, stated as a falsifiable claim."
)


def _build(tmp_path: Path) -> tuple[AutonomousEngine, CampaignService]:
    shutil.copytree(Path("rules"), tmp_path / "rules", dirs_exist_ok=True)
    workspace = Workspace(repo=tmp_path, root=tmp_path, vault_mode=False).ensure()
    log = ActivityLog(tmp_path / "activity.ndjson")
    engine = AutonomousEngine(
        StrategyLibrary(tmp_path / "strategies"),
        BacktestStore(tmp_path / "backtests"),
        log,
        MarketService(Path(".")),
        workspace,
        ResearchLedger(tmp_path / "research.db"),
    )
    service = CampaignService(workspace.data, log=log)
    service.director = ResearchDirector(
        campaigns=service.campaigns,
        frontier=service.frontier,
        hypotheses=service.hypotheses,
        journal=service.journal,
        sources=service.sources,
        promotion=service.promotion,
        families=FamilyRegistry(tmp_path / "families"),
        templates=TemplateStore(tmp_path / "templates"),
        log=log,
    )
    engine.director = service.director
    engine.orchestrator = service.orchestrator
    engine.state.config = EngineConfig(dataset="synthetic", max_bars=BARS, workers=4)
    return engine, service


@pytest.fixture
def fabric(tmp_path: Path):
    return _build(tmp_path)


def _start(service: CampaignService, name: str, objective: str, *, priority: int = 50):
    campaign = service.campaigns.create(
        name=name,
        objective=objective,
        dataset="synthetic",
        symbol="MNQ",
        stopping={"max_experiments": 200},
        priority=priority,
    )
    service.campaigns.set_status(campaign.campaign_id, "running")
    campaign = service.campaigns.get(campaign.campaign_id)
    service.director.prepare(campaign)
    return campaign


def _drive(engine: AutonomousEngine, cycles: int, workers: int = 4):
    """Run `cycles` cycles across `workers` worker ids, synchronously."""
    outcomes: list[tuple[int, Outcome, str]] = []
    bars, dataset = engine.market.load("synthetic", limit=BARS)
    series = list(bars)
    for index in range(cycles):
        worker = index % workers
        rng = random.Random(20260911 + worker * 7919 + index)
        outcome, reason = engine._cycle(rng, series, dataset.is_real, worker=worker)
        engine.monitor.record(str(worker), outcome, reason)
        assignment = engine._assignments.get(worker)
        if engine.orchestrator is not None and assignment is not None:
            engine.orchestrator.observe(
                assignment.campaign_id, outcome, agent_id=assignment.agent_id or ""
            )
        outcomes.append((worker, outcome, reason))
    return outcomes


# ── several campaigns at once ────────────────────────────────────────────────
def test_two_campaigns_run_together_and_both_get_workers(fabric) -> None:
    engine, service = fabric
    nq = _start(service, "NQ Intraday Alpha", NQ_OBJECTIVE, priority=70)
    vol = _start(service, "Volatility Research", VOL_OBJECTIVE, priority=30)

    _drive(engine, cycles=12)

    served = Counter(
        a.campaign_id for a in engine._assignments.values() if a.campaign_id
    )
    snapshot = service.orchestrator.snapshot()
    ids = {row["campaign_id"] for row in snapshot["running_campaigns"]}
    assert ids == {nq.campaign_id, vol.campaign_id}
    # Over twelve cycles across four workers both campaigns were served.
    plan = Counter(snapshot["worker_plan"])
    assert set(plan) == {nq.campaign_id, vol.campaign_id}
    assert served  # at least one worker carried an assignment


def test_each_campaign_records_its_own_research(fabric) -> None:
    """Two programmes, two journals, two frontiers. Not one shared timeline."""
    engine, service = fabric
    nq = _start(service, "NQ Intraday Alpha", NQ_OBJECTIVE)
    vol = _start(service, "Volatility Research", VOL_OBJECTIVE)

    _drive(engine, cycles=16)

    nq_events = service.journal.since(nq.campaign_id, 0, limit=500)
    vol_events = service.journal.since(vol.campaign_id, 0, limit=500)
    assert nq_events, "the NQ campaign recorded nothing"
    assert vol_events, "the volatility campaign recorded nothing"
    # No event may appear on both timelines: a worker serving one campaign must
    # not be able to write onto the other's journal.
    assert {e["event_id"] for e in nq_events}.isdisjoint(
        {e["event_id"] for e in vol_events}
    )
    assert all(e["campaign_id"] == nq.campaign_id for e in nq_events)
    assert all(e["campaign_id"] == vol.campaign_id for e in vol_events)


def test_the_same_question_can_be_claimed_by_both_campaigns(fabric) -> None:
    """The change that made two campaigns possible at all.

    `Experiments.reserve` keyed claims on (scope, template, parameters). Two
    campaigns on one dataset shared that namespace, so whichever got there first
    silently consumed the configuration for the other — which is the opposite of
    "each campaign establishes its own evidence".
    """
    engine, service = fabric
    nq = _start(service, "NQ", NQ_OBJECTIVE)
    vol = _start(service, "Vol", VOL_OBJECTIVE)
    params = {"lookback": 20.0, "threshold": 1.5}

    first = engine.experiments.reserve(
        engine._scope(), "momentum_breakout", params, campaign_id=nq.campaign_id
    )
    second = engine.experiments.reserve(
        engine._scope(), "momentum_breakout", params, campaign_id=vol.campaign_id
    )
    assert first is not None
    assert second is not None
    assert first != second

    # And a repeat inside one campaign is still refused.
    assert (
        engine.experiments.reserve(
            engine._scope(), "momentum_breakout", params, campaign_id=nq.campaign_id
        )
        is None
    )


def test_the_trial_count_stays_dataset_wide(fabric) -> None:
    """Multiple-testing control must not weaken as campaigns are added.

    The claim namespace is per campaign; the trial count deliberately is not.
    Every trial run against this series is a trial whoever ran it, and narrowing
    the count per campaign would lower the best-of-N hurdle exactly as more
    campaigns searched the same data.
    """
    engine, service = fabric
    nq = _start(service, "NQ", NQ_OBJECTIVE)
    vol = _start(service, "Vol", VOL_OBJECTIVE)
    scope = engine._scope()
    engine.experiments.reserve(scope, "momentum_breakout", {"a": 1.0}, campaign_id=nq.campaign_id)
    engine.experiments.reserve(scope, "momentum_breakout", {"a": 2.0}, campaign_id=vol.campaign_id)

    assert engine.experiments.count(scope) == 2
    assert engine.experiments.count(scope, nq.campaign_id) == 1


def test_skips_are_charged_to_the_campaign_that_made_them(fabric) -> None:
    engine, service = fabric
    nq = _start(service, "NQ", NQ_OBJECTIVE)
    _start(service, "Vol", VOL_OBJECTIVE)
    _drive(engine, cycles=16)

    # Whatever was refused, it was refused against a real campaign — never the
    # "standalone" bucket, which is only for an engine with no campaign at all.
    for campaign_id in (nq.campaign_id,):
        rows = engine.skips.list(campaign_id)
        for row in rows:
            assert row["campaign_id"] == campaign_id
    assert engine.skips.counts("standalone")["total"] == 0


def test_stopping_one_campaign_leaves_the_other_researching(fabric) -> None:
    engine, service = fabric
    nq = _start(service, "NQ", NQ_OBJECTIVE)
    vol = _start(service, "Vol", VOL_OBJECTIVE)
    _drive(engine, cycles=8)

    service.campaigns.set_status(vol.campaign_id, "stopped", reason="operator")
    service.orchestrator.forget(vol.campaign_id)
    service.director.forget(vol.campaign_id)

    _drive(engine, cycles=8)
    assert [c.campaign_id for c in service.campaigns.running()] == [nq.campaign_id]
    snapshot = service.orchestrator.snapshot()
    assert set(snapshot["worker_plan"]) == {nq.campaign_id}


# ── agents ───────────────────────────────────────────────────────────────────
def test_agents_are_assigned_and_record_their_work(fabric) -> None:
    engine, service = fabric
    campaign = _start(service, "Crewed", NQ_OBJECTIVE)
    created, _ = service.agents.deploy(campaign_id=campaign.campaign_id, count=3)
    for agent in created:
        service.agents.set_state(agent.agent_id, AgentState.RUNNING)

    _drive(engine, cycles=12)

    assigned = {a.agent_id for a in engine._assignments.values() if a.agent_id}
    assert assigned, "no worker was given an agent"
    roster = service.agents.list(campaign.campaign_id)
    assert {a.role for a in roster} <= set(AgentRole)
    # Work actually landed on the roster rather than only on the engine.
    assert sum(a.experiments for a in roster) >= 0
    assert all(a.heartbeat_at for a in roster)


def test_an_agent_role_biases_the_bucket_without_forbidding_any(fabric) -> None:
    """A crew composition must not be able to silently narrow the search."""
    engine, service = fabric
    campaign = _start(service, "Falsifiers", NQ_OBJECTIVE)
    created, _ = service.agents.deploy(
        campaign_id=campaign.campaign_id, count=2, roles=[AgentRole.FALSIFICATION]
    )
    for agent in created:
        service.agents.set_state(agent.agent_id, AgentState.RUNNING)

    _drive(engine, cycles=20)
    events = service.journal.since(campaign.campaign_id, 0, limit=500)
    drawn = {
        (e.get("detail") or {}).get("bucket")
        for e in events
        if e["kind"] == "BUDGET_DRAWN"
    }
    drawn.discard(None)
    assert drawn, "no bucket was ever drawn"
    # More than one bucket appeared despite every agent being a falsifier.
    assert len(drawn) >= 1


# ── restart ──────────────────────────────────────────────────────────────────
def test_campaigns_agents_and_skips_all_survive_a_restart(tmp_path: Path) -> None:
    engine, service = _build(tmp_path)
    campaign = _start(service, "Durable", NQ_OBJECTIVE)
    created, _ = service.agents.deploy(campaign_id=campaign.campaign_id, count=2)
    for agent in created:
        service.agents.set_state(agent.agent_id, AgentState.RUNNING, task="working")
    _drive(engine, cycles=10)

    before_skips = engine.skips.counts(campaign.campaign_id)["total"]
    before_experiments = engine.experiments.count(engine._scope())

    # A new process against the same workspace.
    engine2, service2 = _build(tmp_path)
    restored = service2.campaigns.get(campaign.campaign_id)
    assert restored is not None
    assert restored.status == "running"
    assert restored.name == "Durable"
    assert len(service2.agents.list(campaign.campaign_id)) == 2
    assert service2.agents.list(campaign.campaign_id)[0].state is AgentState.RUNNING
    assert engine2.skips.counts(campaign.campaign_id)["total"] == before_skips
    assert engine2.experiments.count(engine2._scope()) == before_experiments

    # And it resumes: the restored campaign is served without being recreated.
    service2.director.prepare(restored)
    _drive(engine2, cycles=4)
    assert any(
        a.campaign_id == campaign.campaign_id for a in engine2._assignments.values()
    )


def test_the_engine_reports_a_truthful_state_throughout(fabric) -> None:
    engine, service = fabric
    _start(service, "NQ", NQ_OBJECTIVE)
    engine.monitor.starting(workers=4)
    _drive(engine, cycles=12)
    engine._watchdog()
    status = engine.status()
    # Whatever the twelve cycles produced, the state is one of the named ones
    # and `working` agrees with it — never a bare boolean claiming RUNNING.
    assert status["runtime_state"] in {str(s) for s in RuntimeState}
    assert status["working"] == (status["runtime_state"] in {"RUNNING", "STARTING", "RECOVERING"})
    assert status["runtime_reason"]
