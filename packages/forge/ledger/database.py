from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from forge.contracts.models import DecisionRecord, Preregistration, RunRecord


class LedgerDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS preregistrations (
                preregistration_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL UNIQUE,
                frozen_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                preregistration_id TEXT NOT NULL REFERENCES preregistrations(preregistration_id),
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decisions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id TEXT NOT NULL UNIQUE,
                record_hash TEXT NOT NULL UNIQUE,
                previous_hash TEXT,
                payload TEXT NOT NULL
            );
            CREATE TRIGGER IF NOT EXISTS runs_after_preregistration
            BEFORE INSERT ON runs
            BEGIN
                SELECT CASE WHEN NEW.created_at <= (
                    SELECT frozen_at FROM preregistrations
                    WHERE preregistration_id = NEW.preregistration_id
                ) THEN RAISE(ABORT, 'run predates preregistration') END;
            END;
            """
        )
        self.connection.commit()

    def add_preregistration(self, item: Preregistration) -> None:
        self.connection.execute(
            "INSERT INTO preregistrations VALUES (?, ?, ?, ?)",
            (
                item.preregistration_id,
                item.content_hash,
                item.frozen_at.isoformat(),
                item.model_dump_json(),
            ),
        )
        self.connection.commit()

    def add_run(self, run: RunRecord) -> None:
        known = self.connection.execute(
            "SELECT content_hash FROM preregistrations WHERE preregistration_id = ?",
            (run.preregistration_id,),
        ).fetchone()
        if known is None:
            raise ValueError("REJECTED_NO_PREREG")
        if known["content_hash"] != run.preregistration_hash:
            raise ValueError("preregistration hash mismatch")
        self.connection.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?)",
            (run.run_id, run.preregistration_id, run.created_at.isoformat(), run.model_dump_json()),
        )
        self.connection.commit()

    def list_runs(self) -> list[RunRecord]:
        rows = self.connection.execute(
            "SELECT payload FROM runs ORDER BY created_at DESC"
        ).fetchall()
        return [RunRecord.model_validate(json.loads(row["payload"])) for row in rows]

    def get_run(self, run_id: str) -> RunRecord | None:
        row = self.connection.execute(
            "SELECT payload FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return None if row is None else RunRecord.model_validate(json.loads(row["payload"]))

    def append_decision(self, decision: DecisionRecord) -> None:
        latest = self.connection.execute(
            "SELECT record_hash FROM decisions ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        expected = None if latest is None else latest["record_hash"]
        if decision.previous_hash != expected:
            raise ValueError("decision chain mismatch")
        self.connection.execute(
            """INSERT INTO decisions(decision_id, record_hash, previous_hash, payload)
            VALUES (?, ?, ?, ?)""",
            (
                decision.decision_id,
                decision.record_hash,
                decision.previous_hash,
                decision.model_dump_json(),
            ),
        )
        self.connection.commit()

    def verify_decision_chain(self) -> bool:
        rows = self.connection.execute("SELECT payload FROM decisions ORDER BY sequence").fetchall()
        previous: str | None = None
        for row in rows:
            decision = DecisionRecord.model_validate(json.loads(row["payload"]))
            if decision.previous_hash != previous:
                return False
            previous = decision.record_hash
        return True

    def close(self) -> None:
        self.connection.close()
