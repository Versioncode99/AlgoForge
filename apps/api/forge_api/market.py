"""Dataset resolution for the API.

A dataset is a named window of real market data. Three sources feed it:

- **Streaming providers** (Databento, Binance) fetch a rolling window on demand.
- **Imported archives** are Databento batch downloads already paid for and
  decoded to parquet, so they cost nothing to reuse and carry years of history.
- **Synthetic** bars remain for offline work, labelled honestly, and can never
  clear the judge's data gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
from forge.data.dbn_import import cached_batch
from forge.data.live import DataRequest as LiveRequest
from forge.data.live import MarketDataCache, ProviderError, _frame_to_bars, get_provider
from forge.data.models import Bar
from forge.strategy import generate_bars

BATCH_PROVIDER = "databento-batch"


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

    @property
    def is_imported(self) -> bool:
        return self.provider == BATCH_PROVIDER


DATASETS: dict[str, Dataset] = {
    # Imported batch archives. Years of history, no per-request cost.
    "nq_1m_16y": Dataset(
        "nq_1m_16y", "NQ · 1m · 16 years", "NQ", "1m", 194, BATCH_PROVIDER, "TRUTH", "imported"
    ),
    "gc_1m_5y": Dataset(
        "gc_1m_5y", "GC gold · 1m · 5 years", "GC", "1m", 60, BATCH_PROVIDER, "TRUTH", "imported"
    ),
    # Streaming windows, charged per request.
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

DEFAULT_DATASET = "nq_1m_16y"


class MarketService:
    """Loads and caches dataset bars. Real providers fail closed; nothing is mocked."""

    def __init__(self, root: Path) -> None:
        self.cache = MarketDataCache(root / "data" / "market")
        self._memo: dict[str, list[Bar]] = {}
        # Imported archives run to millions of rows. Caching the frame and
        # building Bar objects only for the slice actually requested keeps a
        # 16-year dataset as cheap to open as a 3-month one; materialising all
        # of them costs minutes and gigabytes for no benefit.
        self._frames: dict[str, pd.DataFrame] = {}

    @staticmethod
    def window(dataset: Dataset, today: date | None = None) -> tuple[str, str]:
        end = today or datetime.now(UTC).date()
        start = end - timedelta(days=dataset.months * 30)
        return start.isoformat(), end.isoformat()

    def _frame_for(self, key: str, dataset: Dataset) -> pd.DataFrame:
        if key not in self._frames:
            path = cached_batch(self.cache.root, key)
            if path is None:
                raise ProviderError(
                    f"'{key}' has not been imported yet. Run: forge data import "
                    f"<archive.zip> --root {dataset.symbol} --dataset {key}"
                )
            self._frames[key] = pd.read_parquet(path)
        return self._frames[key]

    def load(self, key: str, limit: int | None = None) -> tuple[list[Bar], Dataset]:
        dataset = DATASETS.get(key)
        if dataset is None:
            raise ProviderError(f"unknown dataset '{key}'")

        if dataset.is_imported:
            frame = self._frame_for(key, dataset)
            # Slice before building objects, not after: the engine asks for tens
            # of thousands of bars out of a set holding millions.
            window = frame.tail(limit) if limit and limit < len(frame) else frame
            return _frame_to_bars(window, dataset.symbol, dataset.provider), dataset

        if key not in self._memo:
            if dataset.authority == "FIXTURE":
                self._memo[key] = generate_bars(count=6000)
            else:
                start, end = self.window(dataset)
                request = LiveRequest(dataset.symbol, start, end, dataset.interval)
                self._memo[key] = get_provider(dataset.symbol, self.cache).fetch(request)

        bars = self._memo[key]
        return (bars[-limit:] if limit and limit < len(bars) else bars), dataset

    def available_rows(self, key: str) -> int:
        """How many bars an imported dataset holds, without materialising them."""
        dataset = DATASETS.get(key)
        if dataset is None or not dataset.is_imported:
            return 0
        path = cached_batch(self.cache.root, key)
        if path is None:
            return 0
        if key in self._frames:
            return len(self._frames[key])
        return int(pd.read_parquet(path, columns=["event_time"]).shape[0])

    def status(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for dataset in DATASETS.values():
            cached = dataset.key in self._memo or dataset.key in self._frames
            if dataset.is_imported:
                bar_count = self.available_rows(dataset.key)
                available = bar_count > 0
            else:
                bar_count = len(self._memo.get(dataset.key, ()))
                available = True
            rows.append(
                {
                    "key": dataset.key,
                    "label": dataset.label,
                    "symbol": dataset.symbol,
                    "interval": dataset.interval,
                    "provider": dataset.provider,
                    "authority": dataset.authority,
                    "is_real": dataset.is_real,
                    "is_imported": dataset.is_imported,
                    "available": available,
                    "cost_note": dataset.cost_note,
                    "loaded": cached,
                    "bar_count": bar_count,
                }
            )
        return rows
