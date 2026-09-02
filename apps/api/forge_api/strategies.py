from __future__ import annotations

from pathlib import Path
from statistics import median
from typing import Any

from fastapi import APIRouter, HTTPException
from forge.contracts.models import ApiEnvelope
from forge.data.models import Bar
from forge.judge import Judge, JudgeInput
from forge.research import (
    ResearchLedger,
    ResearchSplitReceipt,
    chronological_split,
    source_data_hash,
)
from forge.strategy import (
    TEMPLATES,
    GuardViolation,
    StrategyLibrary,
    check_source,
    generate_bars,
    run_backtest,
    strategy_capability_catalog,
)
from pydantic import BaseModel, Field

from forge_api.activity import ActivityLog, BacktestStore, Level
from forge_api.market import MarketService


class CreateStrategyRequest(BaseModel):
    template: str
    name: str | None = None
    symbol: str = "MNQ.SYNTH"
    market: str = "futures"


class UpdateSourceRequest(BaseModel):
    source: str = Field(min_length=20)


class BacktestRequest(BaseModel):
    parameters: dict[str, float] | None = None
    dataset: str = "mnq_1m_3mo"
    bar_count: int = Field(default=30_000, ge=400, le=200_000)
    seed: int = 20260901


class SweepRequest(BaseModel):
    parameter: str
    dataset: str = "mnq_1m_3mo"
    bar_count: int = Field(default=12_000, ge=400, le=60_000)
    seed: int = 20260901


def build_router(
    root: Path,
    library: StrategyLibrary,
    store: BacktestStore,
    log: ActivityLog,
    market: MarketService,
    research_ledger: ResearchLedger,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["strategies"])

    def _bars(request: BacktestRequest) -> tuple[list[Bar], bool, str]:
        """Resolve a request to bars. Real datasets fail closed rather than
        silently falling back to synthetic data."""
        if request.dataset == "synthetic":
            return generate_bars(count=request.bar_count, seed=request.seed), False, "generator"
        bars, dataset = market.load(request.dataset, limit=request.bar_count)
        return bars, dataset.is_real, dataset.provider

    # ── templates ────────────────────────────────────────────────────────────
    @router.get("/templates", response_model=ApiEnvelope[list[dict[str, Any]]])
    def templates() -> ApiEnvelope[list[dict[str, Any]]]:
        data = [
            {
                "key": t.key,
                "name": t.name,
                "family": t.family,
                "hypothesis": t.hypothesis,
                "falsifiable_prediction": t.falsifiable_prediction,
                "parameters": [p.model_dump() for p in t.parameters],
                "warmup_bars": t.warmup_bars,
                "line_count": len(t.source.splitlines()),
                "data_requirement": t.data_requirement,
                "minimum_timeframe": t.minimum_timeframe,
                "research_status": t.research_status,
            }
            for t in TEMPLATES.values()
        ]
        return ApiEnvelope(data=data, meta={"total": len(data)})

    # ── strategies ───────────────────────────────────────────────────────────
    @router.get("/strategies", response_model=ApiEnvelope[list[dict[str, Any]]])
    def list_strategies() -> ApiEnvelope[list[dict[str, Any]]]:
        data: list[dict[str, Any]] = []
        for spec in library.list_specs():
            backtest_count, latest = store.summary_for(spec.strategy_id)
            data.append(
                {
                    **spec.model_dump(mode="json"),
                    "backtest_count": backtest_count,
                    "latest": None
                    if latest is None
                    else {
                        "backtest_id": latest["backtest_id"],
                        "net_pnl": latest["net_pnl"],
                        "trade_count": len(latest["trades"]),
                        "win_rate": latest["win_rate"],
                        "max_drawdown": latest["max_drawdown"],
                        "finished_at": latest["finished_at"],
                        "evidence_tier": latest.get("evidence_tier", "LEGACY_IN_SAMPLE"),
                        "split_id": (latest.get("split_receipt") or {}).get("split_id"),
                    },
                }
            )
        return ApiEnvelope(data=data, meta={"total": len(data)})

    @router.post("/strategies", response_model=ApiEnvelope[dict[str, Any]], status_code=201)
    def create_strategy(body: CreateStrategyRequest) -> ApiEnvelope[dict[str, Any]]:
        try:
            spec = library.create_from_template(
                body.template, name=body.name, symbol=body.symbol, market=body.market
            )
        except KeyError as exc:
            raise HTTPException(404, {"code": "template_not_found", "detail": str(exc)}) from exc
        except GuardViolation as exc:
            raise HTTPException(422, {"code": "guard_violation", "detail": str(exc)}) from exc
        log.record(
            "STRATEGY", f"created {spec.strategy_id} from {body.template}", "pass", spec.strategy_id
        )
        return ApiEnvelope(
            data={
                **spec.model_dump(mode="json"),
                "path": str(library.dir_for(spec.strategy_id)),
            }
        )

    @router.get("/strategies/{strategy_id}", response_model=ApiEnvelope[dict[str, Any]])
    def strategy_detail(strategy_id: str) -> ApiEnvelope[dict[str, Any]]:
        try:
            spec = library.get_spec(strategy_id)
        except KeyError as exc:
            raise HTTPException(404, {"code": "strategy_not_found"}) from exc
        return ApiEnvelope(
            data={
                "spec": spec.model_dump(mode="json"),
                "source": library.get_source(strategy_id),
                "tests": library.get_tests(strategy_id),
                "code_hash": library.code_hash(strategy_id),
                "path": str(library.dir_for(strategy_id)),
                "backtests": [
                    {
                        "backtest_id": b["backtest_id"],
                        "net_pnl": b["net_pnl"],
                        "trade_count": len(b["trades"]),
                        "win_rate": b["win_rate"],
                        "max_drawdown": b["max_drawdown"],
                        "parameters": b["parameters"],
                        "finished_at": b["finished_at"],
                        "evidence_tier": b.get("evidence_tier", "LEGACY_IN_SAMPLE"),
                        "split_id": (b.get("split_receipt") or {}).get("split_id"),
                    }
                    for b in store.for_strategy(strategy_id)
                ],
            }
        )

    @router.put("/strategies/{strategy_id}/source", response_model=ApiEnvelope[dict[str, Any]])
    def update_source(strategy_id: str, body: UpdateSourceRequest) -> ApiEnvelope[dict[str, Any]]:
        try:
            library.get_spec(strategy_id)
        except KeyError as exc:
            raise HTTPException(404, {"code": "strategy_not_found"}) from exc
        problems = check_source(body.source)
        if problems:
            log.record("GUARD", f"rejected edit to {strategy_id}", "fail", strategy_id)
            raise HTTPException(422, {"code": "guard_violation", "problems": problems})
        library.write_source(strategy_id, body.source)
        log.record("STRATEGY", f"source updated for {strategy_id}", "pass", strategy_id)
        return ApiEnvelope(data={"code_hash": library.code_hash(strategy_id)})

    @router.delete("/strategies/{strategy_id}", response_model=ApiEnvelope[dict[str, Any]])
    def delete_strategy(strategy_id: str) -> ApiEnvelope[dict[str, Any]]:
        try:
            library.delete(strategy_id)
        except KeyError as exc:
            raise HTTPException(404, {"code": "strategy_not_found"}) from exc
        log.record("STRATEGY", f"deleted {strategy_id}", "warn", strategy_id)
        return ApiEnvelope(data={"deleted": strategy_id})

    # ── execution ────────────────────────────────────────────────────────────
    @router.post("/strategies/{strategy_id}/backtest", response_model=ApiEnvelope[dict[str, Any]])
    def backtest(strategy_id: str, body: BacktestRequest) -> ApiEnvelope[dict[str, Any]]:
        try:
            spec = library.get_spec(strategy_id)
        except KeyError as exc:
            raise HTTPException(404, {"code": "strategy_not_found"}) from exc
        try:
            module = library.load_module(strategy_id)
        except GuardViolation as exc:
            log.record("GUARD", f"blocked {strategy_id}: {exc}", "fail", strategy_id)
            raise HTTPException(422, {"code": "guard_violation", "detail": str(exc)}) from exc

        try:
            bars, is_real, provider = _bars(body)
        except Exception as exc:
            log.record("DATA", f"{body.dataset} unavailable: {exc}", "fail")
            raise HTTPException(422, {"code": "data_unavailable", "detail": str(exc)}) from exc

        log.record(
            "BACKTEST",
            f"{strategy_id} started on {len(bars):,} {body.dataset} bars ({provider})",
            "info",
            strategy_id,
        )
        split_meta: dict[str, Any] = {}
        try:
            if is_real:
                partitions = chronological_split(bars, warmup_bars=spec.warmup_bars)
                code_hash = library.code_hash(strategy_id)
                development = run_backtest(
                    module,
                    spec,
                    partitions.development,
                    labels=("REAL_DATA", "DEVELOPMENT_IN_SAMPLE", "UNCALIBRATED"),
                    evidence_tier="DEVELOPMENT_IN_SAMPLE",
                    partition_name="DEVELOPMENT",
                    parameters=body.parameters,
                    code_hash=code_hash,
                    dataset_key=body.dataset,
                    split_receipt=partitions.receipt,
                )
                result = run_backtest(
                    module,
                    spec,
                    partitions.validation,
                    labels=("REAL_DATA", "VALIDATION_OOS", "UNCALIBRATED"),
                    evidence_tier="VALIDATION_OOS",
                    partition_name="VALIDATION",
                    parameters=body.parameters,
                    code_hash=code_hash,
                    dataset_key=body.dataset,
                    split_receipt=partitions.receipt,
                )
                store.save(development)
                split_meta = {
                    "split_receipt": partitions.receipt.model_dump(mode="json"),
                    "development_backtest_id": development.backtest_id,
                    "validation_backtest_id": result.backtest_id,
                    "holdout_consumed": research_ledger.get(spec.lineage) is not None,
                }
            else:
                result = run_backtest(
                    module,
                    spec,
                    bars,
                    parameters=body.parameters,
                    code_hash=library.code_hash(strategy_id),
                    labels=("SYNTHETIC_DATA", "UNCALIBRATED"),
                    evidence_tier="SYNTHETIC",
                    dataset_key=body.dataset,
                )
        except ValueError as exc:
            log.record("BACKTEST", f"{strategy_id} failed: {exc}", "fail", strategy_id)
            raise HTTPException(422, {"code": "backtest_failed", "detail": str(exc)}) from exc

        store.save(result)
        level: Level = "pass" if result.net_pnl > 0 else "warn"
        log.record(
            "BACKTEST",
            f"{strategy_id} finished — {len(result.trades)} trades, net {result.net_pnl:+.2f}",
            level,
            result.backtest_id,
        )
        if not result.lookahead_clean:
            log.record(
                "GATE", f"{strategy_id} FAILED lookahead invariant", "fail", result.backtest_id
            )
        return ApiEnvelope(
            data=result.model_dump(mode="json"),
            meta={
                "dataset": body.dataset,
                "provider": provider,
                "is_real": is_real,
                "trade_count": len(result.trades),
                "evidence_tier": result.evidence_tier,
                **split_meta,
            },
        )

    @router.post("/strategies/{strategy_id}/sweep", response_model=ApiEnvelope[dict[str, Any]])
    def sweep(strategy_id: str, body: SweepRequest) -> ApiEnvelope[dict[str, Any]]:
        """A real parameter sweep. Every combination counts as a trial."""
        try:
            spec = library.get_spec(strategy_id)
            module = library.load_module(strategy_id)
        except KeyError as exc:
            raise HTTPException(404, {"code": "strategy_not_found"}) from exc
        except GuardViolation as exc:
            raise HTTPException(422, {"code": "guard_violation", "detail": str(exc)}) from exc

        target = next((p for p in spec.parameters if p.name == body.parameter), None)
        if target is None:
            raise HTTPException(404, {"code": "parameter_not_found", "parameter": body.parameter})

        bars, is_real, _ = _bars(body)  # type: ignore[arg-type]
        split_receipt: ResearchSplitReceipt | None = None
        if is_real:
            partitions = chronological_split(bars, warmup_bars=spec.warmup_bars)
            bars = partitions.development
            split_receipt = partitions.receipt
        points: list[dict[str, Any]] = []
        value = float(target.low)
        while value <= float(target.high) + 1e-9:
            result = run_backtest(
                module,
                spec,
                bars,
                parameters={target.name: value},
                code_hash=library.code_hash(strategy_id),
                labels=(
                    ("REAL_DATA", "DEVELOPMENT_IN_SAMPLE", "SWEEP", "NON_PROMOTABLE")
                    if is_real
                    else ("SYNTHETIC_DATA", "SWEEP", "NON_PROMOTABLE")
                ),
                evidence_tier="DEVELOPMENT_IN_SAMPLE" if is_real else "SYNTHETIC",
                dataset_key=body.dataset,
                partition_name="DEVELOPMENT" if is_real else None,
                split_receipt=split_receipt,
            )
            points.append(
                {
                    "value": value,
                    "net_pnl": result.net_pnl,
                    "trade_count": len(result.trades),
                    "win_rate": result.win_rate,
                    "max_drawdown": result.max_drawdown,
                }
            )
            value += float(target.step)

        log.record(
            "SWEEP",
            f"{strategy_id} swept {target.name} over {len(points)} values"
            f" — {len(points)} trials counted",
            "info",
            strategy_id,
        )
        best = max(points, key=lambda p: p["net_pnl"]) if points else None
        return ApiEnvelope(
            data={"parameter": target.model_dump(), "points": points, "best": best},
            meta={
                "tier": "SWEEP",
                "promotable": False,
                "trials_counted": len(points),
                "note": "Sweep results are exploratory and can never promote a strategy.",
            },
        )

    @router.post("/strategies/{strategy_id}/judge", response_model=ApiEnvelope[dict[str, Any]])
    def judge_strategy(strategy_id: str) -> ApiEnvelope[dict[str, Any]]:
        """Judge the most recent backtest of this strategy — its real trades, not a fixture."""
        try:
            spec = library.get_spec(strategy_id)
        except KeyError as exc:
            raise HTTPException(404, {"code": "strategy_not_found"}) from exc
        all_runs = store.for_strategy(strategy_id)
        if not all_runs:
            raise HTTPException(422, {"code": "no_backtest", "detail": "run a backtest first"})
        validation_runs = [
            item for item in all_runs if item.get("evidence_tier") == "VALIDATION_OOS"
        ]
        if not validation_runs:
            latest_any = all_runs[0]
            if latest_any.get("evidence_tier") == "SYNTHETIC":
                synthetic_pnl = tuple(float(t["net_pnl"]) for t in latest_any["trades"])
                if not synthetic_pnl:
                    raise HTTPException(
                        422, {"code": "no_trades", "detail": "backtest produced no trades"}
                    )
                synthetic_verdict = Judge().evaluate(
                    JudgeInput(
                        run_id=latest_any["backtest_id"],
                        tier="SWEEP_SYNTHETIC",
                        pnl=synthetic_pnl,
                        trial_count=max(1, len(all_runs)),
                        data_gate_passed=False,
                        preregistered=True,
                        implementation_tests_passed=True,
                        lookahead_detected=not latest_any["lookahead_clean"],
                    )
                )
                return ApiEnvelope(
                    data=synthetic_verdict.model_dump(mode="json"),
                    meta={
                        "evidence_tier": "SYNTHETIC",
                        "holdout_consumed": False,
                        "note": "Synthetic evidence is judgeable for diagnostics but cannot pass.",
                    },
                )
            raise HTTPException(
                422,
                {
                    "code": "validation_oos_required",
                    "detail": "Run a real-data backtest to create a purged validation artifact.",
                },
            )
        latest = validation_runs[0]
        pnl = tuple(float(t["net_pnl"]) for t in latest["trades"])
        if not pnl:
            raise HTTPException(422, {"code": "no_trades", "detail": "backtest produced no trades"})

        trials = max(
            1,
            len(
                {
                    tuple(sorted(item.get("parameters", {}).items()))
                    for item in store.for_strategy(strategy_id)
                }
            ),
        )
        validation_verdict = Judge().evaluate(
            JudgeInput(
                run_id=latest["backtest_id"],
                tier="TRUTH_OOS",
                pnl=pnl,
                trial_count=trials,
                data_gate_passed=True,
                preregistered=True,
                implementation_tests_passed=True,
                lookahead_detected=not latest["lookahead_clean"],
            )
        )
        failed = [g for g in validation_verdict.gates if g.status != "PASS"]
        if validation_verdict.decision != "PASS":
            log.record(
                "JUDGE",
                f"{strategy_id} validation -> {validation_verdict.decision}"
                + (f" (first failure {failed[0].gate})" if failed else ""),
                "fail",
                validation_verdict.verdict_id,
            )
            return ApiEnvelope(
                data=validation_verdict.model_dump(mode="json"),
                meta={
                    "trial_count": trials,
                    "evidence_tier": "VALIDATION_OOS",
                    "holdout_consumed": False,
                    "note": "Validation failed; the burn-once holdout remains unspent.",
                },
            )

        raw_receipt = latest.get("split_receipt")
        dataset_key = latest.get("dataset_key")
        if raw_receipt is None or not dataset_key:
            raise HTTPException(422, {"code": "split_receipt_missing"})
        receipt = ResearchSplitReceipt.model_validate(raw_receipt)
        bars, dataset = market.load(dataset_key, limit=receipt.source_bar_count)
        if not dataset.is_real or source_data_hash(bars) != receipt.source_data_hash:
            raise HTTPException(409, {"code": "source_data_changed_after_validation"})
        if latest["code_hash"] != library.code_hash(strategy_id):
            raise HTTPException(409, {"code": "strategy_code_changed_after_validation"})

        # Verify the frozen inputs before spending the lineage's only holdout.
        # The token is consumed immediately before execution, so retries cannot
        # turn an observed result into another tuning round.
        try:
            research_ledger.consume(spec.lineage, receipt.split_id)
        except ValueError as exc:
            previous = research_ledger.get(spec.lineage)
            raise HTTPException(
                409,
                {
                    "code": "holdout_already_consumed",
                    "result_id": previous.result_id if previous else None,
                },
            ) from exc

        holdout_bars = bars[receipt.holdout.start_index : receipt.holdout.end_index]
        module = library.load_module(strategy_id)
        holdout = run_backtest(
            module,
            spec,
            holdout_bars,
            parameters=latest["parameters"],
            code_hash=latest["code_hash"],
            labels=("REAL_DATA", "HOLDOUT", "BURN_ONCE", "UNCALIBRATED"),
            evidence_tier="HOLDOUT",
            dataset_key=dataset_key,
            partition_name="HOLDOUT",
            split_receipt=receipt,
        )
        store.save(holdout)
        research_ledger.attach_result(spec.lineage, holdout.backtest_id)
        holdout_pnl = tuple(float(trade.net_pnl) for trade in holdout.trades)
        if not holdout_pnl:
            raise HTTPException(422, {"code": "holdout_no_trades", "holdout_spent": True})
        verdict = Judge().evaluate(
            JudgeInput(
                run_id=holdout.backtest_id,
                tier="HOLDOUT",
                pnl=holdout_pnl,
                trial_count=trials,
                data_gate_passed=True,
                preregistered=True,
                implementation_tests_passed=True,
                lookahead_detected=not holdout.lookahead_clean,
            )
        )
        failed = [g for g in verdict.gates if g.status != "PASS"]
        log.record(
            "JUDGE",
            f"{strategy_id} → {verdict.decision}"
            + (f" (first failure {failed[0].gate})" if failed else ""),
            "pass" if verdict.decision == "PASS" else "fail",
            verdict.verdict_id,
        )
        return ApiEnvelope(
            data=verdict.model_dump(mode="json"),
            meta={
                "trial_count": trials,
                "data_gate": "REAL",
                "evidence_tier": "HOLDOUT",
                "holdout_consumed": True,
                "holdout_backtest_id": holdout.backtest_id,
                "validation_verdict_id": validation_verdict.verdict_id,
                "note": "Validation passed and the lineage's burn-once holdout was consumed.",
            },
        )

    @router.get("/backtests/{backtest_id}", response_model=ApiEnvelope[dict[str, Any]])
    def backtest_detail(backtest_id: str) -> ApiEnvelope[dict[str, Any]]:
        payload = store.load(backtest_id)
        if payload is None:
            raise HTTPException(404, {"code": "backtest_not_found"})
        return ApiEnvelope(data=payload)

    # ── activity ─────────────────────────────────────────────────────────────
    @router.get("/activity", response_model=ApiEnvelope[list[dict[str, Any]]])
    def activity(limit: int = 120) -> ApiEnvelope[list[dict[str, Any]]]:
        events = log.recent(limit)
        return ApiEnvelope(
            data=[e.model_dump() for e in events],
            meta={"total": len(events)},
        )

    @router.get("/summary", response_model=ApiEnvelope[dict[str, Any]])
    def summary() -> ApiEnvelope[dict[str, Any]]:
        specs = library.list_specs()
        return ApiEnvelope(
            data={
                "strategy_count": len(specs),
                "backtest_count": store.count(),
                "template_count": len(TEMPLATES),
                "families": sorted({s.family for s in specs}),
                "strategies_path": str(library.root),
                "data_gate": "REAL",
            }
        )

    @router.get("/research/overview", response_model=ApiEnvelope[dict[str, Any]])
    def research_overview() -> ApiEnvelope[dict[str, Any]]:
        specs = library.list_specs()
        markets = ("MNQ.CME", "MES.CME", "NQ.CME", "ES.CME", "MGC.CME", "XAUUSD", "BTCUSD")
        families: list[dict[str, Any]] = []
        matrix: list[dict[str, Any]] = []
        for template in TEMPLATES.values():
            variants = [spec for spec in specs if spec.template == template.key]
            tested: list[tuple[Any, dict[str, Any]]] = []
            for spec in variants:
                latest = store.latest(spec.strategy_id)
                if latest is not None:
                    tested.append((spec, latest))
            expectancies = [
                float(item["net_pnl"]) / max(1, len(item["trades"])) for _, item in tested
            ]
            families.append(
                {
                    "key": template.key,
                    "name": template.name,
                    "family": template.family,
                    "variant_count": len(variants),
                    "tested_count": len(tested),
                    "positive_share": round(
                        sum(value > 0 for value in expectancies) / len(expectancies), 4
                    )
                    if expectancies
                    else None,
                    "median_expectancy": round(median(expectancies), 4) if expectancies else None,
                    "best_expectancy": round(max(expectancies), 4) if expectancies else None,
                    "validation_oos_count": sum(
                        item.get("evidence_tier") == "VALIDATION_OOS" for _, item in tested
                    ),
                    "holdout_count": sum(
                        item.get("evidence_tier") == "HOLDOUT" for _, item in tested
                    ),
                    "required_data": template.data_requirement,
                }
            )
            cells = []
            for market_name in markets:
                candidates = [item for spec, item in tested if spec.symbol == market_name]
                best = (
                    max(candidates, key=lambda item: float(item["net_pnl"])) if candidates else None
                )
                cells.append(
                    {
                        "market": market_name,
                        "status": "TESTED" if best else "NOT_TESTED",
                        "expectancy": (
                            round(float(best["net_pnl"]) / max(1, len(best["trades"])), 4)
                            if best
                            else None
                        ),
                        "evidence_tier": best.get("evidence_tier") if best else None,
                    }
                )
            matrix.append({"key": template.key, "name": template.name, "cells": cells})

        catalog = [item.model_dump(mode="json") for item in strategy_capability_catalog()]
        return ApiEnvelope(
            data={
                "families": families,
                "matrix": {"markets": list(markets), "rows": matrix},
                "catalog": catalog,
            },
            meta={
                "hft_claims_enabled": False,
                "note": "NOT_TESTED is distinct from zero performance.",
            },
        )

    return router
