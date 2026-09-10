"""The hypothesis graph: durable claims and the edges between them.

A hypothesis in this system used to be a string field on a template. That made
two hundred parameter draws against one template look like two hundred
hypotheses, which is the specific illusion this module exists to end.

Here a hypothesis is an object with an identity, a parent, a mechanism, a
falsifiable prediction, a data requirement, a status, and a recorded lineage of
the experiments and results that touched it. The chain the research model
describes —

    Research question → Hypothesis → Mechanism → Feature/Signal → Experiment
    → Result → Validation → Finding → Derived hypothesis

— is stored as typed edges, so every step of it is a query rather than a
reconstruction.

**Three rules, each closing a specific failure.**

*A hypothesis must be falsifiable to exist.* ``propose`` refuses a statement with
no prediction that could come out the other way. This is the same bar
``TemplateStore`` and ``FamilyRegistry`` already hold human-authored work to, and
a machine-generated hypothesis does not get an easier one.

*A derived hypothesis is a question, never a conclusion.* Whatever produced it —
a failure, a finding, a contradiction — it enters as ``UNTESTED``. The thing that
suggested it is recorded as an edge so a reader can see where it came from, and
that edge carries no evidentiary weight of its own.

*Nothing here is evidence.* There is no route from this store into
``JudgeInput``. A hypothesis's ``status`` reflects what the judge said about
experiments that tested it; it can never be an input to what the judge says
next. Same boundary ``forge.research.knowledge`` draws, for the same reason.
"""

from __future__ import annotations

import builtins
import json
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from forge.contracts.hashing import stable_id
from forge.research.frontier import SearchKind

SCHEMA_VERSION = 1

#: Minimum lengths. Short enough that a real machine-written hypothesis clears
#: them, long enough that a label cannot.
MIN_STATEMENT = 40
MIN_PREDICTION = 30
MIN_MECHANISM = 30


class HypothesisError(ValueError):
    """A hypothesis was refused. The message says why."""


class HypothesisStatus(StrEnum):
    """What the evidence currently says.

    Mirrors :class:`~forge.research.frontier.FrontierState` deliberately but is
    not the same enum: the frontier tracks a *question's* exploration status and
    a hypothesis tracks a *claim's* evidential status. A question can be
    PARTIALLY_EXPLORED while every hypothesis under it is still UNTESTED.
    """

    UNTESTED = "UNTESTED"
    TESTING = "TESTING"
    SUPPORTED = "SUPPORTED"
    #: Ran, and the evidence could not decide. Not a refutation.
    INCONCLUSIVE = "INCONCLUSIVE"
    #: A gate failed on evidence that was actually produced.
    REFUTED = "REFUTED"
    VALIDATED = "VALIDATED"
    #: Needs data this installation cannot serve. Says nothing about truth.
    BLOCKED_BY_DATA = "BLOCKED_BY_DATA"
    #: Superseded by a sharper statement of the same claim.
    SUPERSEDED = "SUPERSEDED"


class EdgeKind(StrEnum):
    """How two nodes relate.

    A closed set, for the same reason the IR's feature list and the panel kinds
    are closed: an edge whose meaning is a free string cannot be queried, and a
    graph that cannot be queried is a pile of rows.
    """

    #: Child was generated from parent — a follow-up question from a result.
    DERIVED_FROM = "DERIVED_FROM"
    #: Child narrows or sharpens the parent's claim.
    REFINES = "REFINES"
    #: The two cannot both hold. Recorded, never resolved automatically.
    CONTRADICTS = "CONTRADICTS"
    #: An experiment attempt tested this hypothesis.
    TESTED_BY = "TESTED_BY"
    #: A verdict was returned about this hypothesis.
    RESULT = "RESULT"
    #: A validation run reached a decision about it.
    VALIDATED_BY = "VALIDATED_BY"
    #: A retrieved research source informed it.
    SOURCED_FROM = "SOURCED_FROM"
    #: A promoted finding informed it.
    FINDING = "FINDING"
    #: The strategy template that implements its signal.
    IMPLEMENTED_BY = "IMPLEMENTED_BY"


#: Edges whose target is another hypothesis rather than an external artifact.
INTERNAL_EDGES: frozenset[EdgeKind] = frozenset(
    {EdgeKind.DERIVED_FROM, EdgeKind.REFINES, EdgeKind.CONTRADICTS}
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Hypothesis:
    """One falsifiable claim and everything recorded about it."""

    hypothesis_id: str
    campaign_id: str
    parent_id: str | None
    family: str
    mechanism: str
    statement: str
    prediction: str
    required_data: tuple[str, ...]
    missing_data: tuple[str, ...]
    expected_horizon: str
    status: HypothesisStatus
    origin: str
    search_kind: SearchKind
    novelty: float
    nearest_id: str | None
    similarity: float
    sources: tuple[str, ...]
    frontier_item_id: str | None
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "campaign_id": self.campaign_id,
            "parent_id": self.parent_id,
            "family": self.family,
            "mechanism": self.mechanism,
            "statement": self.statement,
            "falsifiable_prediction": self.prediction,
            "required_data": list(self.required_data),
            "missing_data": list(self.missing_data),
            "expected_horizon": self.expected_horizon,
            "status": str(self.status),
            "origin": self.origin,
            "search_kind": str(self.search_kind),
            "novelty": round(float(self.novelty), 4),
            "nearest_hypothesis_id": self.nearest_id,
            "similarity_to_nearest": round(float(self.similarity), 4),
            "research_sources": list(self.sources),
            "frontier_item_id": self.frontier_item_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class HypothesisGraph:
    """Durable hypothesis nodes and typed edges, in one SQLite file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS hypotheses ("
                "hypothesis_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, parent_id TEXT, "
                "family TEXT NOT NULL, mechanism TEXT NOT NULL, statement TEXT NOT NULL, "
                "prediction TEXT NOT NULL, required_data TEXT NOT NULL, missing_data TEXT NOT NULL,"
                " expected_horizon TEXT NOT NULL, status TEXT NOT NULL, origin TEXT NOT NULL, "
                "search_kind TEXT NOT NULL, novelty REAL NOT NULL, nearest_id TEXT, "
                "similarity REAL NOT NULL, sources TEXT NOT NULL, frontier_item_id TEXT, "
                "schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, "
                "updated_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS edges ("
                "edge_id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, "
                "kind TEXT NOT NULL, target TEXT NOT NULL, note TEXT NOT NULL, at TEXT NOT NULL, "
                "UNIQUE(source, kind, target))"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS hypotheses_campaign ON hypotheses(campaign_id, status)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS edges_source ON edges(source, kind)")
            db.execute("CREATE INDEX IF NOT EXISTS edges_target ON edges(target, kind)")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    # ── writes ───────────────────────────────────────────────────────────────
    def propose(
        self,
        *,
        campaign_id: str,
        statement: str,
        prediction: str,
        mechanism: str,
        family: str,
        search_kind: SearchKind,
        parent_id: str | None = None,
        required_data: Sequence[str] = ("BARS",),
        missing_data: Sequence[str] = (),
        expected_horizon: str = "intraday",
        origin: str = "engine",
        novelty: float = 1.0,
        nearest_id: str | None = None,
        similarity: float = 0.0,
        sources: Sequence[str] = (),
        frontier_item_id: str | None = None,
    ) -> Hypothesis:
        """Record a claim, or return the identical one already recorded.

        Identity is the campaign plus the statement text, so re-proposing the
        same claim is a no-op rather than a duplicate node. The novelty gate
        that decides whether a *near* duplicate should be proposed at all lives
        in :mod:`forge.research.novelty`; this constructor's job is only to
        refuse a claim that could not be wrong.
        """
        statement, prediction, mechanism = (
            statement.strip(),
            prediction.strip(),
            mechanism.strip(),
        )
        if len(statement) < MIN_STATEMENT:
            raise HypothesisError(
                f"A hypothesis statement needs at least {MIN_STATEMENT} characters and must "
                "say what is claimed. Got a label, not a claim."
            )
        if len(prediction) < MIN_PREDICTION:
            raise HypothesisError(
                f"State what result would abandon this hypothesis, in at least "
                f"{MIN_PREDICTION} characters. A prediction that cannot fail is not one."
            )
        if len(mechanism) < MIN_MECHANISM:
            raise HypothesisError(
                f"A hypothesis needs a stated mechanism of at least {MIN_MECHANISM} characters. "
                "Without one the judge's mechanism gate has nothing to test against."
            )
        if parent_id is not None and self.get(parent_id) is None:
            raise HypothesisError(f"Parent hypothesis '{parent_id}' does not exist.")

        hypothesis_id = stable_id("hyp", {"c": campaign_id, "s": statement.lower()})
        existing = self.get(hypothesis_id)
        if existing is not None:
            return existing

        missing = tuple(sorted({d.strip().upper() for d in missing_data if d.strip()}))
        now = _now()
        node = Hypothesis(
            hypothesis_id=hypothesis_id,
            campaign_id=campaign_id,
            parent_id=parent_id,
            family=family,
            mechanism=mechanism,
            statement=statement,
            prediction=prediction,
            required_data=tuple(sorted({d.strip().upper() for d in required_data if d.strip()}))
            or ("BARS",),
            missing_data=missing,
            expected_horizon=expected_horizon,
            # A blocked hypothesis is not untested-and-waiting: nothing here can
            # ever run it, and saying UNTESTED would put it in a queue that will
            # never reach it.
            status=(HypothesisStatus.BLOCKED_BY_DATA if missing else HypothesisStatus.UNTESTED),
            origin=origin,
            search_kind=search_kind,
            novelty=max(0.0, min(1.0, float(novelty))),
            nearest_id=nearest_id,
            similarity=max(0.0, min(1.0, float(similarity))),
            sources=tuple(sources),
            frontier_item_id=frontier_item_id,
            created_at=now,
            updated_at=now,
        )
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO hypotheses VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    node.hypothesis_id,
                    node.campaign_id,
                    node.parent_id,
                    node.family,
                    node.mechanism,
                    node.statement,
                    node.prediction,
                    json.dumps(list(node.required_data)),
                    json.dumps(list(node.missing_data)),
                    node.expected_horizon,
                    str(node.status),
                    node.origin,
                    str(node.search_kind),
                    node.novelty,
                    node.nearest_id,
                    node.similarity,
                    json.dumps(list(node.sources)),
                    node.frontier_item_id,
                    SCHEMA_VERSION,
                    now,
                    now,
                ),
            )
        if parent_id:
            self.link(hypothesis_id, EdgeKind.DERIVED_FROM, parent_id, note=origin)
        for source_id in node.sources:
            self.link(hypothesis_id, EdgeKind.SOURCED_FROM, source_id, note="retrieved source")
        return node

    def set_status(self, hypothesis_id: str, status: HypothesisStatus, *, note: str = "") -> None:
        with self._lock, closing(self._connect()) as db, db:
            changed = db.execute(
                "UPDATE hypotheses SET status=?, updated_at=? WHERE hypothesis_id=?",
                (str(status), _now(), hypothesis_id),
            ).rowcount
        if not changed:
            raise HypothesisError(f"No hypothesis '{hypothesis_id}'.")
        if note:
            self.link(hypothesis_id, EdgeKind.RESULT, f"note:{status}", note=note)

    def link(self, source: str, kind: EdgeKind, target: str, *, note: str = "") -> None:
        """Record an edge. Idempotent: the same edge twice is one edge.

        Self-edges are refused. A hypothesis derived from itself is a cycle of
        length one, and the ancestry walk below would never terminate on it.
        """
        if source == target:
            raise HypothesisError("A hypothesis cannot link to itself.")
        if kind in INTERNAL_EDGES and self.get(target) is None:
            raise HypothesisError(
                f"{kind} must point at another hypothesis, and '{target}' is not one."
            )
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO edges (source, kind, target, note, at) VALUES (?,?,?,?,?)",
                (source, str(kind), target, note[:400], _now()),
            )

    # ── reads ────────────────────────────────────────────────────────────────
    def get(self, hypothesis_id: str) -> Hypothesis | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,)
            ).fetchone()
        return None if row is None else _to_hypothesis(row)

    def list(
        self,
        campaign_id: str | None = None,
        *,
        statuses: Iterable[HypothesisStatus] | None = None,
        family: str | None = None,
        limit: int = 500,
    ) -> builtins.list[Hypothesis]:
        clauses, args = [], []
        if campaign_id:
            clauses.append("campaign_id=?")
            args.append(campaign_id)
        if family:
            clauses.append("family=?")
            args.append(family)
        wanted = tuple(str(s) for s in statuses) if statuses else ()
        if wanted:
            clauses.append(f"status IN ({','.join('?' * len(wanted))})")
            args.extend(wanted)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT * FROM hypotheses{where} ORDER BY created_at DESC LIMIT ?",
                (*args, int(limit)),
            ).fetchall()
        return [_to_hypothesis(row) for row in rows]

    def edges(self, node_id: str, *, kind: EdgeKind | None = None) -> builtins.list[dict[str, Any]]:
        """Every edge touching this node, in either direction."""
        clause = " AND kind=?" if kind else ""
        args: tuple[Any, ...] = (node_id, node_id) + ((str(kind),) if kind else ())
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT * FROM edges WHERE (source=? OR target=?){clause} ORDER BY edge_id", args
            ).fetchall()
        return [
            {
                "source": row["source"],
                "kind": row["kind"],
                "target": row["target"],
                "note": row["note"],
                "at": row["at"],
            }
            for row in rows
        ]

    def ancestry(self, hypothesis_id: str, *, limit: int = 64) -> builtins.list[Hypothesis]:
        """The chain from this hypothesis back to its root, nearest first.

        Bounded by ``limit`` and by a visited set, so a graph that somehow
        acquired a cycle returns a truncated chain rather than hanging the
        engine thread that asked.
        """
        chain: list[Hypothesis] = []
        seen: set[str] = {hypothesis_id}
        node = self.get(hypothesis_id)
        while node is not None and node.parent_id and len(chain) < limit:
            if node.parent_id in seen:
                break
            seen.add(node.parent_id)
            parent = self.get(node.parent_id)
            if parent is None:
                break
            chain.append(parent)
            node = parent
        return chain

    def descendants(self, hypothesis_id: str, *, limit: int = 256) -> builtins.list[Hypothesis]:
        """Everything derived from this hypothesis, breadth-first."""
        found: list[Hypothesis] = []
        seen: set[str] = {hypothesis_id}
        queue = [hypothesis_id]
        while queue and len(found) < limit:
            current = queue.pop(0)
            with closing(self._connect()) as db:
                rows = db.execute(
                    "SELECT hypothesis_id FROM hypotheses WHERE parent_id=? ORDER BY created_at",
                    (current,),
                ).fetchall()
            for row in rows:
                child_id = row["hypothesis_id"]
                if child_id in seen:
                    continue
                seen.add(child_id)
                child = self.get(child_id)
                if child is not None:
                    found.append(child)
                    queue.append(child_id)
        return found

    def lineage(self, hypothesis_id: str) -> dict[str, Any]:
        """The full record for one hypothesis: the node, its chain, its edges.

        This is what "the graph must be queryable" means in practice — one call
        that answers where a claim came from, what tested it, and what came out
        of it.
        """
        node = self.get(hypothesis_id)
        if node is None:
            raise HypothesisError(f"No hypothesis '{hypothesis_id}'.")
        edges = self.edges(hypothesis_id)
        return {
            "hypothesis": node.as_dict(),
            "ancestry": [h.as_dict() for h in self.ancestry(hypothesis_id)],
            "descendants": [h.as_dict() for h in self.descendants(hypothesis_id)],
            "experiments": [e["target"] for e in edges if e["kind"] == EdgeKind.TESTED_BY],
            "results": [e["target"] for e in edges if e["kind"] == EdgeKind.RESULT],
            "validations": [e["target"] for e in edges if e["kind"] == EdgeKind.VALIDATED_BY],
            "sources": [e["target"] for e in edges if e["kind"] == EdgeKind.SOURCED_FROM],
            "findings": [e["target"] for e in edges if e["kind"] == EdgeKind.FINDING],
            "contradictions": [
                e["target"] if e["source"] == hypothesis_id else e["source"]
                for e in edges
                if e["kind"] == EdgeKind.CONTRADICTS
            ],
            "edges": edges,
        }

    def counts(self, campaign_id: str | None = None) -> dict[str, int]:
        where, args = ("WHERE campaign_id=?", (campaign_id,)) if campaign_id else ("", ())
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT status, COUNT(*) AS n FROM hypotheses {where} GROUP BY status", args
            ).fetchall()
        counts = {str(status): 0 for status in HypothesisStatus}
        for row in rows:
            counts[str(row["status"])] = int(row["n"])
        return counts

    def distinct_mechanisms(self, campaign_id: str | None = None) -> int:
        """How many distinct mechanisms the campaign has actually explored.

        The number that separates "a hundred discoveries" from "one idea, a
        hundred times". Reported next to the experiment count everywhere the
        interface shows progress.
        """
        where, args = ("WHERE campaign_id=?", (campaign_id,)) if campaign_id else ("", ())
        with closing(self._connect()) as db:
            row = db.execute(
                f"SELECT COUNT(DISTINCT mechanism) AS n FROM hypotheses {where}", args
            ).fetchone()
        return int(row["n"]) if row else 0


def _to_hypothesis(row: sqlite3.Row) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=row["hypothesis_id"],
        campaign_id=row["campaign_id"],
        parent_id=row["parent_id"],
        family=row["family"],
        mechanism=row["mechanism"],
        statement=row["statement"],
        prediction=row["prediction"],
        required_data=tuple(json.loads(row["required_data"])),
        missing_data=tuple(json.loads(row["missing_data"])),
        expected_horizon=row["expected_horizon"],
        status=HypothesisStatus(row["status"]),
        origin=row["origin"],
        search_kind=SearchKind(row["search_kind"]),
        novelty=float(row["novelty"]),
        nearest_id=row["nearest_id"],
        similarity=float(row["similarity"]),
        sources=tuple(json.loads(row["sources"])),
        frontier_item_id=row["frontier_item_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
