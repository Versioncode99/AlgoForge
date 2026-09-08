"""Durable research memory: failures that prune, not failures that scroll past.

The engine already refuses to re-run a byte-identical parameter set, because
``Experiments.reserve`` hashes the set into SQLite. That is exact-match dedup,
and it is the cheapest possible skip — but it is also the weakest one. Move one
parameter by one step and the candidate runs in full, however many of its
neighbours have already failed for the same reason.

This module is the generalising half. A failure is recorded with a **class**,
and each class declares how far its evidence reaches:

* ``NO_TRADES`` reaches quite far. If a breakout threshold is so high that
  nothing triggers, the threshold one step away almost certainly does not
  trigger either.
* ``NEGATIVE_EXPECTANCY`` reaches less far. Losing money at one setting is
  weaker evidence about the neighbour than producing no trades at all.
* ``LOOKAHEAD`` reaches across the whole template, because lookahead is a
  property of the generated code rather than of the numbers fed into it.
* ``BOOKKEEPING`` and ``INFRASTRUCTURE`` reach nowhere. A consumed holdout or a
  disk error says nothing whatsoever about the parameter region, and treating
  either as research evidence would quietly delete good candidates.

Distance is measured in **normalised parameter space**: each value is mapped to
its declared ``[low, high]`` range, so a radius means "within this fraction of
the declared range on every axis" and is comparable across templates whose
parameters have wildly different units. The metric is Chebyshev (worst axis)
rather than Euclidean, because a candidate that matches a failure closely on
every axis but one is not near it in any useful sense.

Nothing here decides that a strategy is good. It only ever declines to spend
compute re-proving something already disproven, and it records why, so the
decision is inspectable rather than a silent absence of results.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from forge.contracts.hashing import content_hash, stable_id

# Reaches every parameter set for the template, not a neighbourhood of one.
TEMPLATE_WIDE = float("inf")


class FailureClass(StrEnum):
    """Why a candidate stopped. The taxonomy prompt §23 asks for.

    Membership is deliberately small. A class earns its place by implying
    something different about *where else* the failure would occur; two classes
    that would prune identically should be one class.
    """

    NO_TRADES = "NO_TRADES"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    NEGATIVE_EXPECTANCY = "NEGATIVE_EXPECTANCY"
    RISK = "RISK"
    ROBUSTNESS = "ROBUSTNESS"
    WALK_FORWARD = "WALK_FORWARD"
    SELECTION_INTEGRITY = "SELECTION_INTEGRITY"
    LOOKAHEAD = "LOOKAHEAD"
    SAFETY = "SAFETY"
    DATA = "DATA"
    BOOKKEEPING = "BOOKKEEPING"
    INFRASTRUCTURE = "INFRASTRUCTURE"


# How far each class's evidence reaches, as a fraction of the declared range on
# the worst axis. ``None`` means the class never prunes anything.
_REACH: dict[FailureClass, float | None] = {
    FailureClass.NO_TRADES: 0.08,
    FailureClass.INSUFFICIENT_SAMPLE: 0.08,
    FailureClass.NEGATIVE_EXPECTANCY: 0.05,
    FailureClass.RISK: 0.05,
    FailureClass.ROBUSTNESS: 0.04,
    FailureClass.WALK_FORWARD: 0.04,
    FailureClass.SELECTION_INTEGRITY: 0.04,
    # Code-level properties: the numbers are not what went wrong.
    FailureClass.LOOKAHEAD: TEMPLATE_WIDE,
    FailureClass.SAFETY: TEMPLATE_WIDE,
    # Say nothing about the parameter region.
    FailureClass.DATA: None,
    FailureClass.BOOKKEEPING: None,
    FailureClass.INFRASTRUCTURE: None,
}


def reach_of(failure: FailureClass) -> float | None:
    """Radius in normalised parameter space, or ``None`` if it never prunes."""
    return _REACH[failure]


class HasRange(Protocol):
    """The part of ``ParameterSpec`` this module needs."""

    name: str
    low: Any
    high: Any


@dataclass(frozen=True)
class PruneDecision:
    """Why a candidate was skipped, and what evidence did the skipping."""

    failure_class: FailureClass
    reason: str
    distance: float
    reach: float
    source_id: str
    source_parameters: dict[str, float]

    def describe(self) -> str:
        if self.reach == TEMPLATE_WIDE:
            return f"{self.failure_class} applies to the whole template: {self.reason}"
        return (
            f"{self.distance:.3f} from a {self.failure_class} at "
            f"{self.source_parameters} (reach {self.reach:.3f}): {self.reason}"
        )


@dataclass(frozen=True)
class FailureRecord:
    """One durable failure, as stored."""

    record_id: str
    scope: str
    template: str
    failure_class: FailureClass
    reason: str
    parameters: dict[str, float]
    normalised: dict[str, float]
    gate: str | None
    strategy_id: str | None
    created_at: str


def normalise(parameters: Mapping[str, Any], ranges: Sequence[HasRange]) -> dict[str, float]:
    """Map each parameter onto ``[0, 1]`` against its declared range.

    Parameters the template does not declare are ignored rather than guessed
    at: without a range there is no meaningful distance, and inventing one
    would make the radius mean different things on different axes.
    """
    scaled: dict[str, float] = {}
    for spec in ranges:
        if spec.name not in parameters:
            continue
        try:
            value = float(parameters[spec.name])
            low = float(spec.low)
            high = float(spec.high)
        except (TypeError, ValueError):
            continue
        span = high - low
        position = 0.0 if span == 0.0 else (value - low) / span
        scaled[spec.name] = min(1.0, max(0.0, position))
    return scaled


def _distance(left: Mapping[str, float], right: Mapping[str, float]) -> float | None:
    """Chebyshev distance over the axes both sides declare.

    Returns ``None`` when there is no shared axis: the two points are not
    comparable, and a default of 0.0 would prune everything.
    """
    shared = set(left) & set(right)
    if not shared:
        return None
    return max(abs(left[axis] - right[axis]) for axis in shared)


class ResearchMemory:
    """Append-only failure record with a neighbourhood query.

    Backed by SQLite so it survives the restart that ``_constraints`` did not.
    Writes are append-only: a failure is evidence, and evidence is not edited.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS failures ("
                "id TEXT PRIMARY KEY, scope TEXT NOT NULL, template TEXT NOT NULL, "
                "failure_class TEXT NOT NULL, reason TEXT NOT NULL, "
                "parameters TEXT NOT NULL, normalised TEXT NOT NULL, "
                "gate TEXT, strategy_id TEXT, created_at TEXT NOT NULL)"
            )
            self._migrate(db)
            db.execute(
                "CREATE INDEX IF NOT EXISTS failures_scope_template ON failures (scope, template)"
            )

    @staticmethod
    def _migrate(db: sqlite3.Connection) -> None:
        """Add the chain columns to a database written before they existed."""
        existing = {str(row[1]) for row in db.execute("PRAGMA table_info(failures)")}
        for name in ("previous_hash", "entry_hash"):
            if name not in existing:
                db.execute(f"ALTER TABLE failures ADD COLUMN {name} TEXT")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=15)

    def record(
        self,
        *,
        scope: str,
        template: str,
        failure_class: FailureClass,
        reason: str,
        parameters: Mapping[str, Any],
        ranges: Sequence[HasRange],
        gate: str | None = None,
        strategy_id: str | None = None,
        created_at: datetime | None = None,
    ) -> FailureRecord:
        """Persist one classified failure. Idempotent on identical evidence."""
        plain = {key: float(value) for key, value in parameters.items() if _numeric(value)}
        scaled = normalise(parameters, ranges)
        timestamp = (created_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        record = FailureRecord(
            record_id=stable_id(
                "failure",
                {
                    "scope": scope,
                    "template": template,
                    "class": str(failure_class),
                    "parameters": plain,
                },
            ),
            scope=scope,
            template=template,
            failure_class=failure_class,
            reason=reason,
            parameters=plain,
            normalised=scaled,
            gate=gate,
            strategy_id=strategy_id,
            created_at=timestamp,
        )
        with closing(self._connect()) as db, db:
            # One write transaction around the read *and* the write. Reading the
            # tail outside it lets the engine's eight workers all see the same
            # last row and commit siblings that each claim it as predecessor:
            # the chain then forks, and `verify_chain` reports tampering that
            # never happened. BEGIN IMMEDIATE takes the write lock up front, so
            # writers serialise and each one links to the row actually before it.
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT entry_hash FROM failures ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            previous_hash = str(previous[0]) if previous and previous[0] else None
            # INSERT OR IGNORE, not REPLACE: this is append-only evidence, and
            # replacing a row would rewrite a link the chain depends on.
            # Recording the same finding twice is a no-op, not an update.
            db.execute(
                "INSERT OR IGNORE INTO failures VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.record_id,
                    record.scope,
                    record.template,
                    str(record.failure_class),
                    record.reason,
                    json.dumps(record.parameters, sort_keys=True),
                    json.dumps(record.normalised, sort_keys=True),
                    record.gate,
                    record.strategy_id,
                    record.created_at,
                    previous_hash,
                    _chain_hash(record, previous_hash),
                ),
            )
        return record

    def prune(
        self,
        *,
        scope: str,
        template: str,
        parameters: Mapping[str, Any],
        ranges: Sequence[HasRange],
    ) -> PruneDecision | None:
        """The nearest recorded failure that already covers this candidate.

        ``None`` means nothing known rules it out — which is not a prediction
        that it will succeed, only that it has not already been disproven.
        """
        candidate = normalise(parameters, ranges)
        best: PruneDecision | None = None
        for row in self._rows(scope, template):
            failure = FailureClass(row["failure_class"])
            reach = _REACH[failure]
            if reach is None:
                continue
            if reach == TEMPLATE_WIDE:
                # No distance to compute: the finding is about the code.
                return PruneDecision(
                    failure_class=failure,
                    reason=row["reason"],
                    distance=0.0,
                    reach=TEMPLATE_WIDE,
                    source_id=row["id"],
                    source_parameters=json.loads(row["parameters"]),
                )
            stored: dict[str, float] = json.loads(row["normalised"])
            gap = _distance(candidate, stored)
            if gap is None or gap > reach:
                continue
            if best is None or gap < best.distance:
                best = PruneDecision(
                    failure_class=failure,
                    reason=row["reason"],
                    distance=gap,
                    reach=reach,
                    source_id=row["id"],
                    source_parameters=json.loads(row["parameters"]),
                )
        return best

    def _rows(self, scope: str, template: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as db, db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT * FROM failures WHERE scope=? AND template=?", (scope, template)
            ).fetchall()
        return [dict(row) for row in rows]

    def recent(self, scope: str, limit: int = 100) -> list[FailureRecord]:
        """Most recently recorded failures, newest first."""
        with closing(self._connect()) as db, db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                "SELECT * FROM failures WHERE scope=? ORDER BY rowid DESC LIMIT ?",
                (scope, limit),
            ).fetchall()
        return [_to_record(dict(row)) for row in rows]

    def counts(self, scope: str) -> dict[str, int]:
        """How many failures of each class, for observability (prompt §22)."""
        with closing(self._connect()) as db, db:
            rows = db.execute(
                "SELECT failure_class, COUNT(*) FROM failures WHERE scope=? GROUP BY failure_class",
                (scope,),
            ).fetchall()
        return {str(name): int(count) for name, count in rows}

    def verify_chain(self) -> dict[str, Any]:
        """Walk the hash chain and report the first link that does not hold.

        Research memory decides what the engine stops exploring, so a silently
        edited or deleted failure changes what gets searched. Each row commits
        to the one before it, which makes a deletion in the middle or an edit to
        any field detectable — the chain no longer recomputes from that point.

        This detects tampering. It does not prevent it: anyone who can write the
        file can rewrite the whole chain. It is an integrity check, not a lock.
        """
        with closing(self._connect()) as db, db:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute("SELECT * FROM failures ORDER BY rowid")]

        previous: str | None = None
        for index, row in enumerate(rows):
            if row.get("entry_hash") is None:
                # Written before the chain existed; not a break, just unchained.
                previous = None
                continue
            if row.get("previous_hash") != previous:
                return {
                    "intact": False,
                    "entries": len(rows),
                    "broken_at": index,
                    "record_id": row["id"],
                    "problem": "previous_hash does not match the entry before it",
                }
            expected = _chain_hash(_to_record(dict(row)), previous)
            if row["entry_hash"] != expected:
                return {
                    "intact": False,
                    "entries": len(rows),
                    "broken_at": index,
                    "record_id": row["id"],
                    "problem": "entry content does not match its recorded hash",
                }
            previous = str(row["entry_hash"])
        return {"intact": True, "entries": len(rows), "broken_at": None}

    def total(self, scope: str | None = None) -> int:
        with closing(self._connect()) as db, db:
            if scope is None:
                return int(db.execute("SELECT COUNT(*) FROM failures").fetchone()[0])
            return int(
                db.execute("SELECT COUNT(*) FROM failures WHERE scope=?", (scope,)).fetchone()[0]
            )


# Which gate found the problem determines what the problem implies elsewhere.
_GATE_CLASS: dict[str, FailureClass] = {
    "G0": FailureClass.DATA,
    "G1": FailureClass.BOOKKEEPING,
    "G2": FailureClass.SAFETY,
    "G3": FailureClass.INSUFFICIENT_SAMPLE,
    "G4": FailureClass.NEGATIVE_EXPECTANCY,
    "G5": FailureClass.SELECTION_INTEGRITY,
    "G6": FailureClass.ROBUSTNESS,
    "G7": FailureClass.INFRASTRUCTURE,
    "G8": FailureClass.RISK,
    "G9": FailureClass.ROBUSTNESS,
    "G10": FailureClass.BOOKKEEPING,
    "G11": FailureClass.SELECTION_INTEGRITY,
    "G12": FailureClass.WALK_FORWARD,
    "G13": FailureClass.ROBUSTNESS,
}


def classify_gate(gate: str, status: str, *, lookahead: bool = False) -> FailureClass | None:
    """Map a judge gate outcome onto the taxonomy.

    ``INCONCLUSIVE`` returns ``None`` and must never be recorded. The judge uses
    that status to mean *this was never measured*, and absence of evidence is
    the one thing research memory must not learn from: pruning a region because
    nobody looked at it would delete candidates on the strength of nothing.

    ``G2`` splits on ``lookahead`` because the two findings reach the same
    distance — the whole template — but a researcher reading the constraint
    needs to know which one fired.
    """
    if status != "FAIL":
        return None
    if gate == "G2":
        return FailureClass.LOOKAHEAD if lookahead else FailureClass.SAFETY
    return _GATE_CLASS.get(gate)


# Text that names a broken machine or an absent dataset rather than a property
# of the parameters. Checked before the pruning classes, because the classifier
# matches substrings and the pruning classes are the destructive answer: a
# vendor outage phrased as "dataset unavailable: no trades could be loaded"
# otherwise reads as NO_TRADES and deletes a parameter region on the strength of
# an infrastructure failure.
_NEVER_PRUNE_MARKERS = (
    "unavailable",
    "dataset",
    "disk",
    "i/o",
    "ioerror",
    "timeout",
    "timed out",
    "connection",
    "network",
    "crash",
    "out of memory",
    "permission",
    "not found",
    "missing",
    "corrupt",
)


def classify_reason(reason: str) -> FailureClass:
    """Fallback for engine-level stops that never reached the judge.

    Ambiguity resolves toward the class that does *not* prune. A message can
    only be read for keywords, and a wrong guess in the pruning direction
    silently deletes candidates that were never tested; a wrong guess the other
    way only costs the compute of re-running one.
    """
    text = reason.casefold()
    if any(marker in text for marker in _NEVER_PRUNE_MARKERS):
        return FailureClass.INFRASTRUCTURE
    if "no trades" in text:
        return FailureClass.NO_TRADES
    if "holdout already consumed" in text or "lineage" in text:
        return FailureClass.BOOKKEEPING
    if "lookahead" in text:
        return FailureClass.LOOKAHEAD
    return FailureClass.INFRASTRUCTURE


def _chain_hash(record: FailureRecord, previous_hash: str | None) -> str:
    """This entry's content, committed to the one before it."""
    return content_hash(
        {
            "record_id": record.record_id,
            "scope": record.scope,
            "template": record.template,
            "failure_class": str(record.failure_class),
            "reason": record.reason,
            "parameters": record.parameters,
            "gate": record.gate,
            "strategy_id": record.strategy_id,
            "created_at": record.created_at,
            "previous_hash": previous_hash,
        }
    )


def _numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, int | float)


def _to_record(row: dict[str, Any]) -> FailureRecord:
    return FailureRecord(
        record_id=str(row["id"]),
        scope=str(row["scope"]),
        template=str(row["template"]),
        failure_class=FailureClass(row["failure_class"]),
        reason=str(row["reason"]),
        parameters=json.loads(row["parameters"]),
        normalised=json.loads(row["normalised"]),
        gate=row["gate"],
        strategy_id=row["strategy_id"],
        created_at=str(row["created_at"]),
    )
