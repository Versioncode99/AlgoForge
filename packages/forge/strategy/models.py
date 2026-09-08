from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from forge.contracts.hashing import content_hash, stable_id
from forge.contracts.models import FrozenModel
from forge.data.models import DataQualityReceipt
from forge.research.models import EvidenceTier, ResearchSplitReceipt

ParamValue = float | int


class ParameterSpec(FrozenModel):
    """One tunable knob. `sweep` is inclusive [low, high] with `step`."""

    name: str
    default: ParamValue
    low: ParamValue
    high: ParamValue
    step: ParamValue
    description: str = ""


class StrategySpec(FrozenModel):
    """The frozen, human-readable declaration of a strategy.

    `hypothesis` and `falsifiable_prediction` are mandatory by design: a spec that
    cannot be wrong is not a hypothesis, and the judge's mechanism gate has nothing
    to test against.
    """

    strategy_id: str
    name: str
    lineage: str
    # A registry key, not a closed enum. Families are research classifications
    # that the operator and the agents extend at run time; see
    # forge.strategy.families. The pattern is the only structural constraint,
    # and membership is checked where a family is *chosen*, not where a spec is
    # read back, so an old spec never becomes unloadable because a family was
    # renamed.
    family: str = Field(pattern=r"^[a-z][a-z0-9_]{2,39}$")
    market: Literal["futures", "crypto"]
    symbol: str
    bar_spec: str = "1m"
    template: str
    hypothesis: str = Field(min_length=40)
    falsifiable_prediction: str = Field(min_length=30)
    parameters: tuple[ParameterSpec, ...]
    warmup_bars: int = 50
    commission_per_side: float = 0.62
    slippage_ticks: float = 1.0
    tick_value: float = 0.50
    created_at: datetime
    created_by: str = "operator"
    research_sources: tuple[str, ...] = ()
    adaptation_note: str = ""

    @property
    def defaults(self) -> dict[str, ParamValue]:
        return {p.name: p.default for p in self.parameters}

    @property
    def spec_hash(self) -> str:
        return content_hash(self.model_dump(mode="json"))


class Trade(FrozenModel):
    """One completed round trip.

    Both decision indices are carried so the lookahead invariant can be asserted from
    the artifact itself, without re-running anything: every decision must sit exactly
    one bar before the fill it caused.
    """

    trade_id: str
    direction: Literal[1, -1]
    entry_decision_index: int
    entry_index: int
    exit_decision_index: int
    exit_index: int
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    gross_pnl: float
    costs: float
    net_pnl: float
    bars_held: int
    exit_reason: Literal["signal", "stop", "max_bars", "end_of_data"]

    # ── what the trade did while it was open ─────────────────────────────────
    # Measured from the bars the trade actually spanned, in the same currency as
    # `net_pnl`, so "it was up $612 before it gave it back" is a fact from the
    # ledger rather than something re-derived later against different bars.
    #
    # Optional because artifacts written before these fields existed do not
    # carry them, and a missing measurement must read as absent rather than as
    # zero — an MFE of 0.0 and an unrecorded MFE are different claims.
    mfe: float | None = None
    mae: float | None = None
    mfe_index: int | None = None
    mae_index: int | None = None

    # ── the levels the trade ran under ───────────────────────────────────────
    # Frozen at the bar after the fill by the strategy that set them, not
    # recomputed. A chart drawing a stop recomputed from today's ATR would be
    # drawing a line that never existed.
    stop_price: float | None = None
    target_price: float | None = None
    trailing_stop_price: float | None = None

    #: Feature values at the decision bar, so "why did it enter?" can be
    #: answered without the bars. Empty when the strategy did not report any.
    entry_context: dict[str, float] = Field(default_factory=dict)

    @property
    def holding_seconds(self) -> float:
        return (self.exit_time - self.entry_time).total_seconds()


class BacktestResult(FrozenModel):
    calculation_version: str = "legacy-price-points"
    point_value: float | None = None
    tick_size: float | None = None
    backtest_id: str
    strategy_id: str
    spec_hash: str
    code_hash: str
    data_hash: str
    parameters: dict[str, ParamValue]
    bar_count: int
    trades: tuple[Trade, ...]
    equity: tuple[float, ...]
    net_pnl: float
    gross_pnl: float
    total_costs: float
    win_rate: float
    max_drawdown: float
    lookahead_clean: bool
    labels: tuple[str, ...]
    evidence_tier: EvidenceTier = "LEGACY_IN_SAMPLE"
    dataset_key: str | None = None
    partition_name: Literal["DEVELOPMENT", "VALIDATION", "HOLDOUT"] | None = None
    split_receipt: ResearchSplitReceipt | None = None
    # G0's evidence. `run_backtest` has always validated its bars and refused to
    # run on bad ones, but the receipt was discarded, so the gate read a literal
    # `True` instead of the measurement that had just been taken. Optional
    # because artifacts written before this field existed do not carry one, and
    # an absent receipt must read as INCONCLUSIVE rather than as a pass.
    data_quality: DataQualityReceipt | None = None
    # G1's evidence, carried by the run rather than looked up beside it. A
    # store keyed only by strategy could be laundered: rewrite the hypothesis,
    # start any throwaway backtest to freeze the new claim, and the new claim
    # then validated the *old* artifact. Recording the hash on the artifact
    # binds the claim to the run it actually preceded, and re-deriving it from
    # the current spec at judge time is what detects a claim that has moved.
    preregistration_hash: str | None = None
    started_at: datetime
    finished_at: datetime

    @property
    def pnl_series(self) -> tuple[float, ...]:
        return tuple(t.net_pnl for t in self.trades)

    @classmethod
    def make_id(
        cls, spec_hash: str, code_hash: str, data_hash: str, params: dict[str, ParamValue]
    ) -> str:
        return stable_id(
            "backtest",
            {"spec": spec_hash, "code": code_hash, "data": data_hash, "params": params},
        )
