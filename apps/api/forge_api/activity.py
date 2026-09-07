from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from forge_api.artifact_index import ArtifactIndex, project

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


# Reads are overlapped because the disk releases the GIL and `json.loads` does
# not. Small on purpose: this only ever runs during a one-time backfill, and the
# machine is expected to be doing research at the same time.
BACKFILL_READERS = 8

# How many artifacts are written to the index per transaction during a backfill.
# Small enough that an interrupted process keeps most of its progress.
BACKFILL_BATCH = 200

# How long a `.tmp` file must have sat untouched before it is treated as the
# residue of an interrupted write rather than one still in progress. Generous:
# a multi-million-bar artifact takes a while to serialise, and deleting a live
# writer's temporary would be a far worse bug than leaving a stale one.
PARTIAL_WRITE_GRACE_SECONDS = 3600.0


class BacktestStore:
    """Backtest results are immutable artifacts on disk, keyed by content hash.

    The store keeps a **durable** projection of that directory (see
    `forge_api.artifact_index`) so that listing the library costs a database
    read rather than a 3 GB JSON parse on every process start. Anything that
    needs a trade ledger still reads the artifact itself through `load`.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_store = ArtifactIndex(root / "_index.db")
        # name -> (size, projection) for every artifact the index has seen.
        self._known: dict[str, tuple[int, dict[str, Any]]] = self._index_store.load_all()
        # name -> (size, reason) for files on disk that are not readable
        # artifacts. Remembered so they are not re-attempted on every read, and
        # reported so corruption is visible rather than silently absent.
        self._unreadable: dict[str, tuple[int, str]] = self._index_store.load_unreadable()
        # strategy_id -> [(name, finished_at)], newest first. Rebuilt from
        # `_known`, which is why it costs a dictionary walk and not a disk pass.
        self._by_strategy: dict[str, list[tuple[str, str]]] = {}
        # The engine writes from several worker threads.
        self._lock = threading.RLock()
        self._scanned = False
        self._pending: list[str] = []
        self._indexed_this_boot = 0
        self._worker: threading.Thread | None = None
        self._regroup()

    # ── index maintenance ────────────────────────────────────────────────────
    def _regroup(self) -> None:
        grouped: dict[str, list[tuple[str, str]]] = {}
        for name, (_, projection) in self._known.items():
            strategy_id = str(projection.get("strategy_id") or "")
            grouped.setdefault(strategy_id, []).append(
                (name, str(projection.get("finished_at") or ""))
            )
        for entries in grouped.values():
            entries.sort(key=lambda item: item[1], reverse=True)
        self._by_strategy = grouped

    def _scan(self) -> None:
        """Reconcile the index against the directory. Costs a listing, not a read.

        `os.scandir` returns the size alongside the name on Windows and Linux
        alike, so the whole reconciliation is one syscall walk: 0.1s over 1,919
        files against the 78.5s a full parse of the same files costs.
        """
        # Snapshot what the index holds *before* walking the directory. The walk
        # happens outside the lock because it is the slow part, and the engine's
        # writers must not block on it — but that means artifacts saved while it
        # runs are absent from `present` and present in `_known`. Treating those
        # as vanished deleted just-written runs from the index: measured at 142
        # of 150 under six concurrent writers, self-healing only on restart, and
        # silently under-reporting a strategy's runs until then.
        with self._lock:
            before = set(self._known)

        present: dict[str, int] = {}
        with os.scandir(self.root) as entries:
            for entry in entries:
                if not entry.name.endswith(".json"):
                    continue
                try:
                    present[entry.name] = entry.stat().st_size
                except OSError:
                    continue
        with self._lock:
            # Only a name the scan could actually have seen counts as vanished.
            vanished = [name for name in before if name not in present]
            for name in vanished:
                self._known.pop(name, None)
            if vanished:
                self._index_store.forget(vanished)
            healed = [
                name
                for name, (size, _) in self._unreadable.items()
                if name not in present or present[name] != size
            ]
            for name in healed:
                self._unreadable.pop(name, None)
            if healed:
                self._index_store.clear_unreadable(healed)
            # An immutable artifact whose size moved was rewritten by something
            # else. Re-read it rather than trusting the stale projection. A file
            # already known to be damaged at this exact size is left alone.
            self._pending = [
                name
                for name, size in present.items()
                if (name not in self._known or self._known[name][0] != size)
                and self._unreadable.get(name, (-1, ""))[0] != size
            ]
            if vanished:
                self._regroup()
            self._scanned = True
        self._sweep_partial_writes(present)

    def _sweep_partial_writes(self, present: dict[str, int]) -> None:
        """Delete `.tmp` files left by a write that never completed.

        `save` writes to a temporary and renames, which is atomic — but a
        process killed between the two leaves the temporary behind forever.
        Nothing read them, so they were invisible; they simply accumulated in a
        directory that already holds thousands of files.

        Only temporaries with no surviving artifact are removed, and only ones
        old enough that no write could still be in flight.
        """
        cutoff = time.time() - PARTIAL_WRITE_GRACE_SECONDS
        for path in self.root.glob("*.tmp"):
            if path.with_suffix(".json").name in present:
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                # A live writer still holds it, or it went already. Either way
                # this is housekeeping and must never break a read.
                continue

    def _project_file(self, name: str) -> tuple[str, int, dict[str, Any] | str]:
        """Project one artifact, or report why it could not be read.

        The third element is a projection on success and a `DATA_*` reason on
        failure. Failure is a result here, not an exception: a damaged file must
        be recorded as damaged, otherwise it is retried forever.
        """
        path = self.root / name
        try:
            blob = path.read_bytes()
        except OSError as exc:
            return name, -1, f"DATA_UNREADABLE: {exc.strerror or type(exc).__name__}"
        try:
            payload = json.loads(blob)
        except ValueError as exc:
            return name, len(blob), f"DATA_CORRUPTED: {exc}"
        if not isinstance(payload, dict):
            return name, len(blob), "SCHEMA_ERROR: artifact is not a JSON object"
        # Both identities must be non-empty *strings*. A truthiness check alone
        # let a nested object through, which `_regroup` then stringified into a
        # grouping key like "{'nested': 'object'}" — an artifact filed under a
        # strategy that cannot exist.
        for field in ("backtest_id", "strategy_id"):
            value = payload.get(field)
            if not isinstance(value, str) or not value.strip():
                return name, len(blob), f"SCHEMA_ERROR: {field} is not a non-empty string"
        return name, len(blob), project(payload)

    def _absorb(self, rows: list[tuple[str, int, dict[str, Any]]]) -> None:
        if not rows:
            return
        self._index_store.put_many(rows)
        with self._lock:
            for name, size, projection in rows:
                self._known[name] = (size, projection)
            self._indexed_this_boot += len(rows)
            self._regroup()

    def _reject(self, rows: list[tuple[str, int, str]]) -> None:
        if not rows:
            return
        self._index_store.mark_unreadable(rows)
        with self._lock:
            for name, size, reason in rows:
                self._unreadable[name] = (size, reason)

    def _backfill(self) -> None:
        """Parse everything the index has never seen, in batches.

        This is the only place an artifact is fully parsed for its projection,
        and because artifacts are immutable and the index is durable, each one
        is parsed exactly once for the lifetime of the workspace.
        """
        with self._lock:
            queue = list(self._pending)
        if not queue:
            return
        with ThreadPoolExecutor(max_workers=BACKFILL_READERS) as pool:
            batch: list[tuple[str, int, dict[str, Any]]] = []
            damaged: list[tuple[str, int, str]] = []
            for name, size, outcome in pool.map(self._project_file, queue):
                if isinstance(outcome, str):
                    damaged.append((name, size, outcome))
                else:
                    batch.append((name, size, outcome))
                if len(batch) >= BACKFILL_BATCH:
                    self._absorb(batch)
                    batch = []
            self._absorb(batch)
            self._reject(damaged)
        with self._lock:
            self._pending = [
                name
                for name in self._pending
                if name not in self._known and name not in self._unreadable
            ]

    def warm(self) -> None:
        """Start indexing anything new, off the request path.

        Called once at application startup. Nothing depends on it finishing: a
        read that arrives first does the same work synchronously.
        """
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            worker = threading.Thread(target=self._warm_now, name="artifact-index", daemon=True)
            self._worker = worker
        worker.start()

    def _warm_now(self) -> None:
        if not self._scanned:
            self._scan()
        self._backfill()

    def _ready(self) -> dict[str, list[tuple[str, str]]]:
        """The grouped index, with any unseen artifact indexed first.

        A strategy-scoped answer cannot be given from a partial index: until an
        artifact has been read, nothing knows which strategy it belongs to. So
        this waits — but only for artifacts never seen *by any previous boot*,
        which on a warmed workspace is none.
        """
        if not self._scanned:
            self._scan()
        with self._lock:
            worker = self._worker
            pending = bool(self._pending)
        if pending:
            if worker is not None and worker.is_alive():
                worker.join()
            self._backfill()
        with self._lock:
            return self._by_strategy

    def status(self) -> dict[str, Any]:
        """Honest indexing state, for the interface and the API envelope."""
        with self._lock:
            return {
                "indexed": len(self._known),
                "pending": len(self._pending),
                "scanned": self._scanned,
                "built_this_boot": self._indexed_this_boot,
                "ready": self._scanned and not self._pending,
                # Files in the artifact directory that are not readable
                # artifacts. Surfaced rather than skipped silently: a strategy
                # missing a run it did produce should be explainable.
                "unreadable": len(self._unreadable),
            }

    def damaged(self) -> list[dict[str, str]]:
        """Every file that would not parse, with the reason it would not.

        Data Health reads this. The reasons use the shared error taxonomy so a
        truncated file and an unreadable one are distinguishable.
        """
        with self._lock:
            return sorted(
                (
                    {"name": name, "reason": reason, "bytes": str(size)}
                    for name, (size, reason) in self._unreadable.items()
                ),
                key=lambda row: row["name"],
            )

    # ── reads and writes ─────────────────────────────────────────────────────
    def save(self, result: Any) -> None:  # BacktestResult; Any avoids a circular import
        path = self.root / f"{result.backtest_id}.json"
        payload = result.model_dump(mode="json")
        with self._lock:
            if path.exists():
                return
            blob = json.dumps(payload, indent=2)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(blob, encoding="utf-8")
            temporary.replace(path)
            # Project from the payload already in hand. A saved artifact is
            # never re-read to find out what it contains.
            projection = project(payload)
            row = (path.name, len(blob.encode("utf-8")), projection)
            self._known[row[0]] = (row[1], row[2])
            # Insert into the grouping rather than rebuilding it. With four
            # workers saving every few seconds, regrouping the whole library on
            # every write would make the engine pay for the size of its own
            # history.
            entries = self._by_strategy.setdefault(str(projection.get("strategy_id") or ""), [])
            entries.append((path.name, str(projection.get("finished_at") or "")))
            entries.sort(key=lambda item: item[1], reverse=True)
        self._index_store.put_many([row])

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

    def for_strategy(self, strategy_id: str) -> list[dict[str, Any]]:
        """Every artifact for a strategy, in full. Newest first.

        This reads trade ledgers. Callers that only need list-row scalars want
        `projections_for` instead.
        """
        entries = self._ready().get(strategy_id, [])
        items: list[dict[str, Any]] = []
        for name, _ in entries:
            try:
                items.append(json.loads((self.root / name).read_text(encoding="utf-8")))
            except Exception:
                continue
        return items

    def projections_for(self, strategy_id: str) -> list[dict[str, Any]]:
        """Every artifact for a strategy as projections, newest first.

        No trade ledger is read. This is what a detail table, a trial count or a
        ranking should use; `for_strategy` exists for the paths that genuinely
        need the trades.
        """
        entries = self._ready().get(strategy_id, [])
        rows: list[dict[str, Any]] = []
        with self._lock:
            for name, _ in entries:
                known = self._known.get(name)
                if known is not None:
                    rows.append(known[1])
        return rows

    def summary_for(self, strategy_id: str) -> tuple[int, dict[str, Any] | None]:
        """Count plus the newest artifact **in full**, without parsing the rest."""
        entries = self._ready().get(strategy_id, [])
        if not entries:
            return 0, None
        return len(entries), self.load(Path(entries[0][0]).stem)

    def latest(self, strategy_id: str) -> dict[str, Any] | None:
        return self.summary_for(strategy_id)[1]

    def latest_projection(self, strategy_id: str) -> dict[str, Any] | None:
        """The newest run's projected scalars. Reads no artifact at all."""
        return self.list_summary(strategy_id)[1]

    def list_summary(self, strategy_id: str) -> tuple[int, dict[str, Any] | None]:
        """Run count plus a projection of the newest run, for library listings.

        Returns only what a table row displays. The full artifact is still what
        every judging, validation and dossier path reads; this exists so that
        drawing a list does not have to pay for the trade ledger behind it.
        """
        entries = self._ready().get(strategy_id, [])
        if not entries:
            return 0, None
        with self._lock:
            known = self._known.get(entries[0][0])
        return len(entries), (known[1] if known is not None else None)

    def count(self) -> int:
        return sum(len(entries) for entries in self._ready().values())
