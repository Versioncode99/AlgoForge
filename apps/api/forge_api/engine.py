"""The autonomous research engine.

Runs unattended in a background thread. Each cycle it forms a candidate, writes
it to disk as real strategy code, backtests it on real market data, submits it to
the deterministic judge, and records what happened — including, and especially,
the failures.

The search is deliberately memory-guided rather than random, and it is guided
twice. ``Experiments.reserve`` refuses a byte-identical repeat, and
``ResearchMemory.prune`` refuses a candidate whose parameter *neighbourhood* has
already failed for a reason that generalises. Both run here, at the front,
before any compute is spent — not in the judge.

Both survive a restart, which is the point: a research system that forgets what
it disproved is a treadmill.

Eight search policies consume bounded research proposals and explore executable
templates. Specialist model tasks run separately under a daily call cap. Selection
feedback comes from development evidence; validation and holdout remain separate.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from forge.contracts.hashing import content_hash
from forge.contracts.models import Preregistration
from forge.data.models import Bar
from forge.judge import MINIMUM_TRIAL_CONFIGURATIONS, Judge, JudgeInput
from forge.judge.statistics import per_period_sharpe
from forge.memory import FailureClass, ResearchMemory, classify_gate
from forge.prop import MIN_TRADING_DAYS, load_rules, simulate_prop_paths
from forge.provenance import RunSnapshots
from forge.research import ResearchLedger, ResearchPartitions, chronological_split
from forge.strategy import TEMPLATES, ParameterSpec, StrategyLibrary, run_backtest
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
    # Skipped because a *neighbouring* parameter set already failed, as
    # opposed to skipped because this exact set was already attempted.
    skipped_by_region: int = 0
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
            "skipped_by_region": self.skipped_by_region,
            "compute_saved": self.skipped_by_memory + self.skipped_by_region,
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
        # Durable failure record. Exact repeats are already refused by
        # Experiments.reserve; this is what generalises a failure to the region
        # around it, and what makes either survive a restart.
        self.memory = ResearchMemory(workspace.data / "research_memory.db")
        # Snapshots of judged runs: the judge source, the strategy source and
        # the data identity that produced each verdict, so a result can be
        # re-examined rather than only detected as changed.
        self.snapshots = RunSnapshots(workspace.store / "runs")
        self._rules = load_rules(workspace.repo / "rules")

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
                latest = self.store.latest_projection(spec.strategy_id)
                # Never run is worse than run and losing: it occupies a slot
                # while carrying no evidence at all.
                score = -1e18 if latest is None else float(latest.get("net_pnl") or 0.0)
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
        parent_id: str | None = None

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
                # The lineage edge these policies always had in control flow and
                # never had on disk.
                parent_id = str(parent["id"])
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

        # Memory gates: the two cheapest rejections in the system, before any
        # compute. The neighbourhood check runs first because it subsumes the
        # exact match — a repeat is at distance zero from itself.
        pruned = self.memory.prune(
            scope=self._scope(),
            template=template_key,
            parameters=params,
            ranges=template.parameters,
        )
        if pruned is not None:
            self._bump("skipped_by_region")
            self.log.record(
                "MEMORY",
                f"skipped {template_key} {signature}: {pruned.describe()}",
                "info",
            )
            return

        # Freeze the hypothesis *before* the candidate is backtested. G1 is only
        # meaningful if the claim cannot be edited once the numbers are in, so
        # the record is written first and verified against at judge time.
        prereg = _freeze_preregistration(template, params)

        attempt_id = self.experiments.reserve(
            self._scope(),
            template_key,
            params,
            preregistration_id=prereg.preregistration_id,
            preregistration_hash=prereg.content_hash,
            parent_id=parent_id,
            policy=policy,
            family=template.family,
            hypothesis=template.hypothesis,
            dataset=self.state.config.dataset,
            data_version=self._data_version,
            seed=self.state.config.seed,
        )
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
        trade_pnl = tuple(trade.net_pnl for trade in result.trades)
        self.experiments.finish(
            attempt_id,
            status="backtested",
            development_net=result.net_pnl,
            # Recorded whatever the outcome. A search remembered only through
            # its winners has a Sharpe spread that flatters every one of them.
            development_sharpe=per_period_sharpe(trade_pnl) if len(trade_pnl) > 1 else 0.0,
        )
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
            self._remember(
                template_key,
                params,
                template.parameters,
                FailureClass.NO_TRADES,
                "produced no trades",
                strategy_id=spec.strategy_id,
            )
            self.experiments.finish(
                attempt_id,
                status="no_trades",
                failure_class=str(FailureClass.NO_TRADES),
                failure_reason="produced no trades",
            )
            self.log.record("JUDGE", f"{spec.strategy_id} → no trades to judge", "warn")
            self.library.delete(spec.strategy_id)
            self._bump("created", -1)
            return

        evidence_args: dict[str, Any] = {}
        grid = _validation_grid(template, params)
        # One configuration is not a search: PBO has nothing to rank and
        # run_validation refuses it. Leaving evidence absent makes the judge
        # report INCONCLUSIVE, which is the truthful outcome.
        if (
            partitions is not None
            and result.net_pnl > 0
            and len(result.trades) >= 30
            and _grid_size(grid) >= 2
        ):
            from forge.research import run_validation

            from forge_api.strategies import per_bar_pnl, store_evidence

            self._stage(worker, "validating")

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
                # Deliberately NOT evidence.trial_sharpes. Those are the nine
                # points of this candidate's own neighbourhood, and deflating a
                # thousand-trial count against a nine-neighbour variance uses
                # two different searches for the two halves of one statistic.
                # See Experiments.sharpes.
                "trial_sharpes": self._search_sharpes(),
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
                preregistered=self._preregistration_holds(attempt_id, template, params),
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
                # Bookkeeping, not research: this says nothing about the
                # parameters, so it is recorded but never prunes.
                self._remember(
                    template_key,
                    params,
                    template.parameters,
                    FailureClass.BOOKKEEPING,
                    reason,
                    strategy_id=spec.strategy_id,
                )
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
                self._remember(
                    template_key,
                    params,
                    template.parameters,
                    FailureClass.NO_TRADES,
                    reason,
                    strategy_id=spec.strategy_id,
                )
                self.log.record("HOLDOUT", f"{spec.strategy_id} -> {reason}", "fail")
                return
            verdict = Judge().evaluate(
                JudgeInput(
                    run_id=holdout.backtest_id,
                    tier="HOLDOUT",
                    pnl=holdout_pnl,
                    trial_count=max(1, self.state.backtested),
                    data_gate_passed=True,
                    preregistered=self._preregistration_holds(attempt_id, template, params),
                    implementation_tests_passed=True,
                    lookahead_detected=not holdout.lookahead_clean,
                    **evidence_args,
                )
            )
            self._bump("judged")
            failed = [gate for gate in verdict.gates if gate.status != "PASS"]
            if verdict.decision == "PASS":
                self._bump("holdout_passed")

        self._snapshot(verdict, spec, result, params, prereg, partitions)

        if self.mirror is not None:
            self.mirror.verdict(
                verdict.model_dump(mode="json"),
                strategy_name=spec.name,
                strategy_id=spec.strategy_id,
            )

        if verdict.decision == "PASS":
            self._bump("passed")
            self.experiments.finish(
                attempt_id,
                status="passed",
                verdict_id=verdict.verdict_id,
                backtest_id=result.backtest_id,
                code_hash=result.code_hash,
            )
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
            # Learn only from a gate that actually FAILED. `failed` also holds
            # INCONCLUSIVE gates, which mean "never measured" — pruning a region
            # because nobody looked at it would delete candidates on the
            # strength of nothing.
            broken = next((gate for gate in failed if gate.status == "FAIL"), None)
            self.experiments.finish(
                attempt_id,
                # INCONCLUSIVE is not rejection: nothing was disproven, the
                # evidence was simply never produced.
                status="rejected" if broken is not None else "inconclusive",
                verdict_id=verdict.verdict_id,
                backtest_id=result.backtest_id,
                code_hash=result.code_hash,
                failure_gate=broken.gate if broken is not None else None,
                failure_reason=reason,
            )
            if broken is not None:
                failure = classify_gate(
                    broken.gate, broken.status, lookahead=not result.lookahead_clean
                )
                if failure is not None:
                    self.experiments.finish(attempt_id, failure_class=str(failure))
                    self._remember(
                        template_key,
                        params,
                        template.parameters,
                        failure,
                        f"failed {broken.gate} ({broken.name})",
                        gate=broken.gate,
                        strategy_id=spec.strategy_id,
                    )
            self.log.record(
                "JUDGE",
                f"{spec.strategy_id} → {verdict.decision}, {reason}",
                "fail",
                verdict.verdict_id,
            )
            # Keep only candidates that got somewhere; the rest are noise on disk.
            # Preserve failures for the post-mortem and evidence inspector.

        self._stage(worker, "idle")

    def _remember(
        self,
        template_key: str,
        params: dict[str, float],
        ranges: Sequence[ParameterSpec],
        failure: FailureClass,
        reason: str,
        *,
        gate: str | None = None,
        strategy_id: str | None = None,
    ) -> None:
        """Persist one classified failure so the next cycle can act on it.

        The class, not the message, is what carries research meaning: it decides
        how far this finding reaches. A reason string is for a human reading the
        log.
        """
        self.memory.record(
            scope=self._scope(),
            template=template_key,
            failure_class=failure,
            reason=reason,
            parameters=params,
            ranges=ranges,
            gate=gate,
            strategy_id=strategy_id,
        )

    def _snapshot(
        self,
        verdict: Any,
        spec: Any,
        result: Any,
        params: dict[str, float],
        prereg: Any,
        partitions: ResearchPartitions | None,
    ) -> None:
        """Record what produced this verdict. Never fatal to the search.

        A snapshot is evidence about a run, not part of computing it. If the
        disk is full or the workspace moved mid-cycle, the right outcome is a
        logged warning and a completed research cycle — not a lost candidate.
        """
        try:
            self.snapshots.write(
                run_id=verdict.run_id,
                verdict=verdict.model_dump(mode="json"),
                identity={
                    "strategy_id": spec.strategy_id,
                    "lineage": spec.lineage,
                    "template": spec.template,
                    "family": spec.family,
                    "parameters": dict(params),
                    "symbol": spec.symbol,
                    "bar_spec": spec.bar_spec,
                    "dataset": self.state.config.dataset,
                    "data_version": self._data_version,
                    "catalog_version": self._catalog_version,
                    "seed": self.state.config.seed,
                    "code_hash": result.code_hash,
                    "spec_hash": spec.spec_hash,
                    "evidence_tier": getattr(result, "evidence_tier", None),
                    "bar_count": getattr(result, "bar_count", None),
                    "split_id": partitions.receipt.split_id if partitions else None,
                    "cost_model": {
                        "commission_per_side": spec.commission_per_side,
                        "slippage_ticks": spec.slippage_ticks,
                        "tick_value": spec.tick_value,
                    },
                },
                strategy_source=self.library.source_path(spec.strategy_id).read_text(
                    encoding="utf-8"
                ),
                template_source=getattr(TEMPLATES.get(spec.template), "source", None),
                preregistration=prereg.model_dump(mode="json") if prereg else None,
            )
        except Exception as error:
            self.log.record(
                "SNAPSHOT",
                f"could not snapshot {verdict.run_id}: {type(error).__name__}: {error}",
                "warn",
            )

    def _search_sharpes(self) -> tuple[float, ...] | None:
        """The Sharpe spread of the whole search, or None when too few are recorded.

        Returning None rather than a short tuple is what makes the judge report
        G5 as INCONCLUSIVE early in a run: at that point the search genuinely
        has not produced enough trials to estimate the spread it should be
        deflated against, and inventing one would be worse than saying so.
        """
        recorded = self.experiments.sharpes(self._scope())
        return recorded if len(recorded) >= MINIMUM_TRIAL_CONFIGURATIONS else None

    def _preregistration_holds(
        self, attempt_id: str, template: Any, params: dict[str, float]
    ) -> bool:
        """Does the frozen hypothesis still describe what is being judged?

        G1 used to be handed a literal ``True`` at every call site, which made
        it a gate nothing could fail. It now re-freezes the record from the
        template and parameters as they stand at judge time and compares the
        hash with the one stored before the backtest ran. They differ if the
        hypothesis, mechanism, falsification or parameters changed in between —
        which is exactly the "find a good result, then rewrite the claim"
        move that pre-registration exists to prevent.

        A missing record fails closed. An experiment with no frozen hypothesis
        was not pre-registered, whatever else is true of it.
        """
        row = self.experiments.get(attempt_id)
        if row is None:
            return False
        stored = row.get("preregistration_hash")
        if not stored:
            return False
        return str(stored) == _freeze_preregistration(template, params).content_hash

    @staticmethod
    def _symbol(bars: list[Bar]) -> str:
        return bars[0].symbol if bars else "UNKNOWN"

    def constraints(self) -> list[dict[str, str]]:
        """What the engine has learned, newest first, straight from disk."""
        return [
            {
                "template": record.template,
                "parameters": ",".join(
                    f"{name}={value}" for name, value in sorted(record.parameters.items())
                ),
                "reason": record.reason,
                "failure_class": str(record.failure_class),
                "gate": record.gate or "",
                "at": record.created_at,
            }
            for record in self.memory.recent(self._scope(), limit=100)
        ]


# ── validation grid ──────────────────────────────────────────────────────────
# The judge deflates a result against how many configurations were tried, and
# ranks the in-sample winner against its rivals. Both need rivals to exist. The
# engine used to supply exactly two — the chosen value and one neighbour on one
# axis — which produced a V[SR] from two numbers and a PBO that was close to a
# coin flip. G5 and G11 now refuse evidence that thin, so the grid has to be
# wide enough to be worth computing.
#
# Cost is linear: run_validation backtests each configuration once over the
# development slice and reuses that T x N matrix for CSCV, walk-forward and
# CPCV. Nine configurations is therefore 4.5x the old validation cost, not 4.5x
# the whole cycle, and only profitable candidates with enough trades get here.
VALIDATION_AXES = 2
VALIDATION_POINTS_PER_AXIS = 3


def _axis_points(spec: ParameterSpec, current: float, count: int) -> list[float]:
    """``count`` values around ``current``, stepping outwards and staying in range.

    Takes the closest values first so the neighbourhood stays centred on the
    candidate actually being judged, then sorts them for a stable trial order.
    """
    low, high, step = float(spec.low), float(spec.high), float(spec.step)
    centre = round(min(high, max(low, current)), 6)
    if step <= 0.0 or high <= low:
        return [centre]
    chosen = [centre]
    distance = 1
    while len(chosen) < count and distance <= 64:
        for value in (current - distance * step, current + distance * step):
            candidate = round(value, 6)
            if low <= candidate <= high and candidate not in chosen:
                chosen.append(candidate)
                if len(chosen) == count:
                    break
        distance += 1
    return sorted(chosen)


def _grid_size(grid: dict[str, list[float]]) -> int:
    total = 1
    for values in grid.values():
        total *= max(1, len(values))
    return total


def _validation_grid(template: Any, params: dict[str, float]) -> dict[str, list[float]]:
    """A neighbourhood wide enough for G5 and G11 to mean something.

    Widens up to ``VALIDATION_AXES`` axes. If the template has only one axis
    that can move, that axis is widened on its own until it reaches the judge's
    minimum. A template whose parameters genuinely cannot produce that many
    distinct settings yields a smaller grid, and the judge reports INCONCLUSIVE
    rather than pretending the search was broader than it was.
    """
    grid: dict[str, list[float]] = {name: [value] for name, value in params.items()}
    movable = [
        spec
        for spec in template.parameters
        if spec.name in params and float(spec.high) > float(spec.low) and float(spec.step) > 0.0
    ]
    if not movable:
        return grid

    axes = movable[:VALIDATION_AXES]
    width = (
        MINIMUM_TRIAL_CONFIGURATIONS if len(axes) == 1 else VALIDATION_POINTS_PER_AXIS
    )
    for spec in axes:
        grid[spec.name] = _axis_points(spec, params[spec.name], width)

    # A discrete axis may not have offered as many distinct values as asked for.
    # Widen the remaining axes before giving up on reaching the minimum.
    if _grid_size(grid) < MINIMUM_TRIAL_CONFIGURATIONS and len(movable) > len(axes):
        for spec in movable[len(axes) :]:
            grid[spec.name] = _axis_points(spec, params[spec.name], VALIDATION_POINTS_PER_AXIS)
            if _grid_size(grid) >= MINIMUM_TRIAL_CONFIGURATIONS:
                break
    return grid


def _freeze_preregistration(template: Any, params: dict[str, float]) -> Preregistration:
    """The claim, fixed before any number exists.

    The parameters are folded into the falsification text on purpose: "this
    template has an edge" and "this template has an edge at these settings" are
    different claims, and only the second one is falsifiable by the run that
    follows. Including them is what makes the hash change if the search quietly
    moves the goalposts.

    `frozen_at` is deliberately *not* the wall clock. A hypothesis re-derived
    from the same template and the same parameters must hash identically at
    judge time, or the check would fail every candidate for the crime of time
    having passed. Identity here is the content of the claim, not when it was
    written down; when it was written down is recorded on the experiment row.
    """
    settings = ", ".join(f"{name}={params[name]}" for name in sorted(params))
    return Preregistration.freeze(
        hypothesis=template.hypothesis,
        mechanism=f"{template.family} family, {template.key} template",
        falsification=f"{template.falsifiable_prediction} Evaluated at {settings}.",
        frozen_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
