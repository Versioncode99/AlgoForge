"""A finished mission must never read as still running.

The bug this pins produced exactly that: `GET /missions/{id}` reporting
`status: running` with `finished_at: null`, while the job registry reported the
same mission's job as DONE and no worker thread existed any more.

Cause: `_save` evaluated `json.dumps(mission)` while building its statement but
committed at the end of the `with`. Those are two different instants, and the
mission dict is mutated by the worker thread while `launch` still holds a
reference to it. A thread descheduled between dump and commit could commit a
snapshot taken *before* another thread's commit; last-writer-wins then restored
an earlier state permanently.

The test below makes that interleaving deterministic rather than waiting for a
loaded machine to produce it — the original reproduced roughly once in nine
attempts, which is precisely the kind of failure that gets dismissed as flake.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest


class _SlowCommit:
    """A connection that stalls *after* its statement is handed over.

    Where the delay goes is the whole point. `json.dumps(mission)` is evaluated
    to build the arguments, then the statement runs, then the `with` commits.
    Sleeping inside `execute` puts the stall between the dump and the commit —
    the exact window the bug lived in. Sleeping in `connect()` instead would
    land *before* the dump, and then the slow writer serialises the final state
    and nothing is ever stale, which is how the first version of this test
    passed against the unfixed code.
    """

    def __init__(self, inner: sqlite3.Connection, delay: float) -> None:
        self._inner = inner
        self._delay = delay

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        if self._delay:
            time.sleep(self._delay)
        return self._inner.execute(*args, **kwargs)

    def __enter__(self) -> Any:
        self._inner.__enter__()
        return self

    def __exit__(self, *exc: Any) -> Any:
        return self._inner.__exit__(*exc)

    def close(self) -> None:
        self._inner.close()

    @property
    def row_factory(self) -> Any:
        return self._inner.row_factory

    @row_factory.setter
    def row_factory(self, value: Any) -> None:
        self._inner.row_factory = value


class _Store:
    """The persistence half of Orchestrator, with its real locking.

    Constructing a whole Orchestrator needs an app, an action registry and a
    model credential. The invariant under test lives entirely in save/read, so
    this exercises those against the same code path shape.
    """

    def __init__(self, path: Path) -> None:
        from forge_api.orchestrator import Orchestrator

        self.path = path
        self._write_lock = threading.Lock()
        self.delay = 0.0
        self._save = Orchestrator._save.__get__(self)  # type: ignore[attr-defined]
        self.get = Orchestrator.get.__get__(self)  # type: ignore[attr-defined]
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS missions "
                "(id TEXT PRIMARY KEY, started REAL, payload TEXT)"
            )

    def connect(self) -> Any:
        db = sqlite3.connect(self.path, timeout=15)
        db.execute("PRAGMA journal_mode=WAL")
        return _SlowCommit(db, self.delay)


@pytest.fixture
def store(tmp_path: Path) -> _Store:
    return _Store(tmp_path / "missions.db")


def _mission() -> dict[str, Any]:
    return {
        "id": "mission_1",
        "started_at": 1.0,
        "status": "running",
        "finished_at": None,
        "steps": [{"index": 1, "status": "running"}],
    }


def test_a_slow_save_cannot_resurrect_an_earlier_state(store: _Store) -> None:
    """The regression, made deterministic.

    A writer is held inside the dump-to-commit window while the mission
    finishes and is saved by another thread. Whichever commit lands last, the
    persisted row must not be the stale one.
    """
    mission = _mission()

    store.delay = 0.3
    slow = threading.Thread(target=lambda: store._save(mission), name="slow-save")
    slow.start()
    time.sleep(0.05)  # let the slow writer get into the window

    # The mission finishes while that writer is still mid-save.
    mission["status"] = "failed"
    mission["finished_at"] = 2.0
    mission["steps"][0]["status"] = "failed"

    store.delay = 0.0
    store._save(mission)
    slow.join(timeout=10)

    row = store.get("mission_1")
    assert row is not None
    assert row["status"] == "failed", "a finished mission was resurrected as running"
    assert row["finished_at"] == 2.0
    assert row["steps"][0]["status"] == "failed"


def test_concurrent_writers_leave_the_latest_state(store: _Store) -> None:
    """Many interleaved saves must still converge on the final mutation."""
    mission = _mission()
    stop = threading.Event()

    def churn() -> None:
        while not stop.is_set():
            store._save(mission)

    writers = [threading.Thread(target=churn, name=f"churn-{i}") for i in range(3)]
    for worker in writers:
        worker.start()

    for step in range(1, 60):
        mission["status"] = f"step-{step}"
        store._save(mission)

    mission["status"] = "completed"
    mission["finished_at"] = 9.0
    store._save(mission)
    stop.set()
    for worker in writers:
        worker.join(timeout=10)

    store._save(mission)
    row = store.get("mission_1")
    assert row is not None
    assert row["status"] == "completed"
    assert row["finished_at"] == 9.0


def test_the_row_is_always_valid_json_under_concurrency(store: _Store) -> None:
    """A torn write would surface as a JSON error, not a wrong status."""
    mission = _mission()
    errors: list[str] = []

    def churn() -> None:
        for index in range(80):
            mission["status"] = f"s{index}"
            try:
                store._save(mission)
                row = store.get("mission_1")
                assert row is not None
                json.dumps(row)
            except Exception as error:
                errors.append(f"{type(error).__name__}: {error}")

    workers = [threading.Thread(target=churn) for _ in range(3)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)

    assert errors == []
