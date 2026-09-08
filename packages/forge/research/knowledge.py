"""Findings that outlive the analysis that produced them — as knowledge, never proof.

An analysis answers a question and is thrown away. Some of what it says is worth
keeping: *"the edge is concentrated in volatility percentiles 30-70"* is the kind
of sentence a researcher will want six months later, and re-deriving it means
remembering which of four hundred artifacts contained it.

This module keeps those sentences, with the provenance needed to go back to the
thing that said it.

**Three rules shape it, and each closes a specific way this could go wrong.**

*A finding cannot be written by hand.* ``promote`` accepts a statement only if it
appears **verbatim** in the ``findings`` of a stored artifact. Free text with a
provenance block stapled to it would be the most dangerous object in the system:
a claim that looks derived, cites a run, and was in fact typed. The store refuses
it, and ``test_a_statement_that_was_never_computed_cannot_be_promoted`` holds the
line.

*A finding is never evidence.* It carries ``is_evidence = False`` and there is no
route from here into ``JudgeInput`` — the judge reads gates and validation
artifacts, and nothing else. Memory is what the search has learned; a verdict is
what the evidence supports. Letting the first move the second is how a system
starts believing its own summaries.

*A finding can go stale, and saying so is the point.* A statement made about one
backtest can be contradicted by the next. ``supersede`` and ``retract`` keep the
original readable with its status changed, because deleting it would leave a
future reader unable to tell "we never looked" from "we looked and were wrong".

This is deliberately **not** ``forge.memory.research``. That store exists to
*prune*: it records failures and declines to spend compute on their
neighbourhoods. A finding must never prune anything — an observation about where
an edge concentrates is not a licence to stop testing where it does not, and
merging the two would give a descriptive sentence the power to delete
candidates.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import closing
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel

SCHEMA_VERSION = 1


class KnowledgeError(ValueError):
    """A finding could not be promoted, and the message says why."""


class FindingStatus(StrEnum):
    """Whether a finding still stands.

    Nothing is deleted. A retracted finding stays readable, because a future
    reader has to be able to tell "nobody looked at this" from "somebody looked
    and it turned out to be wrong".
    """

    STANDING = "STANDING"
    #: A later finding measures the same thing and disagrees, or measures it
    #: better. The original is kept and points forward.
    SUPERSEDED = "SUPERSEDED"
    #: Withdrawn. The reason is required.
    RETRACTED = "RETRACTED"


class ResearchFinding(FrozenModel):
    """One durable sentence, and the chain back to what produced it."""

    finding_id: str
    #: Copied verbatim from the artifact. Never composed here.
    statement: str
    #: The operator's own words about why it is worth keeping. This is the only
    #: free-text field, and it is explicitly commentary rather than measurement.
    note: str = ""

    analysis: str
    artifact_id: str
    artifact_content_hash: str
    strategy_id: str
    backtest_id: str
    dataset_key: str = ""
    spec_hash: str = ""
    code_hash: str = ""
    data_hash: str = ""
    evidence_tier: str = ""
    partition_name: str = ""
    #: How many of the run's trades the analysis actually covered. A finding
    #: from a slice that covered eleven trades and one that covered nine hundred
    #: are different claims, and the number travels with the sentence.
    covered_trades: int = 0
    total_trades: int = 0

    status: FindingStatus = FindingStatus.STANDING
    #: Set when `status` is SUPERSEDED: the finding that replaced this one.
    superseded_by: str = ""
    #: Set when `status` is RETRACTED. Required by `retract`.
    retraction_reason: str = ""

    created_at: datetime
    created_by: str = "operator"

    #: Structural, not decorative. Nothing in the judge reads a finding, and
    #: this field is asserted in the payload so a consumer that stumbled into
    #: one cannot mistake it for a measured result.
    is_evidence: Literal[False] = False


#: Why this is knowledge and not proof, carried on every response so a reader
#: never has to have read this module to know it.
NOT_EVIDENCE_NOTE = (
    "A finding is something the search learned, recorded with the artifact that "
    "said it. It is not evidence: it was not produced by the gate ladder, it "
    "consumed no holdout, and no verdict reads it."
)


def _row_to_finding(row: tuple[Any, ...]) -> ResearchFinding:
    return ResearchFinding.model_validate(json.loads(row[0]))


class KnowledgeStore:
    """Durable findings, one row each, with a projection for listing."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._prepare()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=15)

    def _prepare(self) -> None:
        with closing(self._connect()) as db, db:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            if version and version != SCHEMA_VERSION:
                # Unlike an analysis artifact, a finding is *not* recomputable —
                # it records that a person judged a sentence worth keeping. So a
                # shape change migrates rather than drops, and until there is a
                # second version this simply refuses to guess.
                raise KnowledgeError(
                    f"knowledge store is schema v{version}, this build expects "
                    f"v{SCHEMA_VERSION}; findings are not recomputable, so this "
                    "will not be dropped and recreated automatically"
                )
            db.execute(
                "CREATE TABLE IF NOT EXISTS findings ("
                "finding_id TEXT PRIMARY KEY, statement TEXT NOT NULL, "
                "analysis TEXT NOT NULL, artifact_id TEXT NOT NULL, "
                "strategy_id TEXT NOT NULL, backtest_id TEXT NOT NULL, "
                "dataset_key TEXT NOT NULL DEFAULT '', "
                "evidence_tier TEXT NOT NULL DEFAULT '', "
                "status TEXT NOT NULL, created_at TEXT NOT NULL, "
                "created_by TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS findings_by_strategy "
                "ON findings (strategy_id, created_at DESC)"
            )
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # ── writing ──────────────────────────────────────────────────────────────
    def promote(
        self,
        artifact: Mapping[str, Any],
        statement: str,
        *,
        note: str = "",
        created_by: str = "operator",
        now: datetime | None = None,
    ) -> ResearchFinding:
        """Keep one sentence an analysis produced.

        `statement` must appear verbatim in the artifact's `findings`. A claim
        that was typed rather than computed is exactly what this store must not
        contain, because it would be indistinguishable from one that was.
        """
        available = tuple(str(item) for item in artifact.get("findings", ()))
        wanted = statement.strip()
        if not wanted:
            raise KnowledgeError("a finding needs a statement")
        if wanted not in available:
            raise KnowledgeError(
                "that statement is not one this artifact produced. A finding is "
                "promoted from what an analysis computed, never written next to "
                "it — otherwise a typed claim and a measured one would be stored "
                "identically. The artifact says: "
                + ("; ".join(available) if available else "(nothing)")
            )

        provenance = dict(artifact.get("provenance") or {})
        artifact_id = str(artifact.get("artifact_id") or provenance.get("artifact_id") or "")
        if not artifact_id:
            raise KnowledgeError("the artifact carries no id, so nothing could cite it")

        finding = ResearchFinding(
            finding_id=stable_id("finding", {"artifact": artifact_id, "statement": wanted}),
            statement=wanted,
            note=note.strip(),
            analysis=str(artifact.get("analysis") or provenance.get("analysis") or ""),
            artifact_id=artifact_id,
            artifact_content_hash=str(artifact.get("content_hash") or ""),
            strategy_id=str(provenance.get("strategy_id") or ""),
            backtest_id=str(provenance.get("backtest_id") or ""),
            dataset_key=str(provenance.get("dataset_key") or ""),
            spec_hash=str(provenance.get("spec_hash") or ""),
            code_hash=str(provenance.get("code_hash") or ""),
            data_hash=str(provenance.get("data_hash") or ""),
            evidence_tier=str(provenance.get("evidence_tier") or ""),
            partition_name=str(provenance.get("partition_name") or ""),
            covered_trades=int(artifact.get("covered_trades") or 0),
            total_trades=int(artifact.get("total_trades") or 0),
            created_at=now or datetime.now(UTC),
            created_by=created_by,
        )
        self._write(finding)
        return finding

    def _write(self, finding: ResearchFinding) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO findings (finding_id, statement, analysis, "
                "artifact_id, strategy_id, backtest_id, dataset_key, evidence_tier, "
                "status, created_at, created_by, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    finding.finding_id,
                    finding.statement,
                    finding.analysis,
                    finding.artifact_id,
                    finding.strategy_id,
                    finding.backtest_id,
                    finding.dataset_key,
                    finding.evidence_tier,
                    str(finding.status),
                    finding.created_at.astimezone(UTC).isoformat(),
                    finding.created_by,
                    finding.model_dump_json(),
                ),
            )

    def retract(self, finding_id: str, reason: str) -> ResearchFinding:
        """Withdraw a finding, keeping it readable with the reason attached."""
        if not reason.strip():
            raise KnowledgeError(
                "a retraction needs a reason. A finding that simply disappears "
                "leaves a later reader unable to tell it from one nobody made."
            )
        existing = self.get(finding_id)
        if existing is None:
            raise KnowledgeError(f"no finding '{finding_id}'")
        updated = existing.model_copy(
            update={
                "status": FindingStatus.RETRACTED,
                "retraction_reason": reason.strip(),
            }
        )
        self._write(updated)
        return updated

    def supersede(self, finding_id: str, by_finding_id: str) -> ResearchFinding:
        """Mark a finding replaced by a later one, keeping both."""
        existing = self.get(finding_id)
        if existing is None:
            raise KnowledgeError(f"no finding '{finding_id}'")
        if self.get(by_finding_id) is None:
            raise KnowledgeError(f"no finding '{by_finding_id}' to supersede it with")
        if finding_id == by_finding_id:
            raise KnowledgeError("a finding cannot supersede itself")
        updated = existing.model_copy(
            update={"status": FindingStatus.SUPERSEDED, "superseded_by": by_finding_id}
        )
        self._write(updated)
        return updated

    # ── reading ──────────────────────────────────────────────────────────────
    def get(self, finding_id: str) -> ResearchFinding | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT payload FROM findings WHERE finding_id = ?", (finding_id,)
            ).fetchone()
        return None if row is None else _row_to_finding(row)

    def recall(
        self,
        *,
        strategy_id: str = "",
        query: str = "",
        status: FindingStatus | None = FindingStatus.STANDING,
        limit: int = 100,
    ) -> list[ResearchFinding]:
        """What is known, newest first.

        `status` defaults to STANDING so a caller asking "what do we know?" is
        not handed retracted claims by accident. Passing `None` returns
        everything, which is what an audit wants.
        """
        sql = "SELECT payload FROM findings"
        where: list[str] = []
        params: list[Any] = []
        if strategy_id:
            where.append("strategy_id = ?")
            params.append(strategy_id)
        if status is not None:
            where.append("status = ?")
            params.append(str(status))
        if query.strip():
            where.append("statement LIKE ?")
            params.append(f"%{query.strip()}%")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with closing(self._connect()) as db:
            rows = db.execute(sql, params).fetchall()
        return [_row_to_finding(row) for row in rows]

    def for_artifact(self, artifact_id: str) -> list[ResearchFinding]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT payload FROM findings WHERE artifact_id = ? ORDER BY created_at DESC",
                (artifact_id,),
            ).fetchall()
        return [_row_to_finding(row) for row in rows]

    def count(self, status: FindingStatus | None = None) -> int:
        with closing(self._connect()) as db:
            if status is None:
                return int(db.execute("SELECT COUNT(*) FROM findings").fetchone()[0])
            return int(
                db.execute(
                    "SELECT COUNT(*) FROM findings WHERE status = ?", (str(status),)
                ).fetchone()[0]
            )

    def counts(self) -> dict[str, int]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT status, COUNT(*) FROM findings GROUP BY status").fetchall()
        return {str(status): int(count) for status, count in rows}


def as_payload(findings: Iterable[ResearchFinding]) -> list[dict[str, Any]]:
    return [finding.model_dump(mode="json") for finding in findings]
