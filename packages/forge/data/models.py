from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from forge.contracts.hashing import content_hash, stable_id
from forge.contracts.models import FrozenModel


class ProviderDescriptor(FrozenModel):
    provider_id: str
    authority: Literal["TRUTH", "CONTEXT", "FIXTURE"]
    capabilities: tuple[str, ...]
    requires_credentials: bool


class Bar(FrozenModel):
    symbol: str
    event_time: datetime
    knowledge_time: datetime
    ingestion_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = Field(ge=0)
    bar_type: Literal["TIME", "VOLUME", "RENKO"] = "TIME"
    source: str


class DataQualityReceipt(FrozenModel):
    accepted: bool
    gate: Literal["G0"] = "G0"
    findings: tuple[str, ...]
    row_count: int
    content_hash: str | None


class DataManifest(FrozenModel):
    manifest_id: str
    provider: ProviderDescriptor
    symbol: str
    row_count: int
    first_event_time: datetime
    last_event_time: datetime
    content_hash: str
    labels: tuple[str, ...]

    @classmethod
    def from_bars(
        cls, provider: ProviderDescriptor, bars: list[Bar], labels: tuple[str, ...]
    ) -> DataManifest:
        payload = [bar.model_dump(mode="json") for bar in bars]
        digest = content_hash(payload)
        return cls(
            manifest_id=stable_id("data", {"provider": provider.provider_id, "hash": digest}),
            provider=provider,
            symbol=bars[0].symbol,
            row_count=len(bars),
            first_event_time=bars[0].event_time,
            last_event_time=bars[-1].event_time,
            content_hash=digest,
            labels=labels,
        )
