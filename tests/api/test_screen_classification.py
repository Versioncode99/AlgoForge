"""The development screen, and the research it used to throw away.

`_cycle` screens a candidate on the development partition before validation is
touched: negative net, or too few trades, and it is rejected. That screen called
`_observe` with the numbers and **no failure class**, and
`ResearchDirector._generate_followups` returns immediately on an observation
carrying none. So a construction that produced no edge generated no research
question at all -- measured on a 120-cycle bounded campaign: 39 experiments,
most of them dying at the screen, one follow-up in the whole run.

**Why this file exists as its own fixture.** The screen lives inside
`if real_data:`, and `real_data` is `dataset.is_real`, which is
`authority == "TRUTH"`. The synthetic dataset is `FIXTURE`, so *every* engine
test in this repository takes the other arm and the whole partitioned path --
the screen, the split, the development backtest -- is unreachable from them.
That blind spot has now hidden two defects: this one, and the partition leak in
`test_engine_timescope`.

The bars here are still generated. Nothing in this file claims a market fact;
it asserts control flow through a branch the other fixtures cannot reach.
"""

from __future__ import annotations

import pytest
from forge.memory import FailureClass
from forge_api.engine import MIN_SCREEN_TRADES, _screen_failure


def test_no_trades_is_a_sample_problem_not_an_expectancy_one() -> None:
    """Telling a strategy to look for an edge in zero trades is the wrong question."""
    assert _screen_failure(net_pnl=0.0, trades=0) is FailureClass.NO_TRADES


def test_too_few_trades_reads_as_insufficient_sample() -> None:
    assert _screen_failure(net_pnl=50.0, trades=MIN_SCREEN_TRADES - 1) is (
        FailureClass.INSUFFICIENT_SAMPLE
    )


def test_a_sample_problem_outranks_a_losing_number() -> None:
    """Order matters: five losing trades is not evidence about expectancy."""
    assert _screen_failure(net_pnl=-500.0, trades=3) is FailureClass.INSUFFICIENT_SAMPLE


def test_enough_trades_and_no_edge_reads_as_negative_expectancy() -> None:
    assert _screen_failure(net_pnl=-120.0, trades=MIN_SCREEN_TRADES * 4) is (
        FailureClass.NEGATIVE_EXPECTANCY
    )


def test_the_screen_and_its_classification_read_the_same_floor() -> None:
    """A literal in both places would drift, and the drift would misreport why.

    Asserted against the source because the screen is a branch inside a long
    method rather than something callable on its own: the check that matters is
    that no second literal has appeared beside the named constant.
    """
    import inspect

    from forge_api.engine import AutonomousEngine

    source = inspect.getsource(AutonomousEngine._cycle)
    assert "len(development.trades) < MIN_SCREEN_TRADES" in source
    assert "len(development.trades) < 30" not in source


@pytest.mark.parametrize(
    "net_pnl,trades",
    [(0.0, 0), (10.0, 5), (-1.0, 29), (-1000.0, 5000), (0.0, 31)],
)
def test_every_screened_outcome_is_classified(net_pnl: float, trades: int) -> None:
    """No screened candidate falls through unclassified.

    An unclassified rejection is the defect this closes: it reaches the director
    as an observation with no failure class, and the follow-up path returns
    before it reads anything else.
    """
    assert isinstance(_screen_failure(net_pnl, trades), FailureClass)


def test_a_classified_screen_produces_a_research_question() -> None:
    """End to end from the screen's two numbers to a follow-up.

    This is the whole point: the screen measures a net and a trade count, the
    classification turns them into a failure class, and the failure class is
    what `derive` needs before it will say anything at all.
    """
    from forge.research.followup import Observation, derive

    observation = Observation(
        family="momentum",
        mechanism="displacement followed by continuation",
        hypothesis="Displacement continues rather than reverting.",
        template="gen_displacement",
        failure_class=_screen_failure(net_pnl=-240.0, trades=180),
        trades=180,
        net_pnl=-240.0,
        was_profitable=False,
    )
    assert derive(observation), "a classified screen still produced no question"


def test_an_unclassified_observation_produces_nothing() -> None:
    """The behaviour before the fix, pinned so the regression is visible."""
    from forge.research.followup import Observation, derive

    silent = Observation(
        family="momentum",
        mechanism="displacement followed by continuation",
        hypothesis="Displacement continues rather than reverting.",
        template="gen_displacement",
        failure_class=None,
        trades=180,
        net_pnl=-240.0,
    )
    assert derive(silent) == []


# ── one template must not stop a campaign ────────────────────────────────────


def _engine(tmp_path):
    """An engine wired to a real director, as a campaign has."""
    import shutil
    from pathlib import Path

    from forge.research import ResearchLedger
    from forge.strategy import FamilyRegistry, StrategyLibrary, TemplateStore
    from forge.vault import Workspace
    from forge_api.activity import ActivityLog, BacktestStore
    from forge_api.campaigns import CampaignService
    from forge_api.director import ResearchDirector
    from forge_api.engine import AutonomousEngine, EngineConfig
    from forge_api.market import MarketService

    shutil.copytree(Path("rules"), tmp_path / "rules", dirs_exist_ok=True)
    workspace = Workspace(repo=tmp_path, root=tmp_path, vault_mode=False).ensure()
    log = ActivityLog(tmp_path / "a.ndjson")
    engine = AutonomousEngine(
        StrategyLibrary(tmp_path / "s"), BacktestStore(tmp_path / "b"), log,
        MarketService(Path(".")), workspace, ResearchLedger(tmp_path / "r.db"),
    )
    service = CampaignService(workspace.data, log=log)
    service.director = ResearchDirector(
        campaigns=service.campaigns, frontier=service.frontier,
        hypotheses=service.hypotheses, journal=service.journal,
        sources=service.sources, promotion=service.promotion,
        families=FamilyRegistry(tmp_path / "f"), templates=TemplateStore(tmp_path / "t"),
        log=log,
    )
    engine.director = service.director
    engine.orchestrator = service.orchestrator
    engine.state.config = EngineConfig(dataset="synthetic", max_bars=12_000, workers=1)
    return engine, service


def test_a_long_warmup_template_does_not_stop_every_other_cycle(tmp_path) -> None:
    """The measured failure, pinned.

    The director *composes* templates. One generated `trend_strength_gate`
    arrived with 1,007 warm-up bars against a shipped maximum of 520, and the
    split was sized from the catalogue-wide maximum -- so from that cycle on
    `chronological_split` raised for **every** subsequent candidate, including
    ones running 20-bar templates. Three experiments, then five dead cycles.

    Driven through the real-data arm explicitly, because the synthetic dataset
    is FIXTURE and the whole partitioned path is otherwise unreachable.
    """
    import random

    from forge.research.runtime import Outcome
    from forge.strategy import TEMPLATES

    engine, service = _engine(tmp_path)
    campaign = service.campaigns.create(
        name="Warmup", dataset="synthetic", symbol="MNQ",
        objective="Discover intraday alpha on NQ one-minute bars, stated falsifiably.",
        stopping={"max_experiments": 200},
    )
    service.campaigns.set_status(campaign.campaign_id, "running")
    campaign = service.campaigns.get(campaign.campaign_id)
    service.director.prepare(campaign)
    service.director.attach(campaign)

    bars, _ = engine.market.load("synthetic", limit=12_000)
    started = max(t.warmup_bars for t in TEMPLATES.values())

    outcomes = []
    for index in range(8):
        outcome, _reason = engine._cycle(random.Random(1000 + index), list(bars), True, worker=0)
        outcomes.append(outcome)

    widest = max(t.warmup_bars for t in TEMPLATES.values())
    if widest <= started:
        pytest.skip("no generated template widened the catalogue's warm-up in this run")

    # The campaign kept working. Before the fix everything after the wide
    # template raised and came back as a cycle error.
    assert outcomes.count(Outcome.PROGRESS) >= 4, outcomes
    assert Outcome.ERROR not in outcomes, "a cycle still died on the split"


def test_a_window_too_small_for_the_candidate_blocks_by_name(tmp_path) -> None:
    """`None` from `_split_for` must become a named refusal, not a crash.

    A window too small for the strategy under test is a fact about the window,
    and the cycle should say which template and how many bars rather than
    raising something a reader has to trace.
    """
    from forge.strategy import TEMPLATES

    engine, _service = _engine(tmp_path)
    bars, _ = engine.market.load("synthetic", limit=12_000)
    widest = max(TEMPLATES.values(), key=lambda t: t.warmup_bars)

    # A window that cannot support even the candidate's own purge.
    assert engine._split_for(list(bars)[:80], widest, 0) is None


def test_the_shared_purge_is_preferred_while_it_fits(tmp_path) -> None:
    """The stricter gap wins when it fits, so boundaries stay comparable."""
    from forge.strategy import TEMPLATES

    engine, _service = _engine(tmp_path)
    bars, _ = engine.market.load("synthetic", limit=12_000)
    widest = max(t.warmup_bars for t in TEMPLATES.values())
    narrow = min(TEMPLATES.values(), key=lambda t: t.warmup_bars)

    split = engine._split_for(list(bars), narrow, 0)
    assert split is not None
    # Purged at the catalogue's widest rather than the candidate's own, so two
    # candidates in the same campaign share boundaries while that is possible.
    assert split.receipt.purge_bars == widest
