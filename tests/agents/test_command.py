from __future__ import annotations

import json
import random
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from forge.research import ResearchLedger
from forge.research.sources import ResearchLibrary
from forge.strategy import StrategyLibrary, generate_bars, run_backtest
from forge.vault import Workspace
from forge_api.activity import ActivityLog, BacktestStore
from forge_api.agent_service import AgentService
from forge_api.engine import AutonomousEngine, EngineConfig
from forge_api.experiments import Experiments
from forge_api.market import MarketService
from forge_api.settings_store import AISettings, Settings, SettingsStore


@pytest.fixture
def service(tmp_path):
    settings = SettingsStore(tmp_path / "settings.json")
    settings.save(Settings(ai=AISettings(enabled=False)))
    return AgentService(tmp_path, ActivityLog(tmp_path / "events.ndjson"), settings)


def test_scholar_search_retains_provenance_and_deduplicates(tmp_path):
    def handler(request):
        assert request.url.host == "api.crossref.org"
        assert request.url.params["rows"] == "8"
        return httpx.Response(
            200,
            json={
                "message": {
                    "items": [
                        {
                            "DOI": "10.1/test",
                            "title": ["Momentum test"],
                            "abstract": "<p>Evidence</p>",
                            "author": [{"family": "Researcher"}],
                            "published": {"date-parts": [[2024]]},
                        }
                    ]
                }
            },
        )

    library = ResearchLibrary(tmp_path / "papers.db")
    first = library.search("momentum", transport=httpx.MockTransport(handler))
    library.search("momentum", transport=httpx.MockTransport(handler))
    assert len(library.list()) == 9
    assert first[0]["evidence"] == "UNREVIEWED"
    assert first[0]["content_level"] == "abstract"
    assert first[0]["url"] == "https://doi.org/10.1/test"


def test_proposals_reject_bad_parameters_and_fabricated_sources(service):
    raw = {
        "template": "vol_normalized_momentum",
        "parameters": {"lookback": 120},
        "hypothesis": "Test normalized momentum after costs with a fixed horizon and null.",
        "source_ids": ["tsmom"],
    }
    proposal = service.propose(raw, {"tsmom"})
    assert service.take_proposal()["id"] == proposal["id"]
    assert service.take_proposal() is None
    for value in [float("nan"), float("inf"), 900, 121]:
        with pytest.raises(ValueError):
            service.propose({**raw, "parameters": {"lookback": value}}, {"tsmom"})
    with pytest.raises(ValueError):
        service.propose({**raw, "source_ids": ["invented"]}, {"tsmom"})


def test_roles_work_and_pause_without_a_model(service):
    # Nine registered roles; eight of them take a direct assignment. The
    # orchestrator is the ninth and is deliberately not one of them — it is given
    # an objective and runs a mission, so submitting a task to it is refused.
    snapshot = service.snapshot()
    assert len(snapshot["roles"]) == 9
    assert snapshot["orchestrator"]["id"] == "orchestrator"
    assert len(snapshot["specialists"]) == 8
    assert "orchestrator" not in {role["id"] for role in snapshot["specialists"]}
    assert len(service.roles()) == 8
    assert "orchestrator" not in service.roles()
    with pytest.raises(ValueError, match="mission"):
        service.submit("orchestrator")
    service.control("risk", False)
    with pytest.raises(ValueError, match="paused"):
        service.submit("risk")
    service.control("risk", True)
    job = service.submit("risk")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not service.snapshot()["tasks"]:
        time.sleep(0.01)
    task = service.snapshot()["tasks"][0]
    assert task["id"] == job["job_id"]
    assert task["mode"] == "deterministic"
    assert task["status"] == "completed"


def test_model_proposal_is_structured_and_never_executes_code(service, monkeypatch):
    import forge_api.agent_service as module

    service.settings.save(Settings(ai=AISettings(enabled=True)))
    monkeypatch.setattr(module, "credential_for", lambda: SimpleNamespace(present=True))
    raw = {
        "summary": "Test one bounded variant",
        "source_ids": ["tsmom"],
        "proposal": {
            "template": "vol_normalized_momentum",
            "parameters": {"lookback": 120},
            "hypothesis": "Normalized returns may isolate persistent futures flow after costs.",
            "source_ids": ["tsmom"],
        },
    }
    monkeypatch.setattr(
        module,
        "client_for",
        lambda: SimpleNamespace(
            chat=lambda **kwargs: {"answer": json.dumps(raw), "model": "test-model"}
        ),
    )
    result = service._run("strategy_code", "propose", SimpleNamespace(job_id="test-task"))
    assert result["status"] == "completed"
    assert result["mode"] == "model"
    assert service.take_proposal()["template"] == "vol_normalized_momentum"


def test_attempt_reservations_are_atomic_and_survive_restart(tmp_path):
    path = tmp_path / "attempts.db"
    attempts = Experiments(path)
    assert attempts.reserve("data1", "momentum", {"n": 10})
    assert Experiments(path).reserve("data1", "momentum", {"n": 10}) is None
    assert attempts.reserve("data2", "momentum", {"n": 10})


@pytest.fixture
def engine(tmp_path):
    shutil.copytree(Path("rules"), tmp_path / "rules")
    return AutonomousEngine(
        StrategyLibrary(tmp_path / "strategies"),
        BacktestStore(tmp_path / "backtests"),
        ActivityLog(tmp_path / "events.ndjson"),
        MarketService(tmp_path),
        Workspace(repo=tmp_path, root=tmp_path, vault_mode=False).ensure(),
        ResearchLedger(tmp_path / "research.db"),
    )


def test_restart_cannot_clear_a_stop_while_old_workers_are_alive(engine, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def cycle(*args):
        entered.set()
        release.wait(3)

    monkeypatch.setattr(engine, "_cycle", cycle)
    engine.start(EngineConfig(dataset="synthetic", workers=1, cycle_seconds=0.01))
    assert entered.wait(2)
    original = engine._threads[0]
    engine.stop()
    engine.start(EngineConfig(dataset="synthetic", workers=8))
    assert engine._threads == [original]
    assert engine._stop.is_set()
    release.set()
    original.join(3)
    assert not original.is_alive()


def test_pruning_keeps_in_flight_candidates(engine):
    engine.state.config = EngineConfig(max_strategies=1)
    candidate = engine.library.create_from_template("momentum_breakout")
    engine._active[0] = candidate.strategy_id
    engine._make_room()
    assert engine.library.get_spec(candidate.strategy_id)


def test_research_policies_preserve_actual_parameters(engine):
    engine.state.config = EngineConfig(dataset="synthetic")
    engine._cycle(random.Random(123), generate_bars(count=1600), False, 1)
    candidate = engine.library.list_specs()[0]
    attempt = engine.experiments.recent(engine._scope())[0]
    assert candidate.defaults == attempt["parameters"]


@pytest.mark.parametrize(
    "symbol,multiplier,tick", [("MNQ", 2, 0.25), ("NQ", 20, 0.25), ("GC", 100, 0.1)]
)
def test_contract_pnl_and_final_position_settlement(tmp_path, symbol, multiplier, tick):
    library = StrategyLibrary(tmp_path / "strategies")
    spec = library.create_from_template("momentum_breakout", symbol=symbol)
    module = SimpleNamespace(entry_signal=lambda w, p: 1, exit_signal=lambda w, p, pos: None)
    bars = [b.model_copy(update={"symbol": symbol}) for b in generate_bars(count=120)]
    result = run_backtest(module, spec, bars)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "end_of_data"
    assert trade.gross_pnl == pytest.approx(
        (trade.exit_price - trade.entry_price) * multiplier, abs=1e-6
    )
    assert trade.costs == pytest.approx(
        2 * (spec.commission_per_side + spec.slippage_ticks * tick * multiplier)
    )
    assert result.calculation_version == "contract-units-v2"
    assert result.lookahead_clean


def test_deleted_strategy_ids_are_never_reused_and_paths_stay_inside(tmp_path):
    library = StrategyLibrary(tmp_path / "strategies")
    first = library.create_from_template("momentum_breakout")
    library.delete(first.strategy_id)
    assert library.create_from_template("momentum_breakout").strategy_id != first.strategy_id
    with pytest.raises(KeyError):
        library.dir_for("../outside")
