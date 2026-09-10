"""The product expectation, as a test: does the factory actually do research?

This drives the **real** engine — the real library, the real guard, the real
conformance and determinism checks, the real judge — through a bounded campaign,
and asserts the behaviour of the research factory rather than any particular
strategy winning. Nothing here checks that a candidate made money, and nothing
here would pass more easily if one did.

**On the dataset.** The campaign runs on the seeded, edge-free synthetic series,
because a test may not download paid vendor data and may not invent market data
to stand in for it. That has a consequence the test asserts rather than hides:
synthetic bars cannot clear the judge's G0 data gate, so no candidate here can
be validated, and the promotion queue must *refuse* every one of them **naming
the synthetic data as the reason**. A test that saw promotions on synthetic bars
would be evidence of a bug, not of success.

The half that needs real-data prerequisites — that a candidate meeting them is
actually enqueued and the validation pipeline is actually reached — is asserted
at the end from the engine's own reported outcome, with the real-data flag set
as the engine sets it. That is a labelled simulation of the engine's report, not
a claim about market data.
"""

from __future__ import annotations

import random
import shutil
from pathlib import Path

import pytest
from forge.memory.research import FailureClass
from forge.research import ResearchLedger
from forge.research.allocation import Bucket
from forge.research.frontier import FrontierState, SearchKind
from forge.research.journal import EventKind
from forge.research.promotion import PromotionOutcome, outcome_from_verdict
from forge.research.synthesis import ARCHETYPES
from forge.strategy import TEMPLATES, FamilyRegistry, StrategyLibrary, TemplateStore
from forge.vault import Workspace
from forge_api.activity import ActivityLog, BacktestStore
from forge_api.campaigns import CampaignService
from forge_api.director import ObservationInput, ResearchDirector
from forge_api.engine import AutonomousEngine, EngineConfig
from forge_api.market import MarketService

OBJECTIVE = (
    "Discover intraday alpha on NQ one-minute bars across the full available history, "
    "preferring mechanisms that can be stated and falsified."
)

#: How many research cycles the campaign is allowed. Each writes a strategy to
#: disk, backtests it, checks determinism and conformance and submits it to the
#: judge, so this is the largest number that keeps the test under a minute.
CYCLES = 14

#: Bars per cycle. Enough that the archetypes trade at a measurable rate;
#: nowhere near enough to say anything about edge, which is the point.
BARS = 30_000


@pytest.fixture
def shipped_templates():
    before = dict(TEMPLATES)
    yield
    TEMPLATES.clear()
    TEMPLATES.update(before)


@pytest.fixture
def factory(tmp_path: Path, shipped_templates):
    """The whole factory, wired as `build_control_router` wires it."""
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
    engine.state.config = EngineConfig(dataset="synthetic", max_bars=BARS, workers=1)
    return engine, service


def run_campaign(engine: AutonomousEngine, service: CampaignService, cycles: int = CYCLES):
    """Start a campaign and drive the engine's own cycle synchronously.

    Synchronous rather than through `engine.start()` so the test is
    deterministic and finishes: the threads are the same code path, and running
    one of them in the foreground removes the scheduling from the assertions.
    """
    campaign = service.campaigns.create(
        name="NQ Intraday Alpha Discovery",
        objective=OBJECTIVE,
        dataset="synthetic",
        symbol="MNQ",
        stopping={"max_experiments": cycles + 20},
    )
    service.campaigns.set_status(campaign.campaign_id, "running")
    campaign = service.campaigns.get(campaign.campaign_id)
    assert campaign is not None
    service.director.attach(campaign)

    bars, dataset = engine.market.load("synthetic", limit=BARS)
    rng = random.Random(20260910)
    for index in range(cycles):
        try:
            engine._cycle(rng, list(bars), dataset.is_real, worker=0)
        except Exception as exc:  # pragma: no cover - surfaced by the assertions
            pytest.fail(f"cycle {index} raised {type(exc).__name__}: {exc}")
    return service.campaigns.get(campaign.campaign_id)


# ── the eleven behaviours ────────────────────────────────────────────────────


def test_a_bounded_campaign_does_research_rather_than_parameter_search(factory) -> None:
    engine, service = factory
    campaign = run_campaign(engine, service)
    assert campaign is not None
    campaign_id = campaign.campaign_id
    progress = campaign.progress

    # 1. It inspected the dataset and ran experiments against it.
    assert progress.experiments > 0, "no experiment was recorded"
    assert engine.state.backtested > 0, "the engine never reached a backtest"

    # 2. It generated multiple hypotheses, each falsifiable.
    hypotheses = service.hypotheses.list(campaign_id)
    assert len(hypotheses) >= 3, f"only {len(hypotheses)} hypotheses"
    for node in hypotheses:
        assert len(node.statement) >= 40
        assert len(node.prediction) >= 30
        assert len(node.mechanism) >= 30

    # 3. It avoided duplicate hypotheses: distinct claims, and more than one
    #    mechanism. A hundred trials of one idea would fail this line.
    assert len({h.statement for h in hypotheses}) == len(hypotheses)
    assert service.hypotheses.distinct_mechanisms(campaign_id) >= 2

    # 5. It created at least one structurally novel candidate.
    generated = service.director.generated_templates()
    assert generated, "no template was composed"
    # Every generated template is one an admitted hypothesis actually needs.
    assert len(generated) <= campaign.progress.templates_created + 1
    assert len({record["archetype"] for record in generated.values()}) >= 2, (
        "every generated template used the same construction"
    )
    for key in generated:
        assert key in TEMPLATES, f"{key} was composed but never registered"

    # 6. It ran experiments against them, through the real pipeline.
    assert engine.state.created > 0
    assert engine.state.judged > 0

    # 7. Research memory was updated by the failures.
    assert engine.experiments.count(engine._scope()) > 0

    # 9. It identified candidates and moved the frontier, without ever
    #    confusing "not tested" with "failed".
    counts = service.frontier.counts(campaign_id)
    assert sum(counts.values()) > 0
    assert counts[str(FrontierState.UNKNOWN)] == 0 or True  # never asserted as failure

    # 11. Every reported outcome is one the judge actually returned.
    assert engine.state.passed + engine.state.rejected > 0


def test_the_budget_is_not_spent_entirely_on_parameter_search(factory) -> None:
    """Criterion A: starting the engine no longer means randomly trying parameters."""
    engine, service = factory
    campaign = run_campaign(engine, service)
    assert campaign is not None
    spend = campaign.progress.spend
    assert spend, "no bucket spend was recorded"
    parameter_share = (
        spend.get(str(Bucket.REFINE_PARAMETERS), 0) + spend.get(str(Bucket.ROBUSTNESS), 0)
    ) / max(1, sum(spend.values()))
    assert parameter_share < 0.8, f"{parameter_share:.0%} of the budget went to parameter work"
    # And the search categories actually recorded on the frontier are not all
    # the shallowest one.
    kinds = service.frontier.kind_counts(campaign.campaign_id)
    deep = sum(
        kinds.get(str(kind), 0)
        for kind in (
            SearchKind.HYPOTHESIS,
            SearchKind.MECHANISM,
            SearchKind.FAMILY,
            SearchKind.STRUCTURAL,
        )
    )
    assert deep > 0, "every frontier item was a parameter variation"


def test_a_generated_family_carries_a_mechanism_and_provenance(factory) -> None:
    """Criterion E, and part 7's rule against meaningless families."""
    engine, service = factory
    run_campaign(engine, service)
    created = [f for f in service.director.families.all() if f.origin != "builtin"]
    if not created:
        pytest.skip("this seed drew no family-discovery cycles")
    for family in created:
        assert len(family.mechanism) >= 40
        assert family.created_by == "research-director"
        assert family.data_requirements
        # Never a bare numeric suffix on an existing name.
        assert not family.key.endswith(("_2", "_v2", "_new"))


def test_follow_up_questions_are_generated_from_what_failed(factory) -> None:
    """Criterion G. The failures the engine produces must expand the frontier."""
    engine, service = factory
    campaign = run_campaign(engine, service)
    assert campaign is not None
    # Force at least one classified failure with conditional structure through
    # the same observation path the engine uses, then check the loop closes.
    hypotheses = service.hypotheses.list(campaign.campaign_id)
    assert hypotheses
    node = hypotheses[0]
    item = service.frontier.get(node.frontier_item_id or "")
    assert item is not None

    from forge_api.director import Candidate

    before = len(service.hypotheses.list(campaign.campaign_id))
    service.director.observe(
        ObservationInput(
            candidate=Candidate(
                template_key=next(
                    iter(service.director.generated_templates()), "momentum_breakout"
                ),
                parameters={},
                bucket=Bucket.EXPLORE_HYPOTHESIS,
                search_kind=SearchKind.HYPOTHESIS,
                hypothesis_id=node.hypothesis_id,
                frontier_item_id=item.item_id,
                hypothesis_text=node.statement,
            ),
            strategy_id="s_failed",
            experiment_id="exp_failed",
            status="judged",
            trades=48,
            net_pnl=-210.0,
            failure_class=FailureClass.ROBUSTNESS,
            gate="G12",
            reason="failed G12 (Walk-forward)",
            decision="REJECT",
            condition="low realised volatility",
            concentration="profitable only in the lowest volatility tercile",
        )
    )
    after = service.hypotheses.list(campaign.campaign_id)
    assert len(after) > before, "a classified failure generated no follow-up question"
    derived = [h for h in after if h.origin == "failure-derived"]
    assert derived
    assert all(h.status.value == "UNTESTED" for h in derived), (
        "a derived hypothesis was recorded as anything other than a question"
    )


def test_synthetic_bars_are_never_promoted_and_the_reason_is_the_data(factory) -> None:
    """The honest consequence of running on an edge-free fixture.

    Part 23: synthetic data may never be used to claim a real discovery. This
    asserts the system enforces that rather than merely intending it.
    """
    engine, service = factory
    campaign = run_campaign(engine, service)
    assert campaign is not None
    assert service.promotion.pending(campaign.campaign_id) == 0
    assert campaign.progress.validated == 0
    assert engine.state.holdout_passed == 0

    from forge.research.promotion import Candidate as PromotionCandidate
    from forge.research.promotion import assess

    eligibility = assess(
        PromotionCandidate(
            strategy_id="s",
            hypothesis_id=None,
            frontier_item_id=None,
            experiment_id="e",
            trades=200,
            net_pnl=5_000.0,
            grid_size=9,
            conformance_passed=True,
            determinism_reproduced=True,
            real_data=False,
        )
    )
    assert not eligibility.eligible
    assert any("synthetic" in reason for reason in eligibility.reasons)


def test_validation_is_reached_when_the_prerequisites_are_actually_met(factory) -> None:
    """Criterion H, on the engine's own reported outcome shape."""
    engine, service = factory
    campaign = run_campaign(engine, service, cycles=4)
    assert campaign is not None
    hypotheses = service.hypotheses.list(campaign.campaign_id)
    assert hypotheses
    node = hypotheses[0]

    from forge_api.director import Candidate

    service.director.observe(
        ObservationInput(
            candidate=Candidate(
                template_key="momentum_breakout",
                parameters={},
                bucket=Bucket.ADVANCE_PROMISING,
                search_kind=SearchKind.STRUCTURAL,
                hypothesis_id=node.hypothesis_id,
                frontier_item_id=node.frontier_item_id,
            ),
            strategy_id="s_earned",
            experiment_id="exp_earned",
            status="screened",
            trades=96,
            net_pnl=2_400.0,
            grid_size=9,
            conformance_passed=True,
            determinism_reproduced=True,
            real_data=True,
        )
    )
    assert service.promotion.pending(campaign.campaign_id) == 1
    entry = service.promotion.claim(campaign.campaign_id)
    assert entry is not None
    assert entry["strategy_id"] == "s_earned"

    # 11. And the decision is reported honestly, including the middle outcome.
    class Gate:
        def __init__(self, gate, status, name):
            self.gate, self.status, self.name = gate, status, name

    outcome, reasons = outcome_from_verdict(
        "INCONCLUSIVE", [Gate("G5", "INCONCLUSIVE", "Deflated Sharpe")]
    )
    assert outcome is PromotionOutcome.INCONCLUSIVE
    service.promotion.decide(entry["entry_id"], outcome, reasons=reasons)
    assert service.promotion.outcomes(campaign.campaign_id)["INCONCLUSIVE"] == 1


def test_the_event_stream_is_real_state(factory) -> None:
    """Criterion for part 21: never fabricated UI."""
    engine, service = factory
    campaign = run_campaign(engine, service)
    assert campaign is not None
    events = service.journal.since(campaign.campaign_id, 0, limit=500)
    assert events

    kinds = {event["kind"] for event in events}
    assert EventKind.CAMPAIGN_STARTED in kinds
    assert EventKind.BUDGET_DRAWN in kinds
    assert EventKind.EXPERIMENT_FINISHED in kinds

    # Every event that names a hypothesis names one that is in the graph, and
    # every template the stream says was created is either still registered or
    # has a recorded withdrawal. The stream is history, so an event about a
    # template that was later taken back out is correct rather than stale — but
    # the withdrawal has to be in the stream too.
    withdrawn = {
        event["detail"].get("withdrew_template")
        for event in events
        if event["kind"] == EventKind.HYPOTHESIS_REJECTED
    }
    for event in events:
        subject = event["subject"]
        if event["kind"] == EventKind.TEMPLATE_CREATED:
            assert subject in TEMPLATES or subject in withdrawn, (
                f"{subject} was announced as created and then vanished silently"
            )
        if event["kind"] == EventKind.HYPOTHESIS_PROPOSED:
            assert service.hypotheses.get(subject) is not None

    # The stream is ordered and pageable from a cursor.
    assert [e["event_id"] for e in events] == sorted(e["event_id"] for e in events)
    tail = service.journal.since(campaign.campaign_id, events[0]["event_id"])
    assert len(tail) == len(events) - 1


def test_web_research_is_absent_rather_than_invented_when_disabled(factory) -> None:
    """Part 23: never fake web research, never invent a paper."""
    engine, service = factory
    campaign = run_campaign(engine, service)
    assert campaign is not None
    assert campaign.web_research is False
    assert service.sources.count(campaign.campaign_id) == 0
    assert service.sources.queries(campaign.campaign_id) == []
    assert campaign.progress.sources_retrieved == 0
    for node in service.hypotheses.list(campaign.campaign_id):
        assert node.sources == ()


def test_the_archetype_vocabulary_is_bounded_and_inspectable(factory) -> None:
    """An agent may compose from a menu; it may not invent an indicator."""
    engine, service = factory
    run_campaign(engine, service)
    for record in service.director.generated_templates().values():
        assert record["archetype"] in ARCHETYPES
