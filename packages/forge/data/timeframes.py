"""Aggregating one-minute archives into the timeframes a chart asks for.

The archives are all one-minute bars, because that is the finest resolution
bought and every coarser bar can be built from it. Building them on every
request cannot work: a daily chart of sixteen years of NQ is four and a half
million rows aggregated to answer a question whose result is four thousand
numbers. So each (dataset, timeframe) is computed once and written to a derived
cache.

**The derived cache is disposable.** Deleting it costs the seconds it takes to
rebuild and nothing else; the archives it is built from are the evidence, and
they are never written here. That is the distinction the storage addendum draws
between data and cache, and it is the reason this lives under `derived/` rather
than beside the archives.

**On daily bars.** A futures session is not a calendar day. CME index futures
open at 18:00 New York and run to 17:00 the next day, so a UTC-midnight bucket
splits one session across two bars and puts the open in the middle of the
previous one. Bucketing on the exchange's own trading date is the only version
of "daily" a trader would recognise, so that is what this does -- and the
timeframe is labelled with the convention it used, because a daily bar whose
boundary is unstated is a number nobody can check.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:  # pragma: no cover - types only
    import pandas as pd

_pandas_cache: Any = None


def _pd() -> Any:
    """`pandas`, imported on first aggregation rather than at module import.

    The startup budget in `tests/test_import_cost.py` exists because pandas and
    scipy cost half a second each at launch. Charting is not on the startup
    path and must not put them back there.
    """
    global _pandas_cache
    if _pandas_cache is None:
        import pandas

        _pandas_cache = pandas
    return _pandas_cache


#: The exchange whose session defines a trading day here. CME index futures and
#: the metals both run the 18:00-17:00 New York session.
SESSION_TIMEZONE = "America/New_York"
SESSION_OPEN_HOUR = 18


@dataclass(frozen=True)
class Timeframe:
    key: str
    label: str
    #: Bucket width in minutes. `None` for the session-based daily bar, which is
    #: not a fixed number of minutes -- holidays and half-days are shorter.
    minutes: int | None
    note: str = ""

    @property
    def is_session_daily(self) -> bool:
        return self.minutes is None


TIMEFRAMES: dict[str, Timeframe] = {
    tf.key: tf
    for tf in (
        Timeframe("1m", "1 minute", 1, "the archive's own resolution; no aggregation"),
        Timeframe("5m", "5 minutes", 5),
        Timeframe("15m", "15 minutes", 15),
        Timeframe("30m", "30 minutes", 30),
        Timeframe("1h", "1 hour", 60),
        Timeframe("4h", "4 hours", 240),
        Timeframe(
            "1d",
            "1 day",
            None,
            f"exchange session, {SESSION_OPEN_HOUR}:00 {SESSION_TIMEZONE} to the next close",
        ),
    )
}


def timeframe(key: str) -> Timeframe:
    found = TIMEFRAMES.get(key)
    if found is None:
        raise KeyError(f"no timeframe '{key}'. Available: {', '.join(TIMEFRAMES)}")
    return found


def trading_dates(times: pd.Series) -> pd.Series:
    """The exchange trading date each timestamp belongs to.

    Shifting into New York and then forward by the hours between the session
    open and midnight makes the calendar date *after* the shift equal to the
    trading date: 18:00 becomes 00:00 of the day the session is named for, and
    everything through the 17:00 close lands on the same date.
    """
    # Not named `pd`: the module-level `pd` is a type-only import, and
    # shadowing it here makes every annotation below refer to a local value.
    pandas = _pd()
    local = times.dt.tz_convert(SESSION_TIMEZONE)
    shifted = (local + pandas.Timedelta(hours=24 - SESSION_OPEN_HOUR)).dt.normalize()
    return cast("pd.Series", shifted)


def aggregate(frame: pd.DataFrame, key: str) -> pd.DataFrame:
    """Resample one-minute bars into `key`.

    Empty buckets are dropped rather than forward-filled. A gap in a futures
    session is a real thing -- a holiday, a maintenance break, an outage -- and
    inventing a flat bar across one would put prices on the chart that never
    traded.
    """
    spec = timeframe(key)
    pandas = _pd()
    if frame.empty:
        return frame.copy()

    working = frame.copy()
    working["event_time"] = pandas.to_datetime(working["event_time"], utc=True)

    if spec.key == "1m":
        return working.sort_values("event_time").reset_index(drop=True)

    if spec.is_session_daily:
        buckets = trading_dates(working["event_time"])
    else:
        assert spec.minutes is not None
        buckets = working["event_time"].dt.floor(f"{spec.minutes}min")

    working["bucket"] = buckets
    grouped = working.groupby("bucket", sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    grouped = grouped.dropna(subset=["open", "high", "low", "close"])
    out = grouped.reset_index().rename(columns={"bucket": "event_time"})
    if spec.is_session_daily:
        # The bucket is a New York date; publish it as a UTC instant so every
        # timestamp leaving this module means the same kind of thing.
        out["event_time"] = out["event_time"].dt.tz_convert("UTC")
    return out


def derived_path(cache_root: Path, dataset_key: str, timeframe_key: str) -> Path:
    return cache_root / "derived" / f"{dataset_key}_{timeframe_key}.parquet"


def cached_timeframe(cache_root: Path, dataset_key: str, timeframe_key: str) -> Path | None:
    path = derived_path(cache_root, dataset_key, timeframe_key)
    return path if path.exists() else None


def write_derived(
    cache_root: Path, dataset_key: str, timeframe_key: str, frame: pd.DataFrame
) -> Path:
    """Persist an aggregate, atomically.

    Via a temporary file because a half-written parquet in the cache is worse
    than none: the next reader finds a file, trusts it, and charts whatever
    happened to be flushed.
    """
    path = derived_path(cache_root, dataset_key, timeframe_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".parquet.tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)
    return path
