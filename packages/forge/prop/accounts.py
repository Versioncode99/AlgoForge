"""Where a funded account's rules and its recorded state live.

Two tables and one deliberate omission.

`accounts` holds the contract the operator configured. `snapshots` holds what
they recorded about the account at a point in time — balance, equity, the open
book. The omission is any code that *invents* a snapshot: this build has no
broker connector, so a state exists because a person entered it or because
`state_from_trades` derived it from trades in the ledger. A store that quietly
manufactured a plausible balance would put fiction into the one panel whose
entire job is to be trusted.

Snapshots are append-only and timestamped. An account's history is what it was
worth at each moment somebody looked, and overwriting a row would destroy the
only record of how close the account came to its floor last Tuesday.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge.contracts.hashing import stable_id
from forge.prop.account import AccountRules, AccountState

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PropAccount:
    account_id: str
    name: str
    rules: AccountRules
    created_at: datetime
    updated_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "name": self.name,
            "rules": self.rules.model_dump(mode="json"),
            "rules_id": self.rules.rules_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class PropAccountStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS accounts ("
                "account_id TEXT PRIMARY KEY, name TEXT NOT NULL, rules TEXT NOT NULL, "
                "schema_version INTEGER NOT NULL, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS snapshots ("
                "snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "account_id TEXT NOT NULL, as_of TEXT NOT NULL, "
                "source TEXT NOT NULL, state TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS snapshots_by_account "
                "ON snapshots(account_id, as_of DESC)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS account_state (key TEXT PRIMARY KEY, value TEXT)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    # ── accounts ─────────────────────────────────────────────────────────────
    def create(self, rules: AccountRules) -> PropAccount:
        now = datetime.now(UTC)
        account = PropAccount(
            account_id=stable_id("propacct", {"name": rules.name, "at": now.isoformat()}),
            name=rules.name,
            rules=rules,
            created_at=now,
            updated_at=now,
        )
        self._write(account)
        # The first account created becomes the selected one. Otherwise the
        # operator configures a contract and lands on a screen telling them no
        # account is selected, holding the only account there is.
        if self.selected_id() is None:
            self.select(account.account_id)
        return account

    def update(self, account_id: str, rules: AccountRules) -> PropAccount:
        existing = self.get(account_id)
        if existing is None:
            raise KeyError(f"no prop account '{account_id}'")
        account = PropAccount(
            account_id=account_id,
            name=rules.name,
            rules=rules,
            created_at=existing.created_at,
            updated_at=datetime.now(UTC),
        )
        self._write(account)
        return account

    def _write(self, account: PropAccount) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(account_id) DO UPDATE SET name=excluded.name, "
                "rules=excluded.rules, schema_version=excluded.schema_version, "
                "updated_at=excluded.updated_at",
                (
                    account.account_id,
                    account.name,
                    json.dumps(account.rules.model_dump(mode="json")),
                    SCHEMA_VERSION,
                    account.created_at.isoformat(),
                    account.updated_at.isoformat(),
                ),
            )

    def get(self, account_id: str) -> PropAccount | None:
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT * FROM accounts WHERE account_id=?", (account_id,)
            ).fetchone()
        return None if row is None else _to_account(dict(row))

    def all_accounts(self) -> list[PropAccount]:
        with closing(self._connect()) as db, db:
            rows = db.execute("SELECT * FROM accounts ORDER BY updated_at DESC").fetchall()
        return [_to_account(dict(row)) for row in rows]

    def delete(self, account_id: str) -> bool:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            removed = db.execute(
                "DELETE FROM accounts WHERE account_id=?", (account_id,)
            ).rowcount
            if removed:
                db.execute("DELETE FROM snapshots WHERE account_id=?", (account_id,))
                db.execute(
                    "DELETE FROM account_state WHERE key='selected' AND value=?",
                    (account_id,),
                )
        return bool(removed)

    # ── which one the operator is looking at ─────────────────────────────────
    def selected_id(self) -> str | None:
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT value FROM account_state WHERE key='selected'"
            ).fetchone()
        return None if row is None else str(row["value"])

    def select(self, account_id: str) -> None:
        if self.get(account_id) is None:
            raise KeyError(f"no prop account '{account_id}'")
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO account_state VALUES ('selected', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (account_id,),
            )

    def selected(self) -> PropAccount | None:
        account_id = self.selected_id()
        return None if account_id is None else self.get(account_id)

    # ── recorded state ───────────────────────────────────────────────────────
    def record(self, account_id: str, state: AccountState, *, source: str) -> AccountState:
        """Append a snapshot. `source` says where the numbers came from.

        Required rather than defaulted: "operator" and "derived from strategy
        trades" are different claims about the same row, and a reader looking at
        a drawdown six months later needs to know which one they are reading.
        """
        if self.get(account_id) is None:
            raise KeyError(f"no prop account '{account_id}'")
        if not source.strip():
            raise ValueError("a snapshot must say where its numbers came from")
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO snapshots (account_id, as_of, source, state) VALUES (?, ?, ?, ?)",
                (
                    account_id,
                    state.as_of.astimezone(UTC).isoformat(),
                    source.strip()[:200],
                    json.dumps(state.model_dump(mode="json")),
                ),
            )
        return state

    def latest(self, account_id: str) -> tuple[AccountState, str] | None:
        """The most recent snapshot and its source, or `None` if none was taken.

        `None` rather than a zeroed state. An account nobody has recorded is not
        an account sitting flat at its starting balance, and rendering it as one
        would show a comfortable drawdown buffer for a position that might be
        open.
        """
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT state, source FROM snapshots WHERE account_id=? "
                "ORDER BY as_of DESC, snapshot_id DESC LIMIT 1",
                (account_id,),
            ).fetchone()
        if row is None:
            return None
        return AccountState.model_validate(json.loads(row["state"])), str(row["source"])

    def history(self, account_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with closing(self._connect()) as db, db:
            rows = db.execute(
                "SELECT as_of, source, state FROM snapshots WHERE account_id=? "
                "ORDER BY as_of DESC, snapshot_id DESC LIMIT ?",
                (account_id, max(1, min(limit, 1000))),
            ).fetchall()
        return [
            {"as_of": row["as_of"], "source": row["source"], "state": json.loads(row["state"])}
            for row in rows
        ]


def _to_account(row: dict[str, Any]) -> PropAccount:
    return PropAccount(
        account_id=str(row["account_id"]),
        name=str(row["name"]),
        rules=AccountRules.model_validate(json.loads(row["rules"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )
