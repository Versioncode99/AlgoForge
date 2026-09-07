from __future__ import annotations

import importlib.util
from importlib.metadata import PackageNotFoundError, version

from forge.contracts.models import FrozenModel


class NautilusCapability(FrozenModel):
    """What is installed, not what has been checked.

    A capability probe: it reports whether ``nautilus_trader`` is importable and
    what data levels it would support. It runs no comparison and produces no
    finding, so it can neither confirm nor contradict a verdict.
    """

    capability_id: str = "nautilus-trader"
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
        # This reports whether a package is installed. It evaluates nothing,
        # and the previous name ("oracle") invited the reading that something
        # independently checks execution realism. Nothing does.
        role="INSTALLED_PACKAGE_PROBE",
        licence="LGPL-3.0",
        limitations=(
            "Not a judge and cannot promote a strategy.",
            "Bars cannot establish spread, intrabar order, depth, latency or queue position.",
            "L2/L3 comparisons require matching historical order-book data.",
            "NinjaTrader Strategy Analyzer calibration remains a separate real-platform gate.",
        ),
    )
