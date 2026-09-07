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
from typing import Any

from forge.research import chronological_split
from forge.strategy import TEMPLATES, TemplateRejected, run_backtest
from forge.vault import VaultMirror

from forge_api.jobs import REGISTRY, JobHandle


class ActionError(Exception):
    """A refusal with a reason the caller can show verbatim."""


@dataclass(frozen=True)
class Action:
    name: str
    summary: str
    parameters: dict[str, Any]
    run: Callable[..., dict[str, Any]]
    mutating: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.summary,
            "mutating": self.mutating,
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
        self._lock = threading.Lock()
        self.history: list[dict[str, Any]] = []
        self._registry: dict[str, Action] = {}
        self._register_all()

    # ── dispatch ─────────────────────────────────────────────────────────────
    def names(self) -> list[str]:
        return sorted(self._registry)

    def schemas(self) -> list[dict[str, Any]]:
        return [self._registry[name].schema() for name in self.names()]

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        action = self._registry.get(name)
        if action is None:
            raise ActionError(f"No action named '{name}'. Available: {', '.join(self.names())}.")
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
    ) -> None:
        self._registry[name] = Action(name, summary, parameters, run, mutating)

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
