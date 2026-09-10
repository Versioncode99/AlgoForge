"""Execution: the lifecycle, the pre-trade gate, and the one road to a fill.

`lifecycle` decides whether a strategy may execute at all. `gate` decides
whether one proposed order may. `oms` is the only thing that routes, and it will
not route an order that does not carry the gate's clearance. `paper` is the sole
adapter in this build, and every fill it produces is labelled simulated.

The ordering — proposal, risk, gate, OMS, adapter — is the whole design. There
is no code here that goes from a proposal to a venue.
"""

from forge.execution.gate import (
    BLOCKING_WHEN_UNKNOWN,
    DEFAULT_MAX_PRICE_AGE_SECONDS,
    CheckResult,
    CheckStatus,
    Clearance,
    Decision,
    GateContext,
    GateDecision,
    InstrumentRule,
    MarketState,
    ProposedOrder,
    Side,
    screen,
    screen_all,
)
from forge.execution.lifecycle import (
    EXECUTION_STAGES,
    LIVE_EXECUTION_AVAILABLE,
    LIVE_STAGES,
    RESEARCH_STAGES,
    Authorization,
    Stage,
    TransitionRefused,
    allowed_from,
    check_transition,
    requires_authorization,
)
from forge.execution.oms import (
    Book,
    BrokerAdapter,
    ExecutionStore,
    Fill,
    Order,
    OrderManagementSystem,
    OrderRefused,
    OrderRequest,
    Position,
    new_order_id,
)
from forge.execution.paper import InstrumentSpec, PaperBroker

__all__ = [
    "BLOCKING_WHEN_UNKNOWN",
    "DEFAULT_MAX_PRICE_AGE_SECONDS",
    "EXECUTION_STAGES",
    "LIVE_EXECUTION_AVAILABLE",
    "LIVE_STAGES",
    "RESEARCH_STAGES",
    "Authorization",
    "Book",
    "BrokerAdapter",
    "CheckResult",
    "CheckStatus",
    "Clearance",
    "Decision",
    "ExecutionStore",
    "Fill",
    "GateContext",
    "GateDecision",
    "InstrumentRule",
    "InstrumentSpec",
    "MarketState",
    "Order",
    "OrderManagementSystem",
    "OrderRefused",
    "OrderRequest",
    "PaperBroker",
    "Position",
    "ProposedOrder",
    "Side",
    "Stage",
    "TransitionRefused",
    "allowed_from",
    "check_transition",
    "new_order_id",
    "requires_authorization",
    "screen",
    "screen_all",
]
