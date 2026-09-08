from __future__ import annotations

import json
from pathlib import Path
from statistics import median
from types import SimpleNamespace
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
    DESCRIBED_TARGETS,
    TEMPLATES,
    VERIFIABLE_TARGETS,
    GuardViolation,
    StrategyLibrary,
    blueprint,
    blueprint_catalogue,
    check_determinism,
    check_source,
    describe_target,
    generate_bars,
    run_backtest,
    strategy_capability_catalog,
    to_python,
)
from forge.strategy.ir import IRError, SessionWindow, StrategyDefinition
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError

from forge_api.activity import ActivityLog, BacktestStore, Level
from forge_api.conformance_store import ensure_conformance
from forge_api.engine import DETERMINISM_WINDOW_BARS
from forge_api.jobs import REGISTRY, JobHandle
from forge_api.market import DEFAULT_DATASET, MarketService
from forge_api.mechanism_check import mechanism_for
from forge_api.preregistration_store import claim_holds_for_run, freeze_claim, record_claim

# The mechanism control is a resampling test, so it needs a seed to be
# reproducible. Fixed rather than per-request: a verdict that changes when it is
# recomputed is not a verdict.
DEFAULT_MECHANISM_SEED = 20260901


class CreateStrategyRequest(BaseModel):
    template: str
    name: str | None = None
    symbol: str = "MNQ.SYNTH"
    market: str = "futures"


class BlueprintRequest(BaseModel):
    """Create a strategy from a shipped blueprint, with the usual adjustments.

    The overrides are the ones a person actually asks for in a sentence: a
    different instrument, a different session cutoff, different defaults. Any
    deeper change is an edit to the definition, which is what
    `DefinitionRequest` is for.
    """

    blueprint: str = Field(min_length=2, max_length=64)
    name: str | None = Field(default=None, max_length=120)
    symbol: str | None = Field(default=None, max_length=16)
    parameters: dict[str, float] = Field(default_factory=dict)
    #: Minutes from midnight UTC. `None` leaves the blueprint's own window.
    session_start_minute: int | None = Field(default=None, ge=0, lt=1440)
    session_end_minute: int | None = Field(default=None, ge=0, lt=1440)
    flat_by_minute: int | None = Field(default=None, ge=0, lt=1440)
    #: Bars used to check that the generated Python reproduces the definition.
    #: Zero skips the check, and the response says so rather than implying it
    #: passed.
    verify_bars: int = Field(default=4000, ge=0, le=50_000)


class DefinitionRequest(BaseModel):
    """Create a strategy from a Strategy IR document.

    This is the route an agent composing a strategy uses, and it is deliberately
    the same one the interface uses. There is no agent-only path: a definition
    that would be refused here is refused there.
    """

    definition: dict[str, Any]
    name: str | None = Field(default=None, max_length=120)
    symbol: str | None = Field(default=None, max_length=16)
    verify_bars: int = Field(default=4000, ge=0, le=50_000)


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


class SurfaceSweepRequest(BaseModel):
    """Two parameters, a grid, and one metric drawn over it.

    `steps` is capped hard: every cell is a full backtest, so a 12x12 grid is
    144 of them. The cap is what keeps an accidental request from becoming an
    afternoon, and it is stated rather than silently truncated.
    """

    x_parameter: str = Field(min_length=1, max_length=64)
    y_parameter: str = Field(min_length=1, max_length=64)
    #: Values per axis. 6x6 is 36 backtests, which is a minute or two.
    x_steps: int = Field(default=6, ge=2, le=12)
    y_steps: int = Field(default=6, ge=2, le=12)
    metric: str = Field(default="net_pnl", max_length=32)
    dataset: str = DEFAULT_DATASET
    bar_count: int = Field(default=12_000, ge=400, le=60_000)
    seed: int = 20260901
    save: bool = True


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

    def _determinism_for(spec: Any, module: Any, code_hash: str) -> bool | None:
        """G7's evidence: does re-running the strategy reproduce its trades?

        Over synthetic bars, deliberately. The question is whether the *engine
        and the strategy code* repeat themselves, which is a property of the
        code and not of any particular dataset — and generating a window costs
        nothing, where re-reading a market archive twice does not.
        """
        window = generate_bars(count=max(spec.warmup_bars + 5, DETERMINISM_WINDOW_BARS), seed=7)

        def once(slice_bars: Any, parameters: Any) -> Any:
            return run_backtest(
                module,
                spec,
                list(slice_bars),
                parameters=dict(parameters or {}),
                code_hash=code_hash,
                labels=("DETERMINISM_CHECK", "NON_PROMOTABLE"),
                evidence_tier="SYNTHETIC",
                dataset_key="synthetic",
            )

        return check_determinism(
            spec.strategy_id, code_hash=code_hash, backtest=once, bars=window, parameters=None
        ).reproduced

    def _preregister(spec: Any, parameters: dict[str, float] | None) -> str:
        """Freeze the claim for the parameters this run will actually use.

        Returns the claim's hash so the run can carry it. The store keeps the
        audit trail; the artifact carries the binding, and only the binding is
        what G1 reads.
        """
        params = dict(spec.defaults) | dict(parameters or {})
        claim = freeze_claim(spec, params)
        if record_claim(root, spec.strategy_id, claim):
            log.record(
                "PREREG",
                f"{spec.strategy_id} claim frozen before the run",
                "info",
                spec.strategy_id,
            )
        return claim.content_hash

    def _data_gate_for(artifact: dict[str, Any]) -> bool | None:
        """G0's evidence: did the bars this run executed over pass validation?

        `run_backtest` has always validated its bars and refused to run on bad
        ones, so the check was real; the receipt was simply thrown away and the
        gate handed a literal instead. New artifacts carry the receipt. Older
        ones do not, and report unmeasured rather than borrowing the fact that
        the run completed — which is an inference about the code path, not a
        measurement of the data.
        """
        receipt = artifact.get("data_quality")
        if not isinstance(receipt, dict):
            return None
        accepted = receipt.get("accepted")
        return accepted if isinstance(accepted, bool) else None

    def _mechanism_for_artifact(spec: Any, artifact: dict[str, Any]) -> bool | None:
        """G9's evidence for a stored run, or None when it cannot be produced.

        The control has to be placed on the same bars the run filled against, so
        the artifact's split receipt is what makes this answerable at all. A
        synthetic or legacy run has no receipt, and reports unmeasured rather
        than being compared against a series it never saw.
        """
        raw_receipt = artifact.get("split_receipt")
        dataset_key = artifact.get("dataset_key")
        partition = artifact.get("partition_name")
        if not raw_receipt or not dataset_key or partition not in {"VALIDATION", "HOLDOUT"}:
            return None
        try:
            receipt = ResearchSplitReceipt.model_validate(raw_receipt)
            source, dataset = market.load(dataset_key, limit=receipt.source_bar_count)
        except Exception:
            return None
        if not dataset.is_real:
            return None
        window = receipt.validation if partition == "VALIDATION" else receipt.holdout
        bars = source[window.start_index : window.end_index]
        if not bars:
            return None
        # Trades come off the artifact as dicts, and the control only reads
        # direction, bars_held and net_pnl.
        trades = [SimpleNamespace(**trade) for trade in artifact["trades"]]
        replayed = SimpleNamespace(trades=trades)
        return mechanism_for(spec, replayed, bars, seed=DEFAULT_MECHANISM_SEED).aligned

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

    # ── the Strategy IR ──────────────────────────────────────────────────────

    @router.get("/blueprints", response_model=ApiEnvelope[list[dict[str, Any]]])
    def list_blueprints() -> ApiEnvelope[list[dict[str, Any]]]:
        """Strategies stated as data rather than as a function body.

        A blueprint knows where its stop is, which is what lets the chart draw
        one and the ledger record the one that was in force.
        """
        items = blueprint_catalogue()
        return ApiEnvelope(
            data=items,
            meta={
                "total": len(items),
                "note": (
                    "Session windows are minutes from midnight UTC. A window written "
                    "in local time and read as UTC is a strategy trading the wrong "
                    "hours, which backtests perfectly well and means nothing."
                ),
            },
        )

    def _write_definition(
        definition: Any, *, name: str | None, symbol: str | None, verify_bars: int
    ) -> ApiEnvelope[dict[str, Any]]:
        bars = generate_bars(count=max(600, verify_bars), seed=20260908) if verify_bars else None
        try:
            spec = library.create_from_definition(
                definition, name=name, symbol=symbol, verify_bars=bars
            )
        except IRError as exc:
            raise HTTPException(
                422, {"code": "invalid_definition", "detail": str(exc)}
            ) from exc
        except GuardViolation as exc:
            raise HTTPException(422, {"code": "guard_violation", "detail": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(
                422, {"code": "export_mismatch", "detail": str(exc)}
            ) from exc
        log.record(
            "STRATEGY",
            f"created {spec.strategy_id} from definition {definition.definition_id}",
            "pass",
            spec.strategy_id,
        )
        return ApiEnvelope(
            data={
                **spec.model_dump(mode="json"),
                "definition_id": definition.definition_id,
                "definition_hash": definition.definition_hash,
                "path": str(library.dir_for(spec.strategy_id)),
                "warmup_bars": spec.warmup_bars,
            },
            meta={
                # Stated either way. "Verified" and "not checked" are different
                # claims and a caller must be able to tell them apart.
                "generated_python_verified": bool(verify_bars),
                "verification": (
                    f"The compiled definition and the generated Python produced "
                    f"identical ledgers over {len(bars):,} synthetic bars."
                    if bars is not None
                    else "Not checked: verify_bars was zero."
                ),
                "canonical": "definition.json. strategy.py is its rendering.",
            },
        )

    @router.post(
        "/strategies/from-blueprint",
        response_model=ApiEnvelope[dict[str, Any]],
        status_code=201,
    )
    def create_from_blueprint(body: BlueprintRequest) -> ApiEnvelope[dict[str, Any]]:
        try:
            definition = blueprint(body.blueprint)
        except KeyError as exc:
            raise HTTPException(
                404, {"code": "blueprint_not_found", "detail": str(exc)}
            ) from exc

        changes: dict[str, Any] = {}
        if body.symbol:
            changes["symbol"] = body.symbol
        if body.parameters:
            unknown = set(body.parameters) - {p.name for p in definition.parameters}
            if unknown:
                raise HTTPException(
                    422,
                    {
                        "code": "unknown_parameter",
                        "detail": (
                            f"{', '.join(sorted(unknown))} — this blueprint declares "
                            f"{', '.join(p.name for p in definition.parameters)}"
                        ),
                    },
                )
            changes["parameters"] = tuple(
                p.model_copy(update={"default": body.parameters[p.name]})
                if p.name in body.parameters
                else p
                for p in definition.parameters
            )
        if body.session_start_minute is not None or body.session_end_minute is not None:
            current = definition.entry.session
            start = body.session_start_minute
            end = body.session_end_minute
            if current is None and (start is None or end is None):
                raise HTTPException(
                    422,
                    {
                        "code": "incomplete_session",
                        "detail": (
                            "this blueprint has no session window, so both a start "
                            "and an end minute are needed to give it one"
                        ),
                    },
                )
            window = SessionWindow(
                start_minute=(
                    start if start is not None else (current.start_minute if current else 0)
                ),
                end_minute=end if end is not None else (current.end_minute if current else 0),
                label="operator window (UTC minutes)",
            )
            changes["entry"] = definition.entry.model_copy(update={"session": window})
        if body.flat_by_minute is not None:
            changes["exit"] = definition.exit.model_copy(
                update={"flat_by_minute": body.flat_by_minute}
            )
        if changes:
            definition = definition.model_copy(update=changes)

        return _write_definition(
            definition,
            name=body.name,
            symbol=body.symbol,
            verify_bars=body.verify_bars,
        )

    @router.post(
        "/strategies/from-definition",
        response_model=ApiEnvelope[dict[str, Any]],
        status_code=201,
    )
    def create_from_definition(body: DefinitionRequest) -> ApiEnvelope[dict[str, Any]]:
        try:
            definition = StrategyDefinition.model_validate(body.definition)
        except PydanticValidationError as exc:
            raise HTTPException(
                422, {"code": "malformed_definition", "detail": exc.errors()}
            ) from exc
        return _write_definition(
            definition, name=body.name, symbol=body.symbol, verify_bars=body.verify_bars
        )

    @router.get(
        "/strategies/{strategy_id}/definition", response_model=ApiEnvelope[dict[str, Any]]
    )
    def strategy_definition(strategy_id: str) -> ApiEnvelope[dict[str, Any]]:
        """The IR this strategy renders, when there is one.

        A hand-written strategy has none, and that is reported as an absence
        rather than reverse-engineered from the source — a definition inferred
        from a function body would be a guess wearing the authority of a
        canonical record.
        """
        try:
            definition = library.get_definition(strategy_id)
        except KeyError as exc:
            raise HTTPException(
                404,
                {
                    "code": "no_definition",
                    "detail": str(exc),
                    "reason": (
                        "This strategy is hand-written Python. AlgoForge does not "
                        "infer a definition from source: a guess presented as the "
                        "canonical record is worse than no record."
                    ),
                },
            ) from exc
        return ApiEnvelope(
            data=definition.model_dump(mode="json"),
            meta={
                "definition_id": definition.definition_id,
                "definition_hash": definition.definition_hash,
                "canonical": True,
            },
        )

    @router.get(
        "/strategies/{strategy_id}/export/{target}",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def export_strategy(strategy_id: str, target: str) -> ApiEnvelope[dict[str, Any]]:
        """Render the strategy into another language, or say why it will not.

        Python is generated because AlgoForge can execute it and compare the
        ledger against the compiled definition. Pine and NinjaScript get a
        coverage report and no code: AlgoForge has no TradingView and no
        NinjaTrader to check a script against, and an unchecked generator is one
        nobody should trade from.
        """
        try:
            definition = library.get_definition(strategy_id)
        except KeyError as exc:
            raise HTTPException(
                404,
                {
                    "code": "no_definition",
                    "detail": (
                        "export renders the Strategy IR, and this strategy is "
                        "hand-written Python. Its source is already at "
                        f"/api/v1/strategies/{strategy_id}."
                    ),
                },
            ) from exc

        if target in VERIFIABLE_TARGETS:
            report = to_python(definition)
        elif target in DESCRIBED_TARGETS:
            report = describe_target(definition, target)
        else:
            raise HTTPException(
                422,
                {
                    "code": "unknown_target",
                    "detail": (
                        f"'{target}' is not an export target. Generated: "
                        f"{', '.join(VERIFIABLE_TARGETS)}. Described but not "
                        f"generated: {', '.join(DESCRIBED_TARGETS)}."
                    ),
                },
            )
        return ApiEnvelope(
            data=report.as_dict(),
            meta={
                "verifiable_targets": list(VERIFIABLE_TARGETS),
                "described_targets": list(DESCRIBED_TARGETS),
                "warranty": (
                    "An export preserves the strategy and its limitations. It is "
                    "not a claim that the strategy is profitable."
                ),
            },
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
        # G1's evidence, frozen here because this is the run the claim has to
        # precede. Append-only: a second freeze of a moved claim is recorded
        # beside the first, never over it.
        claim_hash = _preregister(spec, body.parameters)
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
                    preregistration_hash=claim_hash,
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
                    preregistration_hash=claim_hash,
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
                    preregistration_hash=claim_hash,
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

    @router.post(
        "/strategies/{strategy_id}/sweep-surface",
        response_model=ApiEnvelope[dict[str, Any]],
    )
    def sweep_surface(
        strategy_id: str, body: SurfaceSweepRequest
    ) -> ApiEnvelope[dict[str, Any]]:
        """A two-parameter landscape, run as a job.

        The brief's first example of a 3D visualisation is a strategy's own
        parameter surface. Everything needed to draw one already existed — the
        surface shape, the WebGL renderer, the artifact store, the provenance
        chain — and had nothing to draw, because the sweep could only move one
        parameter at a time.

        Every cell is a real backtest on the development partition. That is
        in-sample by construction and the result says so; a sweep is exploration
        and can never promote a strategy. The cell count is the trial count, and
        it is reported, because that is the number the deflated-Sharpe gate has
        to be told about.
        """
        from forge.research.analyses import make_provenance
        from forge.research.parameter_surface import METRICS, build_surface

        try:
            spec = library.get_spec(strategy_id)
            module = library.load_module(strategy_id)
        except KeyError as exc:
            raise HTTPException(404, {"code": "strategy_not_found"}) from exc
        except GuardViolation as exc:
            raise HTTPException(422, {"code": "guard_violation", "detail": str(exc)}) from exc

        if body.metric not in METRICS:
            raise HTTPException(
                422, {"code": "unknown_metric", "known": sorted(METRICS)}
            )
        if body.x_parameter == body.y_parameter:
            raise HTTPException(
                422,
                {
                    "code": "same_parameter_twice",
                    "detail": (
                        "A surface needs two different parameters. Sweeping one "
                        "against itself is the line the one-parameter sweep draws."
                    ),
                },
            )

        by_name = {p.name: p for p in spec.parameters}
        for axis, name in (("x", body.x_parameter), ("y", body.y_parameter)):
            if name not in by_name:
                raise HTTPException(
                    404,
                    {
                        "code": "parameter_not_found",
                        "axis": axis,
                        "parameter": name,
                        "known": sorted(by_name),
                    },
                )
        x_param = by_name[body.x_parameter]
        y_param = by_name[body.y_parameter]

        def axis_values(param: Any, steps: int) -> list[float]:
            """`steps` values across the parameter's own declared range.

            Snapped to the declared step so every value is one the strategy
            would actually accept, and de-duplicated: a range of 3 with a step
            of 1 cannot supply six distinct values however many are asked for.
            """
            low, high, step = float(param.low), float(param.high), float(param.step or 1.0)
            if steps == 1 or high <= low:
                return [low]
            span = (high - low) / (steps - 1)
            seen: list[float] = []
            for index in range(steps):
                raw = low + span * index
                snapped = low + round((raw - low) / step) * step if step > 0 else raw
                snapped = min(high, max(low, round(snapped, 10)))
                if snapped not in seen:
                    seen.append(snapped)
            return seen

        xs = axis_values(x_param, body.x_steps)
        ys = axis_values(y_param, body.y_steps)
        if len(xs) < 2 or len(ys) < 2:
            raise HTTPException(
                422,
                {
                    "code": "range_too_narrow",
                    "detail": (
                        f"'{x_param.name}' yields {len(xs)} distinct value(s) and "
                        f"'{y_param.name}' {len(ys)} across their declared ranges and "
                        "steps. A surface needs at least two on each axis."
                    ),
                },
            )

        bars, is_real, _ = _bars(body)  # type: ignore[arg-type]
        split_receipt: ResearchSplitReceipt | None = None
        if is_real:
            partitions = chronological_split(bars, warmup_bars=spec.warmup_bars)
            bars = partitions.development
            split_receipt = partitions.receipt

        total = len(xs) * len(ys)

        def work(handle: JobHandle) -> dict[str, Any]:
            points: list[dict[str, Any]] = []
            done = 0
            for x in xs:
                for y in ys:
                    handle.progress(done, f"{x_param.name}={x:g} {y_param.name}={y:g}")
                    result = run_backtest(
                        module,
                        spec,
                        bars,
                        parameters={x_param.name: x, y_param.name: y},
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
                            "x": x,
                            "y": y,
                            "net_pnl": result.net_pnl,
                            "trade_count": len(result.trades),
                            "win_rate": result.win_rate,
                            "max_drawdown": result.max_drawdown,
                            "backtest_id": result.backtest_id,
                        }
                    )
                    done += 1
            handle.progress(done, "building the surface")

            provenance = make_provenance(
                "parameter_surface",
                {
                    "strategy_id": strategy_id,
                    "dataset_key": body.dataset,
                    "spec_hash": spec.spec_hash,
                    "code_hash": library.code_hash(strategy_id),
                    "evidence_tier": "DEVELOPMENT_IN_SAMPLE" if is_real else "SYNTHETIC",
                    "partition_name": "DEVELOPMENT" if is_real else None,
                },
                # The axes and the metric are part of the identity: two surfaces
                # over the same sweep with different metrics are different
                # artifacts and must not collide on one id.
                parameters={
                    "x_parameter": x_param.name,
                    "y_parameter": y_param.name,
                    "metric": body.metric,
                    "x_values": xs,
                    "y_values": ys,
                    "bar_count": len(bars),
                },
                seed=body.seed,
            )
            surface = build_surface(
                points,
                provenance,
                x_name=x_param.name,
                y_name=y_param.name,
                metric=body.metric,
            )
            payload = surface.model_dump(mode="json")
            payload["artifact_id"] = provenance.artifact_id
            payload["content_hash"] = surface.content_hash
            payload["is_evidence"] = False
            payload["evidence_note"] = (
                "Every cell is an in-sample backtest on the development partition. "
                "A sweep is exploration: it produces no verdict, consumes no "
                "holdout, and can never promote a strategy."
            )
            payload["points"] = points
            payload["trials"] = total
            return payload

        log.record(
            "SWEEP",
            f"{strategy_id} surface over {x_param.name} x {y_param.name}"
            f" — {total} configurations",
            "info",
            strategy_id,
        )
        job = REGISTRY.submit(
            "sweep_surface",
            f"{x_param.name} x {y_param.name} — {total} backtests",
            total,
            work,
        )
        return ApiEnvelope(
            data=job.as_dict(),
            meta={
                "tier": "SWEEP",
                "promotable": False,
                "trials_counted": total,
                "is_evidence": False,
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

        # Frozen before the job is queued, not inside the worker: the claim has
        # to precede the run, and "before it was even scheduled" is the earliest
        # honest moment.
        claim_hash = _preregister(spec, body.parameters)

        def work(handle: JobHandle) -> dict[str, Any]:
            handle.progress(0, f"loading {requested:,} bars")
            envelope = _run_backtest_job(strategy_id, spec, body, requested, handle, claim_hash)
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
        claim_hash: str,
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
                preregistration_hash=claim_hash,
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
                preregistration_hash=claim_hash,
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
                preregistration_hash=claim_hash,
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

        # G2 and G7's evidence, on the operator's own path. The engine produces
        # both as it creates a candidate; a strategy judged from the interface
        # has to have them produced here, or the gates read nothing and report
        # INCONCLUSIVE. Absent or stale evidence stays None.
        conformance: bool | None = None
        determinism: bool | None = None
        try:
            module = library.load_module(strategy_id)
            code_hash = library.code_hash(strategy_id)
            conformance = ensure_conformance(
                root, library, strategy_id, module=module, code_hash=code_hash
            )
            determinism = _determinism_for(spec, module, code_hash)
        except GuardViolation as exc:
            # The module will not load, so neither check can have been run
            # against it. That is a refusal, not a pass.
            log.record("GUARD", f"blocked {strategy_id}: {exc}", "fail", strategy_id)
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
                        # Synthetic bars fail the data gate by construction —
                        # a real FAIL, not absent evidence, which is why this
                        # one stays a literal.
                        data_gate_passed=False,
                        preregistered=claim_holds_for_run(spec, latest_any),
                        implementation_tests_passed=conformance,
                        engine_consistent=determinism,
                        mechanism_aligned=None,
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
                data_gate_passed=_data_gate_for(latest),
                preregistered=claim_holds_for_run(spec, latest),
                implementation_tests_passed=conformance,
                engine_consistent=determinism,
                mechanism_aligned=_mechanism_for_artifact(spec, latest),
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
            # The holdout inherits the validated run's claim. It is the same
            # hypothesis being tested on sealed data, not a new one.
            preregistration_hash=latest.get("preregistration_hash"),
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
                # The holdout ran just now, so its receipt is on the result in
                # hand rather than read back off disk.
                data_gate_passed=(
                    holdout.data_quality.accepted if holdout.data_quality is not None else None
                ),
                preregistered=claim_holds_for_run(spec, holdout.model_dump(mode="json")),
                implementation_tests_passed=conformance,
                engine_consistent=determinism,
                # The holdout's own bars, not the validation partition's: a
                # holdout verdict must not carry a number computed on data it is
                # not supposed to have read.
                mechanism_aligned=mechanism_for(
                    spec, holdout, holdout_bars, seed=DEFAULT_MECHANISM_SEED
                ).aligned,
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
