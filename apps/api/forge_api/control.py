"""Engine control, dataset selection, and strategy-linked prop simulation."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from forge.capabilities import nautilus_capability
from forge.contracts.models import ApiEnvelope
from forge.data.live import ProviderError
from forge.prop import assess_day_coverage, load_rules, simulate_prop_paths
from forge.prop.engine import MAX_BACKTEST_BARS, MIN_TRADING_DAYS
from forge.research import ResearchLedger
from forge.strategy import FamilyRegistry, StrategyLibrary, TemplateStore

# `Workspace` here is the storage location, not the screen layout. The two are
# different types with the same name in different packages, so this import is
# worth reading twice before changing it — an import sorter merging them is a
# silent type change, not a formatting one.
from forge.vault import VaultMirror, Workspace
from forge.workstation import WorkspaceStore
from pydantic import BaseModel, Field

from forge_api.actions import ActionError, Actions
from forge_api.activity import ActivityLog, BacktestStore
from forge_api.agent_service import AgentService
from forge_api.assistant import Assistant
from forge_api.engine import AutonomousEngine, EngineConfig
from forge_api.jobs import REGISTRY, JobHandle
from forge_api.market import DATASETS, DEFAULT_DATASET, MarketService
from forge_api.opencode import OPENCODE_GO_DEFAULT_URL
from forge_api.orchestrator import Orchestrator
from forge_api.providers import (
    PROVIDER_OPENCODE,
    PROVIDERS,
    catalog_for,
    status_for,
)
from forge_api.settings_store import (
    KNOWN_MODELS,
    ROLES,
    AISettings,
    BudgetSettings,
    ResearchLoopSettings,
    Settings,
    SettingsStore,
)


class StartEngineRequest(BaseModel):
    dataset: str = DEFAULT_DATASET
    cycle_seconds: float = Field(default=8.0, ge=1.0, le=300.0)
    # A population cap, not a stop point: the engine retires its weakest
    # survivor when full rather than halting.
    max_strategies: int = Field(default=400, ge=1, le=5_000)
    # Only the 20% validation slice reaches the prop simulator, so the window
    # has to be about five times the one the 30-day gate needs.
    max_bars: int = Field(default=250_000, ge=1_000, le=MAX_BACKTEST_BARS)
    workers: int = Field(default=8, ge=1, le=8)


class AgentTaskRequest(BaseModel):
    task: str = Field(default="", max_length=2000)


class AgentControlRequest(BaseModel):
    enabled: bool


class WorkerControlRequest(BaseModel):
    paused: bool


class SettingsPatch(BaseModel):
    ai_enabled: bool | None = None
    ai_provider: str | None = None
    ai_base_url: str | None = None
    routing: dict[str, str] | None = None
    budget: dict[str, float] | None = None
    default_dataset: str | None = None
    engine_cycle_seconds: float | None = Field(default=None, ge=1.0, le=300.0)
    engine_max_strategies: int | None = Field(default=None, ge=1, le=500)
    databento_max_cost_usd: float | None = Field(default=None, ge=0.0, le=100.0)
    research_loop_enabled: bool | None = None
    research_interval_minutes: int | None = Field(default=None, ge=5, le=1440)
    research_topics: list[str] | None = Field(default=None, min_length=1, max_length=12)


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)


class PropRequest(BaseModel):
    rule_id: str
    paths: int = Field(default=1000, ge=100, le=10_000)
    seed: int = 20260901


class CreateWorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    template_key: str | None = None
    activate: bool = True


class RenameWorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class AddPanelRequest(BaseModel):
    kind: str
    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None
    symbol: str | None = None
    timeframe: str | None = None
    title: str | None = None


class PanelGeometryRequest(BaseModel):
    """Move, resize, or both. Omitted pairs are left alone."""

    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None


class PanelSettingRequest(BaseModel):
    key: str = Field(min_length=1, max_length=40)
    value: str = Field(min_length=1, max_length=200)


class LinkPanelsRequest(BaseModel):
    panel_ids: list[str] = Field(min_length=1)
    group: str | None = None


class BuildWorkspaceRequest(BaseModel):
    """What the operator is here to do. Every field is optional except the name:
    a half-filled profile still builds something, and the parts that were not
    stated simply do not add panels."""

    name: str = Field(min_length=1, max_length=120)
    purpose: str | None = None
    markets: list[str] | None = None
    style: str | None = None
    sessions: list[str] | None = None
    research: list[str] | None = None
    risk: str | None = None
    datasets: list[str] | None = None
    preferred_export: str | None = None


class PropMatrixRequest(BaseModel):
    """Run every backtested strategy against every rule set at once."""

    paths: int = Field(default=1000, ge=100, le=10_000)
    seed: int = 20260901
    phase: Literal["CHALLENGE", "FUNDED", "ALL"] = "ALL"
    # Strategies with too short a track record are reported as skipped rather
    # than silently dropped: "no cell here" and "we did not look" are different
    # answers and the grid has to distinguish them.
    strategy_ids: list[str] | None = None


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


def _calendar_span_days(trades: list[dict[str, Any]]) -> int:
    """Calendar days between the first entry and the last exit."""
    if not trades:
        return 0
    stamps = [str(trade["entry_time"])[:10] for trade in trades]
    stamps += [str(trade["exit_time"])[:10] for trade in trades]
    first, last = min(stamps), max(stamps)
    try:
        return (date.fromisoformat(last) - date.fromisoformat(first)).days + 1
    except ValueError:
        return 0


def _partition_fraction(artifact: dict[str, Any]) -> float:
    """Share of the requested window that reached this artifact.

    Only the validation partition is simulated against prop rules, so a
    suggested bar count derived from it has to be divided back out or it
    understates the request by the size of the split.
    """
    receipt = artifact.get("split_receipt")
    bars = int(artifact.get("bar_count") or 0)
    if not isinstance(receipt, dict) or bars <= 0:
        return 1.0
    source = int(receipt.get("source_bar_count") or 0)
    return bars / source if source > 0 else 1.0


@dataclass
class ControlSurface:
    """Everything the app factory needs back from one wiring pass.

    Returning a tuple of seven was how the previous signature would have grown.
    These objects are mutually dependent — the actions registry needs the engine,
    the orchestrator needs the actions, the assistant needs both — so they are
    built together and handed back named.
    """

    router: APIRouter
    engine: AutonomousEngine
    market: MarketService
    agents: AgentService
    actions: Actions
    orchestrator: Orchestrator


def build_control_router(
    workspace: Workspace,
    library: StrategyLibrary,
    store: BacktestStore,
    log: ActivityLog,
    research_ledger: ResearchLedger,
    mirror: VaultMirror,
    families: FamilyRegistry,
    templates: TemplateStore,
) -> ControlSurface:
    router = APIRouter(prefix="/api/v1", tags=["control"])
    root = workspace.repo
    # Vendor archives stay with the checkout; see forge.vault.location.
    market = MarketService(root)
    engine = AutonomousEngine(library, store, log, market, workspace, research_ledger, mirror)
    settings_store = SettingsStore(root / "config" / "settings.json")
    # Layouts are application state, not research: they live in the app-owned
    # data root beside the other databases, and losing them costs a screen
    # arrangement rather than any evidence.
    workspaces = WorkspaceStore(workspace.data / "workspaces.db")
    agents = AgentService(workspace.store, log, settings_store, mirror)
    engine.agents = agents
    actions = Actions(
        workspace=workspace,
        library=library,
        store=store,
        log=log,
        market=market,
        engine=engine,
        agents=agents,
        families=families,
        templates=templates,
        mirror=mirror,
        workspaces=workspaces,
    )
    orchestrator = Orchestrator(
        workspace.data / "missions.db", actions, agents, settings_store, log, mirror
    )
    assistant = Assistant(root, library, store, log, settings_store, actions)
    agents.context = lambda: {
        "running": engine.state.running,
        "dataset": engine.state.config.dataset,
        "experiments": engine.experiments.recent(engine._scope(), 30),
        "worker_stages": engine.status()["worker_stages"],
        "evidence_boundary": "Development-only feedback; validation and holdout values excluded",
    }

    @router.get("/agent-command")
    def agent_command() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data={**agents.snapshot(), "engine": engine.status()})

    @router.get("/research/sources")
    def research_sources() -> ApiEnvelope[list[dict[str, Any]]]:
        return ApiEnvelope(data=agents.research.list())

    @router.post("/agent-command/{role}/run")
    def run_agent(role: str, body: AgentTaskRequest) -> ApiEnvelope[dict[str, Any]]:
        try:
            return ApiEnvelope(data=agents.submit(role, body.task))
        except KeyError as exc:
            raise HTTPException(404, "Unknown agent") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.patch("/agent-command/{role}")
    def control_agent(role: str, body: AgentControlRequest) -> ApiEnvelope[dict[str, Any]]:
        try:
            agents.control(role, body.enabled)
        except KeyError as exc:
            raise HTTPException(404, "Unknown agent") from exc
        return ApiEnvelope(data=agents.snapshot())

    @router.patch("/engine/workers/{worker}")
    def control_worker(worker: int, body: WorkerControlRequest) -> ApiEnvelope[dict[str, Any]]:
        try:
            engine.control_worker(worker, body.paused)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return ApiEnvelope(data=engine.status())

    # ── settings ─────────────────────────────────────────────────────────────
    @router.get("/settings", response_model=ApiEnvelope[dict[str, Any]])
    def read_settings() -> ApiEnvelope[dict[str, Any]]:
        current = settings_store.load()
        gateway_status = status_for(current.ai.provider, current.ai.base_url)
        return ApiEnvelope(
            data={
                "ai": {
                    "enabled": current.ai.enabled,
                    "provider": current.ai.provider,
                    "base_url": current.ai.base_url,
                    "routing": current.ai.routing,
                    "budget": vars(current.ai.budget),
                    "gateway": gateway_status,
                },
                "default_dataset": current.default_dataset,
                "engine_cycle_seconds": current.engine_cycle_seconds,
                "engine_max_strategies": current.engine_max_strategies,
                "databento_max_cost_usd": current.databento_max_cost_usd,
                "research_loop": vars(current.research_loop),
                "models": catalog_for(current.ai.provider),
                "providers": list(PROVIDERS),
                "roles": ROLES,
                "credentials": SettingsStore.credential_status(),
            },
            meta={"secrets_returned": False},
        )

    @router.patch("/settings", response_model=ApiEnvelope[dict[str, Any]])
    def update_settings(body: SettingsPatch) -> ApiEnvelope[dict[str, Any]]:
        current = settings_store.load()
        known_providers = {p["id"] for p in PROVIDERS}
        provider = body.ai_provider or current.ai.provider
        if provider not in known_providers:
            raise HTTPException(
                422, {"code": "unsupported_ai_provider", "known": sorted(known_providers)}
            )

        # The base URL was only ever a knob for the local OmniRoute gateway,
        # which is gone. The hosted endpoint is fixed, so the field is accepted
        # and ignored rather than removed from the wire and breaking clients.
        proposed_url = OPENCODE_GO_DEFAULT_URL
        valid = {m["id"] for m in KNOWN_MODELS} | {m["id"] for m in catalog_for(provider)}
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
                provider=provider,
                base_url=proposed_url,
                routing=routing,
                budget=budget,
            ),
            research_loop=ResearchLoopSettings(
                enabled=current.research_loop.enabled
                if body.research_loop_enabled is None
                else body.research_loop_enabled,
                interval_minutes=body.research_interval_minutes
                or current.research_loop.interval_minutes,
                topics=(
                    [topic.strip()[:240] for topic in body.research_topics if topic.strip()]
                    or current.research_loop.topics
                )
                if body.research_topics is not None else current.research_loop.topics,
            ),
            default_dataset=body.default_dataset or current.default_dataset,
            engine_cycle_seconds=body.engine_cycle_seconds or current.engine_cycle_seconds,
            engine_max_strategies=body.engine_max_strategies or current.engine_max_strategies,
            databento_max_cost_usd=current.databento_max_cost_usd
            if body.databento_max_cost_usd is None
            else body.databento_max_cost_usd,
        )
        settings_store.save(updated)
        log.record("SETTINGS", "updated", "info")
        return read_settings()

    @router.get("/ai/status", response_model=ApiEnvelope[dict[str, Any]])
    def ai_status() -> ApiEnvelope[dict[str, Any]]:
        current = settings_store.load()
        return ApiEnvelope(data=status_for(current.ai.provider, current.ai.base_url))

    @router.post("/ai/test", response_model=ApiEnvelope[dict[str, Any]])
    def ai_test() -> ApiEnvelope[dict[str, Any]]:
        current = settings_store.load()
        status = status_for(current.ai.provider, current.ai.base_url)
        name = status.get("provider") or PROVIDER_OPENCODE
        log.record(
            "AI",
            f"{name} connection verified" if status["connected"] else f"{name} unavailable",
            "pass" if status["connected"] else "warn",
        )
        return ApiEnvelope(data=status, meta={"secrets_returned": False})

    @router.get("/oracles", response_model=ApiEnvelope[list[dict[str, Any]]])
    def oracles() -> ApiEnvelope[list[dict[str, Any]]]:
        status = nautilus_capability(("BARS",))
        return ApiEnvelope(
            data=[status.model_dump(mode="json")],
            meta={
                "promotion_authority": False,
                # The path keeps its name because the interface and any
                # configured MCP client call it. The word oversells what is
                # behind it: this reports whether a package is installed. It
                # evaluates nothing and produces no finding.
                "evaluates": False,
                "note": (
                    "A capability probe, not an oracle: it reports what is installed. "
                    "Nothing here independently checks execution realism, and "
                    "availability is not evidence of engine calibration."
                ),
            },
        )

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
                    workers=body.workers,
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

    @router.get("/experiments", response_model=ApiEnvelope[list[dict[str, Any]]])
    def experiments(limit: int = 80, roots_only: bool = False) -> ApiEnvelope[list[dict[str, Any]]]:
        scope = engine._scope()
        rows = (
            engine.experiments.roots(scope, limit)
            if roots_only
            else engine.experiments.recent(scope, limit)
        )
        return ApiEnvelope(
            data=rows, meta={"total": engine.experiments.count(scope), "scope": scope}
        )

    @router.get("/experiments/{experiment_id}", response_model=ApiEnvelope[dict[str, Any]])
    def experiment(experiment_id: str) -> ApiEnvelope[dict[str, Any]]:
        row = engine.experiments.get(experiment_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No experiment '{experiment_id}'.")
        return ApiEnvelope(data=row)

    @router.get("/experiments/{experiment_id}/lineage", response_model=ApiEnvelope[dict[str, Any]])
    def experiment_lineage(experiment_id: str) -> ApiEnvelope[dict[str, Any]]:
        """Where this experiment came from, and everything that came from it."""
        line = engine.experiments.lineage(experiment_id)
        if line["experiment"] is None:
            raise HTTPException(status_code=404, detail=f"No experiment '{experiment_id}'.")
        return ApiEnvelope(
            data=line,
            meta={
                "depth": len(line["ancestors"]),
                "descendants": len(line["descendants"]),
            },
        )

    @router.get(
        "/strategies/{strategy_id}/dossier", response_model=ApiEnvelope[dict[str, Any]]
    )
    def strategy_dossier(strategy_id: str) -> ApiEnvelope[dict[str, Any]]:
        """Everything known about one candidate, assembled from existing records."""
        from forge_api.dossier import build_dossier

        try:
            data = build_dossier(
                root=root,
                library=library,
                store=store,
                experiments=engine.experiments,
                memory=engine.memory,
                snapshots=engine.snapshots,
                scope=engine._scope(),
                strategy_id=strategy_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail=f"No strategy '{strategy_id}'."
            ) from exc
        return ApiEnvelope(
            data=data,
            meta={
                "sections_available": sorted(
                    name
                    for name, value in data.items()
                    if isinstance(value, dict) and value.get("available")
                )
            },
        )

    @router.get("/memory", response_model=ApiEnvelope[dict[str, Any]])
    def research_memory(limit: int = 100) -> ApiEnvelope[dict[str, Any]]:
        """What the search has learned, and how much of each kind."""
        scope = engine._scope()
        return ApiEnvelope(
            data={
                "scope": scope,
                "counts": engine.memory.counts(scope),
                "total": engine.memory.total(scope),
                "constraints": engine.constraints()[:limit],
            }
        )

    @router.post("/prop/matrix", response_model=ApiEnvelope[dict[str, Any]])
    def prop_matrix(body: PropMatrixRequest) -> ApiEnvelope[dict[str, Any]]:
        """Every strategy against every rule, as one job.

        A single strategy against a single rule answers almost nothing: prop
        rule sets differ in ways that reverse the ranking, and a strategy that
        clears a no-daily-loss-limit evaluation can fail a trailing one on the
        same P&L. The comparison is the product.
        """
        rules = [
            rule
            for rule in load_rules(root / "rules")
            if body.phase == "ALL" or rule.phase == body.phase
        ]
        if not rules:
            raise HTTPException(422, {"code": "no_rules", "phase": body.phase})

        candidates: list[tuple[str, str, tuple[float, ...]]] = []
        skipped: list[dict[str, Any]] = []
        for spec in library.list_specs():
            if body.strategy_ids and spec.strategy_id not in body.strategy_ids:
                continue
            # Two phases. The projection answers "could this possibly qualify?"
            # without touching a trade ledger, and only survivors are read in
            # full. Reading all 399 ledgers to discover that most carry legacy
            # units or too few trades cost 8.7 seconds before the job could even
            # be queued.
            projected = store.latest_projection(spec.strategy_id)
            if projected is None:
                skipped.append(
                    {"strategy_id": spec.strategy_id, "name": spec.name, "reason": "no_backtest"}
                )
                continue
            if projected.get("calculation_version") != "contract-units-v2":
                skipped.append(
                    {
                        "strategy_id": spec.strategy_id,
                        "name": spec.name,
                        "reason": "legacy_units",
                        "detail": "Rerun: old results used price points as dollar P&L.",
                    }
                )
                continue
            # A prop evaluation needs a month of sessions. A run with fewer
            # trades than trading days cannot supply them, and that is knowable
            # from the projection.
            if int(projected.get("trade_count") or 0) < MIN_TRADING_DAYS:
                skipped.append(
                    {
                        "strategy_id": spec.strategy_id,
                        "name": spec.name,
                        "reason": "insufficient_days",
                        "days_observed": 0,
                        "days_required": MIN_TRADING_DAYS,
                        "detail": (
                            f"{projected.get('trade_count')} trades cannot span "
                            f"{MIN_TRADING_DAYS} trading days."
                        ),
                    }
                )
                continue
            latest = store.latest(spec.strategy_id)
            if latest is None:
                skipped.append(
                    {"strategy_id": spec.strategy_id, "name": spec.name, "reason": "no_backtest"}
                )
                continue
            if latest.get("calculation_version") != "contract-units-v2":
                skipped.append(
                    {
                        "strategy_id": spec.strategy_id,
                        "name": spec.name,
                        "reason": "legacy_units",
                        "detail": "Rerun: old results used price points as dollar P&L.",
                    }
                )
                continue
            daily = daily_pnl_from_trades(latest["trades"])
            coverage = assess_day_coverage(
                trading_days=len(daily),
                bars_used=int(latest.get("bar_count") or 0),
                span_days=_calendar_span_days(latest["trades"]),
                partition_fraction=_partition_fraction(latest),
            )
            if not coverage.sufficient:
                skipped.append(
                    {
                        "strategy_id": spec.strategy_id,
                        "name": spec.name,
                        "reason": "insufficient_days",
                        "days_observed": coverage.trading_days,
                        "days_required": coverage.days_required,
                        "detail": coverage.explain(),
                    }
                )
                continue
            candidates.append((spec.strategy_id, spec.name, daily))

        if not candidates:
            raise HTTPException(
                422,
                {
                    "code": "nothing_to_simulate",
                    "skipped": skipped,
                    "detail": (
                        "No strategy has a backtest long enough to simulate. Run a backtest "
                        "over a longer range first."
                    ),
                },
            )

        total = len(candidates) * len(rules)

        def work(handle: JobHandle) -> dict[str, Any]:
            cells: list[dict[str, Any]] = []
            done = 0
            for strategy_id, name, daily in candidates:
                for rule in rules:
                    handle.progress(done, f"{name} vs {rule.display_name}")
                    simulation = simulate_prop_paths(
                        strategy_id,
                        rule,
                        daily,
                        seed=body.seed,
                        paths=body.paths,
                        allow_unverified=True,
                    )
                    cells.append(
                        {
                            "strategy_id": strategy_id,
                            "strategy_name": name,
                            "rule_id": rule.rule_id,
                            "rule_name": rule.display_name,
                            "provider": rule.provider,
                            "phase": rule.phase,
                            "pass_rate": simulation.pass_rate,
                            "interval_low": simulation.interval_low,
                            "interval_high": simulation.interval_high,
                            "risk_of_ruin": simulation.risk_of_ruin,
                            "mean_payout": simulation.mean_payout,
                            "median_terminal": simulation.tail_risk.terminal_median,
                            "var_95": simulation.tail_risk.var_95,
                            "cvar_95": simulation.tail_risk.cvar_95,
                            "trading_days": len(daily),
                            "verified": rule.verified,
                        }
                    )
                    done += 1
            handle.progress(total, "complete")
            log.record(
                "PROP",
                f"matrix: {len(candidates)} strategies x {len(rules)} rules, "
                f"best {max((c['pass_rate'] for c in cells), default=0):.1%}",
                "info",
            )
            return {
                "cells": cells,
                "strategies": [
                    {"strategy_id": sid, "name": name, "trading_days": len(daily)}
                    for sid, name, daily in candidates
                ],
                "rules": [
                    {
                        "rule_id": rule.rule_id,
                        "display_name": rule.display_name,
                        "provider": rule.provider,
                        "phase": rule.phase,
                        "verified": rule.verified,
                    }
                    for rule in rules
                ],
                "skipped": skipped,
                "paths": body.paths,
            }

        job = REGISTRY.submit(
            "prop_matrix",
            f"{len(candidates)} strategies x {len(rules)} rules x {body.paths:,} paths",
            total,
            work,
        )
        return ApiEnvelope(
            data=job.as_dict(),
            meta={
                "strategies": len(candidates),
                "rules": len(rules),
                "skipped": len(skipped),
                "note": "Every cell is a full block-bootstrap simulation.",
            },
        )

    # ── prop simulation, linked to a real strategy ───────────────────────────
    @router.post("/strategies/{strategy_id}/prop", response_model=ApiEnvelope[dict[str, Any]])
    def strategy_prop(strategy_id: str, body: PropRequest) -> ApiEnvelope[dict[str, Any]]:
        latest = store.latest(strategy_id)
        if latest is None:
            raise HTTPException(422, {"code": "no_backtest", "detail": "run a backtest first"})
        if latest.get("calculation_version") != "contract-units-v2":
            raise HTTPException(
                422,
                {
                    "code": "legacy_units",
                    "detail": "Rerun this backtest: old results used price points as dollar P&L.",
                },
            )

        daily = daily_pnl_from_trades(latest["trades"])
        coverage = assess_day_coverage(
            trading_days=len(daily),
            bars_used=int(latest.get("bar_count") or 0),
            span_days=_calendar_span_days(latest["trades"]),
            partition_fraction=_partition_fraction(latest),
        )
        if not coverage.sufficient:
            # The refusal stands, but it now carries the bar count that would
            # lift it — the previous message told the user to "backtest over a
            # longer window" without saying how much longer.
            raise HTTPException(
                422,
                {
                    "code": "insufficient_days",
                    "days_observed": coverage.trading_days,
                    "days_required": coverage.days_required,
                    "bars_used": coverage.bars_used,
                    "span_days": coverage.span_days,
                    "bars_per_trading_day": coverage.bars_per_trading_day,
                    "suggested_bar_count": coverage.suggested_request_bars,
                    "partition_fraction": round(coverage.partition_fraction, 4),
                    "dataset": latest.get("dataset_key"),
                    "detail": coverage.explain(),
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


    def _action(
        name: str, arguments: dict[str, Any], *, confirmed: bool = False
    ) -> dict[str, Any]:
        """Call an action and turn its refusal into the right HTTP status.

        An ActionError is a refusal with a reason written for a person, so it is
        returned verbatim rather than replaced with a generic message. 409 is
        deliberate: the request was well-formed and the application declined it,
        which is different from 422 (the caller sent nonsense) and from 500 (we
        broke).
        """
        try:
            return actions.call(name, arguments, confirmed=confirmed)
        except ActionError as exc:
            raise HTTPException(409, {"code": "action_refused", "reason": str(exc)}) from exc




    @router.get("/data-health", response_model=ApiEnvelope[list[dict[str, Any]]])
    def data_health() -> ApiEnvelope[list[dict[str, Any]]]:
        """Every dataset, measured rather than asserted.

        A dataset is never called healthy because it was paid for or because it
        is named after a range. Each row carries the arithmetic that produced
        its status, and the ones that cannot be measured say so instead of
        showing a tick.
        """
        return ApiEnvelope(data=market.health_matrix())

    @router.get("/data-health/{dataset}", response_model=ApiEnvelope[dict[str, Any]])
    def dataset_health(dataset: str, rebuild: bool = False) -> ApiEnvelope[dict[str, Any]]:
        try:
            return ApiEnvelope(data=market.health(dataset, rebuild=rebuild))
        except ProviderError as exc:
            raise HTTPException(
                404, {"code": "unknown_dataset", "reason": str(exc)}
            ) from exc

    # ── charting ─────────────────────────────────────────────────────────────
    @router.get("/timeframes", response_model=ApiEnvelope[list[dict[str, Any]]])
    def timeframes() -> ApiEnvelope[list[dict[str, Any]]]:
        return ApiEnvelope(data=market.timeframe_catalogue())

    @router.get("/bars", response_model=ApiEnvelope[dict[str, Any]])
    def bars(
        dataset: str = DEFAULT_DATASET,
        timeframe: str = "1m",
        limit: int = 1500,
        before: str | None = None,
    ) -> ApiEnvelope[dict[str, Any]]:
        """Candles for a chart.

        Real bars from a real archive or nothing at all. A dataset that is not
        present is a 409 naming it, never a generated stand-in -- a chart that
        silently invents prices is worse than a chart that refuses to draw.
        """
        try:
            payload = market.chart_bars(dataset, timeframe, limit=limit, before=before)
        except KeyError as exc:
            raise HTTPException(422, {"code": "unknown_timeframe", "reason": str(exc)}) from exc
        except ProviderError as exc:
            raise HTTPException(
                409, {"code": "market_data_unavailable", "reason": str(exc)}
            ) from exc
        return ApiEnvelope(
            data=payload,
            meta={"convention": payload["convention"], "authority": payload["authority"]},
        )

    # ── workspaces ───────────────────────────────────────────────────────────
    # Thin by design. Every one of these delegates to the same action the agent
    # calls, so the interface cannot drift from what the operator can ask for in
    # words. A route with its own layout logic would be the second
    # implementation this architecture exists to avoid.

    @router.get("/workspaces", response_model=ApiEnvelope[dict[str, Any]])
    def list_workspaces() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=actions.call("list_workspaces"))

    @router.get("/workspaces/templates", response_model=ApiEnvelope[dict[str, Any]])
    def workspace_templates() -> ApiEnvelope[dict[str, Any]]:
        """Starting points. Nothing is locked behind one."""
        return ApiEnvelope(data=actions.call("list_workspace_templates"))

    @router.get("/workspaces/active", response_model=ApiEnvelope[dict[str, Any] | None])
    def active_workspace() -> ApiEnvelope[dict[str, Any] | None]:
        """The open workspace, or an honest null when none is.

        Deliberately not "create one on read": a GET that silently builds a desk
        is a side effect nobody asked for, and the caller needs to be able to
        tell "nothing open yet" from "here is your layout".
        """
        active = workspaces.active()
        return ApiEnvelope(
            data=None if active is None else actions.call("describe_workspace"),
            meta={"count": workspaces.count()},
        )


    @router.post("/workspaces/build", response_model=ApiEnvelope[dict[str, Any]])
    def build_workspace(body: BuildWorkspaceRequest) -> ApiEnvelope[dict[str, Any]]:
        """Build a workspace from a stated purpose.

        The same action the agent calls. Understanding a sentence is the model's
        job and happens before this; turning the resulting profile into panels is
        deterministic and happens here, which is why the feature still works with
        no model configured and why the operator can reproduce what it built.
        """
        arguments = {"name": body.name}
        for field in (
            "purpose", "markets", "style", "sessions",
            "research", "risk", "datasets", "preferred_export",
        ):
            value = getattr(body, field)
            if value:
                arguments[field] = value
        return ApiEnvelope(data=_action("build_workspace", arguments))

    @router.get("/workspaces/{workspace_id}", response_model=ApiEnvelope[dict[str, Any]])
    def get_workspace(workspace_id: str) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("describe_workspace", {"workspace_id": workspace_id}))

    @router.post("/workspaces", response_model=ApiEnvelope[dict[str, Any]])
    def create_workspace(body: CreateWorkspaceRequest) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "create_workspace",
                {
                    "name": body.name,
                    "template_key": body.template_key,
                    "activate": body.activate,
                },
            )
        )

    @router.post("/workspaces/{workspace_id}/open", response_model=ApiEnvelope[dict[str, Any]])
    def open_workspace(workspace_id: str) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("open_workspace", {"workspace_id": workspace_id}))

    @router.post("/workspaces/{workspace_id}/clone", response_model=ApiEnvelope[dict[str, Any]])
    def clone_workspace(
        workspace_id: str, body: RenameWorkspaceRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action("clone_workspace", {"workspace_id": workspace_id, "name": body.name})
        )

    @router.post("/workspaces/{workspace_id}/rename", response_model=ApiEnvelope[dict[str, Any]])
    def rename_workspace(
        workspace_id: str, body: RenameWorkspaceRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action("rename_workspace", {"workspace_id": workspace_id, "name": body.name})
        )

    @router.delete("/workspaces/{workspace_id}", response_model=ApiEnvelope[dict[str, Any]])
    def delete_workspace(workspace_id: str) -> ApiEnvelope[dict[str, Any]]:
        """Deleting a layout is a CONFIRM action, so the confirmation is the
        HTTP verb itself: a DELETE is not something a caller issues by accident,
        and the interface asks before sending it."""
        return ApiEnvelope(
            data=_action("delete_workspace", {"workspace_id": workspace_id}, confirmed=True)
        )

    @router.post("/workspaces/{workspace_id}/panels", response_model=ApiEnvelope[dict[str, Any]])
    def add_panel(workspace_id: str, body: AddPanelRequest) -> ApiEnvelope[dict[str, Any]]:
        arguments = {"workspace_id": workspace_id, "kind": body.kind}
        for field in ("x", "y", "width", "height", "symbol", "timeframe", "title"):
            value = getattr(body, field)
            if value is not None:
                arguments[field] = value
        return ApiEnvelope(data=_action("add_panel", arguments))

    @router.delete(
        "/workspaces/{workspace_id}/panels/{panel_id}",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def remove_panel(workspace_id: str, panel_id: str) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action("remove_panel", {"workspace_id": workspace_id, "panel_id": panel_id})
        )

    @router.post(
        "/workspaces/{workspace_id}/panels/{panel_id}/geometry",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def set_panel_geometry(
        workspace_id: str, panel_id: str, body: PanelGeometryRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        """Move and resize in one call, because dragging a corner does both.

        Two separate actions would make an intermediate state observable: a
        panel briefly at its new size in its old place can run off the grid and
        be rejected, even though the gesture as a whole was fine.
        """
        result: dict[str, Any] = {}
        if body.x is not None and body.y is not None:
            result = _action(
                "move_panel",
                {"workspace_id": workspace_id, "panel_id": panel_id, "x": body.x, "y": body.y},
            )
        if body.width is not None and body.height is not None:
            result = _action(
                "resize_panel",
                {
                    "workspace_id": workspace_id,
                    "panel_id": panel_id,
                    "width": body.width,
                    "height": body.height,
                },
            )
        if not result:
            raise HTTPException(422, {"code": "no_geometry_given"})
        return ApiEnvelope(data=result)

    @router.post(
        "/workspaces/{workspace_id}/panels/{panel_id}/settings",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def set_panel_setting(
        workspace_id: str, panel_id: str, body: PanelSettingRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        name = "add_indicator" if body.key == "indicator" else "set_panel_setting"
        arguments = (
            {"workspace_id": workspace_id, "panel_id": panel_id, "indicator": body.value}
            if name == "add_indicator"
            else {
                "workspace_id": workspace_id,
                "panel_id": panel_id,
                "key": body.key,
                "value": body.value,
            }
        )
        return ApiEnvelope(data=_action(name, arguments))

    @router.post("/workspaces/{workspace_id}/link", response_model=ApiEnvelope[dict[str, Any]])
    def link_panels(workspace_id: str, body: LinkPanelsRequest) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "link_panels",
                {
                    "workspace_id": workspace_id,
                    "panel_ids": body.panel_ids,
                    "group": body.group,
                },
            )
        )

    return ControlSurface(
        router=router,
        engine=engine,
        market=market,
        agents=agents,
        actions=actions,
        orchestrator=orchestrator,
    )
