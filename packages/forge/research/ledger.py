from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from forge.contracts.models import FrozenModel


class HoldoutConsumption(FrozenModel):
    lineage: str
    split_id: str
    consumed_at: datetime
    result_id: str | None = None


class ResearchLedger:
    """Persistent burn-once holdout ledger.

    A unique primary key on lineage is the authority. The application cannot
    reset a spent holdout by restarting or by asking a model to reconsider it.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS holdout_consumptions (
                    lineage TEXT PRIMARY KEY,
                    split_id TEXT NOT NULL,
                    consumed_at TEXT NOT NULL,
                    result_id TEXT
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def consume(
        self,
        lineage: str,
        split_id: str,
        *,
        consumed_at: datetime | None = None,
    ) -> HoldoutConsumption:
        stamp = consumed_at or datetime.now(UTC)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO holdout_consumptions VALUES (?, ?, ?, NULL)",
                    (lineage, split_id, stamp.isoformat()),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("HOLDOUT_ALREADY_CONSUMED") from exc
        return HoldoutConsumption(lineage=lineage, split_id=split_id, consumed_at=stamp)

    def attach_result(self, lineage: str, result_id: str) -> HoldoutConsumption:
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE holdout_consumptions SET result_id = ? WHERE lineage = ?",
                (result_id, lineage),
            ).rowcount
            if changed != 1:
                raise KeyError(lineage)
        item = self.get(lineage)
        if item is None:  # defensive; the row was just updated
            raise KeyError(lineage)
        return item

    def get(self, lineage: str) -> HoldoutConsumption | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM holdout_consumptions WHERE lineage = ?", (lineage,)
            ).fetchone()
        return None if row is None else HoldoutConsumption.model_validate(dict(row))

    def list_consumptions(self) -> list[HoldoutConsumption]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM holdout_consumptions ORDER BY consumed_at DESC"
            ).fetchall()
        return [HoldoutConsumption.model_validate(dict(row)) for row in rows]
