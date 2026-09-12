"""Engine control, dataset selection, and strategy-linked prop simulation."""

from __future__ import annotations

from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from forge.analytics.regime import attribute_at_times as attribute_trades
from forge.analytics.resample import compare as compare_resamples
from forge.capabilities import nautilus_capability
from forge.contracts.models import ApiEnvelope
from forge.data.live import ProviderError
from forge.execution.oms import ExecutionStore
from forge.explain import Depth as PassportDepth
from forge.explain import PassportSources, metric_catalogue, question_catalogue
from forge.explain import build as build_passport
from forge.explain import interpret as interpret_metric
from forge.hedgefund.approvals import ApprovalError, ApprovalQueue
from forge.hedgefund.audit import AuditLog
from forge.hedgefund.config import FundConfigStore
from forge.hedgefund.loop import describe as describe_loop
from forge.modes.expertise import ALWAYS_VISIBLE
from forge.modes.expertise import catalogue as expertise_catalogue
from forge.modes.intents import catalogue as intent_catalogue
from forge.modes.models import MODE_ORDER
from forge.modes.models import catalogue as mode_catalogue
from forge.modes.permissions import Actor
from forge.modes.store import ModeStore
from forge.prop import assess_day_coverage, load_rules, simulate_prop_paths
from forge.prop.accounts import PropAccountStore
from forge.prop.engine import MAX_BACKTEST_BARS, MIN_TRADING_DAYS
from forge.propdesk import PropDeskStore
from forge.research import ResearchLedger
from forge.research.knowledge import (
    NOT_EVIDENCE_NOTE,
    FindingStatus,
    KnowledgeError,
    KnowledgeStore,
    as_payload,
)
from forge.research.routing import route as route_question
from forge.strategy import FamilyRegistry, StrategyLibrary, TemplateStore

# `Workspace` here is the storage location, not the screen layout. The two are
# different types with the same name in different packages, so this import is
# worth reading twice before changing it — an import sorter merging them is a
# silent type change, not a formatting one.
from forge.vault import VaultMirror, Workspace
from forge.workstation import WorkspaceStore
from pydantic import BaseModel, Field

from forge_api.actions import ActionError, Actions, ApprovalRequired
from forge_api.activity import ActivityLog, BacktestStore
from forge_api.agent_service import AgentService
from forge_api.assistant import Assistant
from forge_api.campaigns import CampaignService, build_campaign_router
from forge_api.director import ResearchDirector
from forge_api.engine import AutonomousEngine, EngineConfig
from forge_api.fund import FundService
from forge_api.jobs import REGISTRY, JobHandle
from forge_api.ledger_view import LedgerError, TradeLedgerService
from forge_api.ledger_view import _TradeShim as _Shim
from forge_api.market import DATASETS, DEFAULT_DATASET, MarketService
from forge_api.orchestrator import Orchestrator
from forge_api.propdesk import PropDeskService
from forge_api.providers import (
    PROVIDER_OPENCODE,
    PROVIDERS,
    base_url_for,
    catalog_for,
    model_for,
    status_for,
)
from forge_api.research_lab import ArtifactStore, LabError, ResearchLab
from forge_api.settings_store import (
    ACCENTS,
    DENSITIES,
    KNOWN_MODELS,
    MOTIONS,
    ROLES,
    THEMES,
    AISettings,
    AppearanceSettings,
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
    description: str = ""
    icon: str = ""
    #: A built-in mode to inherit the default rail from, or blank for an empty
    #: one the operator fills.
    mode: str = ""
    #: Routes from `/sidebar/destinations`. Composing the rail at creation makes
    #: "a workspace for NQ research and prop trading" one call.
    sidebar_items: list[str] = Field(default_factory=list)


class SidebarItemRequest(BaseModel):
    route: str
    group_id: str | None = None
    label: str = ""
    position: int | None = None


class SidebarMoveRequest(BaseModel):
    group_id: str
    position: int | None = None


class SidebarGroupRequest(BaseModel):
    group_id: str
    label: str = ""
    position: int | None = None


class SidebarFlagRequest(BaseModel):
    value: bool = True


class SidebarOrderRequest(BaseModel):
    group_ids: list[str]


class WorkspaceLinkRequest(BaseModel):
    target_id: str
    linked: bool = True


class WorkspaceDescribeRequest(BaseModel):
    description: str = ""
    icon: str = ""


class ImportWorkspaceRequest(BaseModel):
    document: dict[str, Any]
    name: str | None = None


class CollapsePanelRequest(BaseModel):
    collapsed: bool = True


class ReorderPanelRequest(BaseModel):
    position: int


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


class AppearancePatch(BaseModel):
    """A partial update. Anything omitted keeps the value it already had."""

    theme: str | None = Field(default=None, max_length=32)
    accent: str | None = Field(default=None, max_length=32)
    density: str | None = Field(default=None, max_length=32)
    motion: str | None = Field(default=None, max_length=32)
    sound_enabled: bool | None = None
    sound_volume: float | None = Field(default=None, ge=0.0, le=1.0)


class LabQuestionRequest(BaseModel):
    """A research question in the user's own words, for the lab."""

    question: str = Field(min_length=1, max_length=400)
    strategy_id: str = Field(min_length=1, max_length=120)
    backtest_id: str | None = Field(default=None, max_length=120)
    hour_bucket: int = Field(default=2, ge=1, le=6)
    save: bool = True
    #: Run the top candidate even when the question did not clearly pick one.
    #: Off by default: a confident wrong answer to a question nobody asked is
    #: worse than a refusal, because the result looks like an answer.
    force: bool = False


class PromoteFindingRequest(BaseModel):
    """Keep one sentence an analysis produced.

    `statement` is checked against the artifact and must match verbatim. There
    is deliberately no way to submit a claim of your own: the point of the store
    is that everything in it was computed, and a typed sentence with a
    provenance block attached would be indistinguishable from one that was.
    """

    artifact_id: str = Field(min_length=1, max_length=120)
    statement: str = Field(min_length=1, max_length=600)
    note: str = Field(default="", max_length=600)


class RetractFindingRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=600)


class SupersedeFindingRequest(BaseModel):
    by_finding_id: str = Field(min_length=1, max_length=120)


class AnalysisRequest(BaseModel):
    """One question, asked of one run."""

    analysis: str = Field(min_length=2, max_length=64)
    strategy_id: str = Field(min_length=1, max_length=120)
    backtest_id: str | None = Field(default=None, max_length=120)
    measure: str = Field(default="average_trade", max_length=32)
    #: Hours per bucket on the two-dimensional surface. Twenty-four by ten
    #: buckets over a few hundred trades is a picture of noise.
    hour_bucket: int = Field(default=2, ge=1, le=6)
    save: bool = True
    note: str = Field(default="", max_length=400)


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


class EnterModeRequest(BaseModel):
    stance: str | None = None


class StanceRequest(BaseModel):
    stance: str


class PropAccountRequest(BaseModel):
    rules: dict[str, Any]


class PropStateRequest(BaseModel):
    state: dict[str, Any]
    #: Required, not defaulted. "the operator typed it" and "derived from a
    #: strategy's trades" are different claims about the same row, and a reader
    #: months later needs to know which one they are looking at.
    source: str


class FundConfigRequest(BaseModel):
    config: dict[str, Any]
    note: str = ""


class ConstructRequest(BaseModel):
    capital: float | None = None


class PortfolioRequest(BaseModel):
    portfolio_id: str


class SubmitOrdersRequest(BaseModel):
    order_ids: list[str]


class DecisionRequest(BaseModel):
    note: str = ""


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
    modes: ModeStore
    fund: FundService
    approvals: ApprovalQueue
    audit: AuditLog
    campaigns: CampaignService
    prop_desk: PropDeskService


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
    # The mode session, the prop accounts, the fund's protected configuration and
    # the two records that make an autonomous run answerable. All application
    # state, so all in the app-owned data root beside the layouts.
    modes = ModeStore(workspace.data / "modes.db")
    prop_accounts = PropAccountStore(workspace.data / "prop-accounts.db")
    audit = AuditLog(workspace.data / "audit.db")
    approvals = ApprovalQueue(workspace.data / "approvals.db")
    fund_config = FundConfigStore(workspace.data / "fund-config.db")
    execution_store = ExecutionStore(
        workspace.data / "execution.db", starting_cash=fund_config.get().starting_cash
    )

    # The research factory. All application state, so all in the app-owned data
    # root beside the layouts and the mode session. Deleting these costs the
    # research *map* — the frontier, the hypothesis graph, the campaign journal —
    # and no verdict, artifact or holdout consumption, which live elsewhere.
    campaign_service = CampaignService(workspace.data, log=log)
    campaign_service.director = ResearchDirector(
        campaigns=campaign_service.campaigns,
        frontier=campaign_service.frontier,
        hypotheses=campaign_service.hypotheses,
        journal=campaign_service.journal,
        sources=campaign_service.sources,
        promotion=campaign_service.promotion,
        families=families,
        templates=templates,
        log=log,
        library=library,
    )
    # The engine asks the director what to research next. With no campaign
    # running the director returns nothing and the engine's original template
    # draw runs unchanged, so this attachment costs nothing when unused.
    engine.director = campaign_service.director
    # And the scheduler that deals the engine's workers across however many
    # campaigns are running. Without it every worker serves the attached
    # campaign, which is the single-campaign path the engine had before.
    engine.orchestrator = campaign_service.orchestrator
    # Campaigns that were running when the process died are not running now.
    # Left as "running" they would be scheduled workers that do not exist, and
    # their agents would show as alive with heartbeats hours old.
    for interrupted in campaign_service.campaigns.running():
        campaign_service.campaigns.set_status(
            interrupted.campaign_id,
            "stopped",
            reason="the application restarted while this campaign was running",
        )
        campaign_service.agents.stop_all(
            interrupted.campaign_id, reason="the application restarted"
        )
    # Any claim whose lease has lapsed is released at start-up as well as on the
    # cadence, so a run interrupted mid-experiment does not leave its hypotheses
    # held by an agent that no longer exists.
    campaign_service.agents.release_expired()

    def verdict_for(strategy_id: str) -> str | None:
        """The judge's decision for one strategy, through the dossier's own path.

        Not a second judging implementation and not a cached copy: the fund reads
        the same verdict the Evidence screen shows, so a strategy the judge
        failed cannot be sized by a portfolio that believes otherwise. A strategy
        that has never been judged returns `None`, which construction and the
        pre-trade gate both treat as ineligible rather than as permission.
        """
        from forge_api.dossier import build_dossier

        try:
            dossier = build_dossier(
                root=root,
                library=library,
                store=store,
                experiments=engine.experiments,
                memory=engine.memory,
                snapshots=engine.snapshots,
                scope=engine._scope(),
                strategy_id=strategy_id,
            )
        except (KeyError, ValueError, OSError):
            return None
        section = dossier.get("verdict") or {}
        decision = section.get("decision") if section.get("available") else None
        return str(decision) if decision else None

    def desk_evidence(strategy_id: str) -> dict[str, Any]:
        """What the risk and deployment layers may read about one strategy.

        Every key is derived from an artefact that already exists — the
        strategy's own spec, its out-of-sample runs, and the validation
        evidence. Nothing here computes a statistic or fills a gap: a key this
        cannot establish is simply absent, which the desk reads as unmeasured,
        which in turn can only ever hold or reduce risk.

        The out-of-sample daily series is the one worth reading closely. It is
        aggregated from the *trades of out-of-sample runs only*, by the day each
        trade closed, and it is what the drawdown bootstrap resamples. An
        in-sample run's trades must never reach it: the whole point of sizing
        against a bootstrapped drawdown is that the drawdown was not fitted.
        """
        from forge_api.strategies import load_evidence

        payload: dict[str, Any] = {}
        # A strategy with no readable spec has no declared instrument. The
        # deployment gate reads an absent symbol as unknown, which never passes.
        with suppress(KeyError, FileNotFoundError, OSError):
            payload["symbol"] = library.get_spec(strategy_id).symbol

        daily: dict[str, float] = {}
        for run in store.for_strategy(strategy_id):
            if run.get("evidence_tier") not in {"TRUTH_OOS", "HOLDOUT", "FORWARD"}:
                continue
            for trade in run.get("trades", ()):
                stamp = str(trade.get("exit_time", ""))[:10]
                net = trade.get("net_pnl")
                if not stamp or net is None:
                    continue
                daily[stamp] = daily.get(stamp, 0.0) + float(net)
        if daily:
            payload["oos_daily_pnl"] = tuple(daily[day] for day in sorted(daily))
            payload["evidence_tier"] = "TRUTH_OOS"

        evidence = load_evidence(root, strategy_id)
        if evidence:
            walk_forward = evidence.get("walk_forward")
            paths = evidence.get("paths")
            if isinstance(walk_forward, dict):
                payload["walk_forward_survives"] = bool(walk_forward.get("survives"))
            if isinstance(paths, dict):
                payload["paths_robust"] = bool(paths.get("robust"))
        return payload

    # The Prop Desk. Its store lives in the app-owned data root beside the other
    # databases, and it reads verdicts through `verdict_for` — the same callable
    # the fund uses — so one judge decision serves both.
    prop_desk = PropDeskService(
        store=PropDeskStore(workspace.data / "prop-desk.db"),
        prop_accounts=prop_accounts,
        verdict_for=verdict_for,
        evidence_for=desk_evidence,
    )

    fund = FundService(
        config_store=fund_config,
        execution_store=execution_store,
        audit=audit,
        library=library,
        backtests=store,
        market=market,
        verdict_for=verdict_for,
    )
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
        modes=modes,
        approvals=approvals,
        audit=audit,
        prop_accounts=prop_accounts,
        fund=fund,
        prop_desk=prop_desk,
    )
    orchestrator = Orchestrator(
        workspace.data / "missions.db", actions, agents, settings_store, log, mirror
    )
    # The trade ledger view: the strategy's own trades, keyed to timestamps so a
    # chart can place them at any timeframe. Holds the regime cache, because
    # classifying millions of bars per request would make the inspector unusable.
    ledger_view = TradeLedgerService(market, store, library)
    # Analysis artifacts are derived records, not evidence: every one can be
    # recomputed from the backtest it cites. They live in the app-owned data
    # root beside the other databases.
    research_lab = ResearchLab(ledger_view, ArtifactStore(workspace.data / "analyses.db"))
    knowledge = KnowledgeStore(workspace.data / "knowledge.db")
    # Handed to the registry so the agent reads trades through the same service
    # the interface does, rather than through a second implementation.
    actions.ledger = ledger_view
    actions.lab = research_lab
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
                "appearance": vars(current.appearance),
                # The options travel with the value, so the interface never has
                # its own list of themes to fall out of step with the server's.
                "appearance_options": {
                    "themes": THEMES,
                    "accents": ACCENTS,
                    "densities": DENSITIES,
                    "motions": MOTIONS,
                },
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
        # which is gone. Each provider's endpoint is fixed and derived from its
        # id, so the field is accepted and ignored rather than removed from the
        # wire and breaking clients.
        proposed_url = base_url_for(provider)
        valid = {m["id"] for m in KNOWN_MODELS} | {m["id"] for m in catalog_for(provider)}
        role_keys = {r["key"] for r in ROLES}

        routing = dict(current.ai.routing)
        for role, model in (body.routing or {}).items():
            if role not in role_keys:
                raise HTTPException(422, {"code": "unknown_role", "role": role})
            if model not in valid:
                raise HTTPException(422, {"code": "unknown_model", "model": model})
            routing[role] = model

        # Routing is stored per role, not per provider, so switching provider
        # leaves roles pointing at models the new one cannot serve — they were
        # real models when they were saved, so nothing rejected them. Repair
        # them here rather than at call time: the response carries the settings
        # back, so the operator sees which roles moved instead of discovering it
        # from an upstream 400 mid-run.
        if provider != current.ai.provider:
            routing = {role: model_for(provider, model) for role, model in routing.items()}

        budget = BudgetSettings(**{**vars(current.ai.budget), **(body.budget or {})})
        updated = Settings(
            # Carried through explicitly: `Settings` is rebuilt wholesale here,
            # so anything not named would silently revert to its default.
            appearance=current.appearance,
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
                if body.research_topics is not None
                else current.research_loop.topics,
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

    @router.get("/settings/appearance", response_model=ApiEnvelope[dict[str, Any]])
    def read_appearance() -> ApiEnvelope[dict[str, Any]]:
        """How the workstation should look and sound, and what the options are.

        Server-side for the same reason model routing is: these are operator
        settings, and an operator opening AlgoForge on a second machine should
        find the workstation they configured rather than a default one. It also
        keeps them out of a scattering of localStorage keys written by whichever
        component happened to need one.
        """
        current = settings_store.load()
        return ApiEnvelope(
            data={
                **vars(current.appearance),
                "options": {
                    "themes": THEMES,
                    "accents": ACCENTS,
                    "densities": DENSITIES,
                    "motions": MOTIONS,
                },
            }
        )

    @router.patch("/settings/appearance", response_model=ApiEnvelope[dict[str, Any]])
    def update_appearance(body: AppearancePatch) -> ApiEnvelope[dict[str, Any]]:
        """Change one or more appearance settings.

        A value the stylesheet has no palette for is refused rather than stored:
        a theme key that persisted but did not exist would leave the application
        unstyled with nothing on screen to say why.
        """
        current = settings_store.load()
        existing = current.appearance
        proposed = AppearanceSettings(
            theme=body.theme if body.theme is not None else existing.theme,
            accent=body.accent if body.accent is not None else existing.accent,
            density=body.density if body.density is not None else existing.density,
            motion=body.motion if body.motion is not None else existing.motion,
            sound_enabled=(
                existing.sound_enabled if body.sound_enabled is None else body.sound_enabled
            ),
            sound_volume=(
                existing.sound_volume if body.sound_volume is None else body.sound_volume
            ),
        )
        for field_name, options in (
            ("theme", THEMES),
            ("accent", ACCENTS),
            ("density", DENSITIES),
            ("motion", MOTIONS),
        ):
            value = getattr(proposed, field_name)
            if value not in {item["key"] for item in options}:
                raise HTTPException(
                    422,
                    {
                        "code": f"unknown_{field_name}",
                        "value": value,
                        "known": [item["key"] for item in options],
                    },
                )
        settings_store.save(replace(current, appearance=proposed))
        return read_appearance()

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

    @router.get("/engine/diagnostics", response_model=ApiEnvelope[dict[str, Any]])
    def engine_diagnostics() -> ApiEnvelope[dict[str, Any]]:
        """Why isn't my research running?

        Answered without opening a terminal: the derived state, the reason, a
        remedy, every worker's heartbeat, and the skip accounting behind the
        headline number.
        """
        return ApiEnvelope(
            data={
                "runtime": engine.diagnose(),
                "skips": engine.skips.counts(engine._campaign_id()),
                "recent_skips": engine.skips.list(engine._campaign_id(), limit=50),
                "retryable": engine.skips.retryable(engine._campaign_id(), limit=25),
            }
        )

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

    @router.get("/strategies/{strategy_id}/dossier", response_model=ApiEnvelope[dict[str, Any]])
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
            raise HTTPException(status_code=404, detail=f"No strategy '{strategy_id}'.") from exc
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

    @router.get(
        "/strategies/{strategy_id}/findings",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def strategy_findings(strategy_id: str) -> ApiEnvelope[dict[str, Any]]:
        """The verdict's gate ladder, ranked and explained.

        Presentation only. `forge.judge.explain` copies the decision, the grade
        and every gate status through untouched and cannot alter any of them, so
        this route can never disagree with the dossier it reads. It exists
        because `G5 INCONCLUSIVE DSR_NOT_MEASURABLE` is correct and nearly
        useless to somebody deciding whether to keep working on a strategy.
        """
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
            raise HTTPException(status_code=404, detail=f"No strategy '{strategy_id}'.") from exc
        section: dict[str, Any] = data["findings"]
        return ApiEnvelope(
            data=section,
            meta={
                # Counts, so a caller can see the shape of the answer without
                # reading it. Never a score: the verdict already carries a grade
                # and a second one would invite quoting the kinder of the two.
                "critical": sum(
                    1 for item in section.get("findings", []) if item["severity"] == "CRITICAL"
                ),
                "unmeasured": section.get("unmeasured", 0),
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

    def _source_labels(record: dict[str, Any]) -> tuple[str, ...]:
        """What the daily series being simulated actually is.

        The simulator cannot know, so the caller says. Without this every
        simulation carried `SAMPLE_DATA` — including one run over a real
        strategy's real trade ledger — which is the kind of label a reader
        learns to skip past, taking the meaningful ones with it.
        """
        labels = {str(item) for item in record.get("labels", ())}
        tier = str(record.get("evidence_tier") or "LEGACY_IN_SAMPLE")
        source = "REAL_DATA" if "REAL_DATA" in labels else "SYNTHETIC_DATA"
        return (source, f"EVIDENCE_TIER:{tier}")

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

        candidates: list[tuple[str, str, tuple[float, ...], tuple[str, ...]]] = []
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
            candidates.append((spec.strategy_id, spec.name, daily, _source_labels(latest)))

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
            for strategy_id, name, daily, source_labels in candidates:
                for rule in rules:
                    handle.progress(done, f"{name} vs {rule.display_name}")
                    simulation = simulate_prop_paths(
                        strategy_id,
                        rule,
                        daily,
                        seed=body.seed,
                        paths=body.paths,
                        allow_unverified=True,
                        source_labels=source_labels,
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
                    {
                        "strategy_id": sid,
                        "name": name,
                        "trading_days": len(daily),
                        "source_labels": list(source_labels),
                    }
                    for sid, name, daily, source_labels in candidates
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
            source_labels=_source_labels(latest),
        )

        # `simulation.outcomes` is a sample of the paths, not all of them. Every
        # aggregate here used to be computed from it and reported against the
        # full path count: a thousand accounts all failing on maximum loss
        # displayed as "maximum loss - 100 - 10.0%". The engine now summarises
        # the population, and this reads those summaries.
        terminal = [outcome.terminal_balance for outcome in simulation.outcomes]

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
                # Days-to-pass and days-to-fail come from `boundary_race`, which
                # the engine computes over every path with p10/median/p90. The
                # means that used to sit here were taken from the sample.
                "failure_reasons": dict(simulation.failure_reasons),
                # Explicitly a sample, and it says how large. The middle outcome
                # across *all* paths is `tail_risk.terminal_median`.
                "sampled_terminal_balances": terminal,
                "outcome_sample_size": simulation.outcome_sample_size,
                "median_terminal": simulation.tail_risk.terminal_median,
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

    def _action(name: str, arguments: dict[str, Any], *, confirmed: bool = False) -> dict[str, Any]:
        """Call an action and turn its refusal into the right HTTP status.

        An ActionError is a refusal with a reason written for a person, so it is
        returned verbatim rather than replaced with a generic message. 409 is
        deliberate: the request was well-formed and the application declined it,
        which is different from 422 (the caller sent nonsense) and from 500 (we
        broke).
        """
        try:
            return actions.call(name, arguments, confirmed=confirmed)
        except ApprovalRequired as exc:
            # A distinct code, because this is not a refusal the caller should
            # give up on: the call is queued and a person can still say yes. The
            # interface shows it as a pending item rather than as an error.
            raise HTTPException(
                409,
                {
                    "code": "approval_required",
                    "reason": str(exc),
                    "approval_id": exc.request.request_id,
                },
            ) from exc
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
            raise HTTPException(404, {"code": "unknown_dataset", "reason": str(exc)}) from exc

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

    # ── strategy trades on the chart ─────────────────────────────────────────
    # The feature this whole layer exists for: open a strategy, see what it
    # actually did, over the actual candles. Everything served here is read from
    # a backtest artifact — no value is recomputed, and none is invented.

    @router.get("/strategies/{strategy_id}/trades", response_model=ApiEnvelope[dict[str, Any]])
    def strategy_trades(
        strategy_id: str,
        backtest_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
        side: str = "all",
        outcome: str = "all",
        regime: str = "all",
        exit_reason: str = "all",
        limit: int = 4000,
        with_regimes: bool = True,
    ) -> ApiEnvelope[dict[str, Any]]:
        """The strategy's historical trades, as chart markers.

        Timestamps rather than bar indices, so the markers land correctly at any
        timeframe. A run written before excursion and levels were recorded
        reports them as null, and the chart draws nothing for them rather than
        drawing a zero.
        """
        try:
            payload = ledger_view.ledger(
                strategy_id,
                backtest_id=backtest_id,
                start=start,
                end=end,
                side=side,
                outcome=outcome,
                regime=regime,
                exit_reason=exit_reason,
                limit=max(1, min(int(limit), 20_000)),
                with_regimes=with_regimes,
            )
        except LedgerError as exc:
            raise HTTPException(404, {"code": "ledger_unavailable", "reason": str(exc)}) from exc
        return ApiEnvelope(
            data=payload,
            meta={
                "source": "backtest artifact",
                "note": (
                    "Every marker is a trade the backtest recorded. Levels are the "
                    "ones frozen when the position opened, not recomputed."
                ),
            },
        )

    @router.get(
        "/strategies/{strategy_id}/trades/{trade_id}",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def trade_detail(
        strategy_id: str, trade_id: str, backtest_id: str | None = None
    ) -> ApiEnvelope[dict[str, Any]]:
        """One trade, with the chain back to the run that produced it."""
        try:
            return ApiEnvelope(
                data=ledger_view.inspect(strategy_id, trade_id, backtest_id=backtest_id)
            )
        except LedgerError as exc:
            raise HTTPException(404, {"code": "trade_not_found", "reason": str(exc)}) from exc

    @router.get("/strategies/{strategy_id}/regimes", response_model=ApiEnvelope[dict[str, Any]])
    def strategy_regimes(
        strategy_id: str,
        backtest_id: str | None = None,
        attribution: str = "entry",
        descriptive: bool = False,
    ) -> ApiEnvelope[dict[str, Any]]:
        """Where this strategy's P&L actually came from, by market regime.

        `descriptive=true` takes volatility thresholds from the whole series,
        which uses information later than the bars it labels. It is available
        for slicing results already produced and is marked in every response
        that uses it; it is never the default.
        """
        if attribution not in ("entry", "dominant", "exit"):
            raise HTTPException(
                422,
                {"code": "bad_attribution", "reason": "one of entry, dominant, exit"},
            )
        try:
            payload = ledger_view.regime_report(
                strategy_id,
                backtest_id=backtest_id,
                attribution=attribution,
                descriptive=descriptive,
            )
        except LedgerError as exc:
            raise HTTPException(404, {"code": "regimes_unavailable", "reason": str(exc)}) from exc
        return ApiEnvelope(data=payload, meta={"attribution": attribution})

    @router.get("/strategies/{strategy_id}/resample", response_model=ApiEnvelope[dict[str, Any]])
    def strategy_resample(
        strategy_id: str,
        backtest_id: str | None = None,
        paths: int = 2000,
        seed: int = 20260908,
        attribution: str = "entry",
    ) -> ApiEnvelope[dict[str, Any]]:
        """One backtest turned into a distribution, twice over.

        IID and regime-aware, side by side. The gap between them is the point:
        losses cluster because the conditions that cause them persist, and an
        IID study of a regime-dependent strategy understates its drawdown.
        """
        try:
            payload = ledger_view.resolve(strategy_id, backtest_id)
            series, times, _ = ledger_view.classified(payload)
            trades = _Shim.many(payload)
            if len(trades) < 2:
                raise LedgerError("resampling needs at least two trades")
            marks = attribute_trades(trades, series, times)
            comparison = compare_resamples(
                trades,
                marks,
                series,
                paths=max(100, min(int(paths), 20_000)),
                seed=int(seed),
                attribution=attribution,
            )
        except (LedgerError, ValueError) as exc:
            raise HTTPException(409, {"code": "resample_unavailable", "reason": str(exc)}) from exc
        return ApiEnvelope(
            data={
                **comparison.model_dump(mode="json"),
                "strategy_id": strategy_id,
                "backtest_id": payload.get("backtest_id"),
            },
            meta={
                "note": (
                    "Resampling reorders the strategy's own realised trades. No "
                    "value here was invented."
                )
            },
        )

    # ── research lab ─────────────────────────────────────────────────────────
    # Questions asked of a ledger that already exists. Nothing here runs a
    # strategy, consumes a holdout, or produces a verdict — which is why it is
    # cheap to ask, and why every stored artifact says it is not evidence.

    @router.get("/lab/analyses", response_model=ApiEnvelope[list[dict[str, Any]]])
    def lab_catalogue() -> ApiEnvelope[list[dict[str, Any]]]:
        """What the lab can be asked, and what each question needs."""
        items = research_lab.catalogue()
        return ApiEnvelope(
            data=items,
            meta={
                "total": len(items),
                "note": (
                    "A bounded set rather than generated code: everything here is "
                    "something a person can run and a test can check."
                ),
            },
        )

    @router.post("/lab/run", response_model=ApiEnvelope[dict[str, Any]])
    def lab_run(body: AnalysisRequest) -> ApiEnvelope[dict[str, Any]]:
        """Answer one question about a strategy's trades."""
        try:
            payload = research_lab.run(
                body.analysis,
                body.strategy_id,
                backtest_id=body.backtest_id,
                measure=body.measure,
                hour_bucket=body.hour_bucket,
                save=body.save,
                note=body.note,
            )
        except LedgerError as exc:
            # A strategy or backtest id that does not resolve is the caller's
            # mistake, not a server fault. Uncaught, it left FastAPI to answer
            # with a plain-text 500 -- and `LedgerError` already carries the
            # sentence worth showing ("strategy 'x' has no backtest yet. Run one
            # and its trades will appear on the chart."), which the 500 threw
            # away. Every other ledger-backed endpoint already does this.
            raise HTTPException(404, {"code": "ledger_unavailable", "reason": str(exc)}) from exc
        except LabError as exc:
            raise HTTPException(422, {"code": "analysis_unavailable", "reason": str(exc)}) from exc
        return ApiEnvelope(
            data=payload,
            meta={
                "is_evidence": False,
                "note": payload.get("evidence_note", ""),
            },
        )

    @router.get("/lab/artifacts", response_model=ApiEnvelope[list[dict[str, Any]]])
    def lab_artifacts(strategy_id: str = "", limit: int = 100) -> ApiEnvelope[list[dict[str, Any]]]:
        """Questions that have been asked, newest first."""
        rows = research_lab.store.list(strategy_id, limit)
        return ApiEnvelope(
            data=rows,
            meta={"total": len(rows), "stored": research_lab.store.count()},
        )

    @router.get("/lab/artifacts/{artifact_id}", response_model=ApiEnvelope[dict[str, Any]])
    def lab_artifact(artifact_id: str) -> ApiEnvelope[dict[str, Any]]:
        payload = research_lab.store.get(artifact_id)
        if payload is None:
            raise HTTPException(404, {"code": "artifact_not_found"})
        return ApiEnvelope(data=payload, meta={"is_evidence": False})

    @router.delete("/lab/artifacts/{artifact_id}", response_model=ApiEnvelope[dict[str, Any]])
    def lab_delete(artifact_id: str) -> ApiEnvelope[dict[str, Any]]:
        """Discard a stored analysis.

        Safe in a way deleting evidence would not be: an analysis is derived,
        and re-running it against the same backtest reproduces it exactly.
        """
        if not research_lab.store.delete(artifact_id):
            raise HTTPException(404, {"code": "artifact_not_found"})
        return ApiEnvelope(
            data={"artifact_id": artifact_id, "deleted": True},
            meta={
                "note": (
                    "Derived record. The backtest it cites is untouched, and "
                    "re-running the analysis reproduces it."
                )
            },
        )

    @router.get("/lab/artifacts/{artifact_id}/trades", response_model=ApiEnvelope[dict[str, Any]])
    def lab_drilldown(
        artifact_id: str, coords: str, limit: int = 500
    ) -> ApiEnvelope[dict[str, Any]]:
        """The trades behind one cell.

        `coords` is comma-separated, one index per axis — so a surface cell at
        the third hour slot and the top volatility band is `2,4`. Read from the
        ids the analysis recorded rather than recomputed, so a drilldown cannot
        drift from the picture it came from.
        """
        try:
            parsed = [int(part) for part in coords.split(",") if part.strip() != ""]
        except ValueError as exc:
            raise HTTPException(
                422,
                {
                    "code": "bad_coords",
                    "reason": "coords is comma-separated integers, one per axis",
                },
            ) from exc
        try:
            return ApiEnvelope(data=research_lab.drilldown(artifact_id, parsed, limit=limit))
        except LabError as exc:
            raise HTTPException(404, {"code": "cell_not_found", "reason": str(exc)}) from exc

    @router.post("/lab/ask", response_model=ApiEnvelope[dict[str, Any]])
    def lab_ask(body: LabQuestionRequest) -> ApiEnvelope[dict[str, Any]]:
        """Answer a question asked in words, or say why it cannot be.

        The routing is a scored match against declared vocabulary rather than a
        model call, for two reasons. It can be tested, which no part of the lab
        should be without. And a model asked "which analysis answers this?" will
        answer *something* for a question none of them answer — the result comes
        back correctly computed over real trades, addressing a different
        question, with nothing on the screen to say so.

        A question that does not pick one analysis out clearly is refused, with
        what it did hear. `force` runs the top candidate anyway, which is a
        decision the caller makes rather than one made for them.
        """
        resolved = route_question(body.question)
        chosen = resolved.analysis or (
            resolved.candidates[0].analysis if body.force and resolved.candidates else None
        )
        if chosen is None:
            raise HTTPException(
                422,
                {
                    "code": "question_not_routed",
                    "reason": resolved.reason,
                    "question": body.question,
                    "candidates": [item.model_dump(mode="json") for item in resolved.candidates],
                    "available": research_lab.catalogue(),
                },
            )
        try:
            payload = research_lab.run(
                chosen,
                body.strategy_id,
                backtest_id=body.backtest_id,
                measure=resolved.measure,
                hour_bucket=body.hour_bucket,
                save=body.save,
                note=body.question,
            )
        except LedgerError as exc:
            raise HTTPException(404, {"code": "ledger_unavailable", "reason": str(exc)}) from exc
        except LabError as exc:
            raise HTTPException(422, {"code": "analysis_unavailable", "reason": str(exc)}) from exc
        return ApiEnvelope(
            data=payload,
            meta={
                "is_evidence": False,
                "note": payload.get("evidence_note", ""),
                "routed": {
                    "question": body.question,
                    "analysis": chosen,
                    "measure": resolved.measure,
                    "decisive": resolved.decisive,
                    "reason": resolved.reason,
                    "candidates": [
                        item.model_dump(mode="json") for item in resolved.candidates
                    ],
                },
            },
        )

    # ── research memory ──────────────────────────────────────────────────────
    # What the search has learned, kept with the artifact that said it. Never
    # evidence: the gate ladder does not read this, and there is no route from
    # a finding into a verdict.

    @router.post("/lab/findings", response_model=ApiEnvelope[dict[str, Any]])
    def promote_finding(body: PromoteFindingRequest) -> ApiEnvelope[dict[str, Any]]:
        """Promote one of an artifact's own findings into durable memory."""
        artifact = research_lab.store.get(body.artifact_id)
        if artifact is None:
            raise HTTPException(404, {"code": "artifact_not_found"})
        try:
            finding = knowledge.promote(
                artifact, body.statement, note=body.note, created_by="operator"
            )
        except KnowledgeError as exc:
            raise HTTPException(
                422, {"code": "not_a_computed_finding", "reason": str(exc)}
            ) from exc
        log.record(
            "MEMORY",
            f"remembered: {finding.statement[:90]}",
            "info",
            finding.finding_id,
        )
        return ApiEnvelope(
            data=finding.model_dump(mode="json"),
            meta={"is_evidence": False, "note": NOT_EVIDENCE_NOTE},
        )

    @router.get("/lab/findings", response_model=ApiEnvelope[list[dict[str, Any]]])
    def list_findings(
        strategy_id: str = "",
        q: str = "",
        include_withdrawn: bool = False,
        limit: int = 100,
    ) -> ApiEnvelope[list[dict[str, Any]]]:
        """What is known. Standing findings only, unless an audit asks for all."""
        rows = knowledge.recall(
            strategy_id=strategy_id,
            query=q,
            status=None if include_withdrawn else FindingStatus.STANDING,
            limit=limit,
        )
        return ApiEnvelope(
            data=as_payload(rows),
            meta={
                "total": len(rows),
                "counts": knowledge.counts(),
                "is_evidence": False,
                "note": NOT_EVIDENCE_NOTE,
            },
        )

    @router.post("/lab/findings/{finding_id}/retract", response_model=ApiEnvelope[dict[str, Any]])
    def retract_finding(
        finding_id: str, body: RetractFindingRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        """Withdraw a finding, keeping it readable with the reason attached.

        Deliberately not a delete. A finding that vanished would leave a later
        reader unable to tell it from one nobody ever made.
        """
        try:
            finding = knowledge.retract(finding_id, body.reason)
        except KnowledgeError as exc:
            raise HTTPException(404, {"code": "finding_not_found", "reason": str(exc)}) from exc
        return ApiEnvelope(data=finding.model_dump(mode="json"), meta={"is_evidence": False})

    @router.post(
        "/lab/findings/{finding_id}/supersede", response_model=ApiEnvelope[dict[str, Any]]
    )
    def supersede_finding(
        finding_id: str, body: SupersedeFindingRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        try:
            finding = knowledge.supersede(finding_id, body.by_finding_id)
        except KnowledgeError as exc:
            raise HTTPException(422, {"code": "cannot_supersede", "reason": str(exc)}) from exc
        return ApiEnvelope(data=finding.model_dump(mode="json"), meta={"is_evidence": False})

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
        # `restore_session` is what makes a workspace survive a restart: it
        # returns the last one open, falling back to the operator's default, and
        # marks whichever it found as active. It still creates nothing — a
        # genuinely empty installation gets an honest null.
        active = workspaces.restore_session()
        return ApiEnvelope(
            data=None if active is None else actions.call("describe_workspace"),
            meta={
                "count": workspaces.count(),
                "default_workspace_id": workspaces.default_id(),
            },
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
            "purpose",
            "markets",
            "style",
            "sessions",
            "research",
            "risk",
            "datasets",
            "preferred_export",
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
                    "description": body.description,
                    "icon": body.icon,
                    "mode": body.mode,
                    "sidebar_items": body.sidebar_items,
                },
            )
        )

    # ── the sidebar ──────────────────────────────────────────────────────────
    # Every one of these is a thin call into the action registry, which is the
    # same registry the assistant uses. There is no separate implementation for
    # the interface, so "put my prop accounts on the right" typed to the
    # assistant and dragged in the rail are the same operation and cannot drift.

    @router.get("/sidebar/destinations", response_model=ApiEnvelope[dict[str, Any]])
    def sidebar_destinations_route() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("list_sidebar_destinations", {}))

    @router.get(
        "/workspaces/{workspace_id}/sidebar", response_model=ApiEnvelope[dict[str, Any]]
    )
    def get_sidebar(workspace_id: str) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("describe_sidebar", {"workspace_id": workspace_id}))

    @router.post(
        "/workspaces/{workspace_id}/sidebar/items", response_model=ApiEnvelope[dict[str, Any]]
    )
    def add_sidebar_item(
        workspace_id: str, body: SidebarItemRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "add_sidebar_item",
                {
                    "workspace_id": workspace_id,
                    "route": body.route,
                    "group_id": body.group_id,
                    "label": body.label,
                    "position": body.position,
                },
            )
        )

    @router.delete(
        "/workspaces/{workspace_id}/sidebar/items/{route}",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def remove_sidebar_item(workspace_id: str, route: str) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "remove_sidebar_item", {"workspace_id": workspace_id, "route": route}
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/sidebar/items/{route}/move",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def move_sidebar_item(
        workspace_id: str, route: str, body: SidebarMoveRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "move_sidebar_item",
                {
                    "workspace_id": workspace_id,
                    "route": route,
                    "group_id": body.group_id,
                    "position": body.position,
                },
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/sidebar/items/{route}/rename",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def rename_sidebar_item(
        workspace_id: str, route: str, body: RenameWorkspaceRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "rename_sidebar_item",
                {"workspace_id": workspace_id, "route": route, "label": body.name},
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/sidebar/items/{route}/pin",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def pin_sidebar_item(
        workspace_id: str, route: str, body: SidebarFlagRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "pin_sidebar_item",
                {"workspace_id": workspace_id, "route": route, "pinned": body.value},
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/sidebar/items/{route}/hide",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def hide_sidebar_item(
        workspace_id: str, route: str, body: SidebarFlagRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "hide_sidebar_item",
                {"workspace_id": workspace_id, "route": route, "hidden": body.value},
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/sidebar/groups", response_model=ApiEnvelope[dict[str, Any]]
    )
    def add_sidebar_group(
        workspace_id: str, body: SidebarGroupRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "add_sidebar_group",
                {
                    "workspace_id": workspace_id,
                    "group_id": body.group_id,
                    "label": body.label or body.group_id,
                    "position": body.position,
                },
            )
        )

    @router.delete(
        "/workspaces/{workspace_id}/sidebar/groups/{group_id}",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def remove_sidebar_group(workspace_id: str, group_id: str) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "remove_sidebar_group", {"workspace_id": workspace_id, "group_id": group_id}
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/sidebar/groups/{group_id}/rename",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def rename_sidebar_group(
        workspace_id: str, group_id: str, body: RenameWorkspaceRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "rename_sidebar_group",
                {"workspace_id": workspace_id, "group_id": group_id, "label": body.name},
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/sidebar/groups/{group_id}/collapse",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def collapse_sidebar_group(
        workspace_id: str, group_id: str, body: SidebarFlagRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "collapse_sidebar_group",
                {"workspace_id": workspace_id, "group_id": group_id, "collapsed": body.value},
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/sidebar/groups/order",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def reorder_sidebar_groups(
        workspace_id: str, body: SidebarOrderRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "reorder_sidebar_groups",
                {"workspace_id": workspace_id, "group_ids": body.group_ids},
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/sidebar/reset", response_model=ApiEnvelope[dict[str, Any]]
    )
    def reset_sidebar(workspace_id: str) -> ApiEnvelope[dict[str, Any]]:
        """Rebuild the mode's rail, discarding a custom one.

        A CONFIRM action, and the confirmation is the request: the interface
        asks before calling, the same way deleting a layout does. Throwing away
        an arrangement somebody built is not something to do on a stray click.
        """
        return ApiEnvelope(
            data=_action("reset_sidebar", {"workspace_id": workspace_id}, confirmed=True)
        )

    @router.post(
        "/workspaces/{workspace_id}/campaigns", response_model=ApiEnvelope[dict[str, Any]]
    )
    def link_campaign(
        workspace_id: str, body: WorkspaceLinkRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "link_campaign_to_workspace",
                {
                    "workspace_id": workspace_id,
                    "campaign_id": body.target_id,
                    "linked": body.linked,
                },
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/accounts", response_model=ApiEnvelope[dict[str, Any]]
    )
    def link_account(
        workspace_id: str, body: WorkspaceLinkRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "link_account_to_workspace",
                {
                    "workspace_id": workspace_id,
                    "account_id": body.target_id,
                    "linked": body.linked,
                },
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/describe", response_model=ApiEnvelope[dict[str, Any]]
    )
    def describe_workspace_route(
        workspace_id: str, body: WorkspaceDescribeRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "describe_this_workspace",
                {
                    "workspace_id": workspace_id,
                    "description": body.description,
                    "icon": body.icon,
                },
            )
        )

    @router.post("/workspaces/{workspace_id}/pin", response_model=ApiEnvelope[dict[str, Any]])
    def pin_workspace(
        workspace_id: str, body: SidebarFlagRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action("pin_workspace", {"workspace_id": workspace_id, "pinned": body.value})
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

    @router.post("/workspaces/{workspace_id}/default", response_model=ApiEnvelope[dict[str, Any]])
    def set_default_workspace(workspace_id: str) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("set_default_workspace", {"workspace_id": workspace_id}))

    @router.get("/workspaces/{workspace_id}/export", response_model=ApiEnvelope[dict[str, Any]])
    def export_workspace(workspace_id: str) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("export_workspace", {"workspace_id": workspace_id}))

    @router.post("/workspaces/import", response_model=ApiEnvelope[dict[str, Any]])
    def import_workspace(body: ImportWorkspaceRequest) -> ApiEnvelope[dict[str, Any]]:
        arguments: dict[str, Any] = {"document": body.document}
        if body.name:
            arguments["name"] = body.name
        return ApiEnvelope(data=_action("import_workspace", arguments))

    @router.get("/workspaces/{workspace_id}/history", response_model=ApiEnvelope[dict[str, Any]])
    def workspace_history(workspace_id: str) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("workspace_history", {"workspace_id": workspace_id}))

    @router.post(
        "/workspaces/{workspace_id}/history/{version}/restore",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def restore_workspace_version(workspace_id: str, version: int) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "restore_workspace_version",
                {"workspace_id": workspace_id, "version": version},
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/history/{version}/duplicate",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def duplicate_workspace_version(
        workspace_id: str, version: int, body: RenameWorkspaceRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "duplicate_workspace_version",
                {"workspace_id": workspace_id, "version": version, "name": body.name},
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/panels/{panel_id}/collapse",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def collapse_panel(
        workspace_id: str, panel_id: str, body: CollapsePanelRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "collapse_panel",
                {
                    "workspace_id": workspace_id,
                    "panel_id": panel_id,
                    "collapsed": body.collapsed,
                },
            )
        )

    @router.post(
        "/workspaces/{workspace_id}/panels/{panel_id}/order",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def reorder_panel(
        workspace_id: str, panel_id: str, body: ReorderPanelRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "reorder_panel",
                {
                    "workspace_id": workspace_id,
                    "panel_id": panel_id,
                    "position": body.position,
                },
            )
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

    def _passport_sources(strategy_id: str) -> PassportSources:
        """Gather what exists for one strategy. Anything unreachable stays absent.

        Every field is read from an artefact that already exists — the spec, the
        dossier's verdict, the validation evidence, the desk's allocation. None
        is computed here, and a field this cannot fill is left empty so the
        passport shows it as unmeasured rather than as a plausible default.
        """
        from forge_api.dossier import build_dossier, latest_verdict

        sources: dict[str, Any] = {"strategy_id": strategy_id}
        with suppress(KeyError, FileNotFoundError, OSError):
            spec = library.get_spec(strategy_id)
            sources.update(
                name=spec.name,
                hypothesis=spec.hypothesis,
                falsification=spec.falsifiable_prediction,
                instruments=(spec.symbol,) if spec.symbol else (),
                timeframe=spec.bar_spec,
                lineage=tuple(spec.lineage),
            )
        dossier: dict[str, Any] = {}
        with suppress(KeyError, ValueError, OSError):
            dossier = build_dossier(
                root=root,
                library=library,
                store=store,
                experiments=engine.experiments,
                memory=engine.memory,
                snapshots=engine.snapshots,
                scope=engine._scope(),
                strategy_id=strategy_id,
            )
        with suppress(KeyError, ValueError, OSError):
            # The same verdict the dossier shows, through the same function, so
            # the passport cannot come to disagree with the Evidence screen.
            spec = library.get_spec(strategy_id)
            verdict, _reason = latest_verdict(
                root=root,
                library=library,
                experiments=engine.experiments,
                scope=engine._scope(),
                spec=spec,
                runs=store.for_strategy(strategy_id),
            )
            if verdict is not None:
                sources["verdict"] = verdict
        validation = dossier.get("validation") or {}
        if validation.get("available"):
            walk_forward = validation.get("walk_forward")
            paths = validation.get("paths")
            if isinstance(walk_forward, dict):
                sources["walk_forward"] = walk_forward
            if isinstance(paths, dict):
                sources["monte_carlo"] = paths
            if validation.get("configurations") is not None:
                sources["parameter_surface"] = {
                    "configurations": validation.get("configurations"),
                    "probability_of_overfitting": validation.get(
                        "probability_of_overfitting"
                    ),
                    "cscv_splits": validation.get("cscv_splits"),
                    "best_parameters": validation.get("best_parameters"),
                }
        backtests = dossier.get("backtests") or {}
        if backtests.get("available"):
            runs = backtests.get("runs") or []
            tiers = {str(run.get("evidence_tier")) for run in runs}
            sources["data_summary"] = (
                f"{backtests.get('count', len(runs))} run(s) over "
                f"{', '.join(sorted(tiers)) or 'no recorded tier'}"
            )
        evidence = desk_evidence(strategy_id)
        series = evidence.get("oos_daily_pnl") or ()
        if series:
            from forge.propdesk.survival import Unmeasurable, drawdown_distribution

            distribution = drawdown_distribution(tuple(series))
            if not isinstance(distribution, Unmeasurable):
                sources["expected_drawdown_p95"] = distribution.p95
        allocation = prop_desk.store.allocations()
        placed = [item for item in allocation if item.strategy_id == strategy_id]
        if placed:
            sources["deployment_state"] = (
                f"allocated to {len(placed)} account(s) at "
                f"{placed[0].contracts} contract(s)"
            )
            sources["deployment_detail"] = placed[0].rationale
        sources["assembled_at"] = datetime.now(UTC)
        return PassportSources(**sources)

    # ── modes ────────────────────────────────────────────────────────────────
    # The shell reads these to draw the home screen and the mode's navigation.
    # Everything they return comes from `forge.modes`, so the interface and an
    # agent asking the same question get the same answer.

    @router.get("/modes", response_model=ApiEnvelope[dict[str, Any]])
    def list_modes() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data={"modes": mode_catalogue(), "loop": describe_loop()},
            meta={"order": [mode.value for mode in MODE_ORDER]},
        )

    @router.get("/modes/session", response_model=ApiEnvelope[dict[str, Any]])
    def mode_session() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("current_mode", {}))

    @router.post("/modes/{mode}/enter", response_model=ApiEnvelope[dict[str, Any]])
    def enter_mode(mode: str, body: EnterModeRequest) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action("enter_mode", {"mode": mode, "stance": body.stance}, confirmed=True)
        )

    @router.post("/modes/stance", response_model=ApiEnvelope[dict[str, Any]])
    def set_stance(body: StanceRequest) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("set_stance", {"stance": body.stance}, confirmed=True))

    @router.post("/modes/leave", response_model=ApiEnvelope[dict[str, Any]])
    def leave_mode() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("leave_mode", {}, confirmed=True))

    @router.get("/modes/expertise", response_model=ApiEnvelope[dict[str, Any]])
    def expertise_levels() -> ApiEnvelope[dict[str, Any]]:
        """Guided, Advanced and Quant, and what each one surfaces.

        Declared server-side for the same reason the mode manifest is: an agent
        asked "what can the operator see right now" has to be able to answer
        without a person, and two copies of this mapping is a mapping that
        drifts.
        """
        return ApiEnvelope(
            data={
                "levels": expertise_catalogue(),
                "always_visible": [surface.value for surface in ALWAYS_VISIBLE],
            }
        )

    @router.get("/modes/intents", response_model=ApiEnvelope[dict[str, Any]])
    def front_door(mode: str | None = None) -> ApiEnvelope[dict[str, Any]]:
        """"What do you want to do?", answered with registered actions.

        `mode` narrows to the intents that have a route in that mode. Each entry
        names the actions it would run — the same actions the interface calls,
        under the same permission policy.
        """
        try:
            return ApiEnvelope(data={"intents": intent_catalogue(mode)})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"reason": str(exc)}) from exc

    @router.get("/explain/metrics", response_model=ApiEnvelope[dict[str, Any]])
    def metric_glossary(keys: str = "") -> ApiEnvelope[dict[str, Any]]:
        """What a metric is, why it matters, and what its caveats are.

        `keys` is a comma-separated subset. Every entry names the function that
        computes it and reads its threshold from the module that enforces it, so
        a changed bar changes the glossary rather than making it wrong.
        """
        selected = tuple(key.strip() for key in keys.split(",") if key.strip())
        return ApiEnvelope(data={"metrics": metric_catalogue(selected)})

    @router.get("/explain/metrics/{key}", response_model=ApiEnvelope[dict[str, Any]])
    def metric_value(key: str, value: float | None = None) -> ApiEnvelope[dict[str, Any]]:
        """What one value of one metric means, here."""
        try:
            return ApiEnvelope(data=interpret_metric(key, value).as_dict())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"reason": str(exc)}) from exc

    @router.get("/explain/questions", response_model=ApiEnvelope[dict[str, Any]])
    def explain_questions() -> ApiEnvelope[dict[str, Any]]:
        """Every "why" the system can answer, and what each one needs."""
        return ApiEnvelope(data={"questions": question_catalogue()})

    @router.get(
        "/explain/passport/{strategy_id}", response_model=ApiEnvelope[dict[str, Any]]
    )
    def strategy_passport(
        strategy_id: str, depth: str = "advanced"
    ) -> ApiEnvelope[dict[str, Any]]:
        """Every piece of evidence about one strategy, at one depth.

        It composes and computes nothing: each section names the artefact it
        came from, and a section that was never measured is present, marked
        unmeasured, with what would measure it — so an unvalidated passport
        reads as weaker rather than shorter.
        """
        try:
            chosen = PassportDepth(depth)
        except ValueError as exc:
            valid = ", ".join(item.value for item in PassportDepth)
            raise HTTPException(
                status_code=422, detail={"reason": f"no depth '{depth}'. Depths: {valid}"}
            ) from exc
        return ApiEnvelope(
            data=build_passport(_passport_sources(strategy_id)).at_depth(chosen).as_dict()
        )

    @router.get("/modes/permissions", response_model=ApiEnvelope[dict[str, Any]])
    def mode_permissions() -> ApiEnvelope[dict[str, Any]]:
        """What an AI actor may do with each registered action, right now.

        Computed from the same policy that enforces it rather than described
        separately, because a permissions screen that can disagree with the
        enforcement is worse than none.
        """
        mode, stance = actions.context()
        rows = []
        for schema in actions.schemas():
            judgement = actions.permission(schema["name"], actor=Actor.AI)
            rows.append(
                {
                    "action": schema["name"],
                    "summary": schema["description"],
                    "mutating": schema["mutating"],
                    "risk": schema["risk"],
                    "protected": schema["protected"],
                    "ruling": judgement.ruling.value,
                    "reason": judgement.reason,
                }
            )
        return ApiEnvelope(
            data={"mode": mode.value, "stance": stance.value if stance else None, "actions": rows}
        )

    # ── prop accounts ────────────────────────────────────────────────────────
    @router.get("/prop/accounts", response_model=ApiEnvelope[dict[str, Any]])
    def prop_accounts_list() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("list_prop_accounts", {}))

    @router.post("/prop/accounts", response_model=ApiEnvelope[dict[str, Any]])
    def prop_account_create(body: PropAccountRequest) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action("create_prop_account", {"rules": body.rules}, confirmed=True)
        )

    @router.put("/prop/accounts/{account_id}", response_model=ApiEnvelope[dict[str, Any]])
    def prop_account_update(
        account_id: str, body: PropAccountRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "update_prop_rules",
                {"account_id": account_id, "rules": body.rules},
                confirmed=True,
            )
        )

    @router.post("/prop/accounts/{account_id}/select", response_model=ApiEnvelope[dict[str, Any]])
    def prop_account_select(account_id: str) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action("select_prop_account", {"account_id": account_id}, confirmed=True)
        )

    @router.post("/prop/accounts/{account_id}/state", response_model=ApiEnvelope[dict[str, Any]])
    def prop_account_record(
        account_id: str, body: PropStateRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "record_prop_state",
                {"account_id": account_id, "state": body.state, "source": body.source},
                confirmed=True,
            )
        )

    @router.get("/prop/accounts/status", response_model=ApiEnvelope[dict[str, Any]])
    def prop_account_status(
        account_id: str | None = None, proposed_contracts: int = 0
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "prop_account_status",
                {"account_id": account_id, "proposed_contracts": proposed_contracts},
            )
        )

    @router.get("/prop/accounts/{account_id}/history", response_model=ApiEnvelope[list[Any]])
    def prop_account_history(account_id: str) -> ApiEnvelope[list[Any]]:
        if prop_accounts.get(account_id) is None:
            raise HTTPException(404, f"No prop account '{account_id}'.")
        return ApiEnvelope(data=prop_accounts.history(account_id))

    @router.post(
        "/prop/accounts/replay/{strategy_id}", response_model=ApiEnvelope[dict[str, Any]]
    )
    def prop_account_replay(
        strategy_id: str, account_id: str | None = None
    ) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "assess_prop_account", {"strategy_id": strategy_id, "account_id": account_id}
            )
        )

    # ── the fund ─────────────────────────────────────────────────────────────
    @router.get("/fund/state", response_model=ApiEnvelope[dict[str, Any]])
    def fund_state() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("fund_state", {}))

    @router.get("/fund/config", response_model=ApiEnvelope[dict[str, Any]])
    def read_fund_config() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("fund_config", {}))

    @router.put("/fund/config", response_model=ApiEnvelope[dict[str, Any]])
    def write_fund_config(body: FundConfigRequest) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action(
                "set_fund_config", {"config": body.config, "note": body.note}, confirmed=True
            )
        )

    @router.get("/fund/signals", response_model=ApiEnvelope[dict[str, Any]])
    def fund_signals() -> ApiEnvelope[dict[str, Any]]:
        signals, notes = fund.signals()
        return ApiEnvelope(
            data={
                "signals": [signal.model_dump(mode="json") for signal in signals],
                "limitations": notes,
            }
        )

    @router.post("/fund/portfolio", response_model=ApiEnvelope[dict[str, Any]])
    def construct_portfolio(body: ConstructRequest) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("construct_portfolio", {"capital": body.capital}))

    @router.get("/fund/risk", response_model=ApiEnvelope[dict[str, Any]])
    def fund_risk(portfolio_id: str | None = None) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("calculate_risk", {"portfolio_id": portfolio_id}))

    @router.post("/fund/orders/prepare", response_model=ApiEnvelope[dict[str, Any]])
    def prepare_orders(body: PortfolioRequest) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("prepare_orders", {"portfolio_id": body.portfolio_id}))

    @router.post("/fund/orders/screen", response_model=ApiEnvelope[dict[str, Any]])
    def screen_orders(body: PortfolioRequest) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("screen_orders", {"portfolio_id": body.portfolio_id}))

    @router.get("/fund/orders/screened", response_model=ApiEnvelope[list[Any]])
    def screened_orders() -> ApiEnvelope[list[Any]]:
        return ApiEnvelope(data=fund.screened())

    @router.post("/fund/orders/submit", response_model=ApiEnvelope[dict[str, Any]])
    def submit_orders(body: SubmitOrdersRequest) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=_action("submit_orders", {"order_ids": body.order_ids}, confirmed=True),
            meta={
                "execution": "SIMULATED",
                "note": (
                    "Fills come from a local simulator. No broker, venue or OMS vendor is "
                    "connected in this build."
                ),
            },
        )

    @router.post("/fund/orders/{order_id}/cancel", response_model=ApiEnvelope[dict[str, Any]])
    def cancel_order(order_id: str) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("cancel_order", {"order_id": order_id}, confirmed=True))

    @router.get("/fund/operations", response_model=ApiEnvelope[dict[str, Any]])
    def fund_operations() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("fund_operations", {}))

    @router.get("/fund/performance", response_model=ApiEnvelope[dict[str, Any]])
    def fund_performance() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("fund_performance", {}))

    # ── approvals and audit ──────────────────────────────────────────────────
    @router.get("/approvals", response_model=ApiEnvelope[dict[str, Any]])
    def list_approvals() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("pending_approvals", {}))

    @router.post("/approvals/{request_id}/approve", response_model=ApiEnvelope[dict[str, Any]])
    def approve(request_id: str, body: DecisionRequest) -> ApiEnvelope[dict[str, Any]]:
        """Run a held action as the operator.

        409 rather than 404 on an expired or already-decided request: the id was
        real, and the reason it cannot run now is the thing the caller needs to
        read.
        """
        try:
            decided = actions.approve(request_id, note=body.note or "")
        except ApprovalError as exc:
            raise HTTPException(409, {"code": "approval_refused", "reason": str(exc)}) from exc
        except ActionError as exc:
            raise HTTPException(409, {"code": "action_refused", "reason": str(exc)}) from exc
        return ApiEnvelope(data=decided.as_dict())

    @router.post("/approvals/{request_id}/reject", response_model=ApiEnvelope[dict[str, Any]])
    def reject(request_id: str, body: DecisionRequest) -> ApiEnvelope[dict[str, Any]]:
        try:
            decided = approvals.reject(request_id, note=body.note or "")
        except ApprovalError as exc:
            raise HTTPException(409, {"code": "approval_refused", "reason": str(exc)}) from exc
        return ApiEnvelope(data=decided.as_dict())

    @router.get("/audit", response_model=ApiEnvelope[dict[str, Any]])
    def read_audit(limit: int = 100) -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=_action("audit_log", {"limit": limit}))

    # ── research campaigns ───────────────────────────────────────────────────
    def start_campaign_engine(campaign: Any, settings: Any) -> None:
        """Start the search machinery for a campaign.

        The campaign supplies the dataset and the seed; the request supplies how
        hard to run. Injected into the campaign router so that module never
        touches the engine, and so "start a campaign" is one call rather than a
        sequence the interface has to get right.

        Starting a campaign while the engine is **already running** does not
        restart it. `AutonomousEngine.start` returns the current status when its
        threads are alive, so a second campaign joins the run in progress and the
        orchestrator begins dealing it workers on the next cycle. Restarting
        would abandon every claim the first campaign's workers were holding, and
        a campaign added mid-run would silently interrupt one that was working.
        """
        already = engine.status().get("running")
        engine.start(
            EngineConfig(
                dataset=campaign.dataset,
                cycle_seconds=settings.cycle_seconds,
                max_strategies=settings.max_strategies,
                workers=settings.workers,
                max_bars=settings.max_bars,
                seed=campaign.seed,
            )
        )
        if already:
            log.record(
                "RESEARCH",
                f"'{campaign.name}' joined the run already in progress; "
                f"{len(campaign_service.campaigns.running())} campaign(s) now share "
                f"{engine.state.config.workers} worker(s)",
                "info",
            )

    router.include_router(
        build_campaign_router(
            campaign_service,
            start_engine=start_campaign_engine,
            stop_engine=engine.stop,
        )
    )
    # The registry gets the same service the routes got, now that the engine
    # hooks are attached to it. Attached here rather than passed to the
    # constructor because `start_campaign_engine` is defined in terms of the
    # engine, which is built after the registry — and the alternative, a second
    # way for an agent to start a campaign, is the thing this closes.
    actions.campaigns = campaign_service

    return ControlSurface(
        router=router,
        engine=engine,
        market=market,
        agents=agents,
        actions=actions,
        orchestrator=orchestrator,
        modes=modes,
        fund=fund,
        approvals=approvals,
        audit=audit,
        campaigns=campaign_service,
        prop_desk=prop_desk,
    )
