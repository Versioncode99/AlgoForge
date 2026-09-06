"""Isolated browser QA server. No mutations of the operator's strategy library."""

from pathlib import Path

import uvicorn
from fastapi.staticfiles import StaticFiles
from forge.strategy import StrategyLibrary
from forge_api import control, main
from forge_api.market import MarketService
from forge_api.settings_store import AISettings, Settings, SettingsStore

SOURCE = Path(__file__).resolve().parents[1]
QA = SOURCE / "artifacts" / "qa-agent-command"
QA.mkdir(parents=True, exist_ok=True)
main.ROOT = QA


class QAMarket(MarketService):
    def __init__(self, root: Path) -> None:
        super().__init__(SOURCE)


control.MarketService = QAMarket
SettingsStore(QA / "config" / "settings.json").save(Settings(ai=AISettings(enabled=False)))
library = StrategyLibrary(QA / "strategies")
if not library.list_specs():
    library.create_from_template("momentum_breakout", symbol="NQ")
app = main.create_app(QA / "data" / "qa.db")
app.mount("/", StaticFiles(directory=SOURCE / "apps" / "web" / "dist", html=True))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
