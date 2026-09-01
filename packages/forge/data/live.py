"""Real market data providers.

Two live sources, both returning the same `Bar` contract the backtester already
consumes:

- **Databento** (GLBX.MDP3) for CME futures. Paid, but cheap: roughly $0.025 per
  week of MNQ 1-minute bars. Requires DATABENTO_API_KEY.
- **Binance public** for crypto spot and perpetuals. Free, no credential.

Both cache to parquet on disk and are keyed by the exact request, so a repeated
backtest costs nothing and reproduces byte-identically.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from forge.data.models import Bar

BINANCE_SPOT = "https://api.binance.com/api/v3/klines"
BINANCE_FUTURES = "https://fapi.binance.com/fapi/v1/klines"
BINANCE_MAX_ROWS = 1000

# Databento continuous-contract symbols. `.c.0` is the front month rolled on volume.
FUTURES_SYMBOLS = {
    "MNQ": "MNQ.c.0",
    "NQ": "NQ.c.0",
    "MES": "MES.c.0",
    "ES": "ES.c.0",
    "MGC": "MGC.c.0",
    "GC": "GC.c.0",
}


class ProviderError(RuntimeError):
    """A real provider failed. Never substituted with synthetic or mock data."""


@dataclass(frozen=True)
class DataRequest:
    symbol: str
    start: str  # ISO date
    end: str  # ISO date
    interval: str = "1m"

    @property
    def cache_key(self) -> str:
        safe = self.symbol.replace("/", "_").replace(".", "_")
        return f"{safe}__{self.interval}__{self.start}__{self.end}"


def load_keys(env_path: Path | None = None) -> None:
    """Load credentials into the process. Values are never logged or returned."""
    candidates = [
        env_path,
        Path(__file__).resolve().parents[3] / ".env",
        Path(
            r"F:/Obsidian Vaults/Keslec-Trading/08 Source Material/Trading/Tooling/scripts/keys.env"
        ),
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    key, _, value = line.partition("=")
                    value = value.strip().strip('"').strip("'")
                    if value and not os.environ.get(key.strip()):
                        os.environ[key.strip()] = value


class MarketDataCache:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, provider: str, request: DataRequest) -> Path:
        folder = self.root / provider
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{request.cache_key}.parquet"

    def read(self, provider: str, request: DataRequest) -> pd.DataFrame | None:
        path = self.path_for(provider, request)
        return pd.read_parquet(path) if path.exists() else None

    def write(self, provider: str, request: DataRequest, frame: pd.DataFrame) -> None:
        frame.to_parquet(self.path_for(provider, request), index=False)


def _frame_to_bars(frame: pd.DataFrame, symbol: str, source: str) -> list[Bar]:
    bars: list[Bar] = []
    # Column-wise extraction rather than itertuples: materially faster on the
    # ~90k-row pulls this handles, and the types survive a strict check.
    times = frame["event_time"].dt.to_pydatetime()
    opens = frame["open"].to_numpy(dtype=float)
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    closes = frame["close"].to_numpy(dtype=float)
    volumes = frame["volume"].to_numpy(dtype=float)

    for index, raw_time in enumerate(times):
        event = raw_time if raw_time.tzinfo else raw_time.replace(tzinfo=UTC)
        bars.append(
            Bar(
                symbol=symbol,
                event_time=event,
                # A bar is knowable only once it has closed.
                knowledge_time=event + timedelta(seconds=1),
                ingestion_time=event + timedelta(seconds=2),
                open=opens[index],
                high=highs[index],
                low=lows[index],
                close=closes[index],
                volume=volumes[index],
                bar_type="TIME",
                source=source,
            )
        )
    return bars


class BinancePublicProvider:
    """Free crypto klines. No credential, no cost."""

    provider_id = "binance-public"
    authority = "TRUTH"

    def __init__(self, cache: MarketDataCache, market: str = "spot") -> None:
        self.cache = cache
        self.endpoint = BINANCE_SPOT if market == "spot" else BINANCE_FUTURES

    def fetch(self, request: DataRequest) -> list[Bar]:
        cached = self.cache.read(self.provider_id, request)
        if cached is None:
            cached = self._download(request)
            self.cache.write(self.provider_id, request, cached)
        return _frame_to_bars(cached, request.symbol, self.provider_id)

    def _download(self, request: DataRequest) -> pd.DataFrame:
        start_ms = int(datetime.fromisoformat(request.start).replace(tzinfo=UTC).timestamp() * 1000)
        end_ms = int(datetime.fromisoformat(request.end).replace(tzinfo=UTC).timestamp() * 1000)
        symbol = request.symbol.split(".")[0].replace("/", "").upper()

        rows: list[list[Any]] = []
        cursor = start_ms
        while cursor < end_ms:
            query = urllib.parse.urlencode(
                {
                    "symbol": symbol,
                    "interval": request.interval,
                    "startTime": cursor,
                    "endTime": end_ms,
                    "limit": BINANCE_MAX_ROWS,
                }
            )
            try:
                with urllib.request.urlopen(f"{self.endpoint}?{query}", timeout=30) as response:
                    batch = json.load(response)
            except Exception as exc:  # fail closed; never fall back to fabricated data
                raise ProviderError(f"binance request failed: {exc}") from exc
            if not batch:
                break
            rows.extend(batch)
            cursor = int(batch[-1][0]) + 1
            if len(batch) < BINANCE_MAX_ROWS:
                break

        if not rows:
            raise ProviderError(
                f"binance returned no bars for {symbol} {request.start}..{request.end}"
            )

        frame = pd.DataFrame(rows).iloc[:, :6]
        frame.columns = ["event_time", "open", "high", "low", "close", "volume"]
        frame["event_time"] = pd.to_datetime(frame["event_time"], unit="ms", utc=True)
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = frame[column].astype(float)
        return frame.drop_duplicates(subset="event_time").sort_values("event_time")


class DatabentoProvider:
    """CME futures via Databento. Paid; costs are checked before every download."""

    provider_id = "databento"
    authority = "TRUTH"
    dataset = "GLBX.MDP3"

    def __init__(self, cache: MarketDataCache, max_cost_usd: float = 2.50) -> None:
        self.cache = cache
        self.max_cost_usd = max_cost_usd

    def _client(self) -> Any:  # databento is an optional dependency; type is theirs
        load_keys()
        key = os.environ.get("DATABENTO_API_KEY")
        if not key:
            raise ProviderError("DATABENTO_API_KEY is not set")
        try:
            import databento as db
        except ImportError as exc:
            raise ProviderError("databento package is not installed") from exc
        return db.Historical(key)

    def estimate_cost(self, request: DataRequest) -> float:
        client = self._client()
        return float(
            client.metadata.get_cost(
                dataset=self.dataset,
                symbols=[FUTURES_SYMBOLS.get(request.symbol.split(".")[0], request.symbol)],
                stype_in="continuous",
                schema=f"ohlcv-{request.interval}",
                start=request.start,
                end=request.end,
            )
        )

    def fetch(self, request: DataRequest) -> list[Bar]:
        cached = self.cache.read(self.provider_id, request)
        if cached is None:
            cost = self.estimate_cost(request)
            if cost > self.max_cost_usd:
                raise ProviderError(
                    f"request would cost ${cost:.2f}, above the ${self.max_cost_usd:.2f} ceiling"
                )
            cached = self._download(request)
            self.cache.write(self.provider_id, request, cached)
        return _frame_to_bars(cached, request.symbol, self.provider_id)

    def _download(self, request: DataRequest) -> pd.DataFrame:
        client = self._client()
        root = request.symbol.split(".")[0]
        try:
            store = client.timeseries.get_range(
                dataset=self.dataset,
                symbols=[FUTURES_SYMBOLS.get(root, request.symbol)],
                stype_in="continuous",
                schema=f"ohlcv-{request.interval}",
                start=request.start,
                end=request.end,
            )
            frame = store.to_df()
        except Exception as exc:
            raise ProviderError(f"databento request failed: {exc}") from exc

        if frame.empty:
            raise ProviderError(f"databento returned no bars for {root}")

        frame = frame.reset_index().rename(columns={"ts_event": "event_time"})
        frame = frame[["event_time", "open", "high", "low", "close", "volume"]]
        frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True)
        ordered: pd.DataFrame = frame.sort_values("event_time")
        return ordered


def get_provider(symbol: str, cache: MarketDataCache) -> BinancePublicProvider | DatabentoProvider:
    """Route a symbol to the provider that owns it."""
    root = symbol.split(".")[0].upper()
    if root in FUTURES_SYMBOLS:
        return DatabentoProvider(cache)
    if "USD" in root or root.endswith("USDT") or root.endswith("PERP"):
        return BinancePublicProvider(cache)
    raise ProviderError(f"no provider owns symbol '{symbol}'")


def fetch_bars(
    symbol: str, start: str, end: str, interval: str = "1m", cache_root: Path | None = None
) -> list[Bar]:
    cache = MarketDataCache(cache_root or Path("data") / "market")
    request = DataRequest(symbol=symbol, start=start, end=end, interval=interval)
    bars: list[Bar] = get_provider(symbol, cache).fetch(request)
    return bars
