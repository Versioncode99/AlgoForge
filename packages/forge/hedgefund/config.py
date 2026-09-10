"""The fund's own configuration: capital, limits, constraints, universe, restrictions.

This is the state an AI actor must never be able to change, held in one place so
that "protected" is a property of a store rather than a habit spread across
handlers. Everything here is written by a person through an action marked
`protected=True`, which `forge.modes.permissions` denies to AI in every mode and
every stance.

**Why it is not settings.** `forge_api.settings_store` holds preferences —
providers, appearance, which model to call. Losing it costs convenience. Losing
*this* changes what the system is permitted to do, and the two should not share a
file, a schema version or a backup policy.

**Defaults are conservative and finite.** A fresh installation starts with a
small capital base, tight limits, an empty universe and the kill switch *on*
(that is, `enabled=True` meaning trading is permitted but nothing is tradable
yet, because the universe is empty). Nothing can trade until a person has said
what may be traded, which is the correct starting position for a system that can
place orders.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge.contracts.models import FrozenModel
from forge.portfolio.models import Constraints, Instrument
from forge.risk.portfolio import PortfolioLimits

SCHEMA_VERSION = 1


class Restriction(FrozenModel):
    """One symbol nobody may trade, and why.

    The reason is required. A restricted list without reasons becomes a list
    nobody dares remove anything from, because nobody remembers why it is there.
    """

    symbol: str
    reason: str
    added_at: datetime


class FundConfig(FrozenModel):
    """Everything the fund's deterministic layers read."""

    capital: float = 1_000_000.0
    limits: PortfolioLimits = PortfolioLimits()
    constraints: Constraints = Constraints()
    universe: tuple[Instrument, ...] = ()
    restricted: tuple[Restriction, ...] = ()
    #: Starting cash for the simulated book. Separate from `capital`, which is
    #: the denominator for weights: a book can be levered, and conflating the two
    #: makes every exposure figure wrong the moment it is.
    starting_cash: float = 1_000_000.0
    #: Ticks of adverse slippage the local simulator applies. Stated here rather
    #: than hidden in the adapter so the assumption is visible where the results
    #: are read.
    slippage_ticks: float = 1.0
    updated_at: datetime | None = None

    @property
    def instruments(self) -> dict[str, Instrument]:
        return {instrument.symbol: instrument for instrument in self.universe}

    @property
    def restricted_map(self) -> dict[str, str]:
        return {entry.symbol: entry.reason for entry in self.restricted}

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class FundConfigStore:
    """One row, versioned, rewritten whole.

    Whole-document rather than per-field, because these values constrain each
    other: a leverage ceiling raised without the gross ceiling is a half-applied
    change, and a store that permits one is a store that will eventually hold
    one.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS fund_config ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL, "
                "schema_version INTEGER NOT NULL, updated_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS fund_config_history ("
                "revision INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL, "
                "changed_by TEXT NOT NULL, note TEXT, at TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def get(self) -> FundConfig:
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT payload FROM fund_config WHERE id=1").fetchone()
        if row is None:
            return FundConfig()
        return FundConfig.model_validate(json.loads(row["payload"]))

    def save(self, config: FundConfig, *, changed_by: str, note: str = "") -> FundConfig:
        """Write the configuration, keeping the previous one.

        `changed_by` is required. This table is the record of who widened a
        limit, and a revision with no author is the row somebody will need and
        not find.
        """
        if not changed_by.strip():
            raise ValueError("a configuration change must name who made it")
        now = datetime.now(UTC)
        stamped = config.model_copy(update={"updated_at": now})
        payload = json.dumps(stamped.model_dump(mode="json"))
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO fund_config VALUES (1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, "
                "schema_version=excluded.schema_version, updated_at=excluded.updated_at",
                (payload, SCHEMA_VERSION, now.isoformat()),
            )
            db.execute(
                "INSERT INTO fund_config_history (payload, changed_by, note, at) "
                "VALUES (?, ?, ?, ?)",
                (payload, changed_by.strip()[:120], note.strip()[:500], now.isoformat()),
            )
        return stamped

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._connect()) as db, db:
            rows = db.execute(
                "SELECT revision, changed_by, note, at FROM fund_config_history "
                "ORDER BY revision DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [dict(row) for row in rows]
