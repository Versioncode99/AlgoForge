"""One isolated live provider check; never changes operator settings or strategies."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

from forge_api.activity import ActivityLog
from forge_api.agent_service import AgentService
from forge_api.providers import credential_for
from forge_api.settings_store import SettingsStore

root = Path(__file__).resolve().parents[1]
target = root / "artifacts" / "model-smoke"
target.mkdir(parents=True, exist_ok=True)
if not credential_for().present:
    print("MODEL_CHECK=UNAVAILABLE: credential absent")
    raise SystemExit(0)
settings = SettingsStore(target / "settings.json")
current = SettingsStore(root / "config" / "settings.json").load()
current.ai.enabled = True
settings.save(current)
service = AgentService(target, ActivityLog(target / "events.ndjson"), settings)
started = time.monotonic()
result = service._run(
    "strategy_code",
    "Propose exactly one bounded experiment using vol_normalized_momentum and its default "
    "parameters, citing tsmom. Describe an intraday adaptation and a falsifiable null, "
    "without claiming profitability or replication. Return the required proposal JSON.",
    SimpleNamespace(job_id="live-provider-check"),
)
report = {
    "result": result,
    "elapsed_seconds": round(time.monotonic() - started, 2),
    "proposals": service.snapshot()["proposals"],
}
(target / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
