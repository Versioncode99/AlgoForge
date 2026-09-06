from __future__ import annotations

from forge.contracts.models import FrozenModel
from forge.strategy.templates import TEMPLATES


class StrategyCapability(FrozenModel):
    key: str
    name: str
    family: str
    status: str
    runnable: bool
    required_data: tuple[str, ...]
    minimum_timeframe: str
    description: str
    missing_capability: str | None = None
    template_key: str | None = None


LOCKED_RESEARCH: tuple[StrategyCapability, ...] = (
    StrategyCapability(
        key="options_exposure_walls",
        name="Options-implied range and dealer exposure",
        family="derivatives",
        status="LOCKED_DATA",
        runnable=False,
        required_data=(
            "OPTIONS_CHAIN",
            "IMPLIED_VOLATILITY",
            "OPEN_INTEREST",
            "POSITION_ASSUMPTIONS",
        ),
        minimum_timeframe="snapshot",
        description="Tests options-implied range and exposure hypotheses from timestamped chains.",
        missing_capability="Futures OHLCV cannot identify dealer positioning or a 90% price wall.",
    ),
    StrategyCapability(
        key="factor_residual_stat_arb",
        name="Factor-residual statistical arbitrage",
        family="relative_value",
        status="LOCKED_UNIVERSE",
        runnable=False,
        required_data=("SYNCHRONIZED_MULTI_ASSET", "FACTOR_RETURNS", "BORROW_COSTS"),
        minimum_timeframe="daily",
        description="Fit factor-neutral residuals and trade equilibrium deviations.",
        missing_capability="Single-contract backtests lack a hedge basket and aligned universe.",
    ),
    StrategyCapability(
        key="order_book_imbalance",
        name="Order-book imbalance continuation",
        family="microstructure",
        status="LOCKED_DATA",
        runnable=False,
        required_data=("L2_MBP", "TRADE_TICKS"),
        minimum_timeframe="event",
        description="Tests whether persistent depth imbalance predicts the next trade direction.",
        missing_capability="Historical L2 depth and event-sequenced matching are not configured.",
    ),
    StrategyCapability(
        key="queue_position_market_making",
        name="Queue-position market making",
        family="hft",
        status="LOCKED_DATA_ENGINE",
        runnable=False,
        required_data=("L3_MBO", "LATENCY_MODEL", "FEE_TIERS"),
        minimum_timeframe="event",
        description="Models passive fills, queue depletion, adverse selection and inventory risk.",
        missing_capability=(
            "One-minute bars cannot reveal queue position or passive fill probability."
        ),
    ),
    StrategyCapability(
        key="cross_venue_stat_arb",
        name="Cross-venue statistical arbitrage",
        family="relative_value",
        status="LOCKED_UNIVERSE",
        runnable=False,
        required_data=("MULTI_VENUE_TICKS", "CLOCK_SYNC", "LATENCY_MODEL"),
        minimum_timeframe="tick",
        description="Tests synchronized lead-lag and relative-value dislocations across venues.",
        missing_capability="The current run contains one market and no synchronized venue clocks.",
    ),
    StrategyCapability(
        key="latency_arbitrage",
        name="Latency-arbitrage research",
        family="hft",
        status="LOCKED_DATA_ENGINE",
        runnable=False,
        required_data=("L1_TICKS", "COLOCATED_TIMESTAMPS", "LATENCY_DISTRIBUTION"),
        minimum_timeframe="sub-millisecond",
        description="Research-only latency sensitivity and stale-quote detection.",
        missing_capability="Bar timestamps and desktop execution cannot support this claim.",
    ),
)


def strategy_capability_catalog() -> list[StrategyCapability]:
    runnable = [
        StrategyCapability(
            key=template.key,
            name=template.name,
            family=template.family,
            status=template.research_status,
            runnable=template.research_status == "RUNNABLE",
            required_data=(template.data_requirement,),
            minimum_timeframe=template.minimum_timeframe,
            description=template.hypothesis,
            template_key=template.key,
        )
        for template in TEMPLATES.values()
    ]
    return runnable + list(LOCKED_RESEARCH)
