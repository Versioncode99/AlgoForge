"""A durable projection of the backtest artifact directory.

Backtest artifacts are immutable JSON documents and there are thousands of them.
Almost every screen wants a handful of scalars per artifact — which strategy,
when it finished, net P&L, how many trades — and almost none of them want the
trade ledger and equity curve that make up better than 95% of the bytes.

Before this module that projection lived only in process memory, so every API
restart rebuilt it by fully parsing every artifact. On the reference workspace
that is 1,919 files and 3.19 GB, measured at 78.5 seconds, paid again on every
launch, to recover roughly 200 KB of facts. A directory listing over the same
files takes 0.108 seconds; the cost was entirely `json.loads`, which runs at
about 80 MB/s against a disk that reads at 1,200 MB/s.

So the projection is persisted in SQLite next to the artifacts it describes. A
restart reads 1,919 rows instead of 3.19 GB. Only artifacts the index has never
seen are parsed, and since artifacts are immutable and content-addressed, an
artifact is parsed exactly once — ever, not once per boot.

Two properties are deliberate:

* **Correctness never depends on the backfill having finished.** `BacktestStore`
  falls back to parsing an unknown artifact on demand and indexing the result.
  A cold index makes reads slower, never wrong.
* **The projection is versioned.** Adding a field to `PROJECTION_FIELDS` without
  bumping `PROJECTION_VERSION` would serve rows that silently lack it, so a
  version mismatch discards the table and rebuilds rather than merging shapes.

The index is a cache with a schema, not a source of truth. Deleting the database
costs one rebuild and loses nothing.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Bump whenever the shape of a projection row changes. A mismatch rebuilds.
PROJECTION_VERSION = 1


def project(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The fields a list row, a summary or a ranking needs, and no others.

    Everything here is a scalar or a small mapping. The trade ledger and the
    equity curve are deliberately absent: any caller that needs them is doing
    real work — judging, prop simulation, a dossier — and should read the whole
    artifact through `BacktestStore.load`.
    """
    trades = payload.get("trades")
    receipt = payload.get("split_receipt") or {}
    return {
        "backtest_id": payload.get("backtest_id", ""),
        "strategy_id": payload.get("strategy_id", ""),
        "calculation_version": payload.get("calculation_version", "legacy-price-points"),
        "parameters": payload.get("parameters") or {},
        "bar_count": payload.get("bar_count"),
        "trade_count": len(trades) if isinstance(trades, list) else 0,
        "net_pnl": payload.get("net_pnl"),
        "gross_pnl": payload.get("gross_pnl"),
        "total_costs": payload.get("total_costs"),
        "win_rate": payload.get("win_rate"),
        "max_drawdown": payload.get("max_drawdown"),
        "lookahead_clean": payload.get("lookahead_clean"),
        "labels": list(payload.get("labels") or ()),
        "evidence_tier": payload.get("evidence_tier", "LEGACY_IN_SAMPLE"),
        "dataset_key": payload.get("dataset_key"),
        "partition_name": payload.get("partition_name"),
        "split_id": receipt.get("split_id") if isinstance(receipt, dict) else None,
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
    }


class ArtifactIndex:
    """SQLite-backed projection store. One row per artifact file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Writes queue here rather than on SQLite's busy handler.
        #
        # `BacktestStore.save` deliberately calls `put_many` *outside* its own
        # lock, so a database write does not block another worker's in-memory
        # bookkeeping. That is the right shape, and it left the writes with no
        # serialisation at all: six engine workers saving at once each opened
        # their own connection and fought for the write lock.
        #
        # SQLite's busy handler has no fairness guarantee. Under steady
        # contention a connection can be starved past *any* timeout, which is
        # what "database is locked" was on a loaded Windows runner — not a
        # timeout that was too short, but one connection never winning. A lock
        # makes the writes queue in arrival order and the contention disappears.
        #
        # Reads are deliberately left outside it: they are what the interface
        # waits on, and they do not block each other.
        self._write_lock = threading.Lock()
        self._prepare()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=15)

    def _prepare(self) -> None:
        with closing(self._connect()) as db, db:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            if version and version != PROJECTION_VERSION:
                # The stored rows were projected by different code. Merging two
                # projection shapes would serve rows missing fields the caller
                # believes are always present, so the old ones are discarded.
                db.execute("DROP TABLE IF EXISTS artifacts")
            db.execute(
                "CREATE TABLE IF NOT EXISTS artifacts ("
                "name TEXT PRIMARY KEY, size INTEGER NOT NULL, "
                "strategy_id TEXT NOT NULL, finished_at TEXT NOT NULL, "
                "projection TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS artifacts_by_strategy "
                "ON artifacts (strategy_id, finished_at DESC)"
            )
            # Files that are on disk but are not readable artifacts. Recorded
            # rather than merely skipped: without this the store re-attempts
            # them on every read, and a single damaged file makes every strategy
            # listing spawn a pool to fail at it again. The size is part of the
            # key, so a file that changes is retried rather than written off.
            db.execute(
                "CREATE TABLE IF NOT EXISTS unreadable ("
                "name TEXT PRIMARY KEY, size INTEGER NOT NULL, reason TEXT NOT NULL, "
                "noticed_at TEXT NOT NULL)"
            )
            db.execute(f"PRAGMA user_version = {PROJECTION_VERSION}")

    def load_all(self) -> dict[str, tuple[int, dict[str, Any]]]:
        """Every indexed artifact as ``name -> (size, projection)``.

        Read once at construction. Rows whose JSON will not decode are dropped
        from the result rather than raised: a damaged cache row must cost a
        re-parse of one artifact, not a failed startup.
        """
        rows: dict[str, tuple[int, dict[str, Any]]] = {}
        with closing(self._connect()) as db:
            for name, size, blob in db.execute("SELECT name, size, projection FROM artifacts"):
                try:
                    rows[str(name)] = (int(size), json.loads(blob))
                except (TypeError, ValueError):
                    continue
        return rows

    def put_many(self, entries: Iterable[tuple[str, int, dict[str, Any]]]) -> int:
        """Insert or replace projections. Returns how many rows were written."""
        payload = [
            (
                name,
                size,
                str(projection.get("strategy_id") or ""),
                str(projection.get("finished_at") or ""),
                json.dumps(projection),
            )
            for name, size, projection in entries
        ]
        if not payload:
            return 0
        with self._write_lock, closing(self._connect()) as db, db:
            db.executemany(
                "INSERT OR REPLACE INTO artifacts "
                "(name, size, strategy_id, finished_at, projection) VALUES (?, ?, ?, ?, ?)",
                payload,
            )
        return len(payload)

    def forget(self, names: Iterable[str]) -> int:
        """Drop rows for artifacts that are no longer on disk."""
        doomed = [(name,) for name in names]
        if not doomed:
            return 0
        with self._write_lock, closing(self._connect()) as db, db:
            db.executemany("DELETE FROM artifacts WHERE name = ?", doomed)
        return len(doomed)

    def count(self) -> int:
        with closing(self._connect()) as db:
            return int(db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0])

    # ── damaged files ────────────────────────────────────────────────────────
    def load_unreadable(self) -> dict[str, tuple[int, str]]:
        """``name -> (size, reason)`` for files that would not parse."""
        rows: dict[str, tuple[int, str]] = {}
        with closing(self._connect()) as db:
            for name, size, reason in db.execute("SELECT name, size, reason FROM unreadable"):
                rows[str(name)] = (int(size), str(reason))
        return rows

    def mark_unreadable(self, entries: Iterable[tuple[str, int, str]]) -> int:
        payload = [
            (name, size, reason, datetime.now(UTC).isoformat(timespec="seconds"))
            for name, size, reason in entries
        ]
        if not payload:
            return 0
        with self._write_lock, closing(self._connect()) as db, db:
            db.executemany(
                "INSERT OR REPLACE INTO unreadable (name, size, reason, noticed_at) "
                "VALUES (?, ?, ?, ?)",
                payload,
            )
        return len(payload)

    def clear_unreadable(self, names: Iterable[str]) -> None:
        doomed = [(name,) for name in names]
        if not doomed:
            return
        with self._write_lock, closing(self._connect()) as db, db:
            db.executemany("DELETE FROM unreadable WHERE name = ?", doomed)
