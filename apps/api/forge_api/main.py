from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from forge.contracts.hashing import content_hash
from forge.contracts.models import ApiEnvelope, Preregistration, RunRecord
from forge.judge import Judge, JudgeInput, Verdict
from forge.ledger import LedgerDatabase

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
        description="Private paper-only research API. Bundled output is sample and uncalibrated.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/health", response_model=ApiEnvelope[dict[str, object]])
    def health() -> ApiEnvelope[dict[str, object]]:
        return ApiEnvelope(data={"status": "ok", "paper_only": True, "data_gate": "SAMPLE"})

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

    return app


app = create_app()


def run() -> None:
    uvicorn.run("forge_api.main:app", host="127.0.0.1", port=8765, reload=False)
