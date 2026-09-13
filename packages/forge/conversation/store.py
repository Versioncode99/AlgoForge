"""Where conversations live: SQLite, beside the other application databases.

A conversation is application state, not research. Deleting this database costs
the operator their dialogue history and nothing else — not one strategy, verdict,
experiment or holdout. That is the test the storage rules apply, and this passes
it in both directions: nothing here writes to a research store, and nothing here
is read by the judge.

**Search is over what was said, not over what was meant.** A `LIKE` scan across
turn text, which is exact, explicable and cannot surprise anybody. An embedding
index would find more, and it would also return a conversation because it is
*about the same sort of thing*, which in a system whose whole discipline is
distinguishing a claim from evidence is a way to lose that distinction inside a
search box. If the volume ever makes the scan slow, the fix is FTS5 over the same
literal text, not similarity.

**A turn is never rewritten.** Editing a message would change the record of what
the operator was told before a decision, which is the one thing a research
transcript is for. Titles and archive flags move; text does not.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge.conversation.models import (
    UNTITLED,
    Artifact,
    ArtifactKind,
    AttachedContext,
    ContextKind,
    Conversation,
    Outcome,
    Provenance,
    Role,
    ToolCall,
    Turn,
    derive_title,
    new_conversation_id,
    new_turn_id,
    now,
)

#: Bumped when the stored shape changes in a way a reader must know about.
SCHEMA_VERSION = 1

#: How much of a turn is kept in the conversation list. Enough to recognise the
#: thread, short enough that listing two hundred of them is not a page of prose.
PREVIEW_CHARS = 160


class ConversationError(Exception):
    """A conversation operation that cannot be honoured, with the reason."""


class ConversationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title           TEXT NOT NULL,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL,
                    archived        INTEGER NOT NULL DEFAULT 0,
                    schema_version  INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS turns (
                    turn_id         TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    position        INTEGER NOT NULL,
                    role            TEXT NOT NULL,
                    text            TEXT NOT NULL,
                    created_at      TEXT NOT NULL,
                    provenance      TEXT NOT NULL,
                    model           TEXT NOT NULL DEFAULT '',
                    tool_calls      TEXT NOT NULL DEFAULT '[]',
                    artifacts       TEXT NOT NULL DEFAULT '[]',
                    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
                );
                CREATE INDEX IF NOT EXISTS turns_by_conversation
                    ON turns(conversation_id, position);
                CREATE TABLE IF NOT EXISTS context (
                    conversation_id TEXT NOT NULL,
                    kind            TEXT NOT NULL,
                    ref             TEXT NOT NULL,
                    label           TEXT NOT NULL DEFAULT '',
                    attached_at     TEXT NOT NULL,
                    PRIMARY KEY (conversation_id, kind, ref)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    # ── conversations ────────────────────────────────────────────────────────

    def create(self, title: str = "", *, at: datetime | None = None) -> Conversation:
        """Start a thread. Untitled until the first message names it."""
        stamp = at or now()
        conversation_id = new_conversation_id(stamp, title)
        record = Conversation(
            conversation_id=conversation_id,
            title=title.strip() or UNTITLED,
            created_at=stamp,
            updated_at=stamp,
        )
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO conversations "
                "(conversation_id, title, created_at, updated_at, archived, schema_version) "
                "VALUES (?, ?, ?, ?, 0, ?)",
                (
                    record.conversation_id,
                    record.title,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    SCHEMA_VERSION,
                ),
            )
        return record

    def get(self, conversation_id: str) -> Conversation:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
            if row is None:
                raise ConversationError(f"no conversation '{conversation_id}'")
            return self._conversation(db, row)

    def list(
        self, *, include_archived: bool = False, limit: int = 200, query: str = ""
    ) -> list[Conversation]:
        """Threads, most recently active first.

        `query` matches the title *or* any turn's text. Matching only titles
        would make search useless on the conversations that most need it: the
        ones nobody renamed.
        """
        clauses = [] if include_archived else ["c.archived = 0"]
        params: list[Any] = []
        if query.strip():
            clauses.append(
                "(c.title LIKE ? ESCAPE '\\' OR EXISTS ("
                "SELECT 1 FROM turns t WHERE t.conversation_id = c.conversation_id "
                "AND t.text LIKE ? ESCAPE '\\'))"
            )
            pattern = f"%{_escape(query.strip())}%"
            params += [pattern, pattern]
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT * FROM conversations c {where} ORDER BY c.updated_at DESC LIMIT ?",
                (*params, max(1, min(int(limit), 1000))),
            ).fetchall()
            return [self._conversation(db, row) for row in rows]

    def rename(self, conversation_id: str, title: str) -> Conversation:
        cleaned = " ".join(title.split())
        if not cleaned:
            raise ConversationError("a title cannot be empty")
        with closing(self._connect()) as db, db:
            changed = db.execute(
                "UPDATE conversations SET title = ? WHERE conversation_id = ?",
                (cleaned, conversation_id),
            ).rowcount
        if not changed:
            raise ConversationError(f"no conversation '{conversation_id}'")
        return self.get(conversation_id)

    def set_archived(self, conversation_id: str, archived: bool) -> Conversation:
        """Archiving hides a thread from the list. It deletes nothing.

        Reversible on purpose: the destructive operation is `delete`, and a
        person reaching for "get this out of my way" should not have to choose
        between clutter and losing the record.
        """
        with closing(self._connect()) as db, db:
            changed = db.execute(
                "UPDATE conversations SET archived = ? WHERE conversation_id = ?",
                (1 if archived else 0, conversation_id),
            ).rowcount
        if not changed:
            raise ConversationError(f"no conversation '{conversation_id}'")
        return self.get(conversation_id)

    def delete(self, conversation_id: str) -> bool:
        """Remove a thread and its turns.

        Safe in a way deleting evidence would not be, and worth stating: a
        conversation references strategies, backtests and findings; it does not
        own them. Deleting the thread leaves every one of them exactly where it
        was.
        """
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM turns WHERE conversation_id = ?", (conversation_id,))
            db.execute("DELETE FROM context WHERE conversation_id = ?", (conversation_id,))
            removed = db.execute(
                "DELETE FROM conversations WHERE conversation_id = ?", (conversation_id,)
            ).rowcount
        return bool(removed)

    # ── turns ────────────────────────────────────────────────────────────────

    def append(
        self,
        conversation_id: str,
        role: Role,
        text: str,
        *,
        provenance: Provenance,
        model: str = "",
        tool_calls: tuple[ToolCall, ...] = (),
        artifacts: tuple[Artifact, ...] = (),
        at: datetime | None = None,
    ) -> Turn:
        """Add a message. The first user message names an untitled thread."""
        stamp = at or now()
        with closing(self._connect()) as db, db:
            head = db.execute(
                "SELECT title FROM conversations WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
            if head is None:
                raise ConversationError(f"no conversation '{conversation_id}'")
            position = int(
                db.execute(
                    "SELECT COUNT(*) FROM turns WHERE conversation_id = ?", (conversation_id,)
                ).fetchone()[0]
            )
            turn_id = new_turn_id(conversation_id, position)
            stored = tuple(
                artifact.identified(turn_id, index)
                for index, artifact in enumerate(artifacts)
            )
            db.execute(
                "INSERT INTO turns (turn_id, conversation_id, position, role, text, "
                "created_at, provenance, model, tool_calls, artifacts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    turn_id,
                    conversation_id,
                    position,
                    role.value,
                    text,
                    stamp.isoformat(),
                    provenance.value,
                    model,
                    json.dumps([call.model_dump(mode="json") for call in tool_calls]),
                    json.dumps([art.model_dump(mode="json") for art in stored]),
                ),
            )
            title = head["title"]
            if title == UNTITLED and role is Role.USER:
                title = derive_title(text)
                db.execute(
                    "UPDATE conversations SET title = ? WHERE conversation_id = ?",
                    (title, conversation_id),
                )
            db.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (stamp.isoformat(), conversation_id),
            )
        return Turn(
            turn_id=turn_id,
            conversation_id=conversation_id,
            role=role,
            text=text,
            created_at=stamp,
            provenance=provenance,
            model=model,
            tool_calls=tool_calls,
            artifacts=stored,
        )

    def turns(self, conversation_id: str, *, limit: int = 500) -> tuple[Turn, ...]:
        with closing(self._connect()) as db:
            if (
                db.execute(
                    "SELECT 1 FROM conversations WHERE conversation_id = ?", (conversation_id,)
                ).fetchone()
                is None
            ):
                raise ConversationError(f"no conversation '{conversation_id}'")
            rows = db.execute(
                "SELECT * FROM turns WHERE conversation_id = ? ORDER BY position LIMIT ?",
                (conversation_id, max(1, min(int(limit), 5000))),
            ).fetchall()
        return tuple(_turn(row) for row in rows)

    # ── context ──────────────────────────────────────────────────────────────

    def attach(
        self,
        conversation_id: str,
        kind: ContextKind,
        ref: str,
        label: str = "",
        *,
        at: datetime | None = None,
    ) -> AttachedContext:
        """Put a strategy, account or dataset in scope for one conversation.

        Idempotent by (kind, ref): attaching the same strategy twice is one
        attachment, not two identical chips the operator has to remove
        separately.
        """
        if not ref.strip():
            raise ConversationError(f"a {kind.value} attachment needs an identifier")
        stamp = at or now()
        with closing(self._connect()) as db, db:
            if (
                db.execute(
                    "SELECT 1 FROM conversations WHERE conversation_id = ?", (conversation_id,)
                ).fetchone()
                is None
            ):
                raise ConversationError(f"no conversation '{conversation_id}'")
            db.execute(
                "INSERT INTO context (conversation_id, kind, ref, label, attached_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(conversation_id, kind, ref) DO UPDATE SET label = excluded.label",
                (conversation_id, kind.value, ref.strip(), label, stamp.isoformat()),
            )
        return AttachedContext(kind=kind, ref=ref.strip(), label=label, attached_at=stamp)

    def detach(self, conversation_id: str, kind: ContextKind, ref: str) -> bool:
        with closing(self._connect()) as db, db:
            removed = db.execute(
                "DELETE FROM context WHERE conversation_id = ? AND kind = ? AND ref = ?",
                (conversation_id, kind.value, ref),
            ).rowcount
        return bool(removed)

    def context(self, conversation_id: str) -> tuple[AttachedContext, ...]:
        with closing(self._connect()) as db:
            return self._context(db, conversation_id)

    # ── reading ──────────────────────────────────────────────────────────────

    def _context(
        self, db: sqlite3.Connection, conversation_id: str
    ) -> tuple[AttachedContext, ...]:
        rows = db.execute(
            "SELECT kind, ref, label, attached_at FROM context "
            "WHERE conversation_id = ? ORDER BY attached_at, kind, ref",
            (conversation_id,),
        ).fetchall()
        return tuple(
            AttachedContext(
                kind=ContextKind(row["kind"]),
                ref=row["ref"],
                label=row["label"],
                attached_at=_stamp(row["attached_at"]),
            )
            for row in rows
        )

    def _conversation(self, db: sqlite3.Connection, row: sqlite3.Row) -> Conversation:
        conversation_id = row["conversation_id"]
        counted = db.execute(
            "SELECT COUNT(*) AS n FROM turns WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()["n"]
        last = db.execute(
            "SELECT text FROM turns WHERE conversation_id = ? ORDER BY position DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        preview = " ".join((last["text"] if last else "").split())[:PREVIEW_CHARS]
        return Conversation(
            conversation_id=conversation_id,
            title=row["title"],
            created_at=_stamp(row["created_at"]),
            updated_at=_stamp(row["updated_at"]),
            archived=bool(row["archived"]),
            turn_count=int(counted),
            context=self._context(db, conversation_id),
            last_message=preview,
        )

    def count(self, *, include_archived: bool = True) -> int:
        where = "" if include_archived else " WHERE archived = 0"
        with closing(self._connect()) as db:
            return int(db.execute(f"SELECT COUNT(*) FROM conversations{where}").fetchone()[0])


def _escape(value: str) -> str:
    """Make a search term literal.

    Without this a query containing `%` matches every conversation, which reads
    as a broken search rather than as an operator having typed a wildcard.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _stamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _turn(row: sqlite3.Row) -> Turn:
    return Turn(
        turn_id=row["turn_id"],
        conversation_id=row["conversation_id"],
        role=Role(row["role"]),
        text=row["text"],
        created_at=_stamp(row["created_at"]),
        provenance=Provenance(row["provenance"]),
        model=row["model"],
        tool_calls=tuple(
            ToolCall(
                name=call["name"],
                arguments=call.get("arguments", {}),
                outcome=Outcome(call["outcome"]),
                reason=call.get("reason", ""),
                duration_ms=int(call.get("duration_ms", 0)),
                result_ref=call.get("result_ref", ""),
            )
            for call in json.loads(row["tool_calls"])
        ),
        artifacts=tuple(
            Artifact(
                artifact_id=art.get("artifact_id", ""),
                kind=ArtifactKind(art["kind"]),
                title=art["title"],
                refs=art.get("refs", {}),
                provenance=Provenance(art.get("provenance", Provenance.DETERMINISTIC.value)),
            )
            for art in json.loads(row["artifacts"])
        ),
    )
