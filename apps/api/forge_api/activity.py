from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

Level = Literal["info", "pass", "fail", "warn"]


class ActivityEvent(BaseModel):
    ts: str
    stage: str
    level: Level
    message: str
    ref: str | None = None


class ActivityLog:
    """Append-only operator log. This is what the orchestrator strip renders.

    Events are recorded because something actually happened, never to make the
    interface look busy.
    """

    def __init__(self, path: Path, capacity: int = 500) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._buffer: deque[ActivityEvent] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        """Restore recent history, tolerating a damaged log.

        A truncated write or a stray byte must never stop the application from
        starting: the log is a convenience, not a source of truth. Decoding
        errors are replaced rather than raised, and unparseable lines skipped.
        """
        if not self.path.exists():
            return
        keep = self._buffer.maxlen or 0
        try:
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for line in text.splitlines()[-keep:]:
            try:
                self._buffer.append(ActivityEvent.model_validate_json(line))
            except Exception:
                continue

    def record(
        self, stage: str, message: str, level: Level = "info", ref: str | None = None
    ) -> ActivityEvent:
        event = ActivityEvent(
            ts=datetime.now(UTC).isoformat(timespec="seconds"),
            stage=stage,
            level=level,
            message=message,
            ref=ref,
        )
        with self._lock:
            self._buffer.append(event)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(event.model_dump_json() + "\n")
        return event

    def recent(self, limit: int = 120) -> list[ActivityEvent]:
        with self._lock:
            return list(self._buffer)[-max(1, min(limit, 500)) :][::-1]


# How long the in-memory index may go without a full rescan. Only matters for
# artifacts written by another process; ours are inserted as they are saved.
RESCAN_SECONDS = 30.0


class BacktestStore:
    """Backtest results are immutable artifacts on disk, keyed by content hash."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._meta: dict[str, tuple[str, str]] = {}
        self._index_cache: dict[str, list[tuple[str, str]]] | None = None
        self._index_built: float = -1e9
        # The engine writes from several worker threads.
        self._index_lock = threading.Lock()

    def save(self, result: Any) -> None:  # BacktestResult; Any avoids a circular import
        path = self.root / f"{result.backtest_id}.json"
        payload = result.model_dump(mode="json")
        # Insert into the index rather than discarding it. Throwing the cache
        # away meant a full rebuild on the next read, and with four workers
        # saving every few seconds the list spent most of its time rebuilding:
        # 6.6s while the engine ran, against 0.36s idle.
        finished = str(payload.get("finished_at", ""))
        with self._index_lock:
            if path.exists():
                return
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temporary.replace(path)
            self._meta[path.name] = (result.strategy_id, finished)
            if self._index_cache is not None:
                entries = self._index_cache.setdefault(result.strategy_id, [])
                entries.append((path.name, finished))
                entries.sort(key=lambda item: item[1], reverse=True)

    def load(self, backtest_id: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", backtest_id):
            return None
        path = self.root / f"{backtest_id}.json"
        if not path.exists():
            return None
        try:
            payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload

    # Artifacts are immutable, so a filename -> (strategy, finished_at) index only
    # ever grows. Caching the *file contents* was not enough: `_index` still
    # re-globbed the directory and rebuilt the whole dict once per strategy, so
    # listing 63 strategies over 379 artifacts meant 63 directory scans and took
    # 8.7 seconds while the engine was writing. The built index is cached too,
    # and invalidated by our own writes or by the directory changing underneath
    # us.
    def _index(self) -> dict[str, list[tuple[str, str]]]:
        with self._index_lock:
            # Our own writes keep the index current, so a rescan only catches
            # changes made by another process. Checking the directory mtime
            # instead raced with four workers saving concurrently and forced a
            # full rebuild on almost every read.
            fresh = time.monotonic() - self._index_built < RESCAN_SECONDS
            if self._index_cache is not None and fresh:
                return self._index_cache

            index: dict[str, list[tuple[str, str]]] = {}
            for path in self.root.glob("*.json"):
                cached = self._meta.get(path.name)
                if cached is None:
                    try:
                        payload = json.loads(path.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    cached = (
                        str(payload.get("strategy_id", "")),
                        str(payload.get("finished_at", "")),
                    )
                    self._meta[path.name] = cached
                index.setdefault(cached[0], []).append((path.name, cached[1]))
            # Sort once here rather than at every call site.
            for entries in index.values():
                entries.sort(key=lambda item: item[1], reverse=True)
            self._index_cache = index
            self._index_built = time.monotonic()
            return index

    def for_strategy(self, strategy_id: str) -> list[dict[str, Any]]:
        entries = self._index().get(strategy_id, [])
        items: list[dict[str, Any]] = []
        for name, _ in entries:
            try:
                items.append(json.loads((self.root / name).read_text(encoding="utf-8")))
            except Exception:
                continue
        return items

    def summary_for(self, strategy_id: str) -> tuple[int, dict[str, Any] | None]:
        """Count plus the newest artifact, without parsing the rest."""
        entries = self._index().get(strategy_id, [])
        if not entries:
            return 0, None
        return len(entries), self.load(Path(entries[0][0]).stem)

    def latest(self, strategy_id: str) -> dict[str, Any] | None:
        return self.summary_for(strategy_id)[1]

    def count(self) -> int:
        return sum(len(entries) for entries in self._index().values())
