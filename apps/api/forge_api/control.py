"""Engine control, dataset selection, and strategy-linked prop simulation."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from forge.contracts.models import ApiEnvelope
from forge.data.live import ProviderError
from forge.prop import load_rules, simulate_prop_paths
from forge.strategy import StrategyLibrary
from pydantic import BaseModel, Field

from forge_api.activity import ActivityLog, BacktestStore
from forge_api.engine import AutonomousEngine, EngineConfig
from forge_api.market import DATASETS, MarketService


class StartEngineRequest(BaseModel):
    dataset: str = "mnq_1m_3mo"
    cycle_seconds: float = Field(default=8.0, ge=1.0, le=300.0)
    max_strategies: int = Field(default=40, ge=1, le=500)
    max_bars: int = Field(default=30_000, ge=1_000, le=200_000)


class PropRequest(BaseModel):
    rule_id: str
    paths: int = Field(default=1000, ge=100, le=10_000)
    seed: int = 20260901


def daily_pnl_from_trades(trades: list[dict[str, Any]]) -> tuple[float, ...]:
    """Aggregate trades into calendar-day P&L.

    Prop rules operate on *days*, not trades: a trailing drawdown and a
    consistency cap both measure the daily path. Feeding per-trade values in
    would silently model a firm that settles after every fill.
    """
    buckets: dict[str, float] = defaultdict(float)
    for trade in trades:
        buckets[str(trade["exit_time"])[:10]] += float(trade["net_pnl"])
    return tuple(buckets[day] for day in sorted(buckets))


def build_control_router(
    root: Path,
    library: StrategyLibrary,
    store: BacktestStore,
    log: ActivityLog,
) -> tuple[APIRouter, AutonomousEngine, MarketService]:
    router = APIRouter(prefix="/api/v1", tags=["control"])
    market = MarketService(root)
    engine = AutonomousEngine(library, store, log, market, root)

    # ── datasets ─────────────────────────────────────────────────────────────
    @router.get("/datasets", response_model=ApiEnvelope[list[dict[str, Any]]])
    def datasets() -> ApiEnvelope[list[dict[str, Any]]]:
        rows = market.status()
        return ApiEnvelope(
            data=rows,
            meta={"real_available": sum(1 for r in rows if r["is_real"])},
        )

    @router.post("/datasets/{key}/load", response_model=ApiEnvelope[dict[str, Any]])
    def load_dataset(key: str) -> ApiEnvelope[dict[str, Any]]:
        try:
            bars, dataset = market.load(key)
        except ProviderError as exc:
            log.record("DATA", f"{key} load failed: {exc}", "fail")
            raise HTTPException(422, {"code": "provider_error", "detail": str(exc)}) from exc
        log.record(
            "DATA",
            f"{dataset.label} loaded — {len(bars):,} bars from {dataset.provider}",
            "pass" if dataset.is_real else "warn",
        )
        return ApiEnvelope(
            data={
                "key": key,
                "label": dataset.label,
                "bar_count": len(bars),
                "provider": dataset.provider,
                "is_real": dataset.is_real,
                "first": bars[0].event_time.isoformat(),
                "last": bars[-1].event_time.isoformat(),
            }
        )

    # ── autonomous engine ────────────────────────────────────────────────────
    @router.get("/engine", response_model=ApiEnvelope[dict[str, Any]])
    def engine_status() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=engine.status())

    @router.post("/engine/start", response_model=ApiEnvelope[dict[str, Any]])
    def engine_start(body: StartEngineRequest) -> ApiEnvelope[dict[str, Any]]:
        if body.dataset not in DATASETS:
            raise HTTPException(404, {"code": "unknown_dataset", "dataset": body.dataset})
        return ApiEnvelope(
            data=engine.start(
                EngineConfig(
                    dataset=body.dataset,
                    cycle_seconds=body.cycle_seconds,
                    max_strategies=body.max_strategies,
                    max_bars=body.max_bars,
                )
            )
        )

    @router.post("/engine/stop", response_model=ApiEnvelope[dict[str, Any]])
    def engine_stop() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=engine.stop())

    @router.get("/engine/constraints", response_model=ApiEnvelope[list[dict[str, str]]])
    def engine_constraints() -> ApiEnvelope[list[dict[str, str]]]:
        rows = engine.constraints()
        return ApiEnvelope(data=rows, meta={"total": len(rows)})

    # ── prop simulation, linked to a real strategy ───────────────────────────
    @router.post("/strategies/{strategy_id}/prop", response_model=ApiEnvelope[dict[str, Any]])
    def strategy_prop(strategy_id: str, body: PropRequest) -> ApiEnvelope[dict[str, Any]]:
        latest = store.latest(strategy_id)
        if latest is None:
            raise HTTPException(422, {"code": "no_backtest", "detail": "run a backtest first"})

        daily = daily_pnl_from_trades(latest["trades"])
        if len(daily) < 5:
            raise HTTPException(
                422,
                {
                    "code": "insufficient_days",
                    "detail": f"only {len(daily)} trading days; a prop evaluation needs more",
                },
            )

        rules = {rule.rule_id: rule for rule in load_rules(root / "rules")}
        rule = rules.get(body.rule_id)
        if rule is None:
            raise HTTPException(404, {"code": "rule_not_found", "rule_id": body.rule_id})

        simulation = simulate_prop_paths(
            latest["backtest_id"],
            rule,
            daily,
            seed=body.seed,
            paths=body.paths,
            allow_unverified=True,
        )

        terminal = [outcome.terminal_balance for outcome in simulation.outcomes]
        pass_days = [o.days for o in simulation.outcomes if o.outcome in {"PASS", "SURVIVED"}]
        fail_days = [o.days for o in simulation.outcomes if o.outcome == "FAIL"]
        reasons: dict[str, int] = defaultdict(int)
        for outcome in simulation.outcomes:
            if outcome.failure_reason:
                reasons[outcome.failure_reason] += 1

        log.record(
            "PROP",
            f"{strategy_id} vs {rule.display_name} — {simulation.pass_rate:.1%} pass"
            f" over {simulation.path_count:,} paths",
            "pass" if simulation.pass_rate > 0.3 else "warn",
            simulation.simulation_id,
        )

        return ApiEnvelope(
            data={
                **simulation.model_dump(mode="json"),
                "strategy_id": strategy_id,
                "trading_days": len(daily),
                "daily_pnl": list(daily),
                "avg_days_to_pass": round(sum(pass_days) / len(pass_days), 1)
                if pass_days
                else None,
                "avg_days_to_fail": round(sum(fail_days) / len(fail_days), 1)
                if fail_days
                else None,
                "failure_reasons": dict(reasons),
                "terminal_balances": terminal,
                "median_terminal": sorted(terminal)[len(terminal) // 2] if terminal else 0.0,
            },
            meta={
                "rule_verified": rule.verified,
                "rule_runnable": rule.runnable(date.today()),
                "note": "Pass rate is a simulation of modelled rules, not a real evaluation.",
            },
        )

    return router, engine, market
