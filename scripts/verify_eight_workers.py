"""Bounded real-data engine smoke, using an isolated strategy/result directory."""

import json
import shutil
import time
from pathlib import Path

from forge.research import ResearchLedger
from forge.strategy import StrategyLibrary
from forge_api.activity import ActivityLog, BacktestStore
from forge_api.agent_service import AgentService
from forge_api.engine import AutonomousEngine, EngineConfig
from forge_api.market import MarketService
from forge_api.settings_store import SettingsStore

root = Path(__file__).resolve().parents[1]
target = root / "artifacts" / f"eight-worker-smoke-{int(time.time())}"
target.mkdir(parents=True)
shutil.copytree(root / "rules", target / "rules")
log = ActivityLog(target / "events.ndjson")
engine = AutonomousEngine(
    StrategyLibrary(target / "strategies"),
    BacktestStore(target / "backtests"),
    log,
    MarketService(root),
    target,
    ResearchLedger(target / "research.db"),
)
# Consume the proposal from the separate live model check, without making more calls.
agents = AgentService(
    root / "artifacts" / "model-smoke",
    log,
    SettingsStore(root / "artifacts" / "model-smoke" / "settings.json"),
)
for role in agents.snapshot()["roles"]:
    agents.control(role["id"], False)
engine.agents = agents
started = time.monotonic()
engine.start(EngineConfig(dataset="nq_1m_16y", max_bars=8000, workers=8, cycle_seconds=60))
deadline = started + 90
while time.monotonic() < deadline:
    if engine.state.cycles >= 8 or engine.state.last_error:
        break
    time.sleep(0.25)
engine.stop()
for worker in engine._threads:
    worker.join(30)
report = {
    "status": engine.status(),
    "elapsed_seconds": round(time.monotonic() - started, 2),
    "attempts": engine.experiments.recent(engine._scope()),
    "model_proposals": agents.snapshot()["proposals"],
    "note": "REAL_DATA development smoke only; not a profitability or holdout claim.",
}
(target / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(
    json.dumps(
        {
            "report": str(target / "report.json"),
            "status": report["status"],
            "attempt_count": len(report["attempts"]),
            "proposal_states": [p["status"] for p in report["model_proposals"]],
        },
        indent=2,
    )
)
