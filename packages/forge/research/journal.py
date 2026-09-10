"""The campaign's event stream: what the researcher did, in order, with its reasons.

The observability requirement is specific — the interface must be able to show
what the engine is doing, why, what it has learned and what is next — and the
existing :class:`~forge_api.activity.ActivityLog` cannot serve it. That log is a
global, capacity-bounded, flat operator strip: one stage string, one sentence,
one optional reference. It is the right shape for "the application did
something" and the wrong shape for "this campaign admitted this hypothesis with
this novelty score after colliding with that family".

So this is a **typed, durable, per-campaign** stream. Every event names its
kind, the campaign, the subject it is about, and a detail payload that the
interface renders as structured fields rather than parsing out of prose.

Both are written. The journal carries the research record; the activity log
keeps the one-line human summary so the existing operator strip continues to
show engine work alongside everything else. Neither is derived from the other,
and the journal is the one that must be complete.

**Nothing here is written speculatively.** An event exists because something
happened — a proposal was made, a duplicate was refused, a family was created, a
retrieval returned nothing. There is no code path that emits an event to make a
run look active, which is the entire difference between an event stream and an
animation.
"""

from __future__ import annotations

import builtins
import json
import sqlite3
import threading
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class EventKind(StrEnum):
    """What happened. A closed set, so the interface can render each one."""

    CAMPAIGN_STARTED = "CAMPAIGN_STARTED"
    CAMPAIGN_STOPPED = "CAMPAIGN_STOPPED"
    DATASET_INSPECTED = "DATASET_INSPECTED"
    ALLOCATION_ADAPTED = "ALLOCATION_ADAPTED"
    BUDGET_DRAWN = "BUDGET_DRAWN"
    LITERATURE_SEARCHED = "LITERATURE_SEARCHED"
    SOURCE_FOUND = "SOURCE_FOUND"
    HYPOTHESIS_PROPOSED = "HYPOTHESIS_PROPOSED"
    HYPOTHESIS_REJECTED = "HYPOTHESIS_REJECTED"
    NOVELTY_CHECKED = "NOVELTY_CHECKED"
    DATA_BLOCKED = "DATA_BLOCKED"
    FAMILY_CREATED = "FAMILY_CREATED"
    TEMPLATE_CREATED = "TEMPLATE_CREATED"
    TEMPLATE_REJECTED = "TEMPLATE_REJECTED"
    CONFORMANCE_CHECKED = "CONFORMANCE_CHECKED"
    PILOT_RUN = "PILOT_RUN"
    EXPERIMENT_STARTED = "EXPERIMENT_STARTED"
    EXPERIMENT_FINISHED = "EXPERIMENT_FINISHED"
    FRONTIER_UPDATED = "FRONTIER_UPDATED"
    FOLLOWUP_GENERATED = "FOLLOWUP_GENERATED"
    VALIDATION_QUEUED = "VALIDATION_QUEUED"
    VALIDATION_DECIDED = "VALIDATION_DECIDED"
    ERROR = "ERROR"


#: How much attention an event deserves. Matches the activity log's vocabulary
#: so a mirrored line keeps the same colour in both places.
Level = str


class ResearchJournal:
    """Durable, queryable campaign events."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "event_id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL, "
                "kind TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL, "
                "subject TEXT, detail TEXT NOT NULL, worker INTEGER, "
                "schema_version INTEGER NOT NULL, at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS events_campaign ON events(campaign_id, event_id)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def record(
        self,
        campaign_id: str,
        kind: EventKind,
        message: str,
        *,
        level: Level = "info",
        subject: str | None = None,
        detail: dict[str, Any] | None = None,
        worker: int | None = None,
    ) -> int:
        """Append one event. Returns its id, which is also its order."""
        with self._lock, closing(self._connect()) as db, db:
            cursor = db.execute(
                "INSERT INTO events (campaign_id, kind, level, message, subject, detail, "
                "worker, schema_version, at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    campaign_id,
                    str(kind),
                    level,
                    message[:1000],
                    subject,
                    json.dumps(detail or {}),
                    worker,
                    SCHEMA_VERSION,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
        return int(cursor.lastrowid or 0)

    def recent(
        self,
        campaign_id: str | None = None,
        *,
        since: int = 0,
        kinds: Sequence[EventKind] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Events newest first.

        ``since`` lets the interface poll for what it has not seen without
        re-fetching the whole stream, which is what makes a live view cheap.
        """
        clauses: builtins.list[str] = ["event_id > ?"]
        args: builtins.list[Any] = [int(since)]
        if campaign_id:
            clauses.append("campaign_id=?")
            args.append(campaign_id)
        if kinds:
            names = tuple(str(k) for k in kinds)
            clauses.append(f"kind IN ({','.join('?' * len(names))})")
            args.extend(names)
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT * FROM events WHERE {' AND '.join(clauses)} "
                "ORDER BY event_id DESC LIMIT ?",
                (*args, int(limit)),
            ).fetchall()
        return [_to_event(row) for row in rows]

    def since(self, campaign_id: str, event_id: int, *, limit: int = 200) -> list[dict[str, Any]]:
        """Events after ``event_id``, oldest first — the order they happened in."""
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM events WHERE campaign_id=? AND event_id > ? "
                "ORDER BY event_id LIMIT ?",
                (campaign_id, int(event_id), int(limit)),
            ).fetchall()
        return [_to_event(row) for row in rows]

    def latest_id(self, campaign_id: str | None = None) -> int:
        where, args = ("WHERE campaign_id=?", (campaign_id,)) if campaign_id else ("", ())
        with closing(self._connect()) as db:
            row = db.execute(f"SELECT MAX(event_id) AS n FROM events {where}", args).fetchone()
        return int(row["n"] or 0) if row else 0

    def counts(self, campaign_id: str) -> dict[str, int]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT kind, COUNT(*) AS n FROM events WHERE campaign_id=? GROUP BY kind",
                (campaign_id,),
            ).fetchall()
        return {str(row["kind"]): int(row["n"]) for row in rows}


def _to_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "event_id": int(row["event_id"]),
        "campaign_id": row["campaign_id"],
        "kind": row["kind"],
        "level": row["level"],
        "message": row["message"],
        "subject": row["subject"],
        "detail": json.loads(row["detail"]),
        "worker": row["worker"],
        "at": row["at"],
    }
