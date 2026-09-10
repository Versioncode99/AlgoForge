"""Who did what, on what, with what result — and what they were refused.

The activity log already records that things happened. This is a different
record with a different job: it answers *accountability* questions rather than
progress ones. What happened, why did it happen, who or what initiated it, and
where a person's decision sat in the chain.

Three properties it needs and the activity log does not have.

**Actor and ruling on every row.** A record that an action ran, without whether
a human or an agent ran it and what the permission policy said, cannot answer
the question this log exists for. `ruling` is stored even when it was `allow`,
because "the policy permitted this" is the fact that makes an autonomous run
auditable rather than merely logged.

**Refusals are records.** An action that was denied, or that is sitting in the
approval queue, is written here. A log holding only what succeeded would present
an agent that tried forty times to raise its own limits as an agent that did
nothing.

**Append-only.** There is no update and no delete. A history that can be edited
is not a history, and this is the table a person reads when they want to know
whether the system did something they did not ask for.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel

SCHEMA_VERSION = 1

#: Arguments and results are truncated to this many characters before storage.
#: A log that stores a megabyte of backtest output per row stops being readable
#: and starts being a second copy of the artifacts.
MAX_PAYLOAD = 4000


class Outcome(StrEnum):
    OK = "ok"
    ERROR = "error"
    #: The permission policy refused it outright.
    DENIED = "denied"
    #: Held for a person. Terminal for this row; the approval's own resolution
    #: is a separate row that names it.
    PENDING_APPROVAL = "pending_approval"
    #: A deterministic control refused it — the risk engine, the pre-trade gate,
    #: the prop rule engine. Distinct from DENIED, which is about permission:
    #: this is about the thing being unsafe rather than the caller being
    #: unauthorised, and conflating them makes both unreadable.
    BLOCKED = "blocked"


class AuditEntry(FrozenModel):
    entry_id: str
    at: datetime
    actor: str
    #: Which agent or surface, when the actor was AI or a particular screen.
    origin: str = ""
    mode: str = ""
    stance: str = ""
    action: str
    arguments: str = ""
    ruling: str = ""
    #: Why the policy ruled as it did, verbatim from `forge.modes.permissions`.
    ruling_reason: str = ""
    outcome: Outcome
    result: str = ""
    error: str = ""
    #: The approval this row is waiting on, or the one that authorised it.
    approval_id: str = ""
    #: Evidence the action rested on: a verdict, a clearance, a run id.
    references: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS audit ("
                "entry_id TEXT PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL, "
                "origin TEXT, mode TEXT, stance TEXT, action TEXT NOT NULL, "
                "arguments TEXT, ruling TEXT, ruling_reason TEXT, outcome TEXT NOT NULL, "
                "result TEXT, error TEXT, approval_id TEXT, references_json TEXT, "
                "schema_version INTEGER NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS audit_by_time ON audit(at DESC)")
            db.execute("CREATE INDEX IF NOT EXISTS audit_by_action ON audit(action)")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def record(
        self,
        *,
        actor: str,
        action: str,
        outcome: Outcome,
        origin: str = "",
        mode: str = "",
        stance: str = "",
        arguments: Any = None,
        ruling: str = "",
        ruling_reason: str = "",
        result: Any = None,
        error: str = "",
        approval_id: str = "",
        references: tuple[str, ...] = (),
        at: datetime | None = None,
    ) -> AuditEntry:
        moment = at or datetime.now(UTC)
        entry = AuditEntry(
            entry_id=stable_id(
                "audit",
                {"at": moment.isoformat(), "action": action, "actor": actor,
                 "arguments": _render(arguments)},
            ),
            at=moment,
            actor=actor,
            origin=origin,
            mode=mode,
            stance=stance,
            action=action,
            arguments=_render(arguments),
            ruling=ruling,
            ruling_reason=ruling_reason[:MAX_PAYLOAD],
            outcome=outcome,
            result=_render(result),
            error=error[:MAX_PAYLOAD],
            approval_id=approval_id,
            references=references,
        )
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO audit VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.entry_id, entry.at.isoformat(), entry.actor, entry.origin,
                    entry.mode, entry.stance, entry.action, entry.arguments,
                    entry.ruling, entry.ruling_reason, entry.outcome.value, entry.result,
                    entry.error, entry.approval_id, json.dumps(list(entry.references)),
                    SCHEMA_VERSION,
                ),
            )
        return entry

    def recent(
        self,
        limit: int = 100,
        *,
        actor: str | None = None,
        action: str | None = None,
        outcome: Outcome | None = None,
    ) -> list[AuditEntry]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if actor:
            clauses.append("actor=?")
            parameters.append(actor)
        if action:
            clauses.append("action=?")
            parameters.append(action)
        if outcome:
            clauses.append("outcome=?")
            parameters.append(outcome.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(max(1, min(limit, 2000)))
        with closing(self._connect()) as db, db:
            rows = db.execute(
                f"SELECT * FROM audit{where} ORDER BY at DESC, entry_id DESC LIMIT ?",
                parameters,
            ).fetchall()
        return [_to_entry(dict(row)) for row in rows]

    def get(self, entry_id: str) -> AuditEntry | None:
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT * FROM audit WHERE entry_id=?", (entry_id,)).fetchone()
        return None if row is None else _to_entry(dict(row))

    def count(self) -> int:
        with closing(self._connect()) as db, db:
            return int(db.execute("SELECT COUNT(*) FROM audit").fetchone()[0])

    def summary(self) -> dict[str, int]:
        """Counts by outcome, for the header of the audit screen."""
        with closing(self._connect()) as db, db:
            rows = db.execute(
                "SELECT outcome, COUNT(*) AS n FROM audit GROUP BY outcome"
            ).fetchall()
        return {str(row["outcome"]): int(row["n"]) for row in rows}


def _render(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:MAX_PAYLOAD]
    try:
        return json.dumps(value, default=str)[:MAX_PAYLOAD]
    except (TypeError, ValueError):
        return str(value)[:MAX_PAYLOAD]


def _to_entry(row: dict[str, Any]) -> AuditEntry:
    return AuditEntry(
        entry_id=str(row["entry_id"]),
        at=datetime.fromisoformat(str(row["at"])),
        actor=str(row["actor"]),
        origin=str(row["origin"] or ""),
        mode=str(row["mode"] or ""),
        stance=str(row["stance"] or ""),
        action=str(row["action"]),
        arguments=str(row["arguments"] or ""),
        ruling=str(row["ruling"] or ""),
        ruling_reason=str(row["ruling_reason"] or ""),
        outcome=Outcome(str(row["outcome"])),
        result=str(row["result"] or ""),
        error=str(row["error"] or ""),
        approval_id=str(row["approval_id"] or ""),
        references=tuple(json.loads(row["references_json"] or "[]")),
    )
