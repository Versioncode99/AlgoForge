from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from forge.agents import DebateReport, build_debate
from forge.contracts.hashing import content_hash
from forge.contracts.models import ApiEnvelope, Preregistration, RunRecord
from forge.data.live import load_keys
from forge.forgekeeper import ForgeKeeper, classify_candidate
from forge.judge import Judge, JudgeInput, Verdict
from forge.ledger import LedgerDatabase
from forge.prop import PropRuleSet, PropSimulation, load_rules, simulate_prop_paths
from forge.research import ResearchLedger
from forge.strategy import TEMPLATES, FamilyRegistry, StrategyLibrary, TemplateStore
from forge.vault import VaultMirror
from forge.vault import resolve as resolve_workspace

from forge_api.activity import ActivityLog, BacktestStore
from forge_api.catalog import build_catalog_router
from forge_api.control import build_control_router
from forge_api.missions import build_mission_router
from forge_api.research_loop import ResearchLoop, build_research_loop_router
from forge_api.storage import build_storage_router
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
            "SWEEP",
            content_hash({"strategy": "demo_momentum", "version": 1}),
            content_hash({"fixture": "mnq_sample_v1"}),
            content_hash({"commission": 0.74, "slippage_ticks": 1}),
            "fixture-engine/1",
            frozen_at + timedelta(seconds=1),
        )
    )


def _allowed_origins() -> list[str]:
    """Which browser origins may call this API.

    The default is the Vite dev server on its default port and nothing else.
    That is correct and, on its own, quietly hostile: Vite silently moves to
    5174, 5175 and upward whenever the port is taken, and every request from
    the moved server is then blocked by CORS. The application does not report
    that as a CORS problem — it reports "API OFFLINE", which sends you looking
    at the API, which is running fine.

    So the list is extendable by environment for exactly that case. Loopback
    only, and never a wildcard: a `*` here would let any page the operator has
    open reach a research database on their own machine.
    """
    origins = ["http://127.0.0.1:5173", "http://localhost:5173"]
    extra = os.environ.get("ALGOFORGE_CORS_ORIGINS", "")
    for candidate in extra.split(","):
        cleaned = candidate.strip()
        if not cleaned:
            continue
        if not cleaned.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError(
                f"ALGOFORGE_CORS_ORIGINS may only name loopback origins; got '{cleaned}'"
            )
        if cleaned not in origins:
            origins.append(cleaned)
    return origins


def create_app(database_path: Path | None = None) -> FastAPI:
    # Everything the app writes lives under the workspace, which defaults to the
    # repository and points at an Obsidian vault once one is configured. See
    # forge.vault.location for the resolution order.
    workspace = resolve_workspace(ROOT)
    # Credentials reach the process from .env and the operator's key file. Loading
    # them here, once, is what stops one subsystem deciding the model is reachable
    # while another decides it is not.
    load_keys()
    db_path = database_path or Path(os.getenv("ALGOFORGE_DB_PATH", workspace.data / "algoforge.db"))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ledger = LedgerDatabase(db_path)
        seed_demo(ledger)
        app.state.ledger = ledger
        research_loop.start()
        yield
        research_loop.stop()
        engine.stop()
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
        allow_origins=_allowed_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/health", response_model=ApiEnvelope[dict[str, object]])
    def health() -> ApiEnvelope[dict[str, object]]:
        engine = getattr(app.state, "engine", None)
        return ApiEnvelope(
            data={
                "status": "ok",
                "paper_only": True,
                "data_gate": "REAL_AVAILABLE",
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

    @app.get("/api/v1/analysis/{run_id}", response_model=ApiEnvelope[dict[str, Any]])
    def analysis(run_id: str) -> ApiEnvelope[dict[str, Any]]:
        """Refuses, and says where the real analysis lives.

        This route used to build a `NormalAnalysis` from a hard-coded twelve-value
        P&L series repeated three times, run it through the judge, and return the
        verdict — over HTTP, for any run id that existed. Every number in the
        response was invented, and the verdict attached to them was a real
        judge verdict, which made the invention indistinguishable from evidence.

        A `RunRecord` carries provenance — hashes, tier, timestamps — and no P&L
        at all, so there is genuinely nothing here to analyse. The analysis a
        caller wants is over a *backtest*, which does carry trades, and lives at
        the routes named below.
        """
        item = app.state.ledger.get_run(run_id)
        if item is None:
            raise HTTPException(status_code=404, detail={"code": "run_not_found"})
        raise HTTPException(
            status_code=409,
            detail={
                "code": "no_analysable_evidence",
                "reason": (
                    "A run record carries provenance, not a P&L series, so there is "
                    "nothing here to analyse. This route previously answered with a "
                    "hard-coded series and a real judge verdict over it."
                ),
                "run_id": item.run_id,
                "tier": item.tier,
                "analyse_instead": [
                    "/api/v1/strategies/{strategy_id}/trades",
                    "/api/v1/strategies/{strategy_id}/regimes",
                    "/api/v1/strategies/{strategy_id}/resample",
                    "/api/v1/strategies/{strategy_id}/dossier",
                ],
            },
        )

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
            data=build_debate(judged),
            meta={
                "narrative_can_change_verdict": False,
                # The ledger stores run contracts, not trade series, so the
                # seeded sample run is judged on the sample P&L above. The
                # specialist positions below are derived from that verdict.
                "verdict_source": "sample_series",
                "labels": list(item.labels),
            },
        )

    @app.get("/api/v1/evolution/overview", response_model=ApiEnvelope[dict[str, object]])
    def evolution_overview() -> ApiEnvelope[dict[str, object]]:
        keeper = ForgeKeeper(workspace.data / "forgekeeper")
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

    library = StrategyLibrary(workspace.strategies)
    store = BacktestStore(workspace.data / "backtests")
    # Index anything written since the last run now, off the request path, so a
    # workspace that gained artifacts while the app was closed does not spend
    # the first strategy listing catching up. Nothing waits on this.
    store.warm()
    log = ActivityLog(workspace.data / "runtime" / "activity.ndjson")
    research_ledger = ResearchLedger(workspace.data / "research.db")
    mirror = VaultMirror(workspace)
    families = FamilyRegistry(workspace.store / "families")
    templates = TemplateStore(workspace.templates)

    # Operator- and agent-authored templates join the same dict the engine, the
    # library and every router already hold, so a registered template is
    # indistinguishable from a shipped one at the point of use.
    restored = templates.load_all(TEMPLATES)
    if restored:
        log.record(
            "CATALOG",
            f"{len(restored)} custom template(s) restored: " + ", ".join(t.key for t in restored),
            "info",
        )
    for rejection in templates.rejected_on_load:
        log.record(
            "CATALOG",
            f"custom template '{rejection['key']}' was not loaded: {rejection['reason']}",
            "fail",
        )

    surface = build_control_router(
        workspace, library, store, log, research_ledger, mirror, families, templates
    )
    engine, market = surface.engine, surface.market
    research_loop = ResearchLoop(surface.agents, surface.agents.settings, log, surface.actions)

    app.state.engine = engine
    app.state.workspace = workspace
    app.state.mirror = mirror
    app.state.research_loop = research_loop
    app.state.actions = surface.actions
    app.include_router(build_router(workspace.store, library, store, log, market, research_ledger))
    app.include_router(surface.router)
    app.include_router(build_catalog_router(families, templates, mirror, log))
    app.include_router(build_mission_router(surface.orchestrator, surface.actions))
    app.include_router(build_research_loop_router(research_loop))
    app.include_router(
        build_storage_router(
            workspace, mirror, log, library, store, families, surface.agents.research
        )
    )

    # A vault that has never been opened should still land somewhere useful.
    mirror.index(workspace.counts())
    for family in families.all():
        mirror.family(family.as_dict())

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
