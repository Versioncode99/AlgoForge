"""Dataset resolution for the API.

A dataset is a named, cached window of real market data. Synthetic bars remain
available for offline work, but they are labelled honestly and can never clear
the judge's data gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from forge.data.live import DataRequest as LiveRequest
from forge.data.live import MarketDataCache, ProviderError, get_provider
from forge.data.models import Bar
from forge.strategy import generate_bars


@dataclass(frozen=True)
class Dataset:
    key: str
    label: str
    symbol: str
    interval: str
    months: int
    provider: str
    authority: str  # TRUTH | FIXTURE
    cost_note: str

    @property
    def is_real(self) -> bool:
        return self.authority == "TRUTH"


DATASETS: dict[str, Dataset] = {
    "mnq_1m_3mo": Dataset(
        "mnq_1m_3mo", "MNQ · 1m · 3 months", "MNQ", "1m", 3, "databento", "TRUTH", "~$0.33"
    ),
    "mnq_1m_12mo": Dataset(
        "mnq_1m_12mo", "MNQ · 1m · 12 months", "MNQ", "1m", 12, "databento", "TRUTH", "~$1.31"
    ),
    "mes_1m_3mo": Dataset(
        "mes_1m_3mo", "MES · 1m · 3 months", "MES", "1m", 3, "databento", "TRUTH", "~$0.33"
    ),
    "btc_5m_6mo": Dataset(
        "btc_5m_6mo",
        "BTCUSDT · 5m · 6 months",
        "BTCUSDT",
        "5m",
        6,
        "binance-public",
        "TRUTH",
        "free",
    ),
    "eth_5m_6mo": Dataset(
        "eth_5m_6mo",
        "ETHUSDT · 5m · 6 months",
        "ETHUSDT",
        "5m",
        6,
        "binance-public",
        "TRUTH",
        "free",
    ),
    "synthetic": Dataset(
        "synthetic",
        "Synthetic · seeded, edge-free",
        "MNQ.SYNTH",
        "1m",
        0,
        "generator",
        "FIXTURE",
        "free",
    ),
}

DEFAULT_DATASET = "mnq_1m_3mo"


class MarketService:
    """Loads and caches dataset bars. Real providers fail closed; nothing is mocked."""

    def __init__(self, root: Path) -> None:
        self.cache = MarketDataCache(root / "data" / "market")
        self._memo: dict[str, list[Bar]] = {}

    @staticmethod
    def window(dataset: Dataset, today: date | None = None) -> tuple[str, str]:
        end = today or datetime.now(UTC).date()
        start = end - timedelta(days=dataset.months * 30)
        return start.isoformat(), end.isoformat()

    def load(self, key: str, limit: int | None = None) -> tuple[list[Bar], Dataset]:
        dataset = DATASETS.get(key)
        if dataset is None:
            raise ProviderError(f"unknown dataset '{key}'")

        if key not in self._memo:
            if dataset.authority == "FIXTURE":
                self._memo[key] = generate_bars(count=6000)
            else:
                start, end = self.window(dataset)
                request = LiveRequest(dataset.symbol, start, end, dataset.interval)
                self._memo[key] = get_provider(dataset.symbol, self.cache).fetch(request)

        bars = self._memo[key]
        return (bars[-limit:] if limit and limit < len(bars) else bars), dataset

    def status(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for dataset in DATASETS.values():
            cached = dataset.key in self._memo
            bar_count = len(self._memo[dataset.key]) if cached else 0
            rows.append(
                {
                    "key": dataset.key,
                    "label": dataset.label,
                    "symbol": dataset.symbol,
                    "interval": dataset.interval,
                    "provider": dataset.provider,
                    "authority": dataset.authority,
                    "is_real": dataset.is_real,
                    "cost_note": dataset.cost_note,
                    "loaded": cached,
                    "bar_count": bar_count,
                }
            )
        return rows
