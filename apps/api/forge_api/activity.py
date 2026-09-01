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
        if not self.path.exists():
            return
        keep = self._buffer.maxlen or 0
        for line in self.path.read_text(encoding="utf-8").splitlines()[-keep:]:
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

    def for_strategy(self, strategy_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in self.root.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if payload.get("strategy_id") == strategy_id:
                items.append(payload)
        return sorted(items, key=lambda p: p.get("finished_at", ""), reverse=True)

    def latest(self, strategy_id: str) -> dict[str, Any] | None:
        items = self.for_strategy(strategy_id)
        return items[0] if items else None

    def count(self) -> int:
        return sum(1 for _ in self.root.glob("*.json"))
