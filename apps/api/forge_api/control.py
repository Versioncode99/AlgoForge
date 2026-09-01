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
from forge.prop.engine import MIN_TRADING_DAYS
from forge.strategy import StrategyLibrary
from pydantic import BaseModel, Field

from forge_api.activity import ActivityLog, BacktestStore
from forge_api.assistant import Assistant
from forge_api.engine import AutonomousEngine, EngineConfig
from forge_api.market import DATASETS, MarketService
from forge_api.settings_store import (
    KNOWN_MODELS,
    ROLES,
    AISettings,
    BudgetSettings,
    Settings,
    SettingsStore,
)


class StartEngineRequest(BaseModel):
    dataset: str = "mnq_1m_3mo"
    cycle_seconds: float = Field(default=8.0, ge=1.0, le=300.0)
    max_strategies: int = Field(default=40, ge=1, le=500)
    max_bars: int = Field(default=30_000, ge=1_000, le=200_000)


class SettingsPatch(BaseModel):
    ai_enabled: bool | None = None
    routing: dict[str, str] | None = None
    budget: dict[str, float] | None = None
    default_dataset: str | None = None
    engine_cycle_seconds: float | None = Field(default=None, ge=1.0, le=300.0)
    engine_max_strategies: int | None = Field(default=None, ge=1, le=500)
    databento_max_cost_usd: float | None = Field(default=None, ge=0.0, le=100.0)


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)


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
    settings_store = SettingsStore(root / "config" / "settings.json")
    assistant = Assistant(root, library, store, log, settings_store)

    # ── settings ─────────────────────────────────────────────────────────────
    @router.get("/settings", response_model=ApiEnvelope[dict[str, Any]])
    def read_settings() -> ApiEnvelope[dict[str, Any]]:
        current = settings_store.load()
        return ApiEnvelope(
            data={
                "ai": {
                    "enabled": current.ai.enabled,
                    "routing": current.ai.routing,
                    "budget": vars(current.ai.budget),
                },
                "default_dataset": current.default_dataset,
                "engine_cycle_seconds": current.engine_cycle_seconds,
                "engine_max_strategies": current.engine_max_strategies,
                "databento_max_cost_usd": current.databento_max_cost_usd,
                "models": KNOWN_MODELS,
                "roles": ROLES,
                "credentials": SettingsStore.credential_status(),
            },
            meta={"secrets_returned": False},
        )

    @router.patch("/settings", response_model=ApiEnvelope[dict[str, Any]])
    def update_settings(body: SettingsPatch) -> ApiEnvelope[dict[str, Any]]:
        current = settings_store.load()
        valid = {m["id"] for m in KNOWN_MODELS}
        role_keys = {r["key"] for r in ROLES}

        routing = dict(current.ai.routing)
        for role, model in (body.routing or {}).items():
            if role not in role_keys:
                raise HTTPException(422, {"code": "unknown_role", "role": role})
            if model not in valid:
                raise HTTPException(422, {"code": "unknown_model", "model": model})
            routing[role] = model

        budget = BudgetSettings(**{**vars(current.ai.budget), **(body.budget or {})})
        updated = Settings(
            ai=AISettings(
                enabled=current.ai.enabled if body.ai_enabled is None else body.ai_enabled,
                routing=routing,
                budget=budget,
            ),
            default_dataset=body.default_dataset or current.default_dataset,
            engine_cycle_seconds=body.engine_cycle_seconds or current.engine_cycle_seconds,
            engine_max_strategies=body.engine_max_strategies or current.engine_max_strategies,
            databento_max_cost_usd=body.databento_max_cost_usd or current.databento_max_cost_usd,
        )
        settings_store.save(updated)
        log.record("SETTINGS", "updated", "info")
        return read_settings()

    # ── assistant ────────────────────────────────────────────────────────────
    @router.post("/ask", response_model=ApiEnvelope[dict[str, Any]])
    def ask(body: AskRequest) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=assistant.ask(body.question))

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
        if len(daily) < MIN_TRADING_DAYS:
            raise HTTPException(
                422,
                {
                    "code": "insufficient_days",
                    "days_observed": len(daily),
                    "days_required": MIN_TRADING_DAYS,
                    "detail": (
                        f"Only {len(daily)} trading days. Estimating a "
                        f"{MIN_TRADING_DAYS}+ day evaluation from fewer measures the sample, "
                        "not the strategy. Backtest over a longer window."
                    ),
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
                "days_required": MIN_TRADING_DAYS,
                "resample_ratio": round(rule.timeout_days / len(daily), 2),
                "interval_width": round(simulation.interval_high - simulation.interval_low, 4),
            },
            meta={
                "rule_verified": rule.verified,
                "rule_runnable": rule.runnable(date.today()),
                "note": "Pass rate is a simulation of modelled rules, not a real evaluation.",
                "interval_method": (
                    "Double bootstrap: the observed days are resampled before each batch of "
                    "accounts, so the interval carries sample-size uncertainty, not just path "
                    "noise. Blocks of 5 days preserve losing streaks."
                ),
            },
        )

    return router, engine, market
