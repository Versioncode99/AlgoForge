from __future__ import annotations

import json
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
        self._buffer.append(event)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")
        return event

    def recent(self, limit: int = 120) -> list[ActivityEvent]:
        return list(self._buffer)[-limit:][::-1]


class BacktestStore:
    """Backtest results are immutable artifacts on disk, keyed by content hash."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._meta: dict[str, tuple[str, str]] = {}

    def save(self, result: Any) -> None:  # BacktestResult; Any avoids a circular import
        path = self.root / f"{result.backtest_id}.json"
        if path.exists():
            return  # immutable: identical inputs produce the identical artifact
        path.write_text(json.dumps(result.model_dump(mode="json"), indent=2), encoding="utf-8")

    def load(self, backtest_id: str) -> dict[str, Any] | None:
        path = self.root / f"{backtest_id}.json"
        if not path.exists():
            return None
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return payload

    # Artifacts are immutable, so a filename -> (strategy, finished_at) index only
    # ever grows. Without it, listing N strategies re-parsed every artifact N
    # times, which turned the strategy list into an O(n^2) file read.
    def _index(self) -> dict[str, list[tuple[str, str]]]:
        index: dict[str, list[tuple[str, str]]] = {}
        for path in self.root.glob("*.json"):
            cached = self._meta.get(path.name)
            if cached is None:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                cached = (str(payload.get("strategy_id", "")), str(payload.get("finished_at", "")))
                self._meta[path.name] = cached
            index.setdefault(cached[0], []).append((path.name, cached[1]))
        return index

    def for_strategy(self, strategy_id: str) -> list[dict[str, Any]]:
        entries = sorted(self._index().get(strategy_id, []), key=lambda e: e[1], reverse=True)
        items: list[dict[str, Any]] = []
        for name, _ in entries:
            try:
                items.append(json.loads((self.root / name).read_text(encoding="utf-8")))
            except Exception:
                continue
        return items

    def summary_for(self, strategy_id: str) -> tuple[int, dict[str, Any] | None]:
        """Count plus the newest artifact, without parsing the rest."""
        entries = sorted(self._index().get(strategy_id, []), key=lambda e: e[1], reverse=True)
        if not entries:
            return 0, None
        return len(entries), self.load(Path(entries[0][0]).stem)

    def latest(self, strategy_id: str) -> dict[str, Any] | None:
        return self.summary_for(strategy_id)[1]

    def count(self) -> int:
        return sum(1 for _ in self.root.glob("*.json"))
