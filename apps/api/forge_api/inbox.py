"""Where finished work waits until somebody looks at it.

Long work is the normal case here: a backtest over the full archive is minutes,
a prop matrix over four hundred strategies is longer, and a research campaign
can run for hours. All of it already reports progress while the operator
watches. None of it had anywhere to *land* when they were not watching -- the
job registry is in process, keeps forty jobs and loses everything on restart,
which is correct for a result that is recomputable and useless as a record that
the work happened at all.

**The arrival rule, which is the whole design.** An item arrives when, and only
when, a job reaches a terminal state -- done, failed or cancelled. Nothing
judges whether the work was interesting, nothing summarises it, and no
assistant decides what is worth surfacing. That rule is why this is worth
having: an inbox whose contents depend on a judgement is one an operator has to
audit rather than read, and the first time it withholds something they needed
they stop trusting all of it.

Arrival is keyed by job id and is idempotent, so the same job cannot arrive
twice however many times it is offered.

What is stored is the *fact* of the work and how to get back to its subject --
never the result. A backtest's numbers live in its artifact and a campaign's in
its own store; copying either here would create a second record that can
disagree with the first, which is the failure this codebase spends most of its
effort avoiding. An item whose job has since been evicted says so and still
names what it was about.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter
from forge.contracts.models import ApiEnvelope

Outcome = Literal["done", "failed", "cancelled"]

#: Terminal job statuses, and the outcome each becomes. A job in any other
#: status has not arrived and must not be recorded: an inbox that fills with
#: running work is a job list with a worse name.
TERMINAL: dict[str, Outcome] = {"DONE": "done", "FAILED": "failed", "CANCELLED": "cancelled"}

SCHEMA_VERSION = 1

#: Items are small and a person may reasonably want a month of them. Far above
#: the job registry's forty, because the point of this store is outliving that.
MAX_ITEMS = 2000


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Inbox:
    """A durable record of finished work, one row per job."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with closing(self._connect()) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    item_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    refs TEXT NOT NULL DEFAULT '{}',
                    seconds REAL NOT NULL DEFAULT 0,
                    arrived_at TEXT NOT NULL,
                    read_at TEXT NOT NULL DEFAULT '',
                    dismissed_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS items_arrived ON items (arrived_at DESC)")
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        return db

    # ── arrival ───────────────────────────────────────────────────────────────

    def arrive(self, job: Any, refs: Mapping[str, str] | None = None) -> dict[str, Any] | None:
        """Record a finished job. Returns the item, or None if it did not arrive.

        None covers both ways nothing should happen: a job that is not finished,
        and a job that already arrived. Both are ordinary rather than
        exceptional -- the registry may offer the same job more than once, and
        offering a running one is how a caller asks "is this one done yet".
        """
        outcome = TERMINAL.get(str(getattr(job, "status", "")))
        if outcome is None:
            return None
        job_id = str(getattr(job, "job_id", ""))
        if not job_id:
            return None
        started = getattr(job, "started_at", None)
        finished = getattr(job, "finished_at", None)
        seconds = float(finished - started) if started and finished else 0.0
        row = {
            "item_id": f"inb_{uuid.uuid4().hex[:16]}",
            "job_id": job_id,
            "kind": str(getattr(job, "kind", "")),
            "label": str(getattr(job, "label", "")),
            "outcome": outcome,
            "error": str(getattr(job, "error", "") or ""),
            # The job carries its own subject; an explicit argument overrides it,
            # which is how a caller that knows more than the job did can say so.
            "refs": json.dumps(
                {
                    str(k): str(v)
                    for k, v in dict(refs if refs is not None else getattr(job, "refs", {})).items()
                }
            ),
            "seconds": round(seconds, 2),
            "arrived_at": _now(),
            "read_at": "",
            "dismissed_at": "",
        }
        with self._lock, closing(self._connect()) as db:
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO items
                    (item_id, job_id, kind, label, outcome, error, refs, seconds, arrived_at)
                VALUES
                    (:item_id, :job_id, :kind, :label, :outcome, :error, :refs, :seconds,
                     :arrived_at)
                """,
                {key: row[key] for key in (
                    "item_id", "job_id", "kind", "label", "outcome", "error", "refs",
                    "seconds", "arrived_at",
                )},
            )
            if cursor.rowcount == 0:
                db.commit()
                return None
            self._trim(db)
            db.commit()
        return self._shape(row)

    def _trim(self, db: sqlite3.Connection) -> None:
        """Drop the oldest dismissed items first, then the oldest read ones.

        Never an unread item: the one thing an inbox must not do is quietly lose
        something nobody has seen. If the cap is reached with nothing but unread
        items, the cap yields.
        """
        (count,) = db.execute("SELECT COUNT(*) FROM items").fetchone()
        if count <= MAX_ITEMS:
            return
        db.execute(
            """
            DELETE FROM items WHERE item_id IN (
                SELECT item_id FROM items
                WHERE dismissed_at != '' OR read_at != ''
                ORDER BY dismissed_at = '' ASC, arrived_at ASC
                LIMIT ?
            )
            """,
            (count - MAX_ITEMS,),
        )

    # ── reading ───────────────────────────────────────────────────────────────

    @staticmethod
    def _shape(row: Mapping[str, Any]) -> dict[str, Any]:
        try:
            refs = json.loads(str(row["refs"]))
        except ValueError:
            refs = {}
        return {
            "item_id": row["item_id"],
            "job_id": row["job_id"],
            "kind": row["kind"],
            "label": row["label"],
            "outcome": row["outcome"],
            "error": row["error"],
            "refs": refs if isinstance(refs, dict) else {},
            "seconds": row["seconds"],
            "arrived_at": row["arrived_at"],
            "read_at": row["read_at"],
            "dismissed_at": row["dismissed_at"],
        }

    def items(self, *, include_dismissed: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM items"
        if not include_dismissed:
            query += " WHERE dismissed_at = ''"
        query += " ORDER BY arrived_at DESC, rowid DESC LIMIT ?"
        with closing(self._connect()) as db:
            rows = db.execute(query, (max(1, min(int(limit), MAX_ITEMS)),)).fetchall()
        return [self._shape(dict(row)) for row in rows]

    def unread(self) -> int:
        with closing(self._connect()) as db:
            (count,) = db.execute(
                "SELECT COUNT(*) FROM items WHERE read_at = '' AND dismissed_at = ''"
            ).fetchone()
        return int(count)

    # ── acting on it ──────────────────────────────────────────────────────────

    def mark_read(self, item_id: str) -> bool:
        with self._lock, closing(self._connect()) as db:
            cursor = db.execute(
                "UPDATE items SET read_at = ? WHERE item_id = ? AND read_at = ''",
                (_now(), item_id),
            )
            db.commit()
            return cursor.rowcount > 0

    def dismiss(self, item_id: str) -> bool:
        """Dismissing also marks read: something acted on has been seen."""
        with self._lock, closing(self._connect()) as db:
            cursor = db.execute(
                """
                UPDATE items
                   SET dismissed_at = ?,
                       read_at = CASE WHEN read_at = '' THEN ? ELSE read_at END
                 WHERE item_id = ? AND dismissed_at = ''
                """,
                (_now(), _now(), item_id),
            )
            db.commit()
            return cursor.rowcount > 0

    def mark_all_read(self) -> int:
        """Everything currently unread. Returns how many moved."""
        with self._lock, closing(self._connect()) as db:
            cursor = db.execute(
                "UPDATE items SET read_at = ? WHERE read_at = '' AND dismissed_at = ''",
                (_now(),),
            )
            db.commit()
            return int(cursor.rowcount)


def build_inbox_router(inbox: Inbox) -> APIRouter:
    """The inbox over HTTP.

    Read and act, never create: an item arrives because a job finished, and the
    only way to put one here is to do some work. A route that could manufacture
    an arrival would make the record say something happened that did not.
    """
    router = APIRouter(prefix="/api/v1", tags=["inbox"])

    @router.get("/inbox", response_model=ApiEnvelope[dict[str, Any]])
    def read_inbox(include_dismissed: bool = False, limit: int = 200) -> Any:
        items = inbox.items(include_dismissed=include_dismissed, limit=limit)
        return ApiEnvelope(
            data={"items": items, "unread": inbox.unread()},
            meta={
                "arrival_rule": (
                    "An item arrives when a job reaches a terminal state -- done, failed or "
                    "cancelled -- and at no other time. Nothing judges whether the work was "
                    "worth surfacing."
                ),
            },
        )

    @router.post("/inbox/{item_id}/read", response_model=ApiEnvelope[dict[str, Any]])
    def read_one(item_id: str) -> Any:
        return ApiEnvelope(data={"changed": inbox.mark_read(item_id), "unread": inbox.unread()})

    @router.post("/inbox/read-all", response_model=ApiEnvelope[dict[str, Any]])
    def read_all() -> Any:
        return ApiEnvelope(data={"changed": inbox.mark_all_read(), "unread": inbox.unread()})

    @router.delete("/inbox/{item_id}", response_model=ApiEnvelope[dict[str, Any]])
    def dismiss_one(item_id: str) -> Any:
        return ApiEnvelope(data={"changed": inbox.dismiss(item_id), "unread": inbox.unread()})

    return router
