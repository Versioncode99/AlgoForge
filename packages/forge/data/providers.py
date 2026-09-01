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
