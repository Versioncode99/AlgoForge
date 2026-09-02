"""Import Databento batch downloads into the local parquet cache.

A batch download is a zip containing a compressed DBN file plus its metadata.
Unlike the streaming API these are already paid for, so importing one costs
nothing and gives the engine years of history instead of a single quarter.

The importer resolves the raw instrument ids to symbols using the symbology in
the zip's own metadata, keeps only the requested root, and writes the same
parquet layout the live providers use — so nothing downstream needs to know
whether bars arrived by download or by API.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

BATCH_MEMBER_SUFFIX = ".dbn.zst"


@dataclass(frozen=True)
class ImportResult:
    dataset_key: str
    symbol: str
    interval: str
    rows: int
    first: str
    last: str
    parquet_path: Path
    source_zip: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_key": self.dataset_key,
            "symbol": self.symbol,
            "interval": self.interval,
            "rows": self.rows,
            "first": self.first,
            "last": self.last,
            "parquet_path": str(self.parquet_path),
            "source_zip": self.source_zip,
        }


class DbnImportError(RuntimeError):
    """The archive could not be read. Never silently substituted with other data."""


def _member(archive: zipfile.ZipFile, suffix: str) -> str:
    for name in archive.namelist():
        if name.endswith(suffix):
            return name
    raise DbnImportError(f"no {suffix} member in archive")


def describe(zip_path: Path) -> dict[str, Any]:
    """Read the archive's own metadata without decoding the price data."""
    with zipfile.ZipFile(zip_path) as archive:
        try:
            raw = json.loads(archive.read(_member(archive, "metadata.json")))
        except KeyError as exc:  # pragma: no cover - malformed archive
            raise DbnImportError("metadata.json missing") from exc
    query = raw.get("query", raw)
    return {
        "dataset": query.get("dataset"),
        "schema": query.get("schema"),
        "symbols": query.get("symbols"),
        "stype_in": query.get("stype_in"),
        "start": query.get("start"),
        "end": query.get("end"),
        "limit": query.get("limit"),
    }


def is_outright(symbol: str) -> bool:
    """Reject calendar spreads and combos.

    `parent` symbology returns every listed instrument, which includes spreads
    like `GCZ1-GCG2`. Their prices are differences, not levels, so letting one
    into a continuous series would corrupt it.
    """
    return not any(mark in symbol for mark in ("-", ":", " "))


def _root_of(symbol: str) -> str:
    """`NQZ4` -> `NQ`, `GCJ5` -> `GC`, `NQ.c.0` -> `NQ`."""
    head = symbol.split(".")[0]
    while head and head[-1].isdigit():
        head = head[:-1]
    return head[:-1] if len(head) > 2 and head[-1].isalpha() else head


def _front_month(frame: pd.DataFrame) -> pd.DataFrame:
    """Collapse all listed contracts to one continuous series by volume.

    A batch download of a whole product contains every expiry trading at once.
    Taking the highest-volume contract per minute is the same volume-crossover
    rule the streaming continuous symbol applies, and it keeps the series on the
    contract that actually held the liquidity.
    """
    frame = frame.sort_values(["event_time", "volume"], ascending=[True, False])
    return frame.drop_duplicates(subset="event_time", keep="first")


def load_batch(
    zip_path: Path,
    *,
    root: str | None = None,
    progress: bool = False,
) -> pd.DataFrame:
    """Decode a Databento batch zip into the canonical bar frame."""
    try:
        import databento as db
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise DbnImportError("the databento package is required to read DBN files") from exc

    with zipfile.ZipFile(zip_path) as archive:
        member = _member(archive, BATCH_MEMBER_SUFFIX)
        if progress:
            print(f"  decoding {member} …")
        payload = archive.read(member)

    store = db.DBNStore.from_bytes(payload)
    frame = store.to_df()
    if frame.empty:
        raise DbnImportError(f"{zip_path.name} decoded to zero rows")

    frame = frame.reset_index()
    time_column = "ts_event" if "ts_event" in frame.columns else frame.columns[0]
    frame = frame.rename(columns={time_column: "event_time"})

    if "symbol" not in frame.columns:
        raise DbnImportError("decoded frame has no symbol column; cannot resolve contracts")

    before = len(frame)
    frame = frame[frame["symbol"].map(lambda s: is_outright(str(s)))]
    if progress:
        print(f"  dropped {before - len(frame):,} spread/combo rows")

    if root:
        wanted = root.upper()
        frame = frame[frame["symbol"].map(lambda s: _root_of(str(s)).upper() == wanted)]
        if frame.empty:
            raise DbnImportError(f"no rows for root '{root}' in {zip_path.name}")

    frame = frame[["event_time", "open", "high", "low", "close", "volume"]]
    frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = frame[column].astype(float)

    return _front_month(frame).reset_index(drop=True)


def import_batch(
    zip_path: Path,
    cache_root: Path,
    *,
    symbol: str,
    dataset_key: str,
    interval: str = "1m",
    root: str | None = None,
    progress: bool = False,
) -> ImportResult:
    """Decode an archive and write it into the parquet cache the app reads."""
    if not zip_path.exists():
        raise DbnImportError(f"{zip_path} does not exist")

    frame = load_batch(zip_path, root=root or symbol, progress=progress)
    folder = cache_root / "databento-batch"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{dataset_key}.parquet"
    frame.to_parquet(target, index=False)

    return ImportResult(
        dataset_key=dataset_key,
        symbol=symbol,
        interval=interval,
        rows=len(frame),
        first=str(frame["event_time"].iloc[0]),
        last=str(frame["event_time"].iloc[-1]),
        parquet_path=target,
        source_zip=zip_path.name,
    )


def cached_batch(cache_root: Path, dataset_key: str) -> Path | None:
    path = cache_root / "databento-batch" / f"{dataset_key}.parquet"
    return path if path.exists() else None
