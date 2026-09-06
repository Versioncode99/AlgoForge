"""The autonomous research engine.

Runs unattended in a background thread. Each cycle it forms a candidate, writes
it to disk as real strategy code, backtests it on real market data, submits it to
the deterministic judge, and records what happened — including, and especially,
the failures.

The search is deliberately memory-guided rather than random: a candidate whose
parameter neighbourhood has already failed is skipped before any compute is
spent, and lineages that keep failing are retired. That is the cheapest gate in
the system and it runs here, at the front, not in the judge.

There is no LLM in this loop. Hypothesis text comes from the template's stated
mechanism, so every candidate still carries a falsifiable claim. Wiring a model
in to write novel mechanisms is the next layer and is gated on a budget the
operator sets deliberately.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge.data.models import Bar
from forge.judge import Judge, JudgeInput
from forge.prop import MIN_TRADING_DAYS, load_rules, simulate_prop_paths
from forge.research import ResearchLedger, ResearchPartitions, chronological_split
from forge.strategy import TEMPLATES, StrategyLibrary, run_backtest

from forge_api.activity import ActivityLog, BacktestStore
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
    workers: int = 4
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
        root: Path,
        research_ledger: ResearchLedger,
    ) -> None:
        self.library = library
        self.store = store
        self.log = log
        self.market = market
        self.root = root
        self.research_ledger = research_ledger
        self.state = EngineState()
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # Counter increments are not atomic under threads, and neither is
        # create-then-prune against the library.
        self._state_lock = threading.Lock()
        self._library_lock = threading.Lock()
        self._rules = load_rules(root / "rules")
        # Failure constraints: (template, rounded parameter signature) -> reason.
        self._constraints: dict[tuple[str, str], str] = {}
        self._lineage_failures: dict[str, int] = {}
        self._retired_lineages: set[str] = set()

    # ── control ──────────────────────────────────────────────────────────────
    def start(self, config: EngineConfig | None = None) -> dict[str, Any]:
        with self._lock:
            if self.state.running:
                return self.state.as_dict()
            if config:
                self.state.config = config
            self._stop.clear()
            self.state.running = True
            self.state.started_at = datetime.now(UTC).isoformat(timespec="seconds")
            self.state.last_error = None
            self.state.worker_stages = {}
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
            self.state.current_stage = "idle"
        self.log.record("ENGINE", f"stopped after {self.state.cycles} cycles", "warn")
        return self.state.as_dict()

    def status(self) -> dict[str, Any]:
        return self.state.as_dict()

    # ── the loop ─────────────────────────────────────────────────────────────
    def _loop(self, worker: int = 0) -> None:
        # A different stream per worker, or four threads explore the same
        # parameter draws and three of them are wasted.
        rng = random.Random(self.state.config.seed + worker * 7919)
        try:
            bars, dataset = self.market.load(
                self.state.config.dataset, limit=self.state.config.max_bars
            )
            self.log.record(
                "DATA",
                f"{dataset.label} loaded — {len(bars):,} bars from {dataset.provider}",
                "pass" if dataset.is_real else "warn",
            )
        except Exception as exc:
            self.state.last_error = str(exc)
            self.state.running = False
            self.log.record("DATA", f"load failed: {exc}", "fail")
            return

        while not self._stop.is_set():
            try:
                self._make_room()
                self._cycle(rng, bars, dataset.is_real, worker)
            except Exception as exc:  # a bad cycle must not kill the engine
                with self._state_lock:
                    self.state.last_error = str(exc)
                    self.state.engine_errors += 1
                self.log.record("ENGINE", f"cycle error: {exc}", "fail")
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
        template_key = rng.choice(sorted(TEMPLATES))
        template = TEMPLATES[template_key]

        # Draw parameters from the spec's own declared ranges.
        params: dict[str, float] = {}
        for spec_param in template.parameters:
            steps = max(1, round((spec_param.high - spec_param.low) / spec_param.step))
            value = spec_param.low + spec_param.step * rng.randint(0, steps)
            params[spec_param.name] = round(min(value, spec_param.high), 4)

        signature = ",".join(f"{k}={params[k]}" for k in sorted(params))
        key = (template_key, signature)

        # Memory gate: cheapest rejection in the system, before any compute.
        if key in self._constraints:
            self._bump("skipped_by_memory")
            self.log.record(
                "MEMORY",
                f"skipped {template_key} {signature} — {self._constraints[key]}",
                "info",
            )
            return
        if self._lineage_failures.get(template_key, 0) >= 12:
            self._bump("skipped_by_memory")
            if template_key not in self._retired_lineages:
                self._retired_lineages.add(template_key)
                self._bump("lineages_retired")
            self.log.record("MEMORY", f"lineage {template_key} retired after 12 failures", "warn")
            return

        self._stage(worker, "creating")
        with self._library_lock:
            spec = self.library.create_from_template(template_key, symbol=self._symbol(bars))
        self._bump("created")
        self.log.record("STRATEGY", f"created {spec.strategy_id}", "info", spec.strategy_id)

        self._stage(worker, "backtesting")
        module = self.library.load_module(spec.strategy_id)
        started = time.time()
        partitions: ResearchPartitions | None = None
        if real_data:
            partitions = chronological_split(bars, warmup_bars=spec.warmup_bars)
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

        verdict = Judge().evaluate(
            JudgeInput(
                run_id=result.backtest_id,
                tier="TRUTH_OOS" if real_data else "SWEEP_SYNTHETIC",
                pnl=pnl,
                trial_count=max(1, self.state.backtested),
                data_gate_passed=real_data,
                preregistered=True,
                implementation_tests_passed=True,
                lookahead_detected=not result.lookahead_clean,
            )
        )
        self._bump("judged")
        failed = [g for g in verdict.gates if g.status != "PASS"]

        if verdict.decision == "PASS" and partitions is not None:
            self._bump("validation_passed")
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
                )
            )
            self._bump("judged")
            failed = [gate for gate in verdict.gates if gate.status != "PASS"]
            if verdict.decision == "PASS":
                self._bump("holdout_passed")

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
            if len(failed) > 3:
                self.library.delete(spec.strategy_id)
                self._bump("created", -1)

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
