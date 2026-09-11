"""Why a proposal was not run, recorded so it can be argued with.

The old behaviour: a refused proposal incremented a counter called
``skipped_by_memory`` and wrote a line to the activity log. The counter mixed
five unrelated reasons together, the log line was prose, and neither survived a
restart. A user looking at "Skipped by memory: 1,290" could not tell whether the
system had saved them a fortune in compute or had quietly stopped doing research
eleven minutes ago.

This module is the fix, and it has two parts.

**A novelty hierarchy.** Deduplication used to be a single similarity number
against a single threshold, which forces one decision to answer two different
questions: "is this the same experiment?" and "is this the same idea?". They are
not the same question. A parameter set already tested is an exact duplicate and
running it again buys nothing. A *new mechanism* that happens to be phrased in
familiar words is not a duplicate at all, and refusing it is how a research
system stops discovering. So the verdict is a band —
:class:`NoveltyLevel` — and each band carries its own answer to "may this be
retried, and under what circumstances".

**A durable ledger.** Every skip writes a row naming what was skipped, what it
collided with, which band, what score, and whether a retry is permitted. The row
is the evidence for the counter. "Duplicates prevented" then means something
checkable, and "the engine has refused 1,290 proposals" becomes a question
anybody can answer by reading the reasons.

Nothing in here decides what to research. It records what was declined.
"""

from __future__ import annotations

import builtins
import json
import sqlite3
import threading
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from forge.contracts.hashing import stable_id

SCHEMA_VERSION = 1


class NoveltyLevel(StrEnum):
    """How new a proposal is, as a band rather than a number.

    Ordered from "identical" to "genuinely new". The ordering is meaningful:
    :func:`admits` reads it, and a level further down the list is always at
    least as novel as one above.
    """

    #: Same template, same parameters, same scope. Running it again produces a
    #: byte-identical result and inflates the trial count the Deflated Sharpe
    #: deflates against.
    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    #: Different only in ways that cannot change the answer — a parameter step
    #: inside the neighbourhood of a recorded failure whose reason generalises.
    NEAR_DUPLICATE = "NEAR_DUPLICATE"
    #: The same signal construction, differently parameterised. Legitimate as a
    #: refinement; not a discovery.
    SAME_CONSTRUCTION = "SAME_CONSTRUCTION"
    #: A different construction reading the same economic mechanism.
    SAME_MECHANISM = "SAME_MECHANISM"
    #: A claim adjacent to one already made — a follow-up, an inverse, a
    #: conditional restriction of an existing hypothesis.
    RELATED_HYPOTHESIS = "RELATED_HYPOTHESIS"
    #: A claim nothing on record makes.
    NOVEL_HYPOTHESIS = "NOVEL_HYPOTHESIS"
    #: A mechanism no registered family expresses.
    NOVEL_MECHANISM = "NOVEL_MECHANISM"
    #: A feature construction the catalogue does not contain.
    NOVEL_FEATURE = "NOVEL_FEATURE"


#: Ascending novelty. Index into this for comparisons.
LEVEL_ORDER: tuple[NoveltyLevel, ...] = (
    NoveltyLevel.EXACT_DUPLICATE,
    NoveltyLevel.NEAR_DUPLICATE,
    NoveltyLevel.SAME_CONSTRUCTION,
    NoveltyLevel.SAME_MECHANISM,
    NoveltyLevel.RELATED_HYPOTHESIS,
    NoveltyLevel.NOVEL_HYPOTHESIS,
    NoveltyLevel.NOVEL_MECHANISM,
    NoveltyLevel.NOVEL_FEATURE,
)

#: The levels that are *not* research worth spending compute on by default.
#:
#: ``SAME_CONSTRUCTION`` is deliberately **not** here. Refining parameters
#: within a known construction is ordinary research; the thing that stops it
#: running away is the budget allocation, not the novelty gate. Treating it as
#: a duplicate is what made the engine refuse 94% of its own proposals.
REFUSED_BY_DEFAULT = frozenset({NoveltyLevel.EXACT_DUPLICATE, NoveltyLevel.NEAR_DUPLICATE})


def rank(level: NoveltyLevel) -> int:
    return LEVEL_ORDER.index(level)


def admits(level: NoveltyLevel, *, floor: NoveltyLevel = NoveltyLevel.SAME_CONSTRUCTION) -> bool:
    """Is a proposal at ``level`` novel enough to run?

    ``floor`` is the lowest band a campaign is willing to spend compute on, so a
    discovery-heavy campaign can raise it to ``NOVEL_HYPOTHESIS`` and a
    refinement campaign can leave it at the default.
    """
    return rank(level) >= rank(floor)


def level_from_score(
    score: float,
    *,
    mechanism: float | None = None,
    features: float | None = None,
) -> NoveltyLevel:
    """Map a similarity score, and its components, onto a band.

    ``score`` is the combined similarity to the nearest prior subject, in
    ``[0, 1]`` where 1 is identical. The components refine the band: two
    proposals that read the same mechanism are ``SAME_MECHANISM`` even when
    their statements diverge, because the mechanism is what makes them the same
    piece of research.
    """
    if score >= 0.98:
        return NoveltyLevel.EXACT_DUPLICATE
    if score >= 0.88:
        return NoveltyLevel.NEAR_DUPLICATE
    if features is not None and features >= 0.95 and score >= 0.60:
        return NoveltyLevel.SAME_CONSTRUCTION
    if score >= 0.70:
        return NoveltyLevel.SAME_CONSTRUCTION
    if mechanism is not None and mechanism >= 0.70:
        return NoveltyLevel.SAME_MECHANISM
    if score >= 0.45:
        return NoveltyLevel.RELATED_HYPOTHESIS
    if features is not None and features <= 0.10:
        return NoveltyLevel.NOVEL_FEATURE
    return NoveltyLevel.NOVEL_HYPOTHESIS


class SkipKind(StrEnum):
    """Which gate did the refusing.

    Kept separate from the novelty band because the two answer different
    questions: the band says *how close* the proposal was to existing research,
    and this says *which mechanism* declined it.
    """

    #: `Experiments.reserve` found the identical claim.
    EXPERIMENT_CLAIMED = "EXPERIMENT_CLAIMED"
    #: `ResearchMemory.prune` found a failure whose reason reaches this point.
    FAILURE_REGION = "FAILURE_REGION"
    #: The whole template is condemned by a code-level failure.
    TEMPLATE_CONDEMNED = "TEMPLATE_CONDEMNED"
    #: The novelty gate refused a restatement.
    NOT_NOVEL = "NOT_NOVEL"
    #: The frontier had nothing eligible.
    NO_ELIGIBLE_WORK = "NO_ELIGIBLE_WORK"
    #: A capability the dataset cannot serve.
    CAPABILITY_BLOCKED = "CAPABILITY_BLOCKED"
    #: The campaign reached a stopping criterion.
    CAMPAIGN_EXHAUSTED = "CAMPAIGN_EXHAUSTED"
    #: Another agent holds the claim.
    CLAIMED_BY_AGENT = "CLAIMED_BY_AGENT"
    #: The proposal builder raised.
    PROPOSAL_ERROR = "PROPOSAL_ERROR"


#: Skip kinds that genuinely saved compute: something *would* have been run and
#: was not. The rest refused nothing, because there was nothing to refuse, and
#: counting them as saved compute is how "compute_saved" became fiction.
SAVED_COMPUTE = frozenset(
    {
        SkipKind.EXPERIMENT_CLAIMED,
        SkipKind.FAILURE_REGION,
        SkipKind.TEMPLATE_CONDEMNED,
        SkipKind.NOT_NOVEL,
    }
)

#: Skip kinds that mean the engine has nothing to do, as opposed to having
#: declined something. These are the ones that must never read as "efficiency".
WASTED = frozenset(
    {
        SkipKind.NO_ELIGIBLE_WORK,
        SkipKind.CAPABILITY_BLOCKED,
        SkipKind.PROPOSAL_ERROR,
    }
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Skip:
    """One refusal, with everything needed to argue it was wrong."""

    skip_id: str
    campaign_id: str
    kind: SkipKind
    level: NoveltyLevel
    #: What was proposed, as a human-readable handle.
    subject: str
    reason: str
    #: The prior object this collided with, if there was one. ``None`` for the
    #: kinds that refused nothing.
    matched: str | None = None
    matched_kind: str = ""
    similarity: float | None = None
    retry_permitted: bool = False
    retry_condition: str = ""
    template: str = ""
    parameters: dict[str, float] = field(default_factory=dict)
    worker_id: str = ""
    agent_id: str = ""
    saved_compute: bool = False
    created_at: str = field(default_factory=_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "skip_id": self.skip_id,
            "campaign_id": self.campaign_id,
            "kind": str(self.kind),
            "level": str(self.level),
            "subject": self.subject,
            "reason": self.reason,
            "matched": self.matched,
            "matched_kind": self.matched_kind,
            "similarity": None if self.similarity is None else round(self.similarity, 4),
            "retry_permitted": self.retry_permitted,
            "retry_condition": self.retry_condition,
            "template": self.template,
            "parameters": dict(self.parameters),
            "worker_id": self.worker_id,
            "agent_id": self.agent_id,
            "saved_compute": self.saved_compute,
            "created_at": self.created_at,
        }

    def describe(self) -> str:
        """One sentence a person can read."""
        band = str(self.level).replace("_", " ").lower()
        against = f" against {self.matched}" if self.matched else ""
        score = f" ({self.similarity:.2f})" if self.similarity is not None else ""
        retry = " — retry permitted" if self.retry_permitted else ""
        return f"{self.subject}: {band}{against}{score}. {self.reason}{retry}"


#: What makes each band retryable, and under what condition. A band with an
#: empty condition may be retried freely; one with a condition may be retried
#: only when the condition is met, and the condition is the sentence shown.
RETRY_RULES: dict[NoveltyLevel, tuple[bool, str]] = {
    NoveltyLevel.EXACT_DUPLICATE: (
        False,
        "the identical experiment exists; re-running it would inflate the trial count",
    ),
    NoveltyLevel.NEAR_DUPLICATE: (
        True,
        "permitted once the mechanism, the construction or the data changes — the prior "
        "failure is about the region, not about this claim",
    ),
    NoveltyLevel.SAME_CONSTRUCTION: (True, ""),
    NoveltyLevel.SAME_MECHANISM: (True, ""),
    NoveltyLevel.RELATED_HYPOTHESIS: (True, ""),
    NoveltyLevel.NOVEL_HYPOTHESIS: (True, ""),
    NoveltyLevel.NOVEL_MECHANISM: (True, ""),
    NoveltyLevel.NOVEL_FEATURE: (True, ""),
}


def retry_rule(level: NoveltyLevel) -> tuple[bool, str]:
    return RETRY_RULES.get(level, (True, ""))


class SkipLedger:
    """Durable, queryable record of every refusal.

    One SQLite file, written by every gate that declines a proposal. Reads are
    aggregate: the interface asks "how many, of what kind, against what" and
    gets numbers backed by rows rather than by an in-memory counter that resets
    on restart.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with closing(self._connect()) as db, db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS skips (
                  skip_id TEXT PRIMARY KEY,
                  campaign_id TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  level TEXT NOT NULL,
                  subject TEXT NOT NULL,
                  reason TEXT NOT NULL DEFAULT '',
                  matched TEXT,
                  matched_kind TEXT NOT NULL DEFAULT '',
                  similarity REAL,
                  retry_permitted INTEGER NOT NULL DEFAULT 0,
                  retry_condition TEXT NOT NULL DEFAULT '',
                  template TEXT NOT NULL DEFAULT '',
                  parameters TEXT NOT NULL DEFAULT '{}',
                  worker_id TEXT NOT NULL DEFAULT '',
                  agent_id TEXT NOT NULL DEFAULT '',
                  saved_compute INTEGER NOT NULL DEFAULT 0,
                  occurrences INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS skips_campaign ON skips(campaign_id)")
            db.execute("CREATE INDEX IF NOT EXISTS skips_kind ON skips(campaign_id, kind)")
            db.execute("CREATE INDEX IF NOT EXISTS skips_level ON skips(campaign_id, level)")
            db.execute("CREATE INDEX IF NOT EXISTS skips_time ON skips(created_at)")
            db.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    # ── writing ──────────────────────────────────────────────────────────────
    def record(
        self,
        *,
        campaign_id: str,
        kind: SkipKind,
        level: NoveltyLevel,
        subject: str,
        reason: str,
        matched: str | None = None,
        matched_kind: str = "",
        similarity: float | None = None,
        template: str = "",
        parameters: dict[str, float] | None = None,
        worker_id: str = "",
        agent_id: str = "",
    ) -> Skip:
        """Record one refusal.

        Identical refusals collapse onto one row with an occurrence count. Eight
        workers refusing the same proposal in the same second is one fact about
        the research, not eight, and a ledger that stored it eight times would
        reproduce exactly the inflation this module exists to remove.
        """
        retry_permitted, retry_condition = retry_rule(level)
        params = dict(parameters or {})
        skip_id = stable_id(
            "skip",
            {
                "campaign": campaign_id,
                "kind": str(kind),
                "subject": subject,
                "matched": matched or "",
                "template": template,
                "params": params,
            },
        )
        skip = Skip(
            skip_id=skip_id,
            campaign_id=campaign_id,
            kind=kind,
            level=level,
            subject=subject,
            reason=reason,
            matched=matched,
            matched_kind=matched_kind,
            similarity=similarity,
            retry_permitted=retry_permitted,
            retry_condition=retry_condition,
            template=template,
            parameters=params,
            worker_id=worker_id,
            agent_id=agent_id,
            saved_compute=kind in SAVED_COMPUTE,
        )
        now = _now()
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                """
                INSERT INTO skips
                  (skip_id, campaign_id, kind, level, subject, reason, matched, matched_kind,
                   similarity, retry_permitted, retry_condition, template, parameters,
                   worker_id, agent_id, saved_compute, occurrences, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)
                ON CONFLICT(skip_id) DO UPDATE SET
                  occurrences = occurrences + 1,
                  updated_at = excluded.updated_at,
                  reason = excluded.reason
                """,
                (
                    skip.skip_id,
                    skip.campaign_id,
                    str(skip.kind),
                    str(skip.level),
                    skip.subject,
                    skip.reason,
                    skip.matched,
                    skip.matched_kind,
                    skip.similarity,
                    int(skip.retry_permitted),
                    skip.retry_condition,
                    skip.template,
                    json.dumps(params, sort_keys=True),
                    skip.worker_id,
                    skip.agent_id,
                    int(skip.saved_compute),
                    now,
                    now,
                ),
            )
        return skip

    # ── reading ──────────────────────────────────────────────────────────────
    def list(
        self,
        campaign_id: str | None = None,
        *,
        kind: SkipKind | None = None,
        level: NoveltyLevel | None = None,
        limit: int = 200,
    ) -> builtins.list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if campaign_id:
            clauses.append("campaign_id=?")
            args.append(campaign_id)
        if kind:
            clauses.append("kind=?")
            args.append(str(kind))
        if level:
            clauses.append("level=?")
            args.append(str(level))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT * FROM skips {where} ORDER BY updated_at DESC LIMIT ?",
                (*args, int(limit)),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def counts(self, campaign_id: str | None = None) -> dict[str, Any]:
        """The accounting that replaces ``skipped_by_memory``.

        Every field here is derived from rows, so the numbers can be drilled
        into. ``useful`` and ``wasted`` are the split the dashboard needs: a
        skip that declined an experiment saved compute, and a skip that happened
        because there was nothing to do saved nothing and is a symptom.
        """
        where, args = ("WHERE campaign_id=?", (campaign_id,)) if campaign_id else ("", ())
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT kind, level, saved_compute, SUM(occurrences) AS n, COUNT(*) AS distinct_n "
                f"FROM skips {where} GROUP BY kind, level, saved_compute",
                args,
            ).fetchall()
        by_kind: Counter[str] = Counter()
        by_level: Counter[str] = Counter()
        useful = wasted = total = distinct = 0
        for row in rows:
            n = int(row["n"] or 0)
            by_kind[str(row["kind"])] += n
            by_level[str(row["level"])] += n
            total += n
            distinct += int(row["distinct_n"] or 0)
            if int(row["saved_compute"] or 0):
                useful += n
            elif SkipKind(str(row["kind"])) in WASTED:
                wasted += n
        return {
            "total": total,
            "distinct": distinct,
            "useful": useful,
            "wasted": wasted,
            # Neither useful nor wasted: exhaustion and agent contention are
            # facts about the programme, not about efficiency.
            "neutral": total - useful - wasted,
            "by_kind": {str(k): by_kind.get(str(k), 0) for k in SkipKind},
            "by_level": {str(level): by_level.get(str(level), 0) for level in NoveltyLevel},
        }

    def total(self, campaign_id: str | None = None) -> int:
        return int(self.counts(campaign_id)["total"])

    def retryable(self, campaign_id: str, *, limit: int = 100) -> builtins.list[dict[str, Any]]:
        """Refusals the system is willing to reconsider.

        This is what makes the ledger more than a log: a skip that said "retry
        permitted once the mechanism changes" is an open research question, and
        the director reads these when the frontier looks empty.
        """
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM skips WHERE campaign_id=? AND retry_permitted=1 "
                "ORDER BY occurrences DESC, updated_at DESC LIMIT ?",
                (campaign_id, int(limit)),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def purge(self, campaign_id: str) -> int:
        with self._lock, closing(self._connect()) as db, db:
            cursor = db.execute("DELETE FROM skips WHERE campaign_id=?", (campaign_id,))
        return int(cursor.rowcount or 0)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["parameters"] = json.loads(data.get("parameters") or "{}")
    data["retry_permitted"] = bool(data.get("retry_permitted"))
    data["saved_compute"] = bool(data.get("saved_compute"))
    return data
