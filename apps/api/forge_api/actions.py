"""The verbs AlgoForge can perform on its own behalf.

This module exists because of a specific, correct complaint from the console
assistant: asked to find papers, add families and start backtesting, it replied
that it could do none of those things and was right — it had no tools, only a
context blob.

Everything the interface can do is now also reachable as a named action with a
declared schema. One registry serves three callers:

* the **console assistant**, which turns a request into an action call;
* the **orchestrator**, which sequences actions into a mission;
* the **HTTP API**, so nothing here is a private back door.

Three properties are deliberate:

* **Bounded.** The set is fixed and each action validates its own arguments.
  There is no "run arbitrary code" verb and no way to reach one.
* **Honest.** An action that cannot do the thing returns a refusal with a reason,
  never a plausible-sounding success. `create_family` on a data requirement the
  installed providers cannot serve registers a *blocked* family and says so.
* **Logged.** Every call lands in the activity log, so an autonomous run leaves
  the same trail as an operator clicking.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from forge.research import chronological_split
from forge.strategy import TEMPLATES, TemplateRejected, run_backtest
from forge.vault import VaultMirror
from forge.workstation import (
    GRID_COLUMNS,
    MAX_ROWS,
    Panel,
    PanelKind,
    Workspace,
    WorkspaceStore,
    catalogue,
    new_panel_id,
    template,
)

from forge_api.jobs import REGISTRY, JobHandle


class ActionError(Exception):
    """A refusal with a reason the caller can show verbatim."""


class ActionRisk(StrEnum):
    """How much a caller should have to mean it.

    Separate from `mutating`, which only says whether something is written.
    Rearranging panels writes to a database and is still nothing to worry
    about; cancelling an order writes no more than that and is not. What
    matters is what is lost if the caller misunderstood.
    """

    #: Layout, opening records, changing a chart's symbol. Reversible by doing
    #: the opposite, and nothing downstream depends on it.
    SAFE = "safe"
    #: Destroys something a person made, or changes configuration that outlives
    #: the session. Recoverable only if they happen to have a backup.
    CONFIRM = "confirm"
    #: Reaches a broker, an account, or real money. No agent may perform one of
    #: these on inference alone, ever.
    HIGH = "high"


@dataclass(frozen=True)
class Action:
    name: str
    summary: str
    parameters: dict[str, Any]
    run: Callable[..., dict[str, Any]]
    mutating: bool = False
    risk: ActionRisk = ActionRisk.SAFE

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.summary,
            "mutating": self.mutating,
            "risk": str(self.risk),
            "requires_confirmation": self.risk is not ActionRisk.SAFE,
            "parameters": {
                "type": "object",
                "properties": self.parameters,
                "required": [
                    key
                    for key, value in self.parameters.items()
                    if not value.get("optional", False)
                ],
            },
        }


def _str(value: Any, field: str, *, limit: int = 4000, lower: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActionError(f"'{field}' must be a non-empty string.")
    text = value.strip()[:limit]
    return text.lower() if lower else text


class Actions:
    """The registry. Constructed once and shared."""

    def __init__(
        self,
        *,
        workspace: Any,
        library: Any,
        store: Any,
        log: Any,
        market: Any,
        engine: Any,
        agents: Any,
        families: Any,
        templates: Any,
        mirror: VaultMirror,
        workspaces: WorkspaceStore,
    ) -> None:
        self.workspace = workspace
        self.library = library
        self.store = store
        self.log = log
        self.market = market
        self.engine = engine
        self.agents = agents
        self.families = families
        self.templates = templates
        self.mirror = mirror
        self.workspaces = workspaces
        self._lock = threading.Lock()
        self.history: list[dict[str, Any]] = []
        self._registry: dict[str, Action] = {}
        self._register_all()

    # ── dispatch ─────────────────────────────────────────────────────────────
    def names(self) -> list[str]:
        return sorted(self._registry)

    def schemas(self) -> list[dict[str, Any]]:
        return [self._registry[name].schema() for name in self.names()]

    def call(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Run one action.

        `confirmed` is the operator's answer, not the caller's opinion. Anything
        above SAFE refuses without it, and the refusal names the tier so the
        surface asking can show the right prompt. An agent cannot set this on
        its own behalf: it has to come back through a person, which is the whole
        point of the boundary.
        """
        action = self._registry.get(name)
        if action is None:
            raise ActionError(f"No action named '{name}'. Available: {', '.join(self.names())}.")
        if action.risk is not ActionRisk.SAFE and not confirmed:
            raise ActionError(
                f"'{name}' is a {action.risk} action and needs explicit confirmation "
                f"from the operator before it will run."
            )
        args = dict(arguments or {})
        unknown = set(args) - set(action.parameters)
        if unknown:
            raise ActionError(
                f"'{name}' does not take {', '.join(sorted(unknown))}. "
                f"It takes: {', '.join(sorted(action.parameters)) or 'no arguments'}."
            )
        started = time.time()
        try:
            result = action.run(**args)
            record = {"action": name, "ok": True, "arguments": args, "result": result}
        except ActionError as exc:
            record = {"action": name, "ok": False, "arguments": args, "error": str(exc)}
        except Exception as exc:
            record = {
                "action": name,
                "ok": False,
                "arguments": args,
                "error": f"{type(exc).__name__}: {exc}",
            }
        record["elapsed_seconds"] = round(time.time() - started, 3)
        record["at"] = time.time()
        with self._lock:
            self.history.append(record)
            del self.history[:-200]
        if action.mutating:
            self.log.record(
                "ACTION",
                f"{name} — {'ok' if record['ok'] else record.get('error', 'failed')}"[:400],
                "info" if record["ok"] else "fail",
            )
        if not record["ok"]:
            raise ActionError(str(record["error"]))
        return dict(result)

    def recent(self, limit: int = 40) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.history[-limit:])[::-1]

    # ── registration ─────────────────────────────────────────────────────────
    def _add(
        self,
        name: str,
        summary: str,
        parameters: dict[str, Any],
        run: Callable[..., dict[str, Any]],
        *,
        mutating: bool = False,
        risk: ActionRisk = ActionRisk.SAFE,
    ) -> None:
        self._registry[name] = Action(name, summary, parameters, run, mutating, risk)

    def _register_all(self) -> None:
        self._add(
            "search_papers",
            "Search Crossref for quantitative-finance research and save the metadata and "
            "any available abstract into the research library. Never claims to have read "
            "the full paper.",
            {
                "query": {
                    "type": "string",
                    "description": "A mechanism or topic, at least three characters.",
                }
            },
            self.search_papers,
            mutating=True,
        )
        self._add(
            "list_families",
            "List every strategy family, whether it is runnable on the configured data, "
            "and which templates implement it.",
            {},
            self.list_families,
        )
        self._add(
            "create_family",
            "Register a new strategy family. Requires a stated economic mechanism. A "
            "family needing data the app cannot serve is registered blocked, not runnable.",
            {
                "key": {"type": "string", "description": "lowercase_with_underscores"},
                "label": {"type": "string", "description": "Display name."},
                "mechanism": {
                    "type": "string",
                    "description": "Why this should work, economically. 40 characters minimum.",
                },
                "description": {"type": "string", "optional": True},
                "data_requirements": {
                    "type": "array",
                    "items": {"type": "string"},
                    "optional": True,
                    "description": "Defaults to ['BARS'].",
                },
            },
            self.create_family,
            mutating=True,
        )
        self._add(
            "list_templates",
            "List every executable strategy template with its family, parameters and grid size.",
            {"family": {"type": "string", "optional": True}},
            self.list_templates,
        )
        self._add(
            "create_template",
            "Register a new executable template. The source must define entry_signal(w, p) "
            "and exit_signal(w, p, pos), pass the static guard, and run on synthetic bars "
            "before it is accepted.",
            {
                "key": {"type": "string"},
                "name": {"type": "string"},
                "family": {"type": "string"},
                "hypothesis": {"type": "string", "description": "Mechanism, 40+ characters."},
                "falsifiable_prediction": {
                    "type": "string",
                    "description": "What result would abandon it. 30+ characters.",
                },
                "parameters": {
                    "type": "array",
                    "description": "Objects: name, default, low, high, step, description.",
                    "items": {"type": "object"},
                },
                "source": {"type": "string", "description": "The Python module."},
                "warmup_bars": {"type": "integer", "optional": True},
            },
            self.create_template,
            mutating=True,
        )
        self._add(
            "create_strategy",
            "Write a strategy to disk from a template, with parameters inside the "
            "template's declared ranges.",
            {
                "template": {"type": "string"},
                "name": {"type": "string", "optional": True},
                "parameters": {"type": "object", "optional": True},
                "hypothesis": {"type": "string", "optional": True},
                "research_sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "optional": True,
                },
            },
            self.create_strategy,
            mutating=True,
        )
        self._add(
            "backtest_strategy",
            "Backtest a strategy on real bars: development first, and the reserved "
            "validation slice only if development survives. Runs as a background job.",
            {
                "strategy_id": {"type": "string"},
                "dataset": {"type": "string", "optional": True},
                "max_bars": {"type": "integer", "optional": True},
            },
            self.backtest_strategy,
            mutating=True,
        )
        self._add(
            "list_strategies",
            "List strategies with their latest result, newest first.",
            {"limit": {"type": "integer", "optional": True}},
            self.list_strategies,
        )
        self._add(
            "engine_status",
            "What the autonomous engine is doing right now, per worker.",
            {},
            self.engine_status,
        )
        self._add(
            "list_experiments",
            "Experiments in the current scope, newest first. Each carries the policy "
            "that produced it, its parent, and how it ended.",
            {
                "limit": {"type": "integer", "optional": True},
                "roots_only": {"type": "boolean", "optional": True},
            },
            self.list_experiments,
        )
        self._add(
            "experiment_lineage",
            "Where one experiment came from and everything derived from it: "
            "ancestors back to the root, direct children, and all descendants.",
            {"experiment_id": {"type": "string"}},
            self.experiment_lineage,
        )
        self._add(
            "strategy_dossier",
            "Everything known about one candidate in a single structured record: "
            "spec, backtests, verdict and its gates, validation evidence, provenance "
            "and lineage, related failures, specialist dissent, and stated limitations.",
            {"strategy_id": {"type": "string"}},
            self.strategy_dossier,
        )
        self._add(
            "research_memory",
            "What the search has already disproven: failure counts by class and the "
            "constraints now pruning candidates before any compute is spent.",
            {"limit": {"type": "integer", "optional": True}},
            self.research_memory,
        )
        self._add(
            "start_engine",
            "Start the autonomous engine: it draws candidates, writes them, backtests "
            "and judges them continuously until stopped.",
            {
                "dataset": {"type": "string", "optional": True},
                "workers": {"type": "integer", "optional": True},
                "cycle_seconds": {"type": "number", "optional": True},
                "max_strategies": {"type": "integer", "optional": True},
            },
            self.start_engine,
            mutating=True,
        )
        self._add(
            "stop_engine",
            "Ask the engine to stop after the work in flight finishes.",
            {},
            self.stop_engine,
            mutating=True,
        )
        self._add(
            "run_agent",
            "Assign a task to one specialist and return its job handle.",
            {"role": {"type": "string"}, "task": {"type": "string", "optional": True}},
            self.run_agent,
            mutating=True,
        )
        self._add(
            "read_research",
            "Read the research library: titles, mechanisms, replication gaps.",
            {
                "limit": {"type": "integer", "optional": True},
                "filter": {"type": "string", "optional": True},
            },
            self.read_research,
        )
        self._register_workspace()

    # ── implementations ──────────────────────────────────────────────────────
    def search_papers(self, query: str) -> dict[str, Any]:
        text = _str(query, "query", limit=240)
        if len(text) < 3:
            raise ActionError("A research query needs at least three characters.")
        try:
            found = self.agents.research.search(text)
        except Exception as exc:
            raise ActionError(
                f"The scholarly search failed ({type(exc).__name__}). Crossref may be "
                "unreachable from this machine."
            ) from exc
        for item in found:
            self.mirror.paper(item)
        self.log.record(
            "RESEARCH",
            f"'{text}' — {len(found)} references retrieved and mirrored to the vault",
            "pass" if found else "warn",
        )
        return {
            "query": text,
            "count": len(found),
            "papers": [
                {
                    "id": item["id"],
                    "title": item["title"],
                    "authors": item["authors"],
                    "year": item["year"],
                    "url": item["url"],
                    "content_level": item["content_level"],
                }
                for item in found
            ],
            "note": (
                "Metadata and any published abstract only. Nobody has read these papers; "
                "they are UNREVIEWED until a methodology review says otherwise."
            ),
        }

    def list_families(self) -> dict[str, Any]:
        rows = self.families.with_templates(TEMPLATES)
        return {
            "count": len(rows),
            "runnable": [r["key"] for r in rows if r["runnable"]],
            "blocked": {r["key"]: r["blocked_by"] for r in rows if not r["runnable"]},
            "families": rows,
        }

    def create_family(
        self,
        key: str,
        label: str,
        mechanism: str,
        description: str = "",
        data_requirements: list[str] | None = None,
    ) -> dict[str, Any]:
        try:
            family = self.families.create(
                key=_str(key, "key", limit=40, lower=True),
                label=_str(label, "label", limit=80),
                description=str(description or "")[:600],
                mechanism=_str(mechanism, "mechanism", limit=2000),
                data_requirements=tuple(data_requirements or ("BARS",)),
                created_by="agent",
            )
        except ValueError as exc:
            raise ActionError(str(exc)) from exc
        self.mirror.family(family.as_dict())
        return {
            "created": family.key,
            "runnable": family.runnable,
            "blocked_by": list(family.blocked_by),
            "note": (
                "Registered and usable."
                if family.runnable
                else "Registered but blocked: the data it declares is not configured, so the "
                "strategy writer will refuse templates in it. Adding the family did not add "
                "the data."
            ),
        }

    def list_templates(self, family: str | None = None) -> dict[str, Any]:
        custom = set(self.templates.keys())
        rows = [
            {
                "key": t.key,
                "name": t.name,
                "family": t.family,
                "origin": "custom" if t.key in custom else "builtin",
                "parameters": {
                    p.name: {"default": p.default, "low": p.low, "high": p.high, "step": p.step}
                    for p in t.parameters
                },
            }
            for t in sorted(TEMPLATES.values(), key=lambda t: (t.family, t.key))
            if family is None or t.family == family
        ]
        return {"count": len(rows), "templates": rows}

    def create_template(
        self,
        key: str,
        name: str,
        family: str,
        hypothesis: str,
        falsifiable_prediction: str,
        parameters: list[dict[str, Any]],
        source: str,
        warmup_bars: int = 60,
    ) -> dict[str, Any]:
        if not isinstance(parameters, list):
            raise ActionError("'parameters' must be a list of parameter objects.")
        try:
            template = self.templates.create(
                key=_str(key, "key", limit=50, lower=True),
                name=_str(name, "name", limit=120),
                family=_str(family, "family", limit=40, lower=True),
                hypothesis=_str(hypothesis, "hypothesis"),
                falsifiable_prediction=_str(falsifiable_prediction, "falsifiable_prediction"),
                parameters=parameters,
                source=_str(source, "source", limit=24_000),
                warmup_bars=int(warmup_bars),
                known_families=self.families.runnable_keys(),
                existing_templates=set(TEMPLATES),
                created_by="agent",
            )
        except TemplateRejected as exc:
            raise ActionError(str(exc)) from exc
        TEMPLATES[template.key] = template
        meta = self.templates.metadata(template.key)
        return {
            "created": template.key,
            "family": template.family,
            "smoke_test": meta["smoke_test"],
            "note": (
                "Registered. The engine can draw candidates from it on the next cycle. "
                "The smoke test proves the code runs on edge-free synthetic bars — it is "
                "not evidence the idea works."
            ),
        }

    def create_strategy(
        self,
        template: str,
        name: str | None = None,
        parameters: dict[str, Any] | None = None,
        hypothesis: str | None = None,
        research_sources: list[str] | None = None,
    ) -> dict[str, Any]:
        key = _str(template, "template", limit=50, lower=True)
        item = TEMPLATES.get(key)
        if item is None:
            raise ActionError(f"No template '{key}'. Available: {', '.join(sorted(TEMPLATES))}.")
        family = self.families.get(item.family)
        if family is not None and not family.runnable:
            raise ActionError(
                f"Template '{key}' is in family '{item.family}', which is blocked on "
                f"{', '.join(family.blocked_by)}. That data is not configured, so a strategy "
                "written from it could not be honestly backtested."
            )
        clean: dict[str, float] = {}
        for spec in item.parameters:
            if not parameters or spec.name not in parameters:
                continue
            value = float(parameters[spec.name])
            if not spec.low <= value <= spec.high:
                raise ActionError(
                    f"Parameter '{spec.name}' = {value} is outside its declared range "
                    f"[{spec.low}, {spec.high}]."
                )
            clean[spec.name] = value
        spec_out = self.library.create_from_template(
            key,
            name=name,
            parameters=clean or None,
            created_by="agent",
            research_sources=tuple(str(s) for s in (research_sources or []))[:8],
            hypothesis=hypothesis,
        )
        titles = []
        for source_id in spec_out.research_sources:
            match = next((s for s in self.agents.research.list() if s["id"] == source_id), None)
            if match:
                titles.append(str(match["title"]))
        self.mirror.strategy(spec_out, source_titles=titles)
        self.log.record(
            "STRATEGY", f"created {spec_out.strategy_id} via action", "info", spec_out.strategy_id
        )
        return {
            "strategy_id": spec_out.strategy_id,
            "name": spec_out.name,
            "family": spec_out.family,
            "template": spec_out.template,
            "parameters": spec_out.defaults,
            "note": "Written to disk. Nothing has been measured yet — run backtest_strategy.",
        }

    def backtest_strategy(
        self, strategy_id: str, dataset: str | None = None, max_bars: int | None = None
    ) -> dict[str, Any]:
        sid = _str(strategy_id, "strategy_id", limit=120)
        try:
            spec = self.library.get_spec(sid)
        except KeyError as exc:
            raise ActionError(f"No strategy '{sid}'.") from exc
        key = dataset or self.engine.state.config.dataset
        limit = int(max_bars or 250_000)

        def work(handle: JobHandle) -> dict[str, Any]:
            handle.progress(0, "loading bars")
            bars, meta = self.market.load(key, limit=limit)
            module = self.library.load_module(sid)
            code_hash = self.library.code_hash(sid)
            if not meta.is_real:
                handle.progress(1, "synthetic run")
                result = run_backtest(
                    module,
                    spec,
                    bars,
                    code_hash=code_hash,
                    labels=("SYNTHETIC_DATA", "NON_PROMOTABLE"),
                    evidence_tier="SYNTHETIC",
                    dataset_key=key,
                )
                self.store.save(result)
                self.mirror.backtest(result.model_dump(mode="json"), strategy_name=spec.name)
                return {"partition": "SYNTHETIC", "net_pnl": result.net_pnl}

            partitions = chronological_split(bars, warmup_bars=spec.warmup_bars)
            handle.progress(1, "development partition")
            development = run_backtest(
                module,
                spec,
                partitions.development,
                code_hash=code_hash,
                labels=("REAL_DATA", "DEVELOPMENT_IN_SAMPLE", "UNCALIBRATED"),
                evidence_tier="DEVELOPMENT_IN_SAMPLE",
                dataset_key=key,
                partition_name="DEVELOPMENT",
                split_receipt=partitions.receipt,
            )
            self.store.save(development)
            self.mirror.backtest(development.model_dump(mode="json"), strategy_name=spec.name)
            if development.net_pnl <= 0 or len(development.trades) < 30:
                handle.progress(3, "screened out")
                return {
                    "partition": "DEVELOPMENT",
                    "net_pnl": development.net_pnl,
                    "trades": len(development.trades),
                    "reached_validation": False,
                    "note": (
                        "Development did not survive, so the reserved validation slice was "
                        "not touched. That reservation is the point: looking at it now would "
                        "spend the only unbiased sample this strategy has."
                    ),
                }
            handle.progress(2, "validation partition")
            validation = run_backtest(
                module,
                spec,
                partitions.validation,
                code_hash=code_hash,
                labels=("REAL_DATA", "VALIDATION_OOS", "UNCALIBRATED"),
                evidence_tier="VALIDATION_OOS",
                dataset_key=key,
                partition_name="VALIDATION",
                split_receipt=partitions.receipt,
            )
            self.store.save(validation)
            self.mirror.backtest(validation.model_dump(mode="json"), strategy_name=spec.name)
            handle.progress(3, "complete")
            return {
                "partition": "VALIDATION",
                "development_net": development.net_pnl,
                "net_pnl": validation.net_pnl,
                "trades": len(validation.trades),
                "win_rate": validation.win_rate,
                "max_drawdown": validation.max_drawdown,
                "reached_validation": True,
            }

        job = REGISTRY.submit("backtest", f"{spec.name} on {key}", 3, work)
        return {
            "job_id": job.job_id,
            "strategy_id": sid,
            "dataset": key,
            "note": "Running in the background. Poll /api/v1/jobs/{job_id} for the result.",
        }

    def list_strategies(self, limit: int = 25) -> dict[str, Any]:
        rows = []
        for spec in self.library.list_specs()[: max(1, min(int(limit), 200))]:
            latest = self.store.latest(spec.strategy_id)
            rows.append(
                {
                    "strategy_id": spec.strategy_id,
                    "name": spec.name,
                    "family": spec.family,
                    "template": spec.template,
                    "created_at": spec.created_at.isoformat(),
                    "net_pnl": None if latest is None else latest.get("net_pnl"),
                    "trades": 0 if latest is None else len(latest.get("trades", [])),
                    "evidence_tier": None if latest is None else latest.get("evidence_tier"),
                }
            )
        return {"count": len(rows), "strategies": rows}

    def engine_status(self) -> dict[str, Any]:
        return dict(self.engine.status())

    def list_experiments(
        self, limit: int | None = None, roots_only: bool | None = None
    ) -> dict[str, Any]:
        scope = self.engine._scope()
        count = max(1, min(int(limit or 80), 500))
        rows = (
            self.engine.experiments.roots(scope, count)
            if roots_only
            else self.engine.experiments.recent(scope, count)
        )
        return {"scope": scope, "total": self.engine.experiments.count(scope), "experiments": rows}

    def experiment_lineage(self, experiment_id: str) -> dict[str, Any]:
        key = _str(experiment_id, "experiment_id", limit=120)
        line = self.engine.experiments.lineage(key)
        if line["experiment"] is None:
            raise ActionError(
                f"No experiment '{key}'. Use list_experiments to see what exists."
            )
        return dict(line)

    def strategy_dossier(self, strategy_id: str) -> dict[str, Any]:
        from forge_api.dossier import build_dossier

        key = _str(strategy_id, "strategy_id", limit=120)
        try:
            return build_dossier(
                root=self.workspace.repo,
                library=self.library,
                store=self.store,
                experiments=self.engine.experiments,
                memory=self.engine.memory,
                snapshots=self.engine.snapshots,
                scope=self.engine._scope(),
                strategy_id=key,
            )
        except KeyError as exc:
            raise ActionError(
                f"No strategy '{key}'. Use list_strategies to see what exists."
            ) from exc

    def research_memory(self, limit: int | None = None) -> dict[str, Any]:
        scope = self.engine._scope()
        count = max(1, min(int(limit or 100), 500))
        return {
            "scope": scope,
            "counts": self.engine.memory.counts(scope),
            "total": self.engine.memory.total(scope),
            "constraints": self.engine.constraints()[:count],
        }

    def start_engine(
        self,
        dataset: str | None = None,
        workers: int | None = None,
        cycle_seconds: float | None = None,
        max_strategies: int | None = None,
    ) -> dict[str, Any]:
        from forge_api.engine import EngineConfig
        from forge_api.market import DATASETS

        current = self.engine.state.config
        key = dataset or current.dataset
        if key not in DATASETS:
            raise ActionError(f"Unknown dataset '{key}'. Available: {', '.join(sorted(DATASETS))}.")
        if self.engine.state.running:
            return {"already_running": True, **self.engine.status()}
        config = EngineConfig(
            dataset=key,
            cycle_seconds=float(cycle_seconds or current.cycle_seconds),
            max_strategies=int(max_strategies or current.max_strategies),
            max_bars=current.max_bars,
            workers=max(1, min(int(workers or current.workers), 8)),
        )
        return dict(self.engine.start(config))

    def stop_engine(self) -> dict[str, Any]:
        return dict(self.engine.stop())

    def run_agent(self, role: str, task: str = "") -> dict[str, Any]:
        try:
            return dict(self.agents.submit(_str(role, "role", limit=40, lower=True), task))
        except KeyError as exc:
            raise ActionError(
                f"No specialist '{role}'. Available: {', '.join(self.agents.roles())}."
            ) from exc
        except ValueError as exc:
            raise ActionError(str(exc)) from exc

    def read_research(self, limit: int = 20, filter: str | None = None) -> dict[str, Any]:
        needle = (filter or "").lower()
        rows = [
            {
                "id": item["id"],
                "title": item["title"],
                "authors": item.get("authors"),
                "year": item.get("year"),
                "evidence": item.get("evidence"),
                "replication_gap": item.get("replication_gap"),
                "templates": item.get("templates", []),
            }
            for item in self.agents.research.list()
            if not needle or needle in f"{item.get('title', '')} {item.get('topic', '')}".lower()
        ][: max(1, min(int(limit), 100))]
        return {"count": len(rows), "sources": rows}

    # ── workspace ────────────────────────────────────────────────────────────
    # These verbs are what make the agent an operator rather than a parallel
    # implementation. Each one is what the interface itself calls when a person
    # drags a panel, so there is exactly one way to change a layout and both
    # callers go through it. An agent able to arrange panels by some private
    # route would be a second implementation to keep in step, and the first
    # divergence between them would be silent.

    def _register_workspace(self) -> None:
        self._add(
            "list_workspace_templates",
            "List the workspace templates available as starting points. Templates are "
            "presets, not modes: nothing is locked behind one, and a workspace built "
            "from a template can be changed into anything else.",
            {},
            self.list_workspace_templates,
        )
        self._add(
            "list_workspaces",
            "List the operator's saved workspaces, newest first, with panel counts.",
            {},
            self.list_workspaces,
        )
        self._add(
            "describe_workspace",
            "The full contents of one workspace: every panel, its kind, position and "
            "settings. Omit workspace_id for the one currently open.",
            {"workspace_id": {"type": "string", "optional": True}},
            self.describe_workspace,
        )
        self._add(
            "create_workspace",
            "Create a workspace, optionally seeded from a template.",
            {
                "name": {"type": "string", "description": "What to call it."},
                "template_key": {
                    "type": "string",
                    "optional": True,
                    "description": "A key from list_workspace_templates. Omit for empty.",
                },
                "activate": {"type": "boolean", "optional": True},
            },
            self.create_workspace,
            mutating=True,
        )
        self._add(
            "open_workspace",
            "Make a workspace the active one.",
            {"workspace_id": {"type": "string"}},
            self.open_workspace,
            mutating=True,
        )
        self._add(
            "rename_workspace",
            "Rename a workspace. The layout is untouched.",
            {"workspace_id": {"type": "string"}, "name": {"type": "string"}},
            self.rename_workspace,
            mutating=True,
        )
        self._add(
            "clone_workspace",
            "Copy a workspace under a new name - the way to make an ES version of an NQ "
            "desk without disturbing the original.",
            {"workspace_id": {"type": "string"}, "name": {"type": "string"}},
            self.clone_workspace,
            mutating=True,
        )
        self._add(
            "delete_workspace",
            "Delete a workspace and its layout. Research is not touched: a workspace "
            "holds no experiments, artifacts or evidence.",
            {"workspace_id": {"type": "string"}},
            self.delete_workspace,
            mutating=True,
            risk=ActionRisk.CONFIRM,
        )
        self._add(
            "add_panel",
            "Add a panel to a workspace. Position is in grid units on a 12-column grid; "
            "omit it and the panel lands below everything already there.",
            {
                "kind": {
                    "type": "string",
                    "description": "One of: " + ", ".join(sorted(k.value for k in PanelKind)),
                },
                "workspace_id": {"type": "string", "optional": True},
                "x": {"type": "integer", "optional": True},
                "y": {"type": "integer", "optional": True},
                "width": {"type": "integer", "optional": True},
                "height": {"type": "integer", "optional": True},
                "symbol": {"type": "string", "optional": True},
                "timeframe": {"type": "string", "optional": True},
                "title": {"type": "string", "optional": True},
            },
            self.add_panel,
            mutating=True,
        )
        self._add(
            "remove_panel",
            "Remove a panel from a workspace.",
            {
                "panel_id": {"type": "string"},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.remove_panel,
            mutating=True,
        )
        self._add(
            "move_panel",
            "Move a panel to a new grid position, keeping its size.",
            {
                "panel_id": {"type": "string"},
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.move_panel,
            mutating=True,
        )
        self._add(
            "resize_panel",
            "Resize a panel, keeping its position.",
            {
                "panel_id": {"type": "string"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.resize_panel,
            mutating=True,
        )
        self._add(
            "set_panel_setting",
            "Change one setting on a panel - a chart's symbol or timeframe, a table's "
            "filter. Use add_indicator for indicators.",
            {
                "panel_id": {"type": "string"},
                "key": {"type": "string"},
                "value": {"type": "string"},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.set_panel_setting,
            mutating=True,
        )
        self._add(
            "add_indicator",
            "Add an indicator to a chart panel. Refuses on a panel that is not a chart "
            "rather than storing a setting nothing will read.",
            {
                "panel_id": {"type": "string"},
                "indicator": {"type": "string", "description": "e.g. vwap, ema, atr"},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.add_indicator,
            mutating=True,
        )
        self._add(
            "link_panels",
            "Put panels in a link group so they follow each other's symbol and "
            "timeframe. Pass no group to unlink them.",
            {
                "panel_ids": {"type": "array", "description": "Panel ids to link together."},
                "group": {"type": "string", "optional": True},
                "workspace_id": {"type": "string", "optional": True},
            },
            self.link_panels,
            mutating=True,
        )

    # ── workspace implementations ────────────────────────────────────────────
    def _workspace(self, workspace_id: str | None) -> Workspace:
        """The named workspace, or the open one.

        Refuses rather than creating something. "No workspace is open" is a true
        statement the caller can act on; silently conjuring one would make
        `add_panel` quietly build a desk nobody asked for.
        """
        if workspace_id:
            found = self.workspaces.get(_str(workspace_id, "workspace_id", limit=120))
            if found is None:
                raise ActionError(f"No workspace '{workspace_id}'.")
            return found
        active = self.workspaces.active()
        if active is None:
            raise ActionError(
                "No workspace is open. Create one with create_workspace, or pass "
                "workspace_id to say which you mean."
            )
        return active

    @staticmethod
    def _panel_view(panel: Panel) -> dict[str, Any]:
        return {
            "panel_id": panel.panel_id,
            "kind": panel.kind.value,
            "title": panel.display_title(),
            "x": panel.x,
            "y": panel.y,
            "width": panel.width,
            "height": panel.height,
            "settings": panel.settings,
            "link_group": panel.link_group,
            "collapsed": panel.collapsed,
        }

    def _view(self, workspace: Workspace) -> dict[str, Any]:
        return {
            "workspace_id": workspace.workspace_id,
            "name": workspace.name,
            "template_key": workspace.template_key,
            "updated_at": workspace.updated_at.isoformat(),
            "panels": [self._panel_view(p) for p in workspace.panels],
        }

    def _save_workspace(self, workspace: Workspace) -> dict[str, Any]:
        self.workspaces.save(workspace)
        return self._view(workspace)

    def list_workspace_templates(self) -> dict[str, Any]:
        items = catalogue()
        return {"count": len(items), "templates": items}

    def list_workspaces(self) -> dict[str, Any]:
        rows = self.workspaces.summaries()
        return {"count": len(rows), "active": self.workspaces.active_id(), "workspaces": rows}

    def describe_workspace(self, workspace_id: str | None = None) -> dict[str, Any]:
        return self._view(self._workspace(workspace_id))

    def create_workspace(
        self,
        name: str,
        template_key: str | None = None,
        activate: bool = True,
    ) -> dict[str, Any]:
        label = _str(name, "name", limit=120)
        panels: tuple[Panel, ...] = ()
        key = None
        if template_key:
            key = _str(template_key, "template_key", limit=60, lower=True)
            try:
                preset = template(key)
            except KeyError as exc:
                raise ActionError(str(exc)) from exc
            panels = preset.panels
        workspace = self.workspaces.create(label, panels=panels, template_key=key)
        if activate:
            self.workspaces.set_active(workspace.workspace_id)
        return self._view(workspace)

    def open_workspace(self, workspace_id: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        self.workspaces.set_active(workspace.workspace_id)
        return self._view(workspace)

    def rename_workspace(self, workspace_id: str, name: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        return self._save_workspace(workspace.renamed(_str(name, "name", limit=120)))

    def clone_workspace(self, workspace_id: str, name: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        clone = self.workspaces.clone(workspace.workspace_id, _str(name, "name", limit=120))
        return self._view(clone)

    def delete_workspace(self, workspace_id: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        removed = self.workspaces.delete(workspace.workspace_id)
        return {"workspace_id": workspace.workspace_id, "deleted": removed}

    def add_panel(
        self,
        kind: str,
        workspace_id: str | None = None,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        try:
            panel_kind = PanelKind(_str(kind, "kind", limit=40, lower=True))
        except ValueError as exc:
            raise ActionError(
                f"'{kind}' is not a panel kind. Available: "
                f"{', '.join(sorted(k.value for k in PanelKind))}."
            ) from exc

        settings: dict[str, Any] = {}
        if symbol:
            settings["symbol"] = _str(symbol, "symbol", limit=24).upper()
        if timeframe:
            settings["timeframe"] = _str(timeframe, "timeframe", limit=12, lower=True)

        # Below everything already placed, so a new panel never lands on top of
        # one the operator is using.
        default_y = max((p.y + p.height for p in workspace.panels), default=0)
        try:
            panel = Panel(
                panel_id=new_panel_id(panel_kind, tuple(p.panel_id for p in workspace.panels)),
                kind=panel_kind,
                title=_str(title, "title", limit=80) if title else "",
                x=_bounded(x, 0, 0, GRID_COLUMNS - 1, "x"),
                y=_bounded(y, default_y, 0, MAX_ROWS - 1, "y"),
                width=_bounded(width, 6, 1, GRID_COLUMNS, "width"),
                height=_bounded(height, 6, 1, MAX_ROWS, "height"),
                settings=settings,
            )
            updated = workspace.with_panel(panel)
        except ValueError as exc:
            raise ActionError(str(exc)) from exc
        return self._save_workspace(updated)

    def remove_panel(self, panel_id: str, workspace_id: str | None = None) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        return self._save_workspace(
            workspace.without_panel(self._panel_id(workspace, panel_id))
        )

    def move_panel(
        self, panel_id: str, x: int, y: int, workspace_id: str | None = None
    ) -> dict[str, Any]:
        return self._reshape(workspace_id, panel_id, x=x, y=y)

    def resize_panel(
        self, panel_id: str, width: int, height: int, workspace_id: str | None = None
    ) -> dict[str, Any]:
        return self._reshape(workspace_id, panel_id, width=width, height=height)

    @staticmethod
    def _panel_id(workspace: Workspace, panel_id: str) -> str:
        """Check the panel exists, and turn a miss into a refusal with the list."""
        wanted = _str(panel_id, "panel_id", limit=60)
        if workspace.panel(wanted) is None:
            known = ", ".join(p.panel_id for p in workspace.panels) or "none"
            raise ActionError(f"No panel '{wanted}' in this workspace. Panels: {known}.")
        return wanted

    def _reshape(self, workspace_id: str | None, panel_id: str, **geometry: int) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        panel = workspace.require(self._panel_id(workspace, panel_id))
        try:
            moved = panel.model_copy(update={k: int(v) for k, v in geometry.items()})
            # Re-validate: model_copy skips validators, so a panel that runs off
            # the grid would otherwise be stored and only fail on the way out.
            moved = Panel.model_validate(moved.model_dump())
        except (ValueError, TypeError) as exc:
            raise ActionError(f"That geometry does not fit the grid: {exc}") from exc
        return self._save_workspace(workspace.replacing_panel(moved))

    def set_panel_setting(
        self, panel_id: str, key: str, value: str, workspace_id: str | None = None
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        panel = workspace.require(self._panel_id(workspace, panel_id))
        name = _str(key, "key", limit=40, lower=True)
        settings = {**panel.settings, name: _str(value, "value", limit=200)}
        return self._save_workspace(
            workspace.replacing_panel(panel.model_copy(update={"settings": settings}))
        )

    def add_indicator(
        self, panel_id: str, indicator: str, workspace_id: str | None = None
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        panel = workspace.require(self._panel_id(workspace, panel_id))
        if panel.kind is not PanelKind.CHART:
            raise ActionError(
                f"'{panel.panel_id}' is a {panel.kind} panel, not a chart. An indicator "
                "only means something on a chart."
            )
        name = _str(indicator, "indicator", limit=40, lower=True)
        existing = list(panel.settings.get("indicators", []))
        if name not in existing:
            existing.append(name)
        settings = {**panel.settings, "indicators": existing}
        return self._save_workspace(
            workspace.replacing_panel(panel.model_copy(update={"settings": settings}))
        )

    def link_panels(
        self,
        panel_ids: list[str],
        group: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        if not isinstance(panel_ids, list) or not panel_ids:
            raise ActionError("'panel_ids' must be a non-empty list of panel ids.")
        ids = tuple(self._panel_id(workspace, item) for item in panel_ids)
        name = _str(group, "group", limit=40, lower=True) if group else None
        return self._save_workspace(workspace.linked(name, ids))


def _bounded(value: Any, fallback: int, low: int, high: int, field: str) -> int:
    if value is None:
        return fallback
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ActionError(f"'{field}' must be a whole number.") from exc
    if not low <= number <= high:
        raise ActionError(f"'{field}' must be between {low} and {high}.")
    return number
