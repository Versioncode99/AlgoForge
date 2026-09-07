"""The experiment record: what was tried, why, what came of it, and what it came from.

This began as an attempt-dedup table — four columns, a JSON blob, and a hash
that refused byte-identical repeats. That is still its cheapest and most-used
job, and `reserve` behaves exactly as it did.

What it could not do was answer "where did this strategy come from?". There were
no parent/child edges, so a candidate produced by the `neighbourhood` policy —
which literally starts from a previous attempt and moves one parameter — had no
recorded connection to the attempt it came from. The lineage existed in the
engine's control flow and nowhere on disk.

Every field here is one the engine already knows at the moment it writes. None
is a placeholder waiting to be filled in later: an experiment record that
carries empty columns for `hypothesis` and `seed` is worse than one that does
not claim to have them, because it looks like provenance without being any.

Schema changes are applied by `_migrate` rather than a version table. Columns
are only ever added, existing rows keep their values, and an older database
opens without ceremony — the workspace holds real research and must not need
to be thrown away to gain a column.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
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

# Added after the original four. Every one is written from something the engine
# has in hand; see the module docstring on why there are no speculative columns.
_COLUMNS: tuple[tuple[str, str], ...] = (
    ("parent_id", "TEXT"),
    ("policy", "TEXT"),
    ("family", "TEXT"),
    ("hypothesis", "TEXT"),
    ("dataset", "TEXT"),
    ("data_version", "TEXT"),
    ("code_hash", "TEXT"),
    # Per-trade Sharpe of the development run. Recorded for every candidate,
    # winners and losers alike, because the Deflated Sharpe needs the spread
    # of the whole search and a search remembered only through its successes
    # has a variance that flatters every one of them.
    ("development_sharpe", "REAL"),
    # Frozen before the candidate is backtested, so the judge can tell a
    # pre-registered hypothesis from one written after the numbers came in.
    ("preregistration_id", "TEXT"),
    ("preregistration_hash", "TEXT"),
    ("seed", "INTEGER"),
    ("strategy_id", "TEXT"),
    ("backtest_id", "TEXT"),
    ("verdict_id", "TEXT"),
    ("evidence_id", "TEXT"),
    ("failure_class", "TEXT"),
    ("failure_gate", "TEXT"),
    ("failure_reason", "TEXT"),
    ("status", "TEXT"),
    ("created_at", "TEXT"),
    ("finished_at", "TEXT"),
)

# Guards the ancestor walk against a cycle. Nothing should be able to create
# one, but a corrupted parent_id must not hang a worker thread.
_MAX_LINEAGE_DEPTH = 512


class Experiments:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS attempts "
                "(id TEXT PRIMARY KEY, scope TEXT, template TEXT, payload TEXT)"
            )
            self._migrate(db)

    @staticmethod
    def _migrate(db: sqlite3.Connection) -> None:
        """Add any column this version knows about that the file does not have."""
        existing = {str(row[1]) for row in db.execute("PRAGMA table_info(attempts)")}
        for name, kind in _COLUMNS:
            if name not in existing:
                db.execute(f"ALTER TABLE attempts ADD COLUMN {name} {kind}")
        db.execute("CREATE INDEX IF NOT EXISTS attempts_parent ON attempts (parent_id)")
        db.execute("CREATE INDEX IF NOT EXISTS attempts_scope ON attempts (scope)")

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=15)

    def reserve(
        self,
        scope: str,
        template: str,
        parameters: dict[str, float],
        *,
        parent_id: str | None = None,
        policy: str | None = None,
        family: str | None = None,
        hypothesis: str | None = None,
        dataset: str | None = None,
        data_version: str | None = None,
        seed: int | None = None,
        preregistration_id: str | None = None,
        preregistration_hash: str | None = None,
    ) -> str | None:
        """Claim this parameter set, or decline because it is already claimed.

        The identity deliberately excludes the provenance arguments: two routes
        to the same configuration are the *same experiment*, and recording it
        twice under different parents would inflate the trial count the
        Deflated Sharpe deflates against.
        """
        key = stable_id("experiment", {"scope": scope, "template": template, "params": parameters})
        payload = {
            "id": key,
            "template": template,
            "parameters": parameters,
            "status": "reserved",
        }
        with closing(self.connect()) as db, db:
            inserted = db.execute(
                "INSERT OR IGNORE INTO attempts "
                "(id, scope, template, payload, parent_id, policy, family, hypothesis, "
                "dataset, data_version, seed, status, created_at, "
                "preregistration_id, preregistration_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    scope,
                    template,
                    json.dumps(payload),
                    parent_id,
                    policy,
                    family,
                    hypothesis,
                    dataset,
                    data_version,
                    seed,
                    "reserved",
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    preregistration_id,
                    preregistration_hash,
                ),
            ).rowcount
        return key if inserted else None

    def finish(self, key: str, **fields: Any) -> None:
        """Merge an outcome into the record.

        Keys matching a real column are written there so they can be queried;
        everything else stays in the payload blob, which is what keeps this
        backwards compatible with callers that invented their own field names.
        """
        columns = {name for name, _ in _COLUMNS}
        promoted = {name: value for name, value in fields.items() if name in columns}
        if fields.get("status") is not None and "finished_at" not in promoted:
            promoted["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT payload FROM attempts WHERE id=?", (key,)).fetchone()
            if row is None:
                return
            payload = {**json.loads(row[0]), **fields}
            assignments = ", ".join(f"{name}=?" for name in promoted)
            statement = "UPDATE attempts SET payload=?" + (
                f", {assignments} WHERE id=?" if promoted else " WHERE id=?"
            )
            db.execute(statement, (json.dumps(payload), *promoted.values(), key))

    # ── reads ────────────────────────────────────────────────────────────────
    def recent(self, scope: str, limit: int = 80) -> list[dict[str, Any]]:
        with closing(self.connect()) as db, db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT * FROM attempts WHERE scope=? ORDER BY rowid DESC LIMIT ?",
                (scope, limit),
            ).fetchall()
        return [_merge(dict(row)) for row in rows]

    def get(self, key: str) -> dict[str, Any] | None:
        with closing(self.connect()) as db, db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM attempts WHERE id=?", (key,)).fetchone()
        return _merge(dict(row)) if row else None

    def sharpes(self, scope: str, limit: int = 5000) -> tuple[float, ...]:
        """Every recorded development Sharpe in this scope, newest first.

        This is what ``V[SR]`` should be estimated from. Measuring the spread
        across a nine-point neighbourhood and then deflating against a
        thousand-trial count uses two different searches for the two halves of
        one statistic: neighbouring parameters on one template produce highly
        correlated Sharpes, so their variance understates the real spread and
        the best-of-N hurdle comes out too low.
        """
        with closing(self.connect()) as db, db:
            rows = db.execute(
                "SELECT development_sharpe FROM attempts "
                "WHERE scope=? AND development_sharpe IS NOT NULL "
                "ORDER BY rowid DESC LIMIT ?",
                (scope, limit),
            ).fetchall()
        return tuple(float(row[0]) for row in rows)

    def count(self, scope: str) -> int:
        with closing(self.connect()) as db, db:
            return int(
                db.execute("SELECT COUNT(*) FROM attempts WHERE scope=?", (scope,)).fetchone()[0]
            )

    # ── lineage ──────────────────────────────────────────────────────────────
    def ancestors(self, key: str) -> list[dict[str, Any]]:
        """From the immediate parent back to the root, in that order."""
        chain: list[dict[str, Any]] = []
        seen = {key}
        current = self.get(key)
        depth = 0
        while current and current.get("parent_id") and depth < _MAX_LINEAGE_DEPTH:
            parent_id = str(current["parent_id"])
            if parent_id in seen:
                break
            parent = self.get(parent_id)
            if parent is None:
                break
            chain.append(parent)
            seen.add(parent_id)
            current = parent
            depth += 1
        return chain

    def children(self, key: str) -> list[dict[str, Any]]:
        with closing(self.connect()) as db, db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT * FROM attempts WHERE parent_id=? ORDER BY rowid", (key,)
            ).fetchall()
        return [_merge(dict(row)) for row in rows]

    def descendants(self, key: str) -> list[dict[str, Any]]:
        """Every experiment derived from this one, breadth-first."""
        found: list[dict[str, Any]] = []
        seen = {key}
        frontier = [key]
        while frontier and len(found) < 10_000:
            nxt: list[str] = []
            for parent in frontier:
                for child in self.children(parent):
                    child_id = str(child["id"])
                    if child_id in seen:
                        continue
                    seen.add(child_id)
                    found.append(child)
                    nxt.append(child_id)
            frontier = nxt
        return found

    def lineage(self, key: str) -> dict[str, Any]:
        """The whole line through one experiment: where it came from and what followed."""
        subject = self.get(key)
        if subject is None:
            return {"experiment": None, "ancestors": [], "children": [], "descendants": []}
        return {
            "experiment": subject,
            "ancestors": self.ancestors(key),
            "children": self.children(key),
            "descendants": self.descendants(key),
        }

    def roots(self, scope: str, limit: int = 200) -> list[dict[str, Any]]:
        """Experiments with no parent — where each line of enquiry started."""
        with closing(self.connect()) as db, db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT * FROM attempts WHERE scope=? AND (parent_id IS NULL OR parent_id='') "
                "ORDER BY rowid DESC LIMIT ?",
                (scope, limit),
            ).fetchall()
        return [_merge(dict(row)) for row in rows]

    def iter_scope(self, scope: str) -> Iterator[dict[str, Any]]:
        with closing(self.connect()) as db, db:
            db.row_factory = sqlite3.Row
            for row in db.execute(
                "SELECT * FROM attempts WHERE scope=? ORDER BY rowid", (scope,)
            ):
                yield _merge(dict(row))


def _merge(row: dict[str, Any]) -> dict[str, Any]:
    """Column values win over the payload blob; the blob supplies the rest.

    Callers written against the original schema read `template`, `parameters`
    and `development_net` off the payload, so both have to be present in one
    dictionary.
    """
    payload = row.pop("payload", "{}")
    try:
        blob = json.loads(payload) if isinstance(payload, str) else {}
    except json.JSONDecodeError:
        blob = {}
    merged = {**blob, **{name: value for name, value in row.items() if value is not None}}
    merged["id"] = row.get("id", blob.get("id"))
    return merged
