"""The research frontier: what is known, what is not, and why.

A search engine that cannot distinguish *"we tested this and it failed"* from
*"nobody has looked"* will eventually stop looking at the second kind, because
both read as an absence of results. This module is the map that keeps them
apart.

Every research question the system holds is one :class:`FrontierItem` in one of
nine states. The states are not a quality ranking — they are epistemic status,
and three of them exist specifically to stop a gap being mistaken for a verdict:

* ``UNKNOWN`` — the question exists and nothing has been decided about it.
* ``UNTESTED`` — scheduled, admitted, never run. **This is not a failure.**
* ``PARTIALLY_EXPLORED`` — some evidence exists and it does not settle the question.
* ``INCONCLUSIVE`` — it was run and the evidence was too thin to say. Also not a failure.
* ``PROMISING`` — evidence supports it and it has not yet earned validation.
* ``VALIDATED`` — it cleared the judge on out-of-sample evidence.
* ``FAILED`` — a gate genuinely failed. The only state that carries disproof.
* ``BLOCKED_BY_DATA`` — it needs data this installation cannot serve. Nothing is
  claimed about whether it is true.
* ``EXHAUSTED`` — the region has been searched to the configured depth without a
  result. Distinct from ``FAILED``: the question is not disproven, the budget is.

**The frontier has no veto.** Nothing here may prevent a candidate being tried.
That power belongs to :mod:`forge.memory.research` alone, which acts on a
classified ``FailureClass`` with a declared reach. This distinction is the whole
reason both exist: a map that could delete territory would stop being a map.

Every transition is recorded with an actor, a reason and the evidence that moved
it, and the history is kept forever. Reading an item's history answers "why does
the system believe this?", which is a question a research log has to be able to
answer six months later.
"""

from __future__ import annotations

import builtins
import json
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from contextlib import closing
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from forge.contracts.hashing import stable_id

SCHEMA_VERSION = 1


class FrontierError(ValueError):
    """A frontier operation was refused. The message says why."""


class FrontierState(StrEnum):
    """Epistemic status of one research question.

    Ordered here from least to most settled, which is the order the interface
    displays them in. The order carries no arithmetic meaning.
    """

    UNKNOWN = "UNKNOWN"
    UNTESTED = "UNTESTED"
    PARTIALLY_EXPLORED = "PARTIALLY_EXPLORED"
    INCONCLUSIVE = "INCONCLUSIVE"
    PROMISING = "PROMISING"
    VALIDATED = "VALIDATED"
    FAILED = "FAILED"
    BLOCKED_BY_DATA = "BLOCKED_BY_DATA"
    EXHAUSTED = "EXHAUSTED"


#: States that mean "no evidence has been produced about this". Kept as a set
#: rather than a comparison so a reader never has to work out whether
#: INCONCLUSIVE counts — it does not, because it was run.
OPEN_STATES: frozenset[FrontierState] = frozenset({FrontierState.UNKNOWN, FrontierState.UNTESTED})

#: States a scheduler may still draw from. ``FAILED`` is absent because a gate
#: disproved it; ``BLOCKED_BY_DATA`` because the data does not exist;
#: ``EXHAUSTED`` because the budget for it is spent. ``VALIDATED`` is absent
#: because it has already arrived.
SCHEDULABLE_STATES: frozenset[FrontierState] = frozenset(
    {
        FrontierState.UNKNOWN,
        FrontierState.UNTESTED,
        FrontierState.PARTIALLY_EXPLORED,
        FrontierState.INCONCLUSIVE,
        FrontierState.PROMISING,
    }
)

#: Terminal for this campaign's purposes. Reopening one is allowed — evidence
#: can change — but it takes an explicit reason, which the transition log keeps.
SETTLED_STATES: frozenset[FrontierState] = frozenset(
    {FrontierState.VALIDATED, FrontierState.FAILED, FrontierState.EXHAUSTED}
)


class SearchKind(StrEnum):
    """How far a proposal departs from what has already been tried.

    The five categories the research model is built on. An engine that spends
    its whole budget in ``PARAMETER`` is a parameter search however it is
    described, so the category has to be a recorded property of every candidate
    rather than a claim about the engine.
    """

    #: Same hypothesis, different parameters.
    PARAMETER = "PARAMETER"
    #: Same family and mechanism, materially different signal construction.
    STRUCTURAL = "STRUCTURAL"
    #: A different falsifiable hypothesis.
    HYPOTHESIS = "HYPOTHESIS"
    #: A different economic or statistical explanation.
    MECHANISM = "MECHANISM"
    #: A genuinely new research family.
    FAMILY = "FAMILY"


#: Ordering used when reporting "how deep did this campaign actually go".
KIND_DEPTH: dict[SearchKind, int] = {
    SearchKind.PARAMETER: 0,
    SearchKind.STRUCTURAL: 1,
    SearchKind.HYPOTHESIS: 2,
    SearchKind.MECHANISM: 3,
    SearchKind.FAMILY: 4,
}


class FrontierItem:
    """One research question, its status, and where the status came from.

    Deliberately a plain object rather than a frozen pydantic model: the store
    updates state in place and a frozen model would mean reconstructing the
    whole row for every transition, which reads worse and buys nothing here.
    """

    __slots__ = (
        "campaign_id",
        "created_at",
        "experiments",
        "family",
        "hypothesis_id",
        "item_id",
        "mechanism",
        "missing_data",
        "novelty",
        "origin",
        "question",
        "reason",
        "required_data",
        "search_kind",
        "sources",
        "state",
        "updated_at",
    )

    def __init__(
        self,
        *,
        item_id: str,
        campaign_id: str,
        question: str,
        family: str,
        mechanism: str,
        hypothesis_id: str | None,
        state: FrontierState,
        reason: str,
        required_data: tuple[str, ...],
        missing_data: tuple[str, ...],
        search_kind: SearchKind,
        novelty: float,
        experiments: int,
        sources: tuple[str, ...],
        origin: str,
        created_at: str,
        updated_at: str,
    ) -> None:
        self.item_id = item_id
        self.campaign_id = campaign_id
        self.question = question
        self.family = family
        self.mechanism = mechanism
        self.hypothesis_id = hypothesis_id
        self.state = state
        self.reason = reason
        self.required_data = required_data
        self.missing_data = missing_data
        self.search_kind = search_kind
        self.novelty = novelty
        self.experiments = experiments
        self.sources = sources
        self.origin = origin
        self.created_at = created_at
        self.updated_at = updated_at

    @property
    def schedulable(self) -> bool:
        return self.state in SCHEDULABLE_STATES

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "campaign_id": self.campaign_id,
            "question": self.question,
            "family": self.family,
            "mechanism": self.mechanism,
            "hypothesis_id": self.hypothesis_id,
            "state": str(self.state),
            "reason": self.reason,
            "required_data": list(self.required_data),
            "missing_data": list(self.missing_data),
            "search_kind": str(self.search_kind),
            "novelty": round(self.novelty, 4),
            "experiments": self.experiments,
            "sources": list(self.sources),
            "origin": self.origin,
            "schedulable": self.schedulable,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ResearchFrontier:
    """Durable frontier state, one SQLite file in the workspace data root.

    Deleting this file costs the map, not the research: every verdict, artifact
    and holdout consumption lives elsewhere and is unaffected.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS items ("
                "item_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, question TEXT NOT NULL, "
                "family TEXT NOT NULL, mechanism TEXT NOT NULL, hypothesis_id TEXT, "
                "state TEXT NOT NULL, reason TEXT NOT NULL, required_data TEXT NOT NULL, "
                "missing_data TEXT NOT NULL, search_kind TEXT NOT NULL, novelty REAL NOT NULL, "
                "experiments INTEGER NOT NULL DEFAULT 0, sources TEXT NOT NULL, "
                "origin TEXT NOT NULL, schema_version INTEGER NOT NULL, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS transitions ("
                "transition_id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, "
                "from_state TEXT NOT NULL, to_state TEXT NOT NULL, reason TEXT NOT NULL, "
                "actor TEXT NOT NULL, evidence TEXT NOT NULL, at TEXT NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS items_campaign ON items(campaign_id, state)")
            db.execute("CREATE INDEX IF NOT EXISTS transitions_item ON transitions(item_id)")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    # ── writes ───────────────────────────────────────────────────────────────
    def admit(
        self,
        *,
        campaign_id: str,
        question: str,
        family: str,
        mechanism: str,
        search_kind: SearchKind,
        hypothesis_id: str | None = None,
        required_data: Sequence[str] = ("BARS",),
        missing_data: Sequence[str] = (),
        novelty: float = 1.0,
        sources: Sequence[str] = (),
        origin: str = "engine",
        reason: str = "admitted to the frontier",
        state: FrontierState | None = None,
    ) -> FrontierItem:
        """Put a question on the map, or return the one already there.

        Identity is the campaign plus the question text, so the same question
        proposed twice does not become two items with divergent histories. The
        second proposal is not an error — the deduplication *is* the answer.

        A question whose data cannot be served is admitted ``BLOCKED_BY_DATA``
        rather than refused. Refusing it would lose the fact that it was asked,
        which is exactly the thing a research frontier exists to remember.
        """
        question = question.strip()
        if len(question) < 20:
            raise FrontierError(
                "A frontier question needs at least 20 characters. A label is not a question, "
                "and nothing shorter can be answered or falsified."
            )
        item_id = stable_id("fr", {"campaign": campaign_id, "question": question.lower()})
        existing = self.get(item_id)
        if existing is not None:
            return existing
        missing = tuple(sorted({d.strip().upper() for d in missing_data if d.strip()}))
        resolved = state or (FrontierState.BLOCKED_BY_DATA if missing else FrontierState.UNTESTED)
        if missing and resolved is not FrontierState.BLOCKED_BY_DATA:
            raise FrontierError(
                f"Cannot admit as {resolved}: the data requirement {', '.join(missing)} "
                "is not served by this installation, so nothing about this question has "
                "been or can be measured here."
            )
        now = _now()
        item = FrontierItem(
            item_id=item_id,
            campaign_id=campaign_id,
            question=question,
            family=family,
            mechanism=mechanism.strip(),
            hypothesis_id=hypothesis_id,
            state=resolved,
            reason=(
                f"requires {', '.join(missing)}, which this installation cannot serve"
                if missing
                else reason
            ),
            required_data=tuple(sorted({d.strip().upper() for d in required_data if d.strip()}))
            or ("BARS",),
            missing_data=missing,
            search_kind=search_kind,
            novelty=max(0.0, min(1.0, float(novelty))),
            experiments=0,
            sources=tuple(sources),
            origin=origin,
            created_at=now,
            updated_at=now,
        )
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item.item_id,
                    item.campaign_id,
                    item.question,
                    item.family,
                    item.mechanism,
                    item.hypothesis_id,
                    str(item.state),
                    item.reason,
                    json.dumps(list(item.required_data)),
                    json.dumps(list(item.missing_data)),
                    str(item.search_kind),
                    item.novelty,
                    0,
                    json.dumps(list(item.sources)),
                    item.origin,
                    SCHEMA_VERSION,
                    now,
                    now,
                ),
            )
            db.execute(
                "INSERT INTO transitions (item_id, from_state, to_state, reason, actor, "
                "evidence, at) VALUES (?,?,?,?,?,?,?)",
                (item.item_id, "", str(item.state), item.reason, origin, "[]", now),
            )
        return item

    def transition(
        self,
        item_id: str,
        state: FrontierState,
        *,
        reason: str,
        actor: str = "engine",
        evidence: Sequence[str] = (),
    ) -> FrontierItem:
        """Move an item, recording who moved it and on what.

        A reason is mandatory and is not allowed to be a restatement of the
        state. "FAILED" as the reason for FAILED tells a later reader nothing;
        the gate that failed tells them everything.
        """
        reason = reason.strip()
        if len(reason) < 8:
            raise FrontierError(
                "A transition needs a reason of at least 8 characters. The state name is "
                "not a reason: a reader six months from now needs to know what moved it."
            )
        item = self.get(item_id)
        if item is None:
            raise FrontierError(f"No frontier item '{item_id}'.")
        now = _now()
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "UPDATE items SET state=?, reason=?, updated_at=? WHERE item_id=?",
                (str(state), reason, now, item_id),
            )
            db.execute(
                "INSERT INTO transitions (item_id, from_state, to_state, reason, actor, "
                "evidence, at) VALUES (?,?,?,?,?,?,?)",
                (
                    item_id,
                    str(item.state),
                    str(state),
                    reason,
                    actor,
                    json.dumps(list(evidence)),
                    now,
                ),
            )
        item.state = state
        item.reason = reason
        item.updated_at = now
        return item

    def record_experiment(self, item_id: str, count: int = 1) -> None:
        """Count one experiment against a question. Never changes its state."""
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "UPDATE items SET experiments = experiments + ?, updated_at=? WHERE item_id=?",
                (int(count), _now(), item_id),
            )

    def attach_hypothesis(self, item_id: str, hypothesis_id: str) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "UPDATE items SET hypothesis_id=?, updated_at=? WHERE item_id=?",
                (hypothesis_id, _now(), item_id),
            )

    # ── reads ────────────────────────────────────────────────────────────────
    def get(self, item_id: str) -> FrontierItem | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM items WHERE item_id=?", (item_id,)).fetchone()
        return None if row is None else _to_item(row)

    def list(
        self,
        campaign_id: str | None = None,
        *,
        states: Iterable[FrontierState] | None = None,
        limit: int = 500,
    ) -> builtins.list[FrontierItem]:
        clauses, args = [], []
        if campaign_id:
            clauses.append("campaign_id=?")
            args.append(campaign_id)
        wanted = tuple(str(s) for s in states) if states else ()
        if wanted:
            clauses.append(f"state IN ({','.join('?' * len(wanted))})")
            args.extend(wanted)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT * FROM items{where} ORDER BY updated_at DESC LIMIT ?",
                (*args, int(limit)),
            ).fetchall()
        return [_to_item(row) for row in rows]

    def schedulable(self, campaign_id: str, *, limit: int = 200) -> builtins.list[FrontierItem]:
        return self.list(campaign_id, states=SCHEDULABLE_STATES, limit=limit)

    def history(self, item_id: str) -> builtins.list[dict[str, Any]]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM transitions WHERE item_id=? ORDER BY transition_id", (item_id,)
            ).fetchall()
        return [
            {
                "from": row["from_state"],
                "to": row["to_state"],
                "reason": row["reason"],
                "actor": row["actor"],
                "evidence": json.loads(row["evidence"]),
                "at": row["at"],
            }
            for row in rows
        ]

    def counts(self, campaign_id: str | None = None) -> dict[str, int]:
        """One row per state, including the states with nothing in them.

        Zeroes are included on purpose. "No blocked items" and "we do not track
        blocked items" look identical when the key is simply absent.
        """
        where, args = ("WHERE campaign_id=?", (campaign_id,)) if campaign_id else ("", ())
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT state, COUNT(*) AS n FROM items {where} GROUP BY state", args
            ).fetchall()
        counts = {str(state): 0 for state in FrontierState}
        for row in rows:
            counts[str(row["state"])] = int(row["n"])
        return counts

    def kind_counts(self, campaign_id: str | None = None) -> dict[str, int]:
        """How the frontier is distributed across the five search categories."""
        where, args = ("WHERE campaign_id=?", (campaign_id,)) if campaign_id else ("", ())
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT search_kind, COUNT(*) AS n FROM items {where} GROUP BY search_kind", args
            ).fetchall()
        counts = {str(kind): 0 for kind in SearchKind}
        for row in rows:
            counts[str(row["search_kind"])] = int(row["n"])
        return counts


def _to_item(row: sqlite3.Row) -> FrontierItem:
    return FrontierItem(
        item_id=row["item_id"],
        campaign_id=row["campaign_id"],
        question=row["question"],
        family=row["family"],
        mechanism=row["mechanism"],
        hypothesis_id=row["hypothesis_id"],
        state=FrontierState(row["state"]),
        reason=row["reason"],
        required_data=tuple(json.loads(row["required_data"])),
        missing_data=tuple(json.loads(row["missing_data"])),
        search_kind=SearchKind(row["search_kind"]),
        novelty=float(row["novelty"]),
        experiments=int(row["experiments"]),
        sources=tuple(json.loads(row["sources"])),
        origin=row["origin"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
