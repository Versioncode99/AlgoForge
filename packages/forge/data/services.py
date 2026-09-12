"""Whether a source answered, how fast, and when it last worked.

`forge.data.health` measures a *dataset on disk* -- rows, gaps, OHLC
consistency -- and answers that question very well. Nothing measured whether a
*service* answered. An operator could see that the NQ archive had a four-hour
hole in 2019 and could not find out that Databento had been refusing since
lunchtime.

The shape is OpenTerminal's `providers/registry.ts`, which wraps every provider
call and accumulates ok/failed counts, latency and the last error. Two changes
make it honest for a system that produces evidence:

**A service nobody has called is `NOT_OBSERVED`, never healthy.** Zero failures
out of zero attempts is not a clean bill of health, and a green tick next to a
provider that has never been tried is the same class of error as a dossier
reporting zeroes for a strategy that was never validated.

**Counters are observations since this process started**, and say so. They are
not uptime, they do not survive a restart, and a surface that presented them as
uptime would be inventing history.

The fallback chain is `withFallback` with the silence removed. OpenTerminal's
returns the first success and the caller never learns which link answered; for
a terminal where everything is indicative that is coherent, and here it is the
thing §11 forbids -- switching from high-quality data to low-quality data while
pretending nothing changed. `chain` reports which link served and at what tier,
and a descent that crosses a tier boundary comes back marked `DEGRADED` so the
consumers that declared the higher tier are blocked rather than warned.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from forge.data.freshness import RANK, Freshness, Served, Tier, unavailable

#: How many consecutive failures before a service that has worked is called
#: FAILING rather than DEGRADED. One failure is a bad minute; three in a row is
#: a source that is down.
FAILING_AFTER = 3


class ServiceState(StrEnum):
    """What is known about a service, including that nothing is."""

    #: Never called in this process. The default, and not a kind of healthy.
    NOT_OBSERVED = "NOT_OBSERVED"
    #: Answering.
    HEALTHY = "HEALTHY"
    #: Answering, with recent failures.
    DEGRADED = "DEGRADED"
    #: Consecutive failures. Treat it as down.
    FAILING = "FAILING"
    #: Cannot be called at all -- no credential, no configuration. Distinct
    #: from FAILING: nothing is wrong with the service, something is missing
    #: here, and the remedy is different.
    UNCONFIGURED = "UNCONFIGURED"


class ServiceHealth:
    """One service's record. Mutable, guarded, and only ever appended to."""

    __slots__ = (
        "avg_latency_ms",
        "consecutive_failures",
        "failed",
        "last_error",
        "last_latency_ms",
        "last_success",
        "name",
        "ok",
        "tier",
        "unconfigured_reason",
    )

    def __init__(self, name: str, tier: Tier = Tier.INDICATIVE) -> None:
        self.name = name
        self.tier = tier
        self.ok = 0
        self.failed = 0
        self.consecutive_failures = 0
        self.last_latency_ms: float | None = None
        self.avg_latency_ms: float | None = None
        self.last_error = ""
        self.last_success: datetime | None = None
        self.unconfigured_reason = ""

    @property
    def state(self) -> ServiceState:
        if self.unconfigured_reason:
            return ServiceState.UNCONFIGURED
        if not self.ok and not self.failed:
            return ServiceState.NOT_OBSERVED
        if self.consecutive_failures >= FAILING_AFTER:
            return ServiceState.FAILING
        if self.failed:
            return ServiceState.DEGRADED
        return ServiceState.HEALTHY

    def explain(self) -> dict[str, str]:
        """WHAT / WHY / IMPACT / REMEDY, which is what a health panel owes a reader."""
        state = self.state
        if state is ServiceState.UNCONFIGURED:
            return {
                "what": f"{self.name} is not configured.",
                "why": self.unconfigured_reason,
                "impact": "Nothing from this source is available, and nothing stands in for it.",
                "remedy": "Supply what it needs, or use a source that is configured.",
            }
        if state is ServiceState.NOT_OBSERVED:
            return {
                "what": f"{self.name} has not been called since this process started.",
                "why": "No request has needed it yet.",
                "impact": "Its availability is unknown. It is not known to be working.",
                "remedy": "Nothing to do. It will report a state the first time it is used.",
            }
        if state is ServiceState.FAILING:
            return {
                "what": f"{self.name} is failing.",
                "why": self.last_error or f"{self.consecutive_failures} consecutive failures.",
                "impact": (
                    f"Anything that needs {self.tier} data from it is blocked. "
                    "Cached values may still be shown, marked stale."
                ),
                "remedy": "Check credentials and connectivity, then retry.",
            }
        if state is ServiceState.DEGRADED:
            return {
                "what": f"{self.name} is answering with failures.",
                "why": self.last_error or f"{self.failed} failed call(s).",
                "impact": "Requests may be slower or fall back to another source.",
                "remedy": "Watch it. If the failures become consecutive it will be marked failing.",
            }
        return {
            "what": f"{self.name} is answering.",
            "why": f"{self.ok} successful call(s) since this process started.",
            "impact": "None.",
            "remedy": "",
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tier": str(self.tier),
            "state": str(self.state),
            "ok": self.ok,
            "failed": self.failed,
            "consecutive_failures": self.consecutive_failures,
            "last_latency_ms": self.last_latency_ms,
            "avg_latency_ms": self.avg_latency_ms,
            "last_error": self.last_error,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            # Stated on every row, because counters that look like uptime and
            # are not are worse than no counters.
            "observed": "since this process started",
            "explain": self.explain(),
        }


class ServiceRegistry:
    """Every source this process can call, and what happened when it did."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._services: dict[str, ServiceHealth] = {}

    def declare(self, name: str, tier: Tier, *, unconfigured: str = "") -> ServiceHealth:
        """Register a service so it appears before anything calls it.

        Declaring matters: a health panel that only lists services somebody
        happened to use cannot show that the one you are waiting on has never
        been tried.
        """
        with self._lock:
            service = self._services.get(name)
            if service is None:
                service = ServiceHealth(name, tier)
                self._services[name] = service
            service.tier = tier
            service.unconfigured_reason = unconfigured
            return service

    def get(self, name: str) -> ServiceHealth | None:
        with self._lock:
            return self._services.get(name)

    def record_success(self, name: str, latency_ms: float) -> None:
        with self._lock:
            service = self._services.setdefault(name, ServiceHealth(name))
            service.ok += 1
            service.consecutive_failures = 0
            service.last_latency_ms = latency_ms
            service.avg_latency_ms = (
                latency_ms
                if service.avg_latency_ms is None
                else service.avg_latency_ms * 0.8 + latency_ms * 0.2
            )
            service.last_success = datetime.now(UTC)

    def record_failure(self, name: str, error: str) -> None:
        with self._lock:
            service = self._services.setdefault(name, ServiceHealth(name))
            service.failed += 1
            service.consecutive_failures += 1
            # Truncated, and never formatted into anything that goes to a model
            # or an audit row: a provider's error can carry a signed URL.
            service.last_error = error[:200]

    def tracked[T](self, name: str, call: Callable[[], T]) -> T:
        """Run `call`, recording what happened. Re-raises on failure."""
        started = time.monotonic()
        try:
            result = call()
        except Exception as exc:
            self.record_failure(name, f"{type(exc).__name__}: {exc}")
            raise
        self.record_success(name, (time.monotonic() - started) * 1000.0)
        return result

    def snapshot(self) -> list[dict[str, Any]]:
        """Every declared service, worst first, so a panel reads top-down."""
        order = {
            ServiceState.FAILING: 0,
            ServiceState.UNCONFIGURED: 1,
            ServiceState.DEGRADED: 2,
            ServiceState.NOT_OBSERVED: 3,
            ServiceState.HEALTHY: 4,
        }
        with self._lock:
            rows = [service.as_dict() for service in self._services.values()]
        return sorted(rows, key=lambda row: (order[ServiceState(row["state"])], row["name"]))


def chain[T](
    attempts: Sequence[tuple[str, Tier, Callable[[], T]]],
    *,
    required: Tier = Tier.INDICATIVE,
    registry: ServiceRegistry | None = None,
) -> Served[T]:
    """Try each source in order and report which one answered.

    The order is the order given: deterministic, so the same outage produces
    the same descent twice, and observable, so an operator can see it happened.

    A link that answers below `required` is marked `DEGRADED` rather than
    `FRESH`. It still carries its value -- an informational surface may render
    it with the marker -- and `Served.admissible` refuses it for anything that
    declared the higher tier. That is the whole of "never silently switch from
    high-quality data to low-quality data while pretending nothing changed".
    """
    problems: list[str] = []
    for name, tier, call in attempts:
        try:
            value = registry.tracked(name, call) if registry else call()
        except Exception as exc:
            problems.append(f"{name}: {type(exc).__name__}: {exc}"[:200])
            continue
        degraded = RANK[tier] < RANK[required]
        return Served(
            value,
            provider=name,
            tier=tier,
            freshness=Freshness.DEGRADED if degraded else Freshness.FRESH,
            reason=(
                f"{name} serves {tier}, below the {required} that was asked for"
                if degraded
                else ("; ".join(problems) if problems else "")
            ),
        )
    return unavailable(
        attempts[0][0] if attempts else "",
        "; ".join(problems) if problems else "no sources were configured",
    )


#: Capabilities this build genuinely does not have, stated by name.
#:
#: Reported rather than omitted, because an absent row reads as "fine". A
#: reader looking for news and finding no news section concludes the feed is
#: healthy and quiet; a reader finding a row that says there is no feed
#: concludes correctly. This is the same rule the evidence dossier follows when
#: it reports `available: false` with a reason instead of a section of zeroes.
ABSENT_CAPABILITIES: tuple[dict[str, str], ...] = (
    {
        "capability": "Headline news",
        "state": str(ServiceState.UNCONFIGURED),
        "what": "There is no headline news feed in this build.",
        "why": (
            "No licensed source is configured. The module named `news` models "
            "scheduled economic events, not headlines."
        ),
        "impact": (
            "No panel shows headlines, and no research agent can cite one. "
            "Nothing is fabricated to fill the gap."
        ),
        "remedy": "Configure a licensed news provider. None ships with AlgoForge.",
    },
    {
        "capability": "Live quotes and depth",
        "state": str(ServiceState.UNCONFIGURED),
        "what": "There is no live quote stream and no venue connection.",
        "why": "AlgoForge is paper-only; no live-order path exists anywhere in it.",
        "impact": (
            "Depth and order-entry panels draw nothing rather than something that "
            "would look connected. Backtests are unaffected -- they run on archives."
        ),
        "remedy": "Not available in this build.",
    },
    {
        "capability": "Execution-grade data",
        "state": str(ServiceState.UNCONFIGURED),
        "what": "No source here is execution-grade.",
        "why": (
            "Execution grade means licensed, timestamped and revision-documented. "
            "The configured sources are research-grade at best."
        ),
        "impact": (
            "Anything that declares EXECUTION_GRADE is blocked rather than served "
            "a lower tier."
        ),
        "remedy": "Not available in this build.",
    },
)
