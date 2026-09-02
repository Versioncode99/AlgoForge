from __future__ import annotations

import importlib.util
from importlib.metadata import PackageNotFoundError, version

from forge.contracts.models import FrozenModel


class NautilusCapability(FrozenModel):
    oracle_id: str = "nautilus-trader"
    installed: bool
    version: str | None
    ready: bool
    supported_data_levels: tuple[str, ...]
    configured_data_levels: tuple[str, ...]
    role: str
    licence: str
    limitations: tuple[str, ...]


def nautilus_capability(configured_data_levels: tuple[str, ...] = ("BARS",)) -> NautilusCapability:
    installed = importlib.util.find_spec("nautilus_trader") is not None
    package_version: str | None = None
    if installed:
        try:
            package_version = version("nautilus_trader")
        except PackageNotFoundError:
            package_version = "unknown"
    return NautilusCapability(
        installed=installed,
        version=package_version,
        ready=installed and bool(configured_data_levels),
        supported_data_levels=("BARS", "TRADES", "L1", "L2", "L3"),
        configured_data_levels=configured_data_levels if installed else (),
        role="OPTIONAL_EXECUTION_SEMANTICS_ORACLE",
        licence="LGPL-3.0",
        limitations=(
            "Not a judge and cannot promote a strategy.",
            "Bars cannot establish spread, intrabar order, depth, latency or queue position.",
            "L2/L3 comparisons require matching historical order-book data.",
            "NinjaTrader Strategy Analyzer calibration remains a separate real-platform gate.",
        ),
    )
