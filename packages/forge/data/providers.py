from __future__ import annotations

from typing import Protocol

from forge.data.models import Bar, ProviderDescriptor


class DataProvider(Protocol):
    descriptor: ProviderDescriptor

    async def fetch(self, symbol: str) -> list[Bar]: ...


DATABENTO = ProviderDescriptor(
    provider_id="databento",
    authority="TRUTH",
    capabilities=("CME_FUTURES", "MBO", "TRADES"),
    requires_credentials=True,
)
FRED = ProviderDescriptor(
    provider_id="fred",
    authority="CONTEXT",
    capabilities=("MACRO_SERIES",),
    requires_credentials=True,
)
CRYPTO_PUBLIC = ProviderDescriptor(
    provider_id="crypto-public",
    authority="TRUTH",
    capabilities=("SPOT_KLINES", "PERP_KLINES"),
    requires_credentials=False,
)
LOCAL_FIXTURE = ProviderDescriptor(
    provider_id="local-fixture",
    authority="FIXTURE",
    capabilities=("SAMPLE_BARS",),
    requires_credentials=False,
)


#: What each source is good enough for.
#:
#: `authority` on the descriptor already separates TRUTH from FIXTURE, and this
#: extends that axis rather than introducing a second one: TRUTH splits into
#: what a backtest may rest on and what may only be looked at. Nothing here is
#: EXECUTION_GRADE, because this build has no venue feed and no order path --
#: claiming the tier would be the "call a public endpoint institutional" error
#: that the tier vocabulary exists to make unavailable.
PROVIDER_TIERS: dict[str, str] = {
    DATABENTO.provider_id: "RESEARCH_GRADE",
    FRED.provider_id: "RESEARCH_GRADE",
    # Free, public, no SLA and no stated revision policy. Usable for a chart.
    CRYPTO_PUBLIC.provider_id: "INDICATIVE",
    LOCAL_FIXTURE.provider_id: "SYNTHETIC",
}
