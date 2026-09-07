from __future__ import annotations

import json
from pathlib import Path
from statistics import median
from typing import Any

from fastapi import APIRouter, HTTPException
from forge.contracts.models import ApiEnvelope
from forge.data.models import Bar
from forge.judge import BacktestOverfitting, Judge, JudgeInput
from forge.prop.engine import MAX_BACKTEST_BARS
from forge.research import (
    PathDistribution,
    ResearchLedger,
    ResearchSplitReceipt,
    ValidationEvidence,
    WalkForwardResult,
    chronological_split,
    run_validation,
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
from forge_api.jobs import REGISTRY, JobHandle
from forge_api.market import DEFAULT_DATASET, MarketService


class CreateStrategyRequest(BaseModel):
    template: str
    name: str | None = None
    symbol: str = "MNQ.SYNTH"
    market: str = "futures"


class UpdateSourceRequest(BaseModel):
    source: str = Field(min_length=20)


class BacktestRequest(BaseModel):
    parameters: dict[str, float] | None = None
    dataset: str = DEFAULT_DATASET
    # Nobody thinks in bars. `years` is resolved against the dataset's measured
    # density; `bar_count` still wins when given, so scripts stay exact.
    years: float | None = Field(default=None, gt=0, le=25)
    # Only the 20% validation slice reaches the prop simulator, so the request
    # has to be five times the window the gate needs. At 250k one-minute bars
    # the validation partition spans about 39 trading days, clearing the 30-day
    # minimum; anything much smaller makes the gate unreachable by construction.
    bar_count: int = Field(default=250_000, ge=400, le=MAX_BACKTEST_BARS)
    # A ceiling the operator sets, independent of the range. Asking for sixteen
    # years on a slow machine should be capped by choice, not by a surprise.
    max_bars: int | None = Field(default=None, ge=400, le=MAX_BACKTEST_BARS)
    seed: int = 20260901

    def resolved_bars(self, market: MarketService) -> int:
        """Bar count after the range and the operator's cap are applied."""
        if self.years is not None:
            bars = market.resolve_bars(self.dataset, self.years, None) or self.bar_count
        else:
            bars = self.bar_count
        if self.max_bars is not None:
            bars = min(bars, self.max_bars)
        return max(400, min(bars, MAX_BACKTEST_BARS))


class ValidationRequest(BaseModel):
    dataset: str = DEFAULT_DATASET
    bar_count: int = Field(default=60_000, ge=2_000, le=MAX_BACKTEST_BARS)
    seed: int = 20260901
    parameters: dict[str, float] | None = None
    folds: int = Field(default=6, ge=2, le=20)
    groups: int = Field(default=6, ge=3, le=12)
    test_groups: int = Field(default=2, ge=1, le=5)
    blocks: int = Field(default=8, ge=2, le=16)
    # Every trial is a full backtest, so the grid is capped rather than
    # allowed to grow as the product of every parameter's range.
    max_trials: int = Field(default=16, ge=2, le=120)


class SweepRequest(BaseModel):
    parameter: str
    dataset: str = DEFAULT_DATASET
    bar_count: int = Field(default=12_000, ge=400, le=60_000)
    seed: int = 20260901


def validation_grid(parameters: tuple[Any, ...], max_trials: int) -> dict[str, list[float]]:
    """A coarse but even grid over the spec's own declared ranges.

    Every trial is a full backtest, so the full cartesian product of each
    parameter's low..high range is not affordable. Each range is subsampled
    evenly and the widest axis is thinned first until the product fits, which
    keeps the corners of the space -- where overfitting shows up -- rather than
    clustering around the defaults.
    """
    candidates: dict[str, list[float]] = {}
    for spec in parameters:
        low, high, step = float(spec.low), float(spec.high), float(spec.step)
        if step <= 0 or high <= low:
            continue
        values: list[float] = []
        value = low
        while value <= high + 1e-9:
            values.append(round(value, 10))
            value += step
        if len(values) > 1:
            candidates[spec.name] = values
    if not candidates:
        return {}

    per_axis = max(2, int(max_trials ** (1.0 / len(candidates))))
    grid = {
        name: _subsample(values, min(per_axis, len(values))) for name, values in candidates.items()
    }
    while _product(grid) > max_trials:
        widest = max(grid, key=lambda name: len(grid[name]))
        if len(grid[widest]) <= 2:
            # Cannot thin further without dropping an axis entirely. Keep at
            # least one axis so there is still a selection to audit.
            if len(grid) <= 1:
                break
            del grid[widest]
            continue
        grid[widest] = _subsample(candidates[widest], len(grid[widest]) - 1)
    return grid


def _subsample(values: list[float], count: int) -> list[float]:
    """`count` values spread evenly across the range, endpoints always kept."""
    if count >= len(values):
        return list(values)
    if count <= 1:
        return [values[0]]
    step = (len(values) - 1) / (count - 1)
    return [values[round(index * step)] for index in range(count)]


def _product(grid: dict[str, list[float]]) -> int:
    total = 1
    for values in grid.values():
        total *= len(values)
    return total


def per_bar_pnl(result: Any, bar_count: int) -> tuple[float, ...]:
    """Place each trade's net P&L on the bar it closed on.

    CSCV compares configurations on one shared time grid, so a per-trade series
    is the wrong shape: two configurations that took different numbers of
    trades would not be alignable at all.
    """
    series = [0.0] * bar_count
    for trade in result.trades:
        index = int(trade.exit_index)
        if 0 <= index < bar_count:
            series[index] += float(trade.net_pnl)
    return tuple(series)


def store_evidence(
    root: Path,
    strategy_id: str,
    evidence: ValidationEvidence,
    *,
    code_hash: str = "",
    split_id: str = "",
) -> Path:
    """Persist evidence so the judge can read it without re-running the stack."""
    directory = root / "data" / "validation"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{strategy_id}.json"
    path.write_text(
        json.dumps(
            {
                **evidence.as_metadata(),
                "code_hash": code_hash,
                "split_id": split_id,
                "calculation_version": "contract-units-v2",
                "trial_sharpes": list(evidence.trial_sharpes),
                "overfitting": {
                    "probability": evidence.overfitting.probability,
                    "splits": evidence.overfitting.splits,
                    "trials": evidence.overfitting.trials,
                    "blocks": evidence.overfitting.blocks,
                    "logits": list(evidence.overfitting.logits),
                    "median_logit": evidence.overfitting.median_logit,
                },
                "walk_forward": vars(evidence.walk_forward),
                "paths": vars(evidence.paths),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def load_evidence(root: Path, strategy_id: str) -> dict[str, Any] | None:
    """Read stored evidence, or None when validation has never been run."""
    path = root / "data" / "validation" / f"{strategy_id}.json"
    if not path.exists():
        return None
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Unreadable evidence must read as absent, never as favourable.
        return None
    return payload


def judge_evidence(
    root: Path,
    strategy_id: str,
    *,
    code_hash: str = "",
    split_id: str = "",
) -> dict[str, Any]:
    """Rehydrate stored validation evidence as JudgeInput keyword arguments.

    Anything missing or malformed comes back absent rather than defaulted, so a
    corrupted file downgrades the verdict to INCONCLUSIVE instead of quietly
    supplying favourable numbers.
    """
    payload = load_evidence(root, strategy_id)
    if payload is None:
        return {}
    if code_hash and (
        payload.get("code_hash") != code_hash
        or payload.get("split_id") != split_id
        or payload.get("calculation_version") != "contract-units-v2"
    ):
        return {}
    try:
        overfitting = payload["overfitting"]
        walk = payload["walk_forward"]
        paths = payload["paths"]
        return {
            "trial_sharpes": tuple(float(value) for value in payload["trial_sharpes"]),
            "overfitting": BacktestOverfitting(
                probability=float(overfitting["probability"]),
                splits=int(overfitting["splits"]),
                trials=int(overfitting["trials"]),
                blocks=int(overfitting["blocks"]),
                logits=tuple(float(value) for value in overfitting["logits"]),
                median_logit=float(overfitting["median_logit"]),
            ),
            "walk_forward": WalkForwardResult(**walk),
            "paths": PathDistribution(**paths),
        }
    except (KeyError, TypeError, ValueError):
        return {}


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
        limit = (
            request.resolved_bars(market)
            if isinstance(request, BacktestRequest)
            else request.bar_count
        )
        if request.dataset == "synthetic":
            return generate_bars(count=limit, seed=request.seed), False, "generator"
        bars, dataset = market.load(request.dataset, limit=limit)
        return bars, dataset.is_real, dataset.provider

    # ── templates ────────────────────────────────────────────────────────────
    # ── strategies ───────────────────────────────────────────────────────────
    @router.get("/strategies", response_model=ApiEnvelope[list[dict[str, Any]]])
    def list_strategies() -> ApiEnvelope[list[dict[str, Any]]]:
        data: list[dict[str, Any]] = []
        for spec in library.list_specs():
            backtest_count, latest = store.list_summary(spec.strategy_id)
            data.append(
                {
                    **spec.model_dump(mode="json"),
                    "backtest_count": backtest_count,
                    "latest": latest,
                }
            )
        return ApiEnvelope(data=data, meta={"total": len(data), "index": store.status()})

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
                # Projections, not artifacts: this table shows nine scalars per
                # run, and reading every trade ledger to draw them made opening
                # a well-explored strategy cost hundreds of megabytes.
                "backtests": [
                    {
                        "backtest_id": b["backtest_id"],
                        "net_pnl": b["net_pnl"],
                        "trade_count": b["trade_count"],
                        "win_rate": b["win_rate"],
                        "max_drawdown": b["max_drawdown"],
                        "parameters": b["parameters"],
                        "finished_at": b["finished_at"],
                        "evidence_tier": b.get("evidence_tier", "LEGACY_IN_SAMPLE"),
                        "split_id": b.get("split_id"),
                    }
                    for b in store.projections_for(strategy_id)
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

    # ── long runs ────────────────────────────────────────────────────────────
    @router.post(
        "/strategies/{strategy_id}/backtest/async", response_model=ApiEnvelope[dict[str, Any]]
    )
    def backtest_async(strategy_id: str, body: BacktestRequest) -> ApiEnvelope[dict[str, Any]]:
        """Start a backtest as a job and return its id immediately.

        The synchronous route stays for scripts and tests. Anything driven by a
        person goes through here, because sixteen years is minutes of work and
        an interface with no progress to show is indistinguishable from a hang.
        """
        try:
            spec = library.get_spec(strategy_id)
            library.load_module(strategy_id)
        except KeyError as exc:
            raise HTTPException(404, {"code": "strategy_not_found"}) from exc
        except GuardViolation as exc:
            raise HTTPException(422, {"code": "guard_violation", "detail": str(exc)}) from exc

        requested = body.resolved_bars(market)
        available = market.available_rows(body.dataset)
        if available and requested > available:
            requested = available

        def work(handle: JobHandle) -> dict[str, Any]:
            handle.progress(0, f"loading {requested:,} bars")
            envelope = _run_backtest_job(strategy_id, spec, body, requested, handle)
            return envelope

        job = REGISTRY.submit(
            "backtest",
            f"{spec.name} · {requested:,} bars · {body.dataset}",
            requested,
            work,
        )
        log.record(
            "BACKTEST",
            f"{strategy_id} queued on {requested:,} {body.dataset} bars",
            "info",
            strategy_id,
        )
        return ApiEnvelope(
            data=job.as_dict(),
            meta={"dataset": body.dataset, "requested_bars": requested},
        )

    def _run_backtest_job(
        strategy_id: str,
        spec: Any,
        body: BacktestRequest,
        requested: int,
        handle: JobHandle,
    ) -> dict[str, Any]:
        module = library.load_module(strategy_id)
        if body.dataset == "synthetic":
            bars, is_real, provider = (
                generate_bars(count=requested, seed=body.seed),
                False,
                "generator",
            )
        else:
            loaded, dataset = market.load(body.dataset, limit=requested)
            bars, is_real, provider = loaded, dataset.is_real, dataset.provider

        code_hash = library.code_hash(strategy_id)
        split_meta: dict[str, Any] = {}
        # Two partitions are run, so progress is reported across both rather
        # than jumping back to zero when the validation pass starts.
        if is_real:
            partitions = chronological_split(bars, warmup_bars=spec.warmup_bars)
            dev_total = len(partitions.development)
            val_total = len(partitions.validation)
            combined = max(1, dev_total + val_total)

            def dev_progress(done: int, total: int) -> None:
                handle.progress(
                    int(requested * (done / combined)), f"in-sample · {done:,}/{dev_total:,}"
                )

            def val_progress(done: int, total: int) -> None:
                handle.progress(
                    int(requested * ((dev_total + done) / combined)),
                    f"out-of-sample · {done:,}/{val_total:,}",
                )

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
                progress=dev_progress,
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
                progress=val_progress,
            )
            store.save(development)
            split_meta = {
                "split_receipt": partitions.receipt.model_dump(mode="json"),
                "development_backtest_id": development.backtest_id,
                "validation_backtest_id": result.backtest_id,
                "development_net_pnl": development.net_pnl,
                "development_trade_count": len(development.trades),
            }
        else:

            def plain_progress(done: int, total: int) -> None:
                handle.progress(int(requested * (done / max(total, 1))), f"{done:,}/{total:,}")

            result = run_backtest(
                module,
                spec,
                bars,
                parameters=body.parameters,
                code_hash=code_hash,
                labels=("SYNTHETIC_DATA", "UNCALIBRATED"),
                evidence_tier="SYNTHETIC",
                dataset_key=body.dataset,
                progress=plain_progress,
            )

        store.save(result)
        log.record(
            "BACKTEST",
            f"{strategy_id} finished - {len(result.trades)} trades, net {result.net_pnl:+.2f}",
            "pass" if result.net_pnl > 0 else "warn",
            result.backtest_id,
        )
        return {
            "result": result.model_dump(mode="json"),
            "meta": {
                "dataset": body.dataset,
                "provider": provider,
                "is_real": is_real,
                "bar_count": len(bars),
                "trade_count": len(result.trades),
                "evidence_tier": result.evidence_tier,
                **split_meta,
            },
        }

    @router.get("/jobs", response_model=ApiEnvelope[list[dict[str, Any]]])
    def list_jobs(limit: int = 20) -> ApiEnvelope[list[dict[str, Any]]]:
        rows = [job.as_dict() for job in REGISTRY.recent(limit)]
        return ApiEnvelope(data=rows, meta={"total": len(rows)})

    @router.get("/jobs/{job_id}", response_model=ApiEnvelope[dict[str, Any]])
    def get_job(job_id: str) -> ApiEnvelope[dict[str, Any]]:
        job = REGISTRY.get(job_id)
        if job is None:
            raise HTTPException(404, {"code": "job_not_found", "job_id": job_id})
        payload = job.as_dict()
        # The result rides along only once, on completion, so polling stays cheap.
        if job.status == "DONE":
            payload["result"] = job.result
        return ApiEnvelope(data=payload)

    @router.post("/jobs/{job_id}/cancel", response_model=ApiEnvelope[dict[str, Any]])
    def cancel_job(job_id: str) -> ApiEnvelope[dict[str, Any]]:
        job = REGISTRY.get(job_id)
        if job is None:
            raise HTTPException(404, {"code": "job_not_found", "job_id": job_id})
        accepted = REGISTRY.cancel(job_id)
        return ApiEnvelope(
            data={"job_id": job_id, "cancelling": accepted, "status": job.status},
            meta={"note": "Cancellation takes effect at the run's next checkpoint."},
        )

    @router.post("/strategies/{strategy_id}/validate", response_model=ApiEnvelope[dict[str, Any]])
    def validate_strategy(strategy_id: str, body: ValidationRequest) -> ApiEnvelope[dict[str, Any]]:
        """Run walk-forward, CSCV and CPCV, and store the evidence for the judge.

        Until this has run, gates G5 and G11-G13 have nothing to read and the
        verdict is INCONCLUSIVE by design.
        """
        try:
            spec = library.get_spec(strategy_id)
            module = library.load_module(strategy_id)
        except KeyError as exc:
            raise HTTPException(404, {"code": "strategy_not_found"}) from exc
        except GuardViolation as exc:
            raise HTTPException(422, {"code": "guard_violation", "detail": str(exc)}) from exc

        grid = validation_grid(spec.parameters, body.max_trials)
        if not grid:
            raise HTTPException(
                422,
                {
                    "code": "no_sweepable_parameters",
                    "detail": (
                        "Validation audits how a parameter search behaves. This strategy "
                        "declares no parameter with a usable low/high/step range, so there "
                        "is no selection to audit."
                    ),
                },
            )

        try:
            bars, is_real, provider = _bars(body)  # type: ignore[arg-type]
        except Exception as exc:
            raise HTTPException(422, {"code": "data_unavailable", "detail": str(exc)}) from exc
        if not is_real:
            raise HTTPException(
                422,
                {
                    "code": "real_data_required",
                    "detail": (
                        "Synthetic bars carry no edge by construction, so validating against "
                        "them measures the generator, not the strategy."
                    ),
                },
            )

        # Parameter selection must never inspect the reserved validation or holdout.
        partitions = chronological_split(bars, warmup_bars=spec.warmup_bars)
        bars = list(partitions.development)
        code_hash = library.code_hash(strategy_id)

        def backtest(slice_bars: Any, parameters: Any) -> tuple[float, ...]:
            result = run_backtest(
                module,
                spec,
                list(slice_bars),
                parameters=dict(parameters),
                code_hash=code_hash,
                labels=("REAL_DATA", "VALIDATION_TRIAL", "NON_PROMOTABLE"),
                evidence_tier="DEVELOPMENT_IN_SAMPLE",
                dataset_key=body.dataset,
            )
            return per_bar_pnl(result, len(slice_bars))

        log.record(
            "VALIDATE",
            f"{strategy_id} validating {_product(grid)} configurations on "
            f"{len(bars):,} {body.dataset} bars ({provider})",
            "info",
            strategy_id,
        )
        try:
            evidence = run_validation(
                bars,
                warmup_bars=spec.warmup_bars,
                backtest=backtest,
                parameter_grid=grid,
                folds=body.folds,
                groups=body.groups,
                test_groups=body.test_groups,
                blocks=body.blocks,
            )
        except ValueError as exc:
            log.record("VALIDATE", f"{strategy_id} failed: {exc}", "fail", strategy_id)
            raise HTTPException(422, {"code": "validation_failed", "detail": str(exc)}) from exc

        store_evidence(
            root, strategy_id, evidence, code_hash=code_hash, split_id=partitions.receipt.split_id
        )
        survived = (
            evidence.walk_forward.survives
            and evidence.paths.robust
            and not evidence.overfitting.selection_is_unreliable
        )
        log.record(
            "VALIDATE",
            f"{strategy_id} - PBO {evidence.overfitting.probability:.1%}, "
            f"WF efficiency {evidence.walk_forward.efficiency:.2f}, "
            f"path p05 {evidence.paths.sharpe_p05:+.3f}",
            "pass" if survived else "warn",
            evidence.evidence_id,
        )
        return ApiEnvelope(
            data={
                **evidence.as_metadata(),
                "grid": grid,
                "walk_forward": vars(evidence.walk_forward),
                "paths": vars(evidence.paths),
                "trial_sharpes": list(evidence.trial_sharpes),
                "cscv_logits": list(evidence.overfitting.logits),
            },
            meta={
                "dataset": body.dataset,
                "bar_count": len(bars),
                "survived": survived,
                "note": (
                    "Validation evidence, not a verdict. Run the judge to combine it "
                    "with the remaining gates."
                ),
            },
        )

    @router.get(
        "/strategies/{strategy_id}/validation", response_model=ApiEnvelope[dict[str, Any] | None]
    )
    def validation_evidence(strategy_id: str) -> ApiEnvelope[dict[str, Any] | None]:
        """Return the last stored validation receipt without re-running it."""
        try:
            library.get_spec(strategy_id)
        except KeyError as exc:
            raise HTTPException(404, {"code": "strategy_not_found"}) from exc
        payload = load_evidence(root, strategy_id)
        if payload is None:
            return ApiEnvelope(data=None, meta={"status": "NOT_TESTED"})
        overfitting = payload.get("overfitting") or {}
        return ApiEnvelope(
            data={
                **payload,
                "probability_of_overfitting": overfitting.get("probability"),
                "cscv_splits": overfitting.get("splits"),
                "cscv_logits": overfitting.get("logits", []),
            },
            meta={
                "status": "VALIDATION_RECORDED",
                "note": "Stored development-validation evidence; holdout data is not exposed.",
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
        all_runs = [r for r in all_runs if r.get("calculation_version") == "contract-units-v2"]
        if not all_runs:
            raise HTTPException(
                422,
                {
                    "code": "legacy_units",
                    "detail": "Rerun with corrected contract units before judging this strategy.",
                },
            )
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

        # Distinct parameter sets tried. Counted from projections: the artifacts
        # were already read once above, and a second full pass over every trade
        # ledger to take `parameters` off each one is pure waste.
        trials = max(
            1,
            len(
                {
                    tuple(sorted((item.get("parameters") or {}).items()))
                    for item in store.projections_for(strategy_id)
                }
            ),
        )
        evidence = judge_evidence(
            root,
            strategy_id,
            code_hash=latest["code_hash"],
            split_id=(latest.get("split_receipt") or {}).get("split_id", ""),
        )
        overfitting = evidence.get("overfitting")
        # The recorded artifact count is a floor on the trials run. When the
        # validation sweep ran, its grid is the larger and more honest number.
        if overfitting is not None:
            trials = max(trials, overfitting.trials)
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
                **evidence,
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
        holdout_evidence = evidence
        holdout_overfitting = holdout_evidence.get("overfitting")
        if holdout_overfitting is not None:
            trials = max(trials, holdout_overfitting.trials)
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
                **holdout_evidence,
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
            # Projections only. This endpoint summarises every template across
            # every market; reading one trade ledger per strategy to divide two
            # scalars made the research overview the second slowest route in
            # the application.
            tested: list[tuple[Any, dict[str, Any]]] = []
            for spec in variants:
                latest = store.latest_projection(spec.strategy_id)
                if latest is not None and latest.get("calculation_version") == "contract-units-v2":
                    tested.append((spec, latest))
            expectancies = [
                float(item["net_pnl"]) / max(1, int(item["trade_count"])) for _, item in tested
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
                candidates = [
                    item
                    for spec, item in tested
                    if spec.symbol.split(".")[0] == market_name.split(".")[0]
                ]
                best = (
                    max(candidates, key=lambda item: float(item["net_pnl"])) if candidates else None
                )
                cells.append(
                    {
                        "market": market_name,
                        "status": "TESTED" if best else "NOT_TESTED",
                        "expectancy": (
                            round(float(best["net_pnl"]) / max(1, int(best["trade_count"])), 4)
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
