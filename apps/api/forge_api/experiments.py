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
    # Which research programme claimed this.
    #
    # Deliberately a column and not part of `scope`. The scope is what the
    # *judge* counts trials over, and every trial run against the same data is
    # a trial whoever ran it — deflating a campaign's Sharpe against only its
    # own attempts would understate the search the number came out of. But a
    # *claim* has to be per campaign: two campaigns must be able to establish
    # evidence independently, and sharing one duplicate namespace meant the
    # second one was refused every configuration the first had touched.
    ("campaign_id", "TEXT"),
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

# Fixed when the experiment is reserved, and not an outcome of running it.
#
# `finish` records what happened; provenance records what was claimed and where
# it came from, and the judge reads that provenance as evidence. G1 asks whether
# the pre-registered hypothesis still describes what is being judged, and it
# answers by comparing against `preregistration_hash` on this row -- so a
# `finish` able to write that column is a second route to the very move
# pre-registration exists to prevent: run it, dislike the result, rewrite the
# claim. `parent_id` is here for the same reason in the lineage: an edge that
# can be redirected after the fact can be pointed at an ancestor, and the
# history stops being a history.
#
# Both routes are closed, because there are two. A field that is not promoted
# into its column still lands in the payload blob, and `_merge` lets the blob
# supply anything whose column is NULL -- which is how an experiment that was
# never pre-registered at all could acquire a hash.
_FROZEN_AT_RESERVATION = frozenset(
    {
        "id",
        "scope",
        "campaign_id",
        "template",
        "parameters",
        "parent_id",
        "policy",
        "family",
        "hypothesis",
        "dataset",
        "data_version",
        "seed",
        "preregistration_id",
        "preregistration_hash",
        "created_at",
    }
)

# A claim whose process died. Reservation is a content-derived primary key, so
# without this a candidate interrupted mid-run stayed claimed forever: the row
# said "reserved", nothing was ever written against it, and every later attempt
# to reserve those same parameters was declined as a duplicate. The
# configuration silently left the search and no restart could bring it back.
#
# Held separately from the outcome statuses because it is not an outcome. The
# experiment did not fail and did not succeed; it never ran.
_ABANDONED = "abandoned"

# Statuses that mean a worker holds this claim right now. On a fresh `start`
# nothing does, so anything still wearing one belongs to a process that is gone.
_IN_FLIGHT = ("reserved", "running")

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
        db.execute("CREATE INDEX IF NOT EXISTS attempts_campaign ON attempts (campaign_id)")

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=15)

    def reserve(
        self,
        scope: str,
        template: str,
        parameters: dict[str, float],
        *,
        campaign_id: str = "",
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

        ``campaign_id`` is the one exception, and only when it is set. Two
        campaigns asking the same question are two pieces of research: a finding
        from one may *inform* the other, but the other has to establish its own
        evidence, and a shared claim namespace made that impossible — whichever
        campaign got there first silently consumed the configuration for
        everybody. It is omitted from the identity when empty so a standalone
        engine's keys are unchanged.
        """
        identity: dict[str, Any] = {
            "scope": scope,
            "template": template,
            "params": parameters,
        }
        if campaign_id:
            identity["campaign"] = campaign_id
        key = stable_id("experiment", identity)
        if parent_id is not None and parent_id == key:
            # A policy proposing its own parent again — a neighbourhood step
            # that clamped back to where it started. Declining is the same
            # answer the identity check below would give (the row already
            # exists, so the insert is ignored), and it says the useful half
            # out loud: nothing is claimed, and no self-edge is written.
            return None
        payload = {
            "id": key,
            "template": template,
            "parameters": parameters,
            "status": "reserved",
        }
        with closing(self.connect()) as db, db:
            # Claiming is now a read followed by a write, and eight workers do it
            # at once, so the write lock is taken up front rather than left to
            # the INSERT.
            db.execute("BEGIN IMMEDIATE")
            # Every edge must point at a row that already exists. Together with
            # `parent_id` being frozen after reservation, that makes the lineage
            # acyclic by construction rather than by hoping: a new edge can only
            # ever point backwards, and no existing edge can be redirected. The
            # read-side cycle guards stay where they are, because a database
            # corrupted from outside this class is still possible.
            if parent_id is not None:
                known = db.execute("SELECT 1 FROM attempts WHERE id=?", (parent_id,)).fetchone()
                if known is None:
                    raise ValueError(f"parent experiment does not exist: {parent_id}")
            claimed = db.execute("SELECT status FROM attempts WHERE id=?", (key,)).fetchone()
            if claimed is not None:
                if str(claimed[0] or "") != _ABANDONED:
                    return None
                # A claim left behind by a process that died. It is the same
                # experiment -- same identity, same frozen provenance -- so the
                # record is picked back up rather than written again.
                db.execute(
                    "UPDATE attempts SET status='reserved', finished_at=NULL WHERE id=?", (key,)
                )
                return key
            inserted = db.execute(
                "INSERT OR IGNORE INTO attempts "
                "(id, scope, campaign_id, template, payload, parent_id, policy, family, "
                "hypothesis, dataset, data_version, seed, status, created_at, "
                "preregistration_id, preregistration_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    scope,
                    campaign_id,
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

    def reclaim_abandoned(self, scope: str, campaign_id: str | None = None) -> int:
        """Release claims held by a process that is no longer running.

        Safe only where nothing is in flight — the engine calls this from
        `start`, which has already established that none of its workers are
        alive. Calling it while workers are running would hand their in-progress
        candidates to somebody else.

        Returns how many claims were released, which is worth logging: a number
        that is not zero means the last run did not shut down cleanly.
        """
        placeholders = ", ".join("?" for _ in _IN_FLIGHT)
        # Narrowed to one campaign where one is given, so restarting a campaign
        # cannot release claims another campaign's workers are still holding.
        extra = " AND campaign_id=?" if campaign_id else ""
        args: tuple[Any, ...] = (
            (_ABANDONED, scope, campaign_id, *_IN_FLIGHT)
            if campaign_id
            else (_ABANDONED, scope, *_IN_FLIGHT)
        )
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            return int(
                db.execute(
                    f"UPDATE attempts SET status=? WHERE scope=?{extra} "
                    f"AND status IN ({placeholders})",
                    args,
                ).rowcount
            )

    def finish(self, key: str, **fields: Any) -> None:
        """Merge an outcome into the record.

        Keys matching a real column are written there so they can be queried;
        everything else stays in the payload blob, which is what keeps this
        backwards compatible with callers that invented their own field names.

        Provenance is refused rather than ignored. This method takes arbitrary
        keyword arguments, so the day someone forwards a results dictionary
        into it, a colliding key would silently rewrite what the judge reads as
        evidence. Failing loudly turns that from a corrupted record into a
        stack trace.
        """
        frozen = _FROZEN_AT_RESERVATION & fields.keys()
        if frozen:
            raise ValueError(
                "finish() records an outcome and cannot rewrite provenance fixed "
                f"at reservation: {', '.join(sorted(frozen))}"
            )
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
    def recent(
        self, scope: str, limit: int = 80, *, campaign_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Recent attempts in this scope, newest first.

        ``campaign_id`` narrows to one programme. Lineage work wants that —
        a neighbourhood step should extend its own campaign's parent, not one
        from a different research programme that happens to share the dataset.
        """
        extra = " AND campaign_id=?" if campaign_id else ""
        args: tuple[Any, ...] = (
            (scope, campaign_id, limit) if campaign_id else (scope, limit)
        )
        with closing(self.connect()) as db, db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                f"SELECT * FROM attempts WHERE scope=?{extra} ORDER BY rowid DESC LIMIT ?",
                args,
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

    def count(self, scope: str, campaign_id: str | None = None) -> int:
        """How many attempts this scope holds.

        Called with no campaign by the judge, on purpose: the trial count the
        Deflated Sharpe deflates against is every trial run against this data,
        not only the ones this campaign ran. Narrowing it per campaign would
        lower the best-of-N hurdle exactly as more campaigns searched the same
        series, which is the opposite of what multiple-testing control is for.
        """
        extra = " AND campaign_id=?" if campaign_id else ""
        args: tuple[Any, ...] = (scope, campaign_id) if campaign_id else (scope,)
        with closing(self.connect()) as db, db:
            return int(
                db.execute(
                    f"SELECT COUNT(*) FROM attempts WHERE scope=?{extra}", args
                ).fetchone()[0]
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
