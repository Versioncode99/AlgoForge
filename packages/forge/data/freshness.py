"""How old a value is, where it came from, and whether it may be evidence.

OpenTerminal's cache has the right mechanism and the wrong return type::

    } catch (err) {
      const stale = staleGet<T>(key);
      if (stale !== undefined) return stale;
      throw err;
    }

On provider failure the last-known value comes back and the caller cannot tell.
`cached<T>()` returns `T` whether the data is one second old or one week old.
For a quote panel that is correct -- a slightly old price beats a blank panel.
For a backtest it is a fabricated result.

So the mechanism is kept and the silence is removed: a value arrives in a
`Served` envelope carrying the provider that answered, the tier it answers at,
how fresh it is, and when it was retrieved. Making staleness part of the type is
what turns "stale data cannot silently masquerade as fresh" from a convention
into something the type checker and the tests can both check.

**Tiers are not a "close enough" ladder.** Crossing down a tier *blocks* the
consumers that declared the higher one; it does not warn them. A backtest that
declared EXECUTION_GRADE and is handed INDICATIVE does not run with a footnote.
This is the same rule the judge already applies to missing evidence, where
absence is INCONCLUSIVE rather than a pass.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any


class Tier(StrEnum):
    """What a source is good enough for. Ordered by `RANK`.

    The vocabulary is deliberately about *use*, not about price or vendor. A
    free public endpoint and a paid one can both be INDICATIVE, and calling
    either "institutional" because an invoice exists is the mistake this enum
    is here to make unavailable.
    """

    #: Licensed, timestamped, revision-documented. Fit to size an order from.
    EXECUTION_GRADE = "EXECUTION_GRADE"
    #: Reproducible and provenanced, but not a venue feed. Fit for a backtest.
    RESEARCH_GRADE = "RESEARCH_GRADE"
    #: Public or undocumented endpoints. Fit to look at, not to conclude from.
    INDICATIVE = "INDICATIVE"
    #: Generated. Fit only for testing the machinery, never for a finding.
    SYNTHETIC = "SYNTHETIC"
    #: Nothing answered.
    UNAVAILABLE = "UNAVAILABLE"


#: Higher is better. `UNAVAILABLE` is 0 rather than absent so a comparison
#: against it is arithmetic rather than a special case somebody forgets.
RANK: dict[Tier, int] = {
    Tier.UNAVAILABLE: 0,
    Tier.SYNTHETIC: 1,
    Tier.INDICATIVE: 2,
    Tier.RESEARCH_GRADE: 3,
    Tier.EXECUTION_GRADE: 4,
}


class Freshness(StrEnum):
    """How old the value is relative to what the caller asked for."""

    #: Inside its time-to-live. What it says it is.
    FRESH = "FRESH"
    #: Past its TTL, and a refresh is under way. Still the last good value.
    REFRESHING = "REFRESHING"
    #: Past its TTL and the refresh failed. A real value, from before.
    STALE = "STALE"
    #: Served, but by a lower tier than was asked for.
    DEGRADED = "DEGRADED"
    #: Nothing to serve. There is no value in the envelope.
    UNAVAILABLE = "UNAVAILABLE"


#: The states a research consumer may never silently accept. Kept as data so
#: the rule can be asserted in a test rather than remembered by a reader.
NOT_EVIDENCE = frozenset({Freshness.STALE, Freshness.DEGRADED, Freshness.UNAVAILABLE})


class Served[T]:
    """A value, and everything needed to decide whether to believe it.

    Deliberately not a `FrozenModel`: it wraps an arbitrary payload including
    data frames, and pydantic would try to validate one.
    """

    __slots__ = ("as_of", "freshness", "provider", "reason", "retrieved_at", "tier", "value")

    def __init__(
        self,
        value: T | None,
        *,
        provider: str,
        tier: Tier,
        freshness: Freshness,
        retrieved_at: datetime | None = None,
        as_of: datetime | None = None,
        reason: str = "",
    ) -> None:
        self.value = value
        self.provider = provider
        self.tier = tier
        self.freshness = freshness
        #: When this process fetched it.
        self.retrieved_at = retrieved_at or datetime.now(UTC)
        #: What instant the data itself describes, when the source says so.
        #: `None` means the source did not say, which is not the same as "now".
        self.as_of = as_of
        self.reason = reason

    @property
    def usable(self) -> bool:
        """There is a value here. Says nothing about whether it may be evidence."""
        return self.value is not None and self.freshness is not Freshness.UNAVAILABLE

    def admissible(self, required: Tier) -> tuple[bool, str]:
        """May this be evidence for a consumer that declared `required`?

        Returns the answer *and the reason*, because a caller that is refused
        has to be able to tell an operator what to do about it, and "False" on
        its own sends them to look at the wrong thing.
        """
        if self.value is None or self.freshness is Freshness.UNAVAILABLE:
            return False, (
                f"{self.provider or 'no provider'} returned nothing"
                + (f": {self.reason}" if self.reason else ".")
            )
        if RANK[self.tier] < RANK[required]:
            return False, (
                f"{self.provider} serves {self.tier}; this needs {required}. "
                "Substituting a lower tier would change what the result means, "
                "so it is blocked rather than warned about."
            )
        if self.freshness in NOT_EVIDENCE:
            return False, (
                f"the value from {self.provider} is {self.freshness}"
                + (f" ({self.reason})" if self.reason else "")
                + ". Informational surfaces may show it; a result may not rest on it."
            )
        return True, ""

    def as_dict(self) -> dict[str, Any]:
        """The envelope without the payload, for a surface or an audit row."""
        return {
            "provider": self.provider,
            "tier": str(self.tier),
            "freshness": str(self.freshness),
            "retrieved_at": self.retrieved_at.isoformat(),
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "reason": self.reason,
            "has_value": self.value is not None,
        }

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return f"Served({self.provider} {self.tier} {self.freshness})"


def unavailable(provider: str, reason: str) -> Served[Any]:
    """Nothing answered, and why. Never an empty value dressed as a result."""
    return Served(
        None,
        provider=provider,
        tier=Tier.UNAVAILABLE,
        freshness=Freshness.UNAVAILABLE,
        reason=reason,
    )


def classify(
    *,
    retrieved_at: datetime,
    ttl: timedelta,
    refreshing: bool = False,
    now: datetime | None = None,
) -> Freshness:
    """FRESH inside the TTL; REFRESHING or STALE past it.

    Past the TTL the distinction is whether anything is being done about it. A
    surface showing REFRESHING is telling the reader to wait; one showing STALE
    is telling them the source is down.
    """
    moment = now or datetime.now(UTC)
    if moment - retrieved_at <= ttl:
        return Freshness.FRESH
    return Freshness.REFRESHING if refreshing else Freshness.STALE


#: What each tier means, in the words a surface should use. Kept beside the
#: enum so a panel cannot invent its own gloss for a term the engine enforces.
TIER_MEANING: dict[Tier, str] = {
    Tier.EXECUTION_GRADE: (
        "Licensed, timestamped and revision-documented. Fit to size an order from."
    ),
    Tier.RESEARCH_GRADE: (
        "Reproducible and provenanced, but not a venue feed. Fit for a backtest."
    ),
    Tier.INDICATIVE: (
        "Public or undocumented endpoints. Fit to look at, not to conclude from."
    ),
    Tier.SYNTHETIC: (
        "Generated. Fit for testing the machinery, never for a finding."
    ),
    Tier.UNAVAILABLE: "Nothing answered.",
}
