"""Two campaigns, two windows, one engine.

The property this file exists to prove: a campaign that names dates gets
*those* bars, and a campaign that does not keeps the run's default window
exactly as before. The engine loaded once per run and handed every worker the
same `tail(max_bars)` slice, so this was not previously possible at all.

Driven through the real engine, the real campaign store and the real market
service on the synthetic dataset, for the reason the other engine tests give:
a test may not download vendor data and may not invent market data to stand in
for it.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from forge.research import ResearchLedger
from forge.strategy import FamilyRegistry, StrategyLibrary, TemplateStore
from forge.vault import Workspace
from forge_api.activity import ActivityLog, BacktestStore
from forge_api.campaigns import CampaignService
from forge_api.director import ResearchDirector
from forge_api.engine import AutonomousEngine, EngineConfig
from forge_api.market import MarketService

OBJECTIVE = (
    "Discover intraday alpha on NQ one-minute bars, preferring mechanisms that can be "
    "stated and falsified."
)


def _build(tmp_path: Path):
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
    engine.state.config = EngineConfig(dataset="synthetic", max_bars=6000, workers=1)
    return engine, service


@pytest.fixture
def built(tmp_path: Path):
    return _build(tmp_path)


def _reservoir(engine) -> tuple[datetime, datetime]:
    return engine.market.reservoir("synthetic")


def test_a_campaign_with_no_dates_keeps_the_default_window(built) -> None:
    """Every campaign configured before this change runs exactly as it did."""
    engine, service = built
    campaign = service.campaigns.create(
        name="No dates", objective=OBJECTIVE, dataset="synthetic"
    )
    engine.director.attach(campaign)
    assert engine.scope_for(0) is None


def test_a_campaign_with_dates_selects_that_window(built) -> None:
    """The field that was stored and read by nothing now decides the bars."""
    engine, service = built
    start, end = _reservoir(engine)
    half = start + (end - start) / 2
    campaign = service.campaigns.create(
        name="Recent half", objective=OBJECTIVE, dataset="synthetic",
        start_date=half.isoformat(), end_date=end.isoformat(),
    )
    engine.director.attach(campaign)

    scope = engine.scope_for(0)
    assert scope is not None
    assert scope.selected_start >= half
    assert scope.coverage < 0.75
    # And the reason is on the record, not inferred.
    assert campaign.name in scope.rationale


def test_the_engine_loads_the_selected_window_not_the_tail(built) -> None:
    engine, service = built
    start, end = _reservoir(engine)
    half = start + (end - start) / 2
    campaign = service.campaigns.create(
        name="Recent half", objective=OBJECTIVE, dataset="synthetic",
        start_date=half.isoformat(), end_date=end.isoformat(),
    )
    engine.director.attach(campaign)

    default_bars, dataset = engine.market.load("synthetic", limit=6000)
    scoped, scoped_dataset, _version = engine._bars_for(0, (default_bars, dataset))

    assert len(scoped) < len(default_bars), "the scope returned the whole default window"
    assert scoped[0].event_time >= half
    assert scoped_dataset.key == dataset.key


def test_two_campaigns_researching_different_windows_get_different_bars(built) -> None:
    """The whole point. One engine, one dataset, two temporal designs."""
    engine, service = built
    start, end = _reservoir(engine)
    third = (end - start) / 3

    early = service.campaigns.create(
        name="Early", objective=OBJECTIVE, dataset="synthetic",
        start_date=start.isoformat(), end_date=(start + third).isoformat(),
    )
    late = service.campaigns.create(
        name="Late", objective=OBJECTIVE, dataset="synthetic",
        start_date=(end - third).isoformat(), end_date=end.isoformat(),
    )

    engine.director.attach(early)
    early_scope = engine.scope_for(0)
    engine.director.attach(late)
    late_scope = engine.scope_for(0)

    assert early_scope.fingerprint() != late_scope.fingerprint()
    assert early_scope.selected_start < late_scope.selected_start

    default = engine.market.load("synthetic", limit=6000)
    engine.director.attach(early)
    early_bars, _, _ = engine._bars_for(0, default)
    engine.director.attach(late)
    late_bars, _, _ = engine._bars_for(0, default)

    assert early_bars[0].event_time < late_bars[0].event_time
    assert early_bars[-1].event_time < late_bars[-1].event_time


def test_the_same_window_is_loaded_once_and_shared(built) -> None:
    """Keyed by fingerprint, so two campaigns on the same window share one load."""
    engine, service = built
    start, end = _reservoir(engine)
    half = start + (end - start) / 2
    for name in ("A", "B"):
        campaign = service.campaigns.create(
            name=name, objective=OBJECTIVE, dataset="synthetic",
            start_date=half.isoformat(), end_date=end.isoformat(),
        )
        engine.director.attach(campaign)
        engine._bars_for(0, engine.market.load("synthetic", limit=6000))
    assert len(engine._scoped) == 1, "the identical window was loaded twice"


def test_an_unreadable_window_falls_back_and_says_so(built) -> None:
    """A window that cannot be served is not a reason to stop researching.

    It is a reason to use the run's window and record that it happened, which
    is the difference between a degraded cycle and a silent one.
    """
    engine, service = built
    start, end = _reservoir(engine)
    campaign = service.campaigns.create(
        name="Beyond", objective=OBJECTIVE, dataset="synthetic",
        start_date=start.isoformat(), end_date=end.isoformat(),
    )
    engine.director.attach(campaign)

    default_bars, dataset = engine.market.load("synthetic", limit=6000)

    def refuse(*_args, **_kwargs):
        raise RuntimeError("archive unavailable")

    engine.market.load_scope = refuse  # type: ignore[method-assign]
    bars, _, _ = engine._bars_for(0, (default_bars, dataset))
    assert bars is default_bars
    assert any(
        "could not be loaded" in event.message for event in engine.log.recent(20)
    ), "the fallback was silent"


def test_an_incoherent_date_range_falls_back_rather_than_refusing(built) -> None:
    """End before start is a misconfiguration, not a research emergency."""
    engine, service = built
    start, end = _reservoir(engine)
    campaign = service.campaigns.create(
        name="Backwards", objective=OBJECTIVE, dataset="synthetic",
        start_date=end.isoformat(), end_date=start.isoformat(),
    )
    engine.director.attach(campaign)
    assert engine.scope_for(0) is None


def test_a_range_reaching_before_the_archive_is_clamped_and_stays_visible(built) -> None:
    engine, service = built
    start, end = _reservoir(engine)
    campaign = service.campaigns.create(
        name="Too early", objective=OBJECTIVE, dataset="synthetic",
        start_date=(start - timedelta(days=4000)).isoformat(), end_date=end.isoformat(),
    )
    engine.director.attach(campaign)
    scope = engine.scope_for(0)
    assert scope is not None
    assert scope.selected_start >= start
    # Both ends are on the record, so the clamp is inspectable rather than lost.
    assert scope.available_start == start
