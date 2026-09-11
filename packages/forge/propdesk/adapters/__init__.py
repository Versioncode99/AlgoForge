"""Provider adapters. One executes; three declare and refuse.

`forge.propdesk.adapters.simulated` is a working implementation of the whole
adapter interface against a local simulator. `forge.propdesk.adapters.declared`
holds Rithmic, Tradovate and ProjectX: each carries the provider's real
interface declaration and the outstanding work — engineering and external —
that a live connector would need, and each refuses every command with the
reason. Nothing here imports a broker SDK, and nothing here reaches a network.
"""

from forge.propdesk.adapters.declared import (
    PROJECTX_WORK,
    RITHMIC_WORK,
    TRADOVATE_WORK,
    DeclaredAdapter,
    RequiredWork,
    projectx_adapter,
    rithmic_adapter,
    tradovate_adapter,
)
from forge.propdesk.adapters.simulated import SimulatedAccount, SimulatedAdapter

__all__ = [
    "PROJECTX_WORK",
    "RITHMIC_WORK",
    "TRADOVATE_WORK",
    "DeclaredAdapter",
    "RequiredWork",
    "SimulatedAccount",
    "SimulatedAdapter",
    "projectx_adapter",
    "rithmic_adapter",
    "tradovate_adapter",
]
