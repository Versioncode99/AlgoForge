from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from forge.agents import DebateReport, build_demo_debate
from forge.analytics import NormalAnalysis, build_normal_analysis
from forge.contracts.hashing import content_hash
from forge.contracts.models import ApiEnvelope, Preregistration, RunRecord
from forge.forgekeeper import ForgeKeeper, classify_candidate
from forge.judge import Judge, JudgeInput, Verdict
from forge.ledger import LedgerDatabase
from forge.prop import PropRuleSet, PropSimulation, load_rules, simulate_prop_paths
from forge.strategy import StrategyLibrary

from forge_api.activity import ActivityLog, BacktestStore
from forge_api.control import build_control_router
from forge_api.strategies import build_router

ROOT = Path(__file__).resolve().parents[3]


def seed_demo(ledger: LedgerDatabase) -> None:
    if ledger.list_runs():
        return
    frozen_at = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    prereg = Preregistration.freeze(
        "A session-aware momentum filter may improve risk-adjusted MNQ sample returns.",
        "Delayed continuation after an opening volatility shock.",
        "Reject when signed OOS Sharpe is non-positive or drawdown exceeds the declared limit.",
        frozen_at,
    )
    ledger.add_preregistration(prereg)
    ledger.add_run(
        RunRecord.create(
            prereg,
            "TRUTH_OOS",
            content_hash({"strategy": "demo_momentum", "version": 1}),
            content_hash({"fixture": "mnq_sample_v1"}),
            content_hash({"commission": 0.74, "slippage_ticks": 1}),
            "fixture-engine/1",
            frozen_at + timedelta(seconds=1),
        )
    )


def create_app(database_path: Path | None = None) -> FastAPI:
    db_path = database_path or Path(os.getenv("ALGOFORGE_DB_PATH", ROOT / "data" / "algoforge.db"))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ledger = LedgerDatabase(db_path)
        seed_demo(ledger)
        app.state.ledger = ledger
        yield
        ledger.close()

    app = FastAPI(
        title="AlgoForge API",
        version="0.1.0",
        description=(
            "Private paper-only API. Strategies are real code on disk under strategies/; "
            "backtests execute that code. No live trading path exists."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/health", response_model=ApiEnvelope[dict[str, object]])
    def health() -> ApiEnvelope[dict[str, object]]:
        engine = getattr(app.state, "engine", None)
        return ApiEnvelope(
            data={
                "status": "ok",
                "paper_only": True,
                "data_gate": "REAL",
                "engine_running": bool(engine and engine.state.running),
            }
        )

    @app.get("/api/v1/capabilities", response_model=ApiEnvelope[dict[str, object]])
    def capabilities() -> ApiEnvelope[dict[str, object]]:
        payload = json.loads((ROOT / "config" / "capabilities.json").read_text("utf-8"))
        return ApiEnvelope(data=payload)

    @app.get("/api/v1/runs", response_model=ApiEnvelope[list[RunRecord]])
    def runs() -> ApiEnvelope[list[RunRecord]]:
        items = app.state.ledger.list_runs()
        return ApiEnvelope(data=items, meta={"total": len(items)})

    @app.get("/api/v1/runs/{run_id}", response_model=ApiEnvelope[RunRecord])
    def run_detail(run_id: str) -> ApiEnvelope[RunRecord]:
        item = app.state.ledger.get_run(run_id)
        if item is None:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"})
        return ApiEnvelope(data=item)

    @app.get("/api/v1/verdicts/{run_id}", response_model=ApiEnvelope[Verdict])
    def verdict(run_id: str) -> ApiEnvelope[Verdict]:
        item = app.state.ledger.get_run(run_id)
        if item is None:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"})
        demo_pnl = (80, -25, 95, -30, 70, -20, 110, -35, 60, 45, -15, 85) * 3
        result = Judge().evaluate(
            JudgeInput(
                run_id=item.run_id,
                tier=item.tier,
                pnl=demo_pnl,
                trial_count=4,
                data_gate_passed=True,
                preregistered=True,
                implementation_tests_passed=True,
            )
        )
        return ApiEnvelope(data=result)

    @app.get("/api/v1/analysis/{run_id}", response_model=ApiEnvelope[NormalAnalysis])
    def analysis(run_id: str) -> ApiEnvelope[NormalAnalysis]:
        item = app.state.ledger.get_run(run_id)
        if item is None:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"})
        demo_pnl = (80, -25, 95, -30, 70, -20, 110, -35, 60, 45, -15, 85) * 3
        judged = Judge().evaluate(
            JudgeInput(
                run_id=item.run_id,
                tier=item.tier,
                pnl=demo_pnl,
                trial_count=4,
                data_gate_passed=True,
                preregistered=True,
                implementation_tests_passed=True,
            )
        )
        return ApiEnvelope(data=build_normal_analysis(item.run_id, judged, demo_pnl))

    @app.get("/api/v1/prop/rules", response_model=ApiEnvelope[list[PropRuleSet]])
    def prop_rules() -> ApiEnvelope[list[PropRuleSet]]:
        items = load_rules(ROOT / "rules")
        return ApiEnvelope(data=items, meta={"total": len(items), "runnable": 0})

    @app.get("/api/v1/prop/simulations/{run_id}", response_model=ApiEnvelope[PropSimulation])
    def prop_simulation(run_id: str, rule_id: str) -> ApiEnvelope[PropSimulation]:
        run = app.state.ledger.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"})
        if run.tier not in {"TRUTH_OOS", "FORWARD"}:
            raise HTTPException(status_code=422, detail={"code": "truth_or_forward_required"})
        rules = {rule.rule_id: rule for rule in load_rules(ROOT / "rules")}
        rule = rules.get(rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail={"code": "rule_not_found"})
        # A prop evaluation cannot be estimated from a handful of days, so the
        # fixture carries a realistic-length daily series.
        demo_pnl = tuple(float(v) for v in np.random.default_rng(20260901).normal(35.0, 320.0, 90))
        result = simulate_prop_paths(run.run_id, rule, demo_pnl, paths=300, allow_unverified=True)
        return ApiEnvelope(data=result, meta={"rule_locked": True, "research_override": True})

    @app.get("/api/v1/agents/{run_id}", response_model=ApiEnvelope[DebateReport])
    def agents(run_id: str) -> ApiEnvelope[DebateReport]:
        item = app.state.ledger.get_run(run_id)
        if item is None:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"})
        judged = Judge().evaluate(
            JudgeInput(
                run_id=item.run_id,
                tier=item.tier,
                pnl=(80, -25, 95, -30, 70, -20, 110, -35, 60, 45, -15, 85) * 3,
                trial_count=4,
                data_gate_passed=True,
                preregistered=True,
                implementation_tests_passed=True,
            )
        )
        return ApiEnvelope(
            data=build_demo_debate(item.run_id, judged.verdict_id),
            meta={"narrative_can_change_verdict": False},
        )

    @app.get("/api/v1/evolution/overview", response_model=ApiEnvelope[dict[str, object]])
    def evolution_overview() -> ApiEnvelope[dict[str, object]]:
        keeper = ForgeKeeper(ROOT / "data" / "forgekeeper")
        candidate = classify_candidate(
            "graficogit/mnq-1450-strategy",
            "DISCOVERED_UNVERIFIED",
            None,
            "https://github.com/graficogit/mnq-1450-strategy",
            ("MNQ research reference",),
        )
        return ApiEnvelope(
            data={**keeper.overview(), "candidate": candidate.model_dump()},
            meta={"research_intake_only": True, "human_activation_required": True},
        )

    library = StrategyLibrary(ROOT / "strategies")
    store = BacktestStore(ROOT / "data" / "backtests")
    log = ActivityLog(ROOT / "data" / "runtime" / "activity.ndjson")
    control_router, engine, market = build_control_router(ROOT, library, store, log)

    app.state.engine = engine
    app.include_router(build_router(ROOT, library, store, log, market))
    app.include_router(control_router)

    # Serve the built interface from the API itself. The desktop shell then loads
    # a same-origin page, so there is no CORS surface and no separate web server
    # to keep alive. Mounted last so it never shadows an API route.
    web_dist = ROOT / "apps" / "web" / "dist"
    if (web_dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="ui")

    return app


app = create_app()


def run() -> None:
    uvicorn.run("forge_api.main:app", host="127.0.0.1", port=8765, reload=False)
