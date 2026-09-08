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
from typing import TYPE_CHECKING, Any

from forge.data.dbn_import import cached_batch
from forge.data.live import DataRequest as LiveRequest
from forge.data.live import MarketDataCache, ProviderError, _frame_to_bars, get_provider
from forge.data.models import Bar
from forge.data.timeframes import (
    TIMEFRAMES,
    aggregate,
    cached_timeframe,
    timeframe,
    write_derived,
)
from forge.strategy import generate_bars

if TYPE_CHECKING:  # pragma: no cover - types only
    import pandas as pd

BATCH_PROVIDER = "databento-batch"

_pandas_cache: Any = None


def _pd() -> Any:
    """``pandas``, imported the first time a dataset is actually read.

    Resolving *which* datasets exist is directory work; only loading their bars
    needs a DataFrame. Importing pandas at module scope put 0.42 seconds on
    every launch, including launches that never open a dataset.
    """
    global _pandas_cache
    if _pandas_cache is None:
        import pandas

        _pandas_cache = pandas
    return _pandas_cache


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
    "es_1m_10y": Dataset(
        "es_1m_10y", "ES · 1m · 10 years", "ES", "1m", 128, BATCH_PROVIDER, "TRUTH", "imported"
    ),
    # The micros, over their whole life. They began trading in May 2019, so
    # there is no more history to have — the span is the contract's, not a
    # window someone chose.
    "mnq_1m_7y": Dataset(
        "mnq_1m_7y", "MNQ · 1m · full history", "MNQ", "1m", 88, BATCH_PROVIDER, "TRUTH", "imported"
    ),
    "mes_1m_7y": Dataset(
        "mes_1m_7y", "MES · 1m · full history", "MES", "1m", 88, BATCH_PROVIDER, "TRUTH", "imported"
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

# How much history a run covers, offered as a choice rather than a bar count.
# Nobody thinks in bars; they think "the last three years".
RANGE_YEARS: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 16.0)

RANGE_LABELS: dict[float, str] = {
    0.25: "3 months",
    0.5: "6 months",
    1.0: "1 year",
    2.0: "2 years",
    3.0: "3 years",
    5.0: "5 years",
    10.0: "10 years",
    16.0: "Max",
}


def range_label(years: float) -> str:
    return RANGE_LABELS.get(years, f"{years:g} years")


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
        # Row counts read from parquet footers. Separate from `_frames` because
        # the count is wanted far more often than the bars, and is thousands of
        # times cheaper to get.
        self._row_counts: dict[str, int] = {}
        # Derived aggregates, keyed by dataset@timeframe. Small next to the
        # archives: a daily series over sixteen years is about four thousand
        # rows, and the whole point is not to touch the archive again.
        self._aggregates: dict[str, pd.DataFrame] = {}

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
            self._frames[key] = _pd().read_parquet(path)
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


    # ── charting ─────────────────────────────────────────────────────────────
    def chart_bars(
        self,
        key: str,
        timeframe_key: str = "1m",
        limit: int = 1500,
        before: str | None = None,
    ) -> dict[str, Any]:
        """OHLCV for a chart, newest `limit` bars at `timeframe_key`.

        Reads a derived aggregate rather than the archive, and builds that
        aggregate the first time it is asked for. A daily chart of sixteen years
        of NQ is 4.7 million rows reduced to about four thousand: doing that per
        request would be the same mistake as parsing every artifact to render a
        table.

        `before` pages backwards for pan-left, in the only direction a chart
        actually needs: give it the timestamp of the oldest bar on screen and it
        returns the `limit` bars before it.
        """
        dataset = DATASETS.get(key)
        if dataset is None:
            raise ProviderError(f"unknown dataset '{key}'")
        spec = timeframe(timeframe_key)
        frame = self._timeframe_frame(key, dataset, spec.key)

        total = len(frame)
        if before:
            cutoff = _pd().Timestamp(before)
            if cutoff.tzinfo is None:
                cutoff = cutoff.tz_localize("UTC")
            frame = frame[frame["event_time"] < cutoff]
        window = frame.tail(max(1, min(int(limit), 20_000)))

        return {
            "dataset": key,
            "symbol": dataset.symbol,
            "timeframe": spec.key,
            "timeframe_label": spec.label,
            # Stated rather than implied: a daily bar's boundary is a
            # convention, and a chart that does not say which one it used is
            # showing numbers nobody can check.
            "convention": spec.note,
            "authority": dataset.authority,
            "is_real": dataset.is_real,
            "bar_count": len(window),
            "total_bars": total,
            # True when there is more history to the left, so the chart knows
            # whether panning further can load anything.
            "has_more": bool(len(frame) > len(window)),
            "bars": [
                {
                    "time": stamp.isoformat(),
                    "open": float(o),
                    "high": float(h),
                    "low": float(low),
                    "close": float(c),
                    "volume": float(v),
                }
                for stamp, o, h, low, c, v in zip(
                    window["event_time"],
                    window["open"],
                    window["high"],
                    window["low"],
                    window["close"],
                    window["volume"],
                    strict=True,
                )
            ],
        }

    def _timeframe_frame(self, key: str, dataset: Dataset, timeframe_key: str) -> pd.DataFrame:
        """The aggregate for one dataset and timeframe, built once and kept."""
        memo_key = f"{key}@{timeframe_key}"
        if memo_key in self._aggregates:
            return self._aggregates[memo_key]

        if not dataset.is_imported:
            # Streaming and synthetic datasets are small enough to aggregate in
            # memory; there is no archive to derive a cache from.
            bars, _ = self.load(key)
            source = _pd().DataFrame(
                [
                    {
                        "event_time": bar.event_time,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                    }
                    for bar in bars
                ]
            )
            frame = aggregate(source, timeframe_key) if not source.empty else source
            self._aggregates[memo_key] = frame
            return frame

        cached = cached_timeframe(self.cache.root, key, timeframe_key)
        if cached is None:
            source = self._frame_for(key, dataset)
            frame = aggregate(source, timeframe_key)
            cached = write_derived(self.cache.root, key, timeframe_key, frame)
        else:
            frame = _pd().read_parquet(cached)
            frame["event_time"] = _pd().to_datetime(frame["event_time"], utc=True)

        self._aggregates[memo_key] = frame
        return frame

    def timeframe_catalogue(self) -> list[dict[str, Any]]:
        return [
            {"key": tf.key, "label": tf.label, "note": tf.note}
            for tf in TIMEFRAMES.values()
        ]

    def available_rows(self, key: str) -> int:
        """How many bars an imported dataset holds, without materialising them.

        From the parquet footer, which already records the row count, rather
        than by reading a column. Reading `event_time` out of five archives to
        count them meant decoding 15.3 million timestamps on every
        `GET /datasets` -- measured at 3.0 seconds, on a request that renders a
        dropdown. The footer answers the same question in constant time.

        Cached because these archives are immutable: an imported dataset is
        bought, written once, and never appended to.
        """
        dataset = DATASETS.get(key)
        if dataset is None or not dataset.is_imported:
            return 0
        path = cached_batch(self.cache.root, key)
        if path is None:
            return 0
        if key in self._frames:
            return len(self._frames[key])
        if key not in self._row_counts:
            import pyarrow.parquet as pq

            self._row_counts[key] = int(pq.ParquetFile(path).metadata.num_rows)
        return self._row_counts[key]

    def bars_per_year(self, key: str) -> float:
        """Bars per calendar year, measured from the archive rather than assumed.

        A session-hour assumption would be wrong for every dataset in a
        different way — holidays, half-days, the 23-hour futures session, the
        24/7 crypto one. Dividing what is actually there by the span it covers
        is both simpler and correct.
        """
        dataset = DATASETS.get(key)
        if dataset is None or dataset.months <= 0:
            return 0.0
        rows = self.available_rows(key)
        if rows <= 0:
            return 0.0
        return rows / (dataset.months / 12.0)

    def resolve_bars(self, key: str, years: float | None, bar_count: int | None) -> int:
        """Turn a requested range into a bar count for this dataset.

        `bar_count` wins when both are given, so a caller that knows exactly
        what it wants is never second-guessed.
        """
        if bar_count is not None:
            return bar_count
        if years is None:
            return 0
        density = self.bars_per_year(key)
        if density <= 0:
            # Streaming and synthetic sets have no measurable archive; fall back
            # to the session-hours estimate rather than refusing outright.
            dataset = DATASETS.get(key)
            interval = dataset.interval if dataset else "1m"
            per_day = {"1m": 1380, "5m": 276, "1h": 23, "1d": 1}.get(interval, 1380)
            density = per_day * 252
        return round(density * years)

    def ranges_for(self, key: str) -> list[dict[str, object]]:
        """The ranges offered for a dataset, each with the bars it resolves to.

        Ranges longer than the archive are still listed but marked unavailable,
        so the interface can show why 10 years is greyed out on a 5-year set
        instead of silently omitting it.
        """
        dataset = DATASETS.get(key)
        if dataset is None:
            return []
        rows = self.available_rows(key) if dataset.is_imported else 0
        span_years = dataset.months / 12.0 if dataset.months else 0.0
        options: list[dict[str, object]] = []
        for years in RANGE_YEARS:
            bars = self.resolve_bars(key, years, None)
            if rows > 0:
                bars = min(bars, rows)
            fits = span_years <= 0 or years <= span_years + 0.01
            options.append(
                {
                    "years": years,
                    "label": range_label(years),
                    "bars": bars,
                    "available": fits,
                }
            )
        return options

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
                    "span_years": round(dataset.months / 12.0, 2) if dataset.months else 0.0,
                    "bars_per_year": round(self.bars_per_year(dataset.key), 1),
                    "ranges": self.ranges_for(dataset.key),
                }
            )
        return rows
