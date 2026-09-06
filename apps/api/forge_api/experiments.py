"""Persistent attempt ledger; selection uses development evidence only."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from forge.contracts.hashing import stable_id

POLICIES = (
    "literature",
    "momentum",
    "reversion",
    "volatility",
    "neighbourhood",
    "ablation",
    "exploration",
    "replication",
)


class Experiments:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS attempts "
                "(id TEXT PRIMARY KEY, scope TEXT, template TEXT, payload TEXT)"
            )

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=15)

    def reserve(self, scope: str, template: str, parameters: dict[str, float]) -> str | None:
        key = stable_id("experiment", {"scope": scope, "template": template, "params": parameters})
        with self.connect() as db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO attempts VALUES (?, ?, ?, ?)",
                (
                    key,
                    scope,
                    template,
                    json.dumps(
                        {
                            "id": key,
                            "template": template,
                            "parameters": parameters,
                            "status": "reserved",
                        }
                    ),
                ),
            ).rowcount
        return key if inserted else None

    def finish(self, key: str, **fields: Any) -> None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM attempts WHERE id=?", (key,)).fetchone()
            if row:
                payload = {**json.loads(row[0]), **fields}
                db.execute("UPDATE attempts SET payload=? WHERE id=?", (json.dumps(payload), key))

    def recent(self, scope: str, limit: int = 80) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM attempts WHERE scope=? ORDER BY rowid DESC LIMIT ?",
                (scope, limit),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def count(self, scope: str) -> int:
        with self.connect() as db:
            return int(
                db.execute("SELECT COUNT(*) FROM attempts WHERE scope=?", (scope,)).fetchone()[0]
            )
