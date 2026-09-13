"""The G0 data gate: is this batch of bars fit to be measured on, and what is it?

Two answers, and they cost very different amounts.

**Is it fit?** Six structural checks — finite values, the high and low
invariants, knowledge never before the event, strictly increasing event times,
no duplicate bar. These are cheap and they are computed over arrays rather than
in a loop, because a sixteen-year one-minute reservoir is five and a half
million bars and a Python loop over it is seconds of wall clock per backtest.

**What is it?** A content hash over the canonical serialisation of every bar.
This is the expensive half and it cannot be made cheaper without changing what
the hash *is*: the digest is data identity, it keys `BacktestResult.make_id`,
and it is written into split receipts and preregistrations. A faster digest
would be a different digest, and every result recorded before it would stop
comparing.

So it is computed once per distinct dataset instead. `run_backtest` validates
the bars it was handed on every single run, and a campaign runs hundreds of
backtests over one dataset — the same digest, recomputed from scratch, for an
answer that could not have changed. The cache below is keyed on a fingerprint
of the bars themselves rather than on the identity of the list object, so two
equal lists share an entry and two different ones never can.

The fingerprint covers every field that reaches the canonical form. That is the
whole safety argument: a fingerprint that skipped a field would let a batch
differing only in that field answer from another batch's receipt, which would
attach one dataset's identity to another's results.
"""

from __future__ import annotations

import hashlib
import math
import threading
from collections import OrderedDict

import numpy as np

from forge.contracts.hashing import content_hash
from forge.data.models import Bar, DataQualityReceipt

#: How many distinct datasets keep a receipt. Small: a session works over a
#: handful of datasets, and each entry holds a receipt rather than the bars.
_CACHE_LIMIT = 12

_CACHE: OrderedDict[str, DataQualityReceipt] = OrderedDict()
_LOCK = threading.Lock()

#: The checks, in the order a bar-by-bar pass would meet them. The order is not
#: cosmetic: `findings` is part of the receipt, the receipt is stored, and a
#: receipt whose findings came back in a different order would be a different
#: receipt for the same data.
_CHECK_ORDER = (
    "NON_FINITE_VALUE",
    "HIGH_INVARIANT",
    "LOW_INVARIANT",
    "KNOWLEDGE_BEFORE_EVENT",
    "NON_MONOTONIC_EVENT_TIME",
    "DUPLICATE_BAR",
)


class _Columns:
    """The batch as arrays, built once and used for both halves of the answer."""

    __slots__ = ("close", "event", "high", "knowledge", "low", "open", "size", "volume")

    def __init__(self, bars: list[Bar]) -> None:
        count = len(bars)
        self.size = count
        self.open = np.fromiter((b.open for b in bars), dtype=np.float64, count=count)
        self.high = np.fromiter((b.high for b in bars), dtype=np.float64, count=count)
        self.low = np.fromiter((b.low for b in bars), dtype=np.float64, count=count)
        self.close = np.fromiter((b.close for b in bars), dtype=np.float64, count=count)
        self.volume = np.fromiter((b.volume for b in bars), dtype=np.float64, count=count)
        self.event = np.fromiter(
            (b.event_time.timestamp() for b in bars), dtype=np.float64, count=count
        )
        self.knowledge = np.fromiter(
            (b.knowledge_time.timestamp() for b in bars), dtype=np.float64, count=count
        )


def _string_digest(values: list[str]) -> bytes:
    """A digest of a per-bar string column, cheaply when it does not vary.

    Almost every batch carries one symbol, one source and one bar type. Hashing
    the single distinct value and the count captures the column exactly and
    costs nothing; a column that genuinely varies falls back to hashing all of
    it rather than to an approximation.
    """
    distinct = set(values)
    hasher = hashlib.sha256()
    if len(distinct) <= 1:
        hasher.update(next(iter(distinct), "").encode("utf-8"))
        hasher.update(str(len(values)).encode("ascii"))
    else:
        hasher.update(b"\x00".join(value.encode("utf-8") for value in values))
    return hasher.digest()


def fingerprint(bars: list[Bar], columns: _Columns | None = None) -> str:
    """A strong identity for a batch of bars, computed from raw buffers.

    Every field that reaches the canonical serialisation is covered, because a
    fingerprint that skipped one would let a batch differing only in that field
    answer from another batch's receipt — and the receipt carries the content
    hash, so that would be one dataset's identity attached to another's results.

    Roughly an order of magnitude cheaper than the canonical digest it keys,
    which is the whole point: the expensive answer is computed once and this is
    what says whether it still applies.
    """
    cols = columns or _Columns(bars)
    hasher = hashlib.sha256()
    hasher.update(str(cols.size).encode("ascii"))
    for array in (
        cols.open,
        cols.high,
        cols.low,
        cols.close,
        cols.volume,
        cols.event,
        cols.knowledge,
    ):
        hasher.update(array.tobytes())
    hasher.update(
        np.fromiter(
            (b.ingestion_time.timestamp() for b in bars), dtype=np.float64, count=cols.size
        ).tobytes()
    )
    hasher.update(_string_digest([b.symbol for b in bars]))
    hasher.update(_string_digest([b.source for b in bars]))
    hasher.update(_string_digest([b.bar_type for b in bars]))
    return hasher.hexdigest()


def _findings(bars: list[Bar], cols: _Columns) -> tuple[str, ...]:
    """The structural findings, in the order a bar-by-bar pass would meet them.

    Each check produces a mask; the finding's position is the first bar that
    fails it. Reproducing the order exactly is what keeps a receipt computed
    this way identical to one computed by walking the bars.
    """
    values = (cols.open, cols.high, cols.low, cols.close, cols.volume)
    masks: dict[str, np.ndarray] = {}

    finite = np.ones(cols.size, dtype=bool)
    for array in values:
        finite &= np.isfinite(array)
    masks["NON_FINITE_VALUE"] = ~finite

    masks["HIGH_INVARIANT"] = cols.high < np.maximum(
        np.maximum(cols.open, cols.close), cols.low
    )
    masks["LOW_INVARIANT"] = cols.low > np.minimum(np.minimum(cols.open, cols.close), cols.high)
    masks["KNOWLEDGE_BEFORE_EVENT"] = cols.knowledge < cols.event

    monotonic = np.zeros(cols.size, dtype=bool)
    if cols.size > 1:
        monotonic[1:] = cols.event[1:] <= cols.event[:-1]
    masks["NON_MONOTONIC_EVENT_TIME"] = monotonic

    # A duplicate is the same symbol at the same instant. The common case is one
    # symbol, where the timestamps alone decide it; mixed symbols fall back to
    # the pair, which is also the case `MIXED_SYMBOLS` has already reported.
    duplicate = np.zeros(cols.size, dtype=bool)
    symbols = {bar.symbol for bar in bars}
    if len(symbols) <= 1:
        _, first = np.unique(cols.event, return_index=True)
        seen = np.zeros(cols.size, dtype=bool)
        seen[first] = True
        duplicate = ~seen
    else:
        keys: set[tuple[str, float]] = set()
        for index, bar in enumerate(bars):
            key = (bar.symbol, cols.event[index])
            if key in keys:
                duplicate[index] = True
            keys.add(key)
    masks["DUPLICATE_BAR"] = duplicate

    ordered: list[tuple[int, int, str]] = []
    for rank, name in enumerate(_CHECK_ORDER):
        mask = masks[name]
        if bool(mask.any()):
            ordered.append((int(np.argmax(mask)), rank, name))
    ordered.sort()
    found = [name for _position, _rank, name in ordered]
    if len(symbols) != 1:
        # Reported before the per-bar pass starts, so it leads whatever follows.
        found.insert(0, "MIXED_SYMBOLS")
    return tuple(found)


def validate_bars(bars: list[Bar]) -> DataQualityReceipt:
    if not bars:
        return DataQualityReceipt(
            accepted=False, findings=("EMPTY_BATCH",), row_count=0, content_hash=None
        )

    columns = _Columns(bars)
    key = fingerprint(bars, columns)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached

    findings = _findings(bars, columns)
    # The one expensive step, and the reason for everything above it. The
    # serialisation is pydantic's and the hash is the canonical one, unchanged:
    # this digest is data identity and a faster one would be a different one.
    digest = content_hash([bar.model_dump(mode="json") for bar in bars])
    receipt = DataQualityReceipt(
        accepted=not findings, findings=findings, row_count=len(bars), content_hash=digest
    )
    with _LOCK:
        _CACHE[key] = receipt
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)
    return receipt


def clear_cache() -> None:
    """Forget every cached receipt. For tests that need the uncached path."""
    with _LOCK:
        _CACHE.clear()


def _isfinite(value: float) -> bool:
    """Kept for callers that checked a single value through this module."""
    return math.isfinite(value)
