"""The validation promotion queue: deciding when a candidate has earned it.

Validation is expensive — the parameter grid is the largest single cost in a
research cycle — and it is also the only thing that turns a promising
development result into evidence. Running it on everything wastes the campaign;
running it on nothing leaves candidates sitting at "promising" forever, which is
the failure the research model names explicitly.

So promotion is an explicit stage with explicit prerequisites. A candidate is
enqueued when it satisfies every one of them, and the queue records *which*
prerequisite it missed when it does not — because "not promoted" without a
reason is indistinguishable from "nobody looked".

**The judge is untouched.** Nothing here weakens, reorders or reinterprets
G0-G13. This module decides *what gets measured*; the judge decides what the
measurements mean, exactly as before. A candidate that clears this queue still
faces the full ladder and still fails it routinely.

**Three outcomes, and the middle one is real.** ``PASS``, ``FAIL`` and
``INCONCLUSIVE``. The third is not a softer failure — it means the evidence was
never produced, most often because the search had too few trials for the
deflation to mean anything. Recording it as a failure would let a gap in the
evidence prune a candidate, which is the thing
:mod:`forge.research.frontier` exists to prevent.
"""

from __future__ import annotations

import builtins
import json
import sqlite3
import threading
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

#: A development result with fewer trades than this cannot support a
#: neighbourhood search: the per-configuration Sharpes would be estimated from
#: single figures. Matches the engine's own screening threshold.
MIN_TRADES = 30

#: A parameter grid smaller than this gives PBO nothing to rank and the judge
#: reports G11 INCONCLUSIVE, so spending the compute buys no evidence.
MIN_GRID = 2


class PromotionOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class PromotionState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DECIDED = "DECIDED"
    #: Withdrawn before it ran — the campaign stopped, or the strategy was
    #: retired to make room. Distinct from a decision.
    ABANDONED = "ABANDONED"


@dataclass(frozen=True)
class Prerequisites:
    """What a candidate has to show before validation is worth running.

    Configurable per campaign, with defaults matching what the engine already
    screens on. Raising them is a research decision — fewer, better-supported
    validations — and the campaign records which set was in force.
    """

    min_trades: int = MIN_TRADES
    min_grid_size: int = MIN_GRID
    require_positive_development: bool = True
    require_conformance: bool = True
    require_determinism: bool = True
    require_real_data: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "min_trades": self.min_trades,
            "min_grid_size": self.min_grid_size,
            "require_positive_development": self.require_positive_development,
            "require_conformance": self.require_conformance,
            "require_determinism": self.require_determinism,
            "require_real_data": self.require_real_data,
        }


@dataclass(frozen=True)
class Candidate:
    """What is known about a candidate at the moment promotion is considered."""

    strategy_id: str
    hypothesis_id: str | None
    frontier_item_id: str | None
    experiment_id: str
    trades: int
    net_pnl: float
    grid_size: int
    conformance_passed: bool | None
    determinism_reproduced: bool | None
    real_data: bool


@dataclass(frozen=True)
class Eligibility:
    """Whether this candidate has earned validation, and what it missed."""

    eligible: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"eligible": self.eligible, "reasons": list(self.reasons)}


def assess(candidate: Candidate, prerequisites: Prerequisites | None = None) -> Eligibility:
    """Does this candidate qualify for validation?

    Every unmet prerequisite is reported, not just the first. A candidate that
    misses on three counts and is told about one would be re-proposed twice for
    no reason.
    """
    need = prerequisites or Prerequisites()
    missing: list[str] = []

    if need.require_real_data and not candidate.real_data:
        missing.append(
            "the development run was on synthetic bars, which cannot clear the judge's "
            "data gate whatever the validation shows"
        )
    if need.require_positive_development and candidate.net_pnl <= 0:
        missing.append(f"development net is {candidate.net_pnl:+.2f}; there is nothing to validate")
    if candidate.trades < need.min_trades:
        missing.append(
            f"{candidate.trades} trades, {need.min_trades} needed before a neighbourhood "
            "search can estimate anything"
        )
    if candidate.grid_size < need.min_grid_size:
        missing.append(
            f"the parameter grid has {candidate.grid_size} configuration(s); PBO needs at "
            f"least {need.min_grid_size} to have anything to rank"
        )
    if need.require_conformance and candidate.conformance_passed is not True:
        missing.append("the implementation conformance suite has not passed")
    if need.require_determinism and candidate.determinism_reproduced is not True:
        missing.append("the run has not been shown to reproduce exactly")

    if missing:
        return Eligibility(eligible=False, reasons=tuple(missing))
    return Eligibility(
        eligible=True,
        reasons=(
            f"{candidate.trades} trades, development net {candidate.net_pnl:+.2f}, "
            f"{candidate.grid_size}-configuration grid, conformance and determinism both "
            "measured",
        ),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class PromotionQueue:
    """Candidates that earned validation, and what came of each.

    Durable, because a campaign that restarts must not lose the fact that a
    candidate was owed a validation run.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS queue ("
                "entry_id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL, "
                "strategy_id TEXT NOT NULL, hypothesis_id TEXT, frontier_item_id TEXT, "
                "experiment_id TEXT NOT NULL, state TEXT NOT NULL, outcome TEXT, "
                "reasons TEXT NOT NULL, detail TEXT NOT NULL, priority REAL NOT NULL, "
                "schema_version INTEGER NOT NULL, queued_at TEXT NOT NULL, "
                "decided_at TEXT, UNIQUE(campaign_id, strategy_id))"
            )
            db.execute("CREATE INDEX IF NOT EXISTS queue_state ON queue(campaign_id, state)")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def enqueue(
        self,
        campaign_id: str,
        candidate: Candidate,
        eligibility: Eligibility,
        *,
        priority: float = 0.0,
    ) -> int | None:
        """Queue a candidate that has earned validation.

        Returns the entry id, or ``None`` when the candidate was not eligible —
        in which case nothing is queued and the caller records the reasons on
        the frontier item instead. Re-queuing the same strategy in the same
        campaign is a no-op: it is already owed one run, not two.
        """
        if not eligibility.eligible:
            return None
        with self._lock, closing(self._connect()) as db, db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO queue (campaign_id, strategy_id, hypothesis_id, "
                "frontier_item_id, experiment_id, state, outcome, reasons, detail, priority, "
                "schema_version, queued_at, decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    campaign_id,
                    candidate.strategy_id,
                    candidate.hypothesis_id,
                    candidate.frontier_item_id,
                    candidate.experiment_id,
                    str(PromotionState.QUEUED),
                    None,
                    json.dumps(list(eligibility.reasons)),
                    "{}",
                    float(priority),
                    SCHEMA_VERSION,
                    _now(),
                    None,
                ),
            )
        return int(cursor.lastrowid) if cursor.lastrowid else None

    def claim(self, campaign_id: str) -> dict[str, Any] | None:
        """Take the highest-priority queued entry and mark it running.

        Atomic under the lock and the UPDATE's own WHERE clause, so two workers
        claiming at once cannot both get the same entry and run the validation
        twice.
        """
        with self._lock, closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT * FROM queue WHERE campaign_id=? AND state=? "
                "ORDER BY priority DESC, entry_id LIMIT 1",
                (campaign_id, str(PromotionState.QUEUED)),
            ).fetchone()
            if row is None:
                return None
            changed = db.execute(
                "UPDATE queue SET state=? WHERE entry_id=? AND state=?",
                (str(PromotionState.RUNNING), row["entry_id"], str(PromotionState.QUEUED)),
            ).rowcount
        if not changed:
            return None
        # The row was read before the update, so its `state` still says QUEUED.
        # Returning it unmodified would hand the caller an entry that claims to
        # be unclaimed, which is exactly the confusion this method exists to
        # prevent.
        return {**_to_entry(row), "state": str(PromotionState.RUNNING)}

    def decide(
        self,
        entry_id: int,
        outcome: PromotionOutcome,
        *,
        reasons: Sequence[str],
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Record the outcome. A reason is required whichever way it went.

        Especially for ``PASS``: a validation that passed without a recorded
        reason cannot be distinguished later from one that was never run.
        """
        if not reasons:
            raise ValueError(
                "A validation decision needs a stated reason. An outcome with no reason "
                "cannot be audited and cannot be told apart from a run that never happened."
            )
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "UPDATE queue SET state=?, outcome=?, reasons=?, detail=?, decided_at=? "
                "WHERE entry_id=?",
                (
                    str(PromotionState.DECIDED),
                    str(outcome),
                    json.dumps(list(reasons)),
                    json.dumps(detail or {}),
                    _now(),
                    entry_id,
                ),
            )

    def abandon(self, entry_id: int, reason: str) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "UPDATE queue SET state=?, reasons=?, decided_at=? WHERE entry_id=?",
                (str(PromotionState.ABANDONED), json.dumps([reason]), _now(), entry_id),
            )

    def release_running(self, campaign_id: str) -> int:
        """Put anything left mid-run back in the queue.

        Called at campaign start. An entry marked RUNNING when nothing is
        running was left by a process that died, and leaving it there would owe
        a validation to a worker that no longer exists.
        """
        with self._lock, closing(self._connect()) as db, db:
            return int(
                db.execute(
                    "UPDATE queue SET state=? WHERE campaign_id=? AND state=?",
                    (str(PromotionState.QUEUED), campaign_id, str(PromotionState.RUNNING)),
                ).rowcount
            )

    def list(
        self,
        campaign_id: str | None = None,
        *,
        state: PromotionState | None = None,
        limit: int = 200,
    ) -> builtins.list[dict[str, Any]]:
        clauses, args = [], []
        if campaign_id:
            clauses.append("campaign_id=?")
            args.append(campaign_id)
        if state:
            clauses.append("state=?")
            args.append(str(state))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT * FROM queue{where} ORDER BY entry_id DESC LIMIT ?", (*args, int(limit))
            ).fetchall()
        return [_to_entry(row) for row in rows]

    def pending(self, campaign_id: str) -> int:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT COUNT(*) AS n FROM queue WHERE campaign_id=? AND state IN (?,?)",
                (campaign_id, str(PromotionState.QUEUED), str(PromotionState.RUNNING)),
            ).fetchone()
        return int(row["n"]) if row else 0

    def outcomes(self, campaign_id: str | None = None) -> dict[str, int]:
        """How many of each outcome, including the zeroes."""
        where, args = ("WHERE campaign_id=?", (campaign_id,)) if campaign_id else ("", ())
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT outcome, COUNT(*) AS n FROM queue {where} "
                "AND outcome IS NOT NULL GROUP BY outcome"
                if campaign_id
                else "SELECT outcome, COUNT(*) AS n FROM queue WHERE outcome IS NOT NULL "
                "GROUP BY outcome",
                args,
            ).fetchall()
        counts = {str(outcome): 0 for outcome in PromotionOutcome}
        for row in rows:
            counts[str(row["outcome"])] = int(row["n"])
        return counts


def _to_entry(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "entry_id": int(row["entry_id"]),
        "campaign_id": row["campaign_id"],
        "strategy_id": row["strategy_id"],
        "hypothesis_id": row["hypothesis_id"],
        "frontier_item_id": row["frontier_item_id"],
        "experiment_id": row["experiment_id"],
        "state": row["state"],
        "outcome": row["outcome"],
        "reasons": json.loads(row["reasons"]),
        "detail": json.loads(row["detail"]),
        "priority": float(row["priority"]),
        "queued_at": row["queued_at"],
        "decided_at": row["decided_at"],
    }


def outcome_from_verdict(decision: str, gates: Sequence[Any]) -> tuple[PromotionOutcome, list[str]]:
    """Translate a judge verdict into a promotion outcome and its reasons.

    The translation is mechanical and the judge remains authoritative: a PASS is
    a PASS, a genuinely failed gate is a FAIL, and gates that were never
    measured make the result INCONCLUSIVE rather than a failure. That last case
    is the one that matters — it is the difference between "this was disproven"
    and "the evidence for it was never produced".
    """
    failed = [g for g in gates if getattr(g, "status", "") == "FAIL"]
    unmeasured = [g for g in gates if getattr(g, "status", "") == "INCONCLUSIVE"]
    if decision == "PASS":
        return PromotionOutcome.PASS, [
            f"cleared the full gate ladder; {len(gates)} gates evaluated"
        ]
    if failed:
        return PromotionOutcome.FAIL, [f"{gate.gate} ({gate.name}) failed" for gate in failed[:4]]
    if unmeasured:
        return PromotionOutcome.INCONCLUSIVE, [
            f"{gate.gate} ({gate.name}) could not be measured" for gate in unmeasured[:4]
        ]
    return PromotionOutcome.INCONCLUSIVE, [
        f"the judge returned {decision} with no failing gate; the evidence was not decisive"
    ]
