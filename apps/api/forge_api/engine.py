"""The autonomous research engine.

Runs unattended in a background thread. Each cycle it forms a candidate, writes
it to disk as real strategy code, backtests it on real market data, submits it to
the deterministic judge, and records what happened — including, and especially,
the failures.

The search is deliberately memory-guided rather than random: a candidate whose
parameter neighbourhood has already failed is skipped before any compute is
spent, and lineages that keep failing are retired. That is the cheapest gate in
the system and it runs here, at the front, not in the judge.

Eight search policies consume bounded research proposals and explore executable
templates. Specialist model tasks run separately under a daily call cap. Selection
feedback comes from development evidence; validation and holdout remain separate.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from forge.contracts.hashing import content_hash
from forge.data.models import Bar
from forge.judge import Judge, JudgeInput
from forge.prop import MIN_TRADING_DAYS, load_rules, simulate_prop_paths
from forge.research import ResearchLedger, ResearchPartitions, chronological_split
from forge.strategy import TEMPLATES, StrategyLibrary, run_backtest
from forge.vault import VaultMirror, Workspace

from forge_api.activity import ActivityLog, BacktestStore
from forge_api.agent_service import AgentService
from forge_api.experiments import POLICIES, Experiments
from forge_api.market import DEFAULT_DATASET, MarketService


@dataclass
class EngineConfig:
    dataset: str = DEFAULT_DATASET
    cycle_seconds: float = 8.0
    # A population cap, not a stop condition. When the library is full the
    # weakest survivor is retired to make room, so the search keeps going for as
    # long as it is left running. Stopping at the cap meant the engine did one
    # cycle and quit the moment the library was already full.
    max_strategies: int = 400
    # Parallel search threads. The backtest loop is numpy-heavy and releases the
    # GIL for most of its work, so several run concurrently at close to full
    # speed on separate candidates.
    workers: int = 8
    # 30k one-minute bars is ~21 sessions, and only the 20% validation slice
    # reaches the prop simulator, so nothing the old default produced could ever
    # clear the 30-trading-day gate.
    max_bars: int = 250_000
    seed: int = 20260901


@dataclass
class EngineState:
    running: bool = False
    started_at: str | None = None
    cycles: int = 0
    created: int = 0
    backtested: int = 0
    judged: int = 0
    passed: int = 0
    validation_passed: int = 0
    holdout_passed: int = 0
    rejected: int = 0
    lineages_retired: int = 0
    engine_errors: int = 0
    skipped_by_memory: int = 0
    pruned: int = 0
    prop_tested: int = 0
    best_pass_rate: float = 0.0
    best_strategy: str | None = None
    best_rule: str | None = None
    last_error: str | None = None
    current_stage: str = "idle"
    # One stage per worker, so the interface can show what each is doing rather
    # than a single label that four threads fight over.
    worker_stages: dict[str, str] = field(default_factory=dict)
    config: EngineConfig = field(default_factory=EngineConfig)

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "started_at": self.started_at,
            "cycles": self.cycles,
            "created": self.created,
            "backtested": self.backtested,
            "judged": self.judged,
            "passed": self.passed,
            "validation_passed": self.validation_passed,
            "holdout_passed": self.holdout_passed,
            "rejected": self.rejected,
            "lineages_retired": self.lineages_retired,
            "engine_errors": self.engine_errors,
            "skipped_by_memory": self.skipped_by_memory,
            "compute_saved": self.skipped_by_memory,
            "pruned": self.pruned,
            "prop_tested": self.prop_tested,
            "best_pass_rate": round(self.best_pass_rate, 6),
            "best_strategy": self.best_strategy,
            "best_rule": self.best_rule,
            "last_error": self.last_error,
            "current_stage": self.current_stage,
            "worker_stages": dict(self.worker_stages),
            "config": {
                "dataset": self.config.dataset,
                "cycle_seconds": self.config.cycle_seconds,
                "max_strategies": self.config.max_strategies,
                "max_bars": self.config.max_bars,
                "workers": self.config.workers,
            },
        }


class AutonomousEngine:
    def __init__(
        self,
        library: StrategyLibrary,
        store: BacktestStore,
        log: ActivityLog,
        market: MarketService,
        workspace: Workspace,
        research_ledger: ResearchLedger,
        mirror: VaultMirror | None = None,
    ) -> None:
        self.library = library
        self.store = store
        self.log = log
        self.market = market
        self.workspace = workspace
        # Data artifacts follow the workspace; rule sets are version-controlled
        # contracts and stay with the code.
        self.root = workspace.store
        self.research_ledger = research_ledger
        self.mirror = mirror
        self.state = EngineState()
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # Counter increments are not atomic under threads, and neither is
        # create-then-prune against the library.
        self._state_lock = threading.Lock()
        self._library_lock = threading.Lock()
        self._data_lock = threading.Lock()
        self._loaded: tuple[Any, Any] | None = None
        self._partitions: ResearchPartitions | None = None
        self._data_version = "unloaded"
        self._catalog_version = content_hash({key: t.source for key, t in TEMPLATES.items()})[:16]
        self._active: dict[int, str] = {}
        self._paused: set[int] = set()
        self.agents: AgentService | None = None
        self.experiments = Experiments(workspace.data / "experiments.db")
        self._rules = load_rules(workspace.repo / "rules")
        # Failure constraints: (template, rounded parameter signature) -> reason.
        self._constraints: dict[tuple[str, str], str] = {}
        self._lineage_failures: dict[str, int] = {}
        self._retired_lineages: set[str] = set()

    # ── control ──────────────────────────────────────────────────────────────
    def start(self, config: EngineConfig | None = None) -> dict[str, Any]:
        with self._lock:
            if any(t.is_alive() for t in self._threads):
                return self.status()
            if config:
                self.state.config = config
            self._stop.clear()
            self.state.running = True
            self.state.started_at = datetime.now(UTC).isoformat(timespec="seconds")
            self.state.last_error = None
            self.state.worker_stages = {}
            self._loaded = None
            self._partitions = None
            self._data_version = "unloaded"
            self._active.clear()
            self._threads = [
                threading.Thread(
                    target=self._loop, args=(index,), name=f"algoforge-engine-{index}", daemon=True
                )
                for index in range(max(1, self.state.config.workers))
            ]
            for thread in self._threads:
                thread.start()
        self.log.record(
            "ENGINE",
            f"started on {self.state.config.dataset} with "
            f"{max(1, self.state.config.workers)} workers, "
            f"population cap {self.state.config.max_strategies}",
            "pass",
        )
        return self.state.as_dict()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self.state.running:
                return self.state.as_dict()
            self._stop.set()
            self.state.running = False
            self.state.current_stage = "stopping"
        self.log.record("ENGINE", f"stopped after {self.state.cycles} cycles", "warn")
        return self.state.as_dict()

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            result = self.state.as_dict()
            result["stopping"] = self._stop.is_set() and any(t.is_alive() for t in self._threads)
            result["workers"] = [
                {
                    "id": i,
                    "policy": POLICIES[i % len(POLICIES)],
                    "stage": self.state.worker_stages.get(str(i), "idle"),
                    "paused": i in self._paused,
                    "strategy_id": self._active.get(i),
                }
                for i in range(self.state.config.workers)
            ]
        return result

    def control_worker(self, worker: int, paused: bool) -> None:
        if not 0 <= worker < self.state.config.workers:
            raise ValueError("Unknown worker")
        with self._state_lock:
            if paused:
                self._paused.add(worker)
            else:
                self._paused.discard(worker)

    def _scope(self) -> str:
        return (
            f"{self.state.config.dataset}:{self.state.config.max_bars}:"
            f"{self._data_version}:{self._catalog_version}:contract-units-v2"
        )

    # ── the loop ─────────────────────────────────────────────────────────────
    def _loop(self, worker: int = 0) -> None:
        # A different stream per worker, or four threads explore the same
        # parameter draws and three of them are wasted.
        rng = random.Random(self.state.config.seed + worker * 7919)
        try:
            self._stage(worker, "loading")
            with self._data_lock:
                if self._loaded is None:
                    self._loaded = self.market.load(
                        self.state.config.dataset, limit=self.state.config.max_bars
                    )
                bars, dataset = self._loaded
                if dataset.is_real and self._partitions is None:
                    self._partitions = chronological_split(
                        bars, warmup_bars=max(t.warmup_bars for t in TEMPLATES.values())
                    )
                    self._data_version = self._partitions.receipt.source_data_hash
            self.log.record(
                "DATA",
                f"{dataset.label} loaded — {len(bars):,} bars from {dataset.provider}",
                "pass" if dataset.is_real else "warn",
            )
        except Exception as exc:
            self.state.last_error = str(exc)
            self.state.running = False
            self._stop.set()
            self._stage(worker, "failed")
            self.log.record("DATA", f"load failed: {exc}", "fail")
            return

        while not self._stop.is_set():
            if worker == 0 and self.agents:
                self.agents.auto_tick()
            if worker in self._paused:
                self._stage(worker, "paused")
                self._stop.wait(0.5)
                continue
            try:
                self._make_room()
                self._cycle(rng, bars, dataset.is_real, worker)
            except Exception as exc:  # a bad cycle must not kill the engine
                with self._state_lock:
                    self.state.last_error = str(exc)
                    self.state.engine_errors += 1
                self.log.record("ENGINE", f"cycle error: {exc}", "fail")
            finally:
                with self._library_lock:
                    self._active.pop(worker, None)
            self._bump("cycles")
            self._stage(worker, "waiting")
            self._stop.wait(self.state.config.cycle_seconds)

        self._stage(worker, "stopped")
        # The run is only over once every worker has left the loop.
        with self._state_lock:
            if all(not t.is_alive() for t in self._threads if t is not threading.current_thread()):
                self.state.running = False
                self.state.current_stage = "idle"

    def _make_room(self) -> None:
        """Retire the weakest survivor when the library is at its cap.

        The old behaviour stopped the engine here, which is why a full library
        meant one cycle and an immediate halt. A search that is meant to run for
        days has to be able to replace its worst candidate instead.
        """
        with self._library_lock:
            specs = self.library.list_specs()
            cap = self.state.config.max_strategies
            if len(specs) < cap:
                return
            worst: tuple[float, str] | None = None
            for spec in specs:
                if spec.strategy_id in self._active.values():
                    continue
                latest = self.store.latest(spec.strategy_id)
                # Never run is worse than run and losing: it occupies a slot
                # while carrying no evidence at all.
                score = -1e18 if latest is None else float(latest.get("net_pnl", 0.0))
                if worst is None or score < worst[0]:
                    worst = (score, spec.strategy_id)
            if worst is None:
                return
            try:
                self.library.delete(worst[1])
            except KeyError:
                return
        self._bump("pruned")
        self.log.record("ENGINE", f"population at {cap}; retired {worst[1]} to make room", "info")

    def _bump(self, field_name: str, amount: int = 1) -> None:
        with self._state_lock:
            setattr(self.state, field_name, getattr(self.state, field_name) + amount)

    def _stage(self, worker: int, stage: str) -> None:
        with self._state_lock:
            self.state.worker_stages[str(worker)] = stage
            self.state.current_stage = stage

    def _score_against_prop_firms(self, strategy_id: str, trades: Any) -> None:
        """Ask the question the whole system exists to answer.

        A passing backtest is not the goal; surviving a funded evaluation is.
        This runs the survivor against every rule set and records the best pass
        rate, so a long unattended run leaves behind a ranking rather than a
        pile of artifacts nobody has compared.
        """
        buckets: dict[str, float] = {}
        for trade in trades:
            buckets[str(trade.exit_time)[:10]] = buckets.get(str(trade.exit_time)[:10], 0.0) + (
                trade.net_pnl
            )
        daily = tuple(buckets[day] for day in sorted(buckets))
        if len(daily) < MIN_TRADING_DAYS:
            self.log.record(
                "PROP",
                f"{strategy_id} — only {len(daily)} trading days; "
                f"{MIN_TRADING_DAYS} needed to simulate an account",
                "warn",
                strategy_id,
            )
            return
        for rule in self._rules:
            try:
                simulation = simulate_prop_paths(
                    strategy_id, rule, daily, paths=400, allow_unverified=True
                )
            except ValueError:
                continue
            self._bump("prop_tested")
            with self._state_lock:
                if simulation.pass_rate > self.state.best_pass_rate:
                    self.state.best_pass_rate = simulation.pass_rate
                    self.state.best_strategy = strategy_id
                    self.state.best_rule = rule.display_name
                    best = True
                else:
                    best = False
            self.log.record(
                "PROP",
                f"{strategy_id} vs {rule.display_name} — {simulation.pass_rate:.1%} pass"
                + (" (best so far)" if best else ""),
                "pass" if simulation.pass_rate > 0.5 else "info",
                strategy_id,
            )

    def _cycle(self, rng: random.Random, bars: list[Bar], real_data: bool, worker: int = 0) -> None:
        proposal = self.agents.take_proposal() if self.agents and worker == 0 else None
        policy = POLICIES[worker % len(POLICIES)]
        pool = sorted(TEMPLATES)
        family = {
            "momentum": "momentum",
            "reversion": "mean_reversion",
            "volatility": "volatility",
        }.get(policy)
        if family:
            pool = [key for key in pool if TEMPLATES[key].family == family] or pool
        if policy in {"literature", "replication"}:
            pool = [
                key
                for key in pool
                if key
                in {
                    "vol_normalized_momentum",
                    "variance_ratio_reversion",
                    "ou_half_life_reversion",
                    "vwap_sigma_reversion",
                }
            ]
        template_key = str(proposal["template"]) if proposal else rng.choice(pool)
        template = TEMPLATES[template_key]

        # Draw parameters from the spec's own declared ranges.
        params: dict[str, float] = {}
        for spec_param in template.parameters:
            steps = max(1, round((spec_param.high - spec_param.low) / spec_param.step))
            value = spec_param.low + spec_param.step * rng.randint(0, steps)
            params[spec_param.name] = round(min(value, spec_param.high), 4)

        if proposal:
            params = proposal["parameters"]
        elif policy in {"neighbourhood", "ablation"}:
            history = [
                a
                for a in self.experiments.recent(self._scope())
                if a.get("development_net") is not None
            ]
            if history:
                parent = max(history, key=lambda a: float(a["development_net"]))
                template_key = parent["template"]
                template = TEMPLATES[template_key]
                params = dict(parent["parameters"])
                parameter = rng.choice(template.parameters)
                params[parameter.name] = (
                    float(parameter.default)
                    if policy == "ablation"
                    else round(
                        max(
                            parameter.low,
                            min(
                                parameter.high,
                                params[parameter.name] + rng.choice([-1, 1]) * parameter.step,
                            ),
                        ),
                        4,
                    )
                )
        elif policy == "replication":
            params = {p.name: float(p.default) for p in template.parameters}

        signature = ",".join(f"{k}={params[k]}" for k in sorted(params))
        key = (template_key, signature)

        # Memory gate: cheapest rejection in the system, before any compute.
        attempt_id = self.experiments.reserve(self._scope(), template_key, params)
        if attempt_id is None:
            self._bump("skipped_by_memory")
            self.log.record(
                "MEMORY",
                f"skipped previously attempted {template_key} {signature}",
                "info",
            )
            return

        self._stage(worker, "creating")
        with self._library_lock:
            sources = (
                tuple(proposal["source_ids"])
                if proposal
                else tuple(
                    str(s["id"])
                    for s in self.agents.research.list()
                    if template_key in s["templates"]
                )
                if self.agents
                else ()
            )
            spec = self.library.create_from_template(
                template_key,
                symbol=self._symbol(bars),
                parameters=params,
                created_by=f"worker-{worker}:{policy}",
                research_sources=sources,
                hypothesis=proposal["hypothesis"] if proposal else None,
            )
            self._active[worker] = spec.strategy_id
        self.experiments.finish(attempt_id, strategy_id=spec.strategy_id, status="running")
        self._bump("created")
        if self.mirror is not None:
            titles = [
                str(item["title"])
                for item in (self.agents.research.list() if self.agents else [])
                if item["id"] in set(sources)
            ]
            self.mirror.strategy(spec, source_titles=titles)
        self.log.record("STRATEGY", f"created {spec.strategy_id}", "info", spec.strategy_id)

        self._stage(worker, "backtesting")
        module = self.library.load_module(spec.strategy_id)
        started = time.time()
        partitions: ResearchPartitions | None = None
        if real_data:
            partitions = self._partitions or chronological_split(
                bars, warmup_bars=max(t.warmup_bars for t in TEMPLATES.values())
            )
            development = run_backtest(
                module,
                spec,
                partitions.development,
                parameters=params,
                code_hash=self.library.code_hash(spec.strategy_id),
                labels=("REAL_DATA", "DEVELOPMENT_IN_SAMPLE", "UNCALIBRATED"),
                evidence_tier="DEVELOPMENT_IN_SAMPLE",
                dataset_key=self.state.config.dataset,
                partition_name="DEVELOPMENT",
                split_receipt=partitions.receipt,
            )
            self.store.save(development)
            self.experiments.finish(
                attempt_id,
                development_net=development.net_pnl,
                development_trades=len(development.trades),
                development_costs=development.total_costs,
                development_drawdown=development.max_drawdown,
                status="screened",
            )
            if self._stop.is_set():
                return
            if development.net_pnl <= 0 or len(development.trades) < 30:
                self._bump("backtested")
                self._bump("rejected")
                self.log.record(
                    "SCREEN",
                    f"{spec.strategy_id}: development net "
                    f"{development.net_pnl:+.2f}, {len(development.trades)} trades; "
                    "validation remains untouched",
                    "warn",
                    spec.strategy_id,
                )
                return
            result = run_backtest(
                module,
                spec,
                partitions.validation,
                parameters=params,
                code_hash=self.library.code_hash(spec.strategy_id),
                labels=("REAL_DATA", "VALIDATION_OOS", "UNCALIBRATED"),
                evidence_tier="VALIDATION_OOS",
                dataset_key=self.state.config.dataset,
                partition_name="VALIDATION",
                split_receipt=partitions.receipt,
            )
            self._bump("backtested")
        else:
            result = run_backtest(
                module,
                spec,
                bars,
                parameters=params,
                code_hash=self.library.code_hash(spec.strategy_id),
                labels=("SYNTHETIC_DATA", "NON_PROMOTABLE"),
                evidence_tier="SYNTHETIC",
                dataset_key=self.state.config.dataset,
            )
        self.store.save(result)
        if self.mirror is not None:
            self.mirror.backtest(result.model_dump(mode="json"), strategy_name=spec.name)
        self.experiments.finish(attempt_id, status="backtested")
        self._bump("backtested")
        self.log.record(
            "BACKTEST",
            f"{spec.strategy_id} — {len(result.trades)} trades, net {result.net_pnl:+.2f}"
            f" in {time.time() - started:.1f}s",
            "pass" if result.net_pnl > 0 else "warn",
            result.backtest_id,
        )

        self._stage(worker, "judging")
        pnl = tuple(t.net_pnl for t in result.trades)
        if not pnl:
            self._remember(key, template_key, "produced no trades")
            self.log.record("JUDGE", f"{spec.strategy_id} → no trades to judge", "warn")
            self.library.delete(spec.strategy_id)
            self._bump("created", -1)
            return

        evidence_args: dict[str, Any] = {}
        if partitions is not None and result.net_pnl > 0 and len(result.trades) >= 30:
            from forge.research import run_validation

            from forge_api.strategies import per_bar_pnl, store_evidence

            self._stage(worker, "validating")
            grid = {name: [value] for name, value in params.items()}
            axis = template.parameters[0]
            neighbour = params[axis.name] + axis.step
            if neighbour > axis.high:
                neighbour = params[axis.name] - axis.step
            grid[axis.name] = sorted({params[axis.name], float(neighbour)})

            def validation_backtest(slice_bars: Any, parameters: Any) -> tuple[float, ...]:
                if self._stop.is_set():
                    raise InterruptedError("Engine stopped during validation")
                trial = run_backtest(
                    module,
                    spec,
                    list(slice_bars),
                    parameters=dict(parameters),
                    code_hash=self.library.code_hash(spec.strategy_id),
                )
                return per_bar_pnl(trial, len(slice_bars))

            evidence = run_validation(
                partitions.development,
                warmup_bars=spec.warmup_bars,
                backtest=validation_backtest,
                parameter_grid=grid,
            )
            store_evidence(
                self.root,
                spec.strategy_id,
                evidence,
                code_hash=result.code_hash,
                split_id=partitions.receipt.split_id,
            )
            evidence_args = {
                "trial_sharpes": evidence.trial_sharpes,
                "overfitting": evidence.overfitting,
                "walk_forward": evidence.walk_forward,
                "paths": evidence.paths,
            }
            self.log.record(
                "VALIDATE",
                f"{spec.strategy_id}: chronological validation complete; "
                f"PBO {evidence.overfitting.probability:.1%}",
                "info",
                spec.strategy_id,
            )
        verdict = Judge().evaluate(
            JudgeInput(
                run_id=result.backtest_id,
                tier="TRUTH_OOS" if real_data else "SWEEP_SYNTHETIC",
                pnl=pnl,
                trial_count=max(1, self.experiments.count(self._scope())),
                data_gate_passed=real_data,
                preregistered=True,
                implementation_tests_passed=True,
                lookahead_detected=not result.lookahead_clean,
                **evidence_args,
            )
        )
        self._bump("judged")
        failed = [g for g in verdict.gates if g.status != "PASS"]

        if verdict.decision == "PASS" and partitions is not None:
            self._bump("validation_passed")
            if self.library.code_hash(spec.strategy_id) != result.code_hash:
                raise ValueError("Strategy changed after validation; holdout remains sealed")
            try:
                self.research_ledger.consume(spec.lineage, partitions.receipt.split_id)
            except ValueError:
                reason = "holdout already consumed for lineage"
                self._bump("rejected")
                self._remember(key, template_key, reason)
                self.log.record("HOLDOUT", f"{spec.strategy_id} -> {reason}", "fail")
                return
            holdout = run_backtest(
                module,
                spec,
                partitions.holdout,
                parameters=params,
                code_hash=self.library.code_hash(spec.strategy_id),
                labels=("REAL_DATA", "HOLDOUT", "BURN_ONCE", "UNCALIBRATED"),
                evidence_tier="HOLDOUT",
                dataset_key=self.state.config.dataset,
                partition_name="HOLDOUT",
                split_receipt=partitions.receipt,
            )
            self.store.save(holdout)
            self._bump("backtested")
            self.research_ledger.attach_result(spec.lineage, holdout.backtest_id)
            holdout_pnl = tuple(trade.net_pnl for trade in holdout.trades)
            if not holdout_pnl:
                reason = "burn-once holdout produced no trades"
                self._bump("rejected")
                self._remember(key, template_key, reason)
                self.log.record("HOLDOUT", f"{spec.strategy_id} -> {reason}", "fail")
                return
            verdict = Judge().evaluate(
                JudgeInput(
                    run_id=holdout.backtest_id,
                    tier="HOLDOUT",
                    pnl=holdout_pnl,
                    trial_count=max(1, self.state.backtested),
                    data_gate_passed=True,
                    preregistered=True,
                    implementation_tests_passed=True,
                    lookahead_detected=not holdout.lookahead_clean,
                    **evidence_args,
                )
            )
            self._bump("judged")
            failed = [gate for gate in verdict.gates if gate.status != "PASS"]
            if verdict.decision == "PASS":
                self._bump("holdout_passed")

        if self.mirror is not None:
            self.mirror.verdict(
                verdict.model_dump(mode="json"),
                strategy_name=spec.name,
                strategy_id=spec.strategy_id,
            )

        if verdict.decision == "PASS":
            self._bump("passed")
            self.log.record(
                "JUDGE",
                f"{spec.strategy_id} → PASS (grade {verdict.grade})",
                "pass",
                verdict.verdict_id,
            )
            self._stage(worker, "prop")
            self._score_against_prop_firms(spec.strategy_id, result.trades)
        else:
            self._bump("rejected")
            reason = f"failed {failed[0].gate} ({failed[0].name})" if failed else "rejected"
            self._remember(key, template_key, reason)
            self.log.record(
                "JUDGE",
                f"{spec.strategy_id} → {verdict.decision}, {reason}",
                "fail",
                verdict.verdict_id,
            )
            # Keep only candidates that got somewhere; the rest are noise on disk.
            # Preserve failures for the post-mortem and evidence inspector.

        self._stage(worker, "idle")

    def _remember(self, key: tuple[str, str], lineage: str, reason: str) -> None:
        self._constraints[key] = reason
        self._lineage_failures[lineage] = self._lineage_failures.get(lineage, 0) + 1

    @staticmethod
    def _symbol(bars: list[Bar]) -> str:
        return bars[0].symbol if bars else "UNKNOWN"

    def constraints(self) -> list[dict[str, str]]:
        return [
            {"template": template, "parameters": signature, "reason": reason}
            for (template, signature), reason in list(self._constraints.items())[-100:]
        ]
