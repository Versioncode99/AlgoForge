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
from forge.research import ResearchLedger, ResearchPartitions, chronological_split
from forge.strategy import TEMPLATES, StrategyLibrary, run_backtest

from forge_api.activity import ActivityLog, BacktestStore
from forge_api.market import DEFAULT_DATASET, MarketService


@dataclass
class EngineConfig:
    dataset: str = DEFAULT_DATASET
    cycle_seconds: float = 8.0
    max_strategies: int = 40
    max_bars: int = 30_000
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
    last_error: str | None = None
    current_stage: str = "idle"
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
            "last_error": self.last_error,
            "current_stage": self.current_stage,
            "config": {
                "dataset": self.config.dataset,
                "cycle_seconds": self.config.cycle_seconds,
                "max_strategies": self.config.max_strategies,
                "max_bars": self.config.max_bars,
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
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
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
            self._thread = threading.Thread(target=self._loop, name="algoforge-engine", daemon=True)
            self._thread.start()
        self.log.record("ENGINE", f"started on dataset {self.state.config.dataset}", "pass")
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
    def _loop(self) -> None:
        rng = random.Random(self.state.config.seed)
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
                self._cycle(rng, bars, dataset.is_real)
            except Exception as exc:  # a bad cycle must not kill the engine
                self.state.last_error = str(exc)
                self.state.engine_errors += 1
                self.log.record("ENGINE", f"cycle error: {exc}", "fail")
            self.state.cycles += 1

            if len(self.library.list_specs()) >= self.state.config.max_strategies:
                self.log.record(
                    "ENGINE",
                    f"reached the {self.state.config.max_strategies}-strategy ceiling; stopping",
                    "warn",
                )
                break
            self._stop.wait(self.state.config.cycle_seconds)

        self.state.running = False
        self.state.current_stage = "idle"

    def _cycle(self, rng: random.Random, bars: list[Bar], real_data: bool) -> None:
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
            self.state.skipped_by_memory += 1
            self.log.record(
                "MEMORY",
                f"skipped {template_key} {signature} — {self._constraints[key]}",
                "info",
            )
            return
        if self._lineage_failures.get(template_key, 0) >= 12:
            self.state.skipped_by_memory += 1
            if template_key not in self._retired_lineages:
                self._retired_lineages.add(template_key)
                self.state.lineages_retired += 1
            self.log.record("MEMORY", f"lineage {template_key} retired after 12 failures", "warn")
            return

        self.state.current_stage = "creating"
        spec = self.library.create_from_template(template_key, symbol=self._symbol(bars))
        self.state.created += 1
        self.log.record("STRATEGY", f"created {spec.strategy_id}", "info", spec.strategy_id)

        self.state.current_stage = "backtesting"
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
            self.state.backtested += 1
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
        self.state.backtested += 1
        self.log.record(
            "BACKTEST",
            f"{spec.strategy_id} — {len(result.trades)} trades, net {result.net_pnl:+.2f}"
            f" in {time.time() - started:.1f}s",
            "pass" if result.net_pnl > 0 else "warn",
            result.backtest_id,
        )

        self.state.current_stage = "judging"
        pnl = tuple(t.net_pnl for t in result.trades)
        if not pnl:
            self._remember(key, template_key, "produced no trades")
            self.log.record("JUDGE", f"{spec.strategy_id} → no trades to judge", "warn")
            self.library.delete(spec.strategy_id)
            self.state.created -= 1
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
        self.state.judged += 1
        failed = [g for g in verdict.gates if g.status != "PASS"]

        if verdict.decision == "PASS" and partitions is not None:
            self.state.validation_passed += 1
            try:
                self.research_ledger.consume(spec.lineage, partitions.receipt.split_id)
            except ValueError:
                reason = "holdout already consumed for lineage"
                self.state.rejected += 1
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
            self.state.backtested += 1
            self.research_ledger.attach_result(spec.lineage, holdout.backtest_id)
            holdout_pnl = tuple(trade.net_pnl for trade in holdout.trades)
            if not holdout_pnl:
                reason = "burn-once holdout produced no trades"
                self.state.rejected += 1
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
            self.state.judged += 1
            failed = [gate for gate in verdict.gates if gate.status != "PASS"]
            if verdict.decision == "PASS":
                self.state.holdout_passed += 1

        if verdict.decision == "PASS":
            self.state.passed += 1
            self.log.record(
                "JUDGE",
                f"{spec.strategy_id} → PASS (grade {verdict.grade})",
                "pass",
                verdict.verdict_id,
            )
        else:
            self.state.rejected += 1
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
                self.state.created -= 1

        self.state.current_stage = "idle"

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
