"""Research agents: who is asking which question, and who may not ask it too.

Before this, "agents" meant two unrelated things and neither was a research
worker. `forge.agents.skills` holds *LLM role contracts* — a scout, a hypothesis
analyst — run on a daily call cap by `forge_api.agent_service`. And the engine's
"workers" were threads whose research policy was a function of their **thread
index**: worker 3 did neighbourhood search because it was worker 3. Adding a
worker did not add a researcher; it added one more thread drawing from the same
pool, which is how eight of them ended up proposing each other's candidates.

A `ResearchAgent` here is a first-class, persisted record: a role, a campaign, a
state, a lease, a heartbeat and a claim. Three things follow that could not
before.

**Roles are a research decision, not a thread id.** A campaign can ask for two
falsification agents and one literature agent, and that allocation survives a
restart.

**Claims stop duplicated expensive work.** An agent claims a hypothesis before
spending compute on it, and the claim has an expiry: an agent that dies releases
its work when the lease lapses rather than when the process restarts. Replication
is still possible — it is just no longer accidental, because a replica has to ask
for the claim knowing one exists.

**Capacity is honest.** :func:`capacity_for` answers how many agents this
installation can actually run. A user asking for 32 on a machine that can serve 8
is told so rather than shown 32 rows doing nothing.

Nothing here can produce evidence. An agent records *what it is working on*; the
judge remains the only thing that can say what a result means.
"""

from __future__ import annotations

import builtins
import os
import sqlite3
import threading
from collections import Counter
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from forge.contracts.hashing import stable_id

SCHEMA_VERSION = 1

#: How long a claim is held without a heartbeat before another agent may take
#: it. Longer than the slowest single experiment, because expiring a lease an
#: agent is still working under is how two agents run the same expensive thing.
CLAIM_LEASE_SECONDS = 900.0

#: An agent that has not beaten within this is presumed gone.
AGENT_STALE_SECONDS = 300.0

#: The ceiling the interface must not exceed regardless of what is asked for.
#: Not a licence limit — a statement about what a single process can usefully
#: schedule before the scheduling costs more than the research.
MAX_AGENTS = 64


class AgentRole(StrEnum):
    """What kind of research this agent does.

    Each role is a different *question*, not a different parameter draw. That is
    the distinction the old policy-per-thread scheme could not express: a
    falsification agent and a discovery agent disagree about what a good
    candidate looks like, and running eight copies of one loop cannot represent
    that disagreement.
    """

    #: Search for genuinely new mechanisms.
    DISCOVERY = "DISCOVERY"
    #: Retrieve academic and industry evidence, with citations kept.
    LITERATURE = "LITERATURE"
    #: Investigate feature and signal constructions.
    FEATURE = "FEATURE"
    #: Generate falsifiable hypotheses.
    HYPOTHESIS = "HYPOTHESIS"
    #: Attack promising candidates rather than confirm them.
    FALSIFICATION = "FALSIFICATION"
    #: Investigate conditional and regime-dependent effects.
    REGIME = "REGIME"
    #: Stress-test candidates that survived.
    ROBUSTNESS = "ROBUSTNESS"
    #: Run formal validation on eligible candidates only.
    VALIDATION = "VALIDATION"
    #: Critique research lineage and evidence quality.
    REVIEWER = "REVIEWER"
    #: A user-defined role, described by its objective.
    SPECIALIST = "SPECIALIST"


class AgentState(StrEnum):
    CREATED = "CREATED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    IDLE = "IDLE"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


#: States in which an agent should be given work.
ELIGIBLE = frozenset({AgentState.STARTING, AgentState.RUNNING, AgentState.IDLE, AgentState.WAITING})

#: What each role is for, in one sentence, for the interface. Kept beside the
#: enum so a role added without an explanation is obvious in review.
ROLE_PURPOSE: dict[AgentRole, str] = {
    AgentRole.DISCOVERY: "Proposes mechanisms nothing on record already claims.",
    AgentRole.LITERATURE: "Retrieves published evidence and keeps its provenance.",
    AgentRole.FEATURE: "Investigates how a signal is constructed, not how it is tuned.",
    AgentRole.HYPOTHESIS: "Turns a mechanism into a claim that can be shown false.",
    AgentRole.FALSIFICATION: "Attacks the candidates that look best.",
    AgentRole.REGIME: "Asks whether an effect only exists under a condition.",
    AgentRole.ROBUSTNESS: "Stress-tests what survived, on purpose, until it breaks.",
    AgentRole.VALIDATION: "Runs formal validation on candidates that earned it.",
    AgentRole.REVIEWER: "Critiques lineage and evidence quality rather than results.",
    AgentRole.SPECIALIST: "A role you defined, described by its own objective.",
}

#: Which allocation buckets each role prefers to draw from. Advisory: the
#: orchestrator uses it to bias assignment, never to forbid one.
ROLE_BUCKETS: dict[AgentRole, tuple[str, ...]] = {
    AgentRole.DISCOVERY: ("DISCOVER_FAMILY", "EXPLORE_HYPOTHESIS"),
    AgentRole.LITERATURE: ("DISCOVER_FAMILY",),
    # Composing a construction, and changing the *structure* of one that
    # exists. `BUCKET_KIND` makes this exact: DISCOVER_FAMILY produces
    # SearchKind.FAMILY and ADVANCE_PROMISING produces SearchKind.STRUCTURAL,
    # which is what "how a signal is constructed" means here. REFINE_PARAMETERS
    # is deliberately absent -- the role's own sentence says "not how it is
    # tuned". It previously held the same two buckets as DISCOVERY, in the
    # other order, and since the draw tests membership rather than order the
    # two roles were one role with two names.
    AgentRole.FEATURE: ("DISCOVER_FAMILY", "ADVANCE_PROMISING"),
    AgentRole.HYPOTHESIS: ("EXPLORE_HYPOTHESIS",),
    AgentRole.FALSIFICATION: ("ADVANCE_PROMISING", "ROBUSTNESS"),
    AgentRole.REGIME: ("EXPLORE_HYPOTHESIS", "ADVANCE_PROMISING"),
    AgentRole.ROBUSTNESS: ("ROBUSTNESS", "REFINE_PARAMETERS"),
    AgentRole.VALIDATION: ("ADVANCE_PROMISING",),
    AgentRole.REVIEWER: ("ROBUSTNESS",),
    AgentRole.SPECIALIST: (),
}

def default_roles(count: int) -> tuple[AgentRole, ...]:
    """``count`` roles, in the order they earn their place.

    Discovery first, because a campaign with no discovery agent can only refine
    what it already has. Falsification third, because a crew that only ever
    confirms is worse than no crew: it produces the same number of findings and
    none of the objections.
    """
    order = (
        AgentRole.DISCOVERY,
        AgentRole.HYPOTHESIS,
        AgentRole.FALSIFICATION,
        AgentRole.FEATURE,
        AgentRole.ROBUSTNESS,
        AgentRole.REGIME,
        AgentRole.VALIDATION,
        AgentRole.LITERATURE,
        AgentRole.REVIEWER,
    )
    if count <= 0:
        return ()
    return tuple(order[index % len(order)] for index in range(count))


class AgentError(ValueError):
    """A refused agent operation."""


def capacity_for(*, requested: int, cpu_count: int | None = None) -> tuple[int, str]:
    """How many agents this installation can actually run, and why.

    Returns the granted count and a sentence explaining any shortfall. The
    interface shows that sentence rather than silently rendering thirty-two rows
    that will never do anything — an interface that pretends compute exists is a
    worse failure than one that says it does not.
    """
    requested = max(0, int(requested))
    cores = cpu_count if cpu_count is not None else (os.cpu_count() or 2)
    # Two agents per core: the backtest loop is numpy-heavy and releases the GIL
    # for most of its work, so agents overlap well, but not without limit.
    servable = max(1, min(MAX_AGENTS, cores * 2))
    if requested <= servable:
        return requested, ""
    return servable, (
        f"{requested} agents were requested; this installation can serve {servable} "
        f"({cores} cores). The rest would queue without doing research, so they "
        "were not created."
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime | None) -> str:
    return (moment or _now()).isoformat(timespec="seconds")


@dataclass
class ResearchAgent:
    """One research worker, as stored."""

    agent_id: str
    campaign_id: str
    role: AgentRole
    name: str
    objective: str
    state: AgentState
    #: What it is working on right now, in words.
    current_task: str = ""
    #: The hypothesis or frontier item it holds a claim on.
    current_claim: str = ""
    #: Backtest-equivalent units this agent may still spend. Zero means
    #: unmetered, which is the default for a locally-run installation.
    compute_budget: float = 0.0
    compute_spent: float = 0.0
    experiments: int = 0
    findings: int = 0
    errors: int = 0
    heartbeat_at: str = field(default_factory=lambda: _iso(None))
    progress_at: str = ""
    last_result: str = ""
    last_error: str = ""
    created_at: str = field(default_factory=lambda: _iso(None))
    updated_at: str = field(default_factory=lambda: _iso(None))

    def stale(self, *, now: datetime | None = None) -> bool:
        if self.state not in ELIGIBLE:
            return False
        moment = now or _now()
        return (moment - datetime.fromisoformat(self.heartbeat_at)).total_seconds() > (
            AGENT_STALE_SECONDS
        )

    def exhausted(self) -> bool:
        return bool(self.compute_budget) and self.compute_spent >= self.compute_budget

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "campaign_id": self.campaign_id,
            "role": str(self.role),
            "role_purpose": ROLE_PURPOSE[self.role],
            "name": self.name,
            "objective": self.objective,
            "state": str(self.state),
            "current_task": self.current_task,
            "current_claim": self.current_claim,
            "compute_budget": round(self.compute_budget, 2),
            "compute_spent": round(self.compute_spent, 2),
            "experiments": self.experiments,
            "findings": self.findings,
            "errors": self.errors,
            "heartbeat_at": self.heartbeat_at,
            "progress_at": self.progress_at,
            "stale": self.stale(),
            "exhausted": self.exhausted(),
            "last_result": self.last_result,
            "last_error": self.last_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class Claim:
    """One agent's exclusive hold on a piece of research, with an expiry."""

    claim_id: str
    campaign_id: str
    subject: str
    agent_id: str
    #: A replica is a deliberate second run of the same subject. It is recorded
    #: as one so the trial count knows, and so "we ran it twice" can never be
    #: mistaken for "two independent findings".
    replica_of: str = ""
    claimed_at: str = field(default_factory=lambda: _iso(None))
    expires_at: str = ""

    def expired(self, *, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        return (now or _now()) > datetime.fromisoformat(self.expires_at)

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "campaign_id": self.campaign_id,
            "subject": self.subject,
            "agent_id": self.agent_id,
            "replica_of": self.replica_of,
            "claimed_at": self.claimed_at,
            "expires_at": self.expires_at,
            "expired": self.expired(),
        }


class AgentRegistry:
    """Durable agents and their claims.

    One SQLite file. Everything an agent is — including what it currently holds
    — survives a restart, because an unattended run that forgets which agent was
    doing what has to start the whole campaign again.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with closing(self._connect()) as db, db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS agents (
                  agent_id TEXT PRIMARY KEY,
                  campaign_id TEXT NOT NULL,
                  role TEXT NOT NULL,
                  name TEXT NOT NULL,
                  objective TEXT NOT NULL DEFAULT '',
                  state TEXT NOT NULL,
                  current_task TEXT NOT NULL DEFAULT '',
                  current_claim TEXT NOT NULL DEFAULT '',
                  compute_budget REAL NOT NULL DEFAULT 0,
                  compute_spent REAL NOT NULL DEFAULT 0,
                  experiments INTEGER NOT NULL DEFAULT 0,
                  findings INTEGER NOT NULL DEFAULT 0,
                  errors INTEGER NOT NULL DEFAULT 0,
                  heartbeat_at TEXT NOT NULL,
                  progress_at TEXT NOT NULL DEFAULT '',
                  last_result TEXT NOT NULL DEFAULT '',
                  last_error TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS claims (
                  claim_id TEXT PRIMARY KEY,
                  campaign_id TEXT NOT NULL,
                  subject TEXT NOT NULL,
                  agent_id TEXT NOT NULL,
                  replica_of TEXT NOT NULL DEFAULT '',
                  claimed_at TEXT NOT NULL,
                  expires_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS agents_campaign ON agents(campaign_id)")
            db.execute("CREATE INDEX IF NOT EXISTS claims_campaign ON claims(campaign_id)")
            # The uniqueness that makes a claim a claim. Two agents cannot hold
            # the same subject in the same campaign; a deliberate replica is a
            # different claim_id with `replica_of` set, which is why that column
            # is part of the key.
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS claims_subject "
                "ON claims(campaign_id, subject, replica_of)"
            )
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION:d}")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    # ── agents ───────────────────────────────────────────────────────────────
    def create(
        self,
        *,
        campaign_id: str,
        role: AgentRole | str,
        name: str = "",
        objective: str = "",
        compute_budget: float = 0.0,
    ) -> ResearchAgent:
        role = AgentRole(str(role).upper())
        now = _iso(None)
        existing = len(self.list(campaign_id))
        agent = ResearchAgent(
            agent_id=stable_id(
                "agent", {"campaign": campaign_id, "role": str(role), "n": existing, "at": now}
            ),
            campaign_id=campaign_id,
            role=role,
            name=name.strip()[:80] or f"{str(role).title().replace('_', ' ')} {existing + 1}",
            objective=objective.strip()[:1000] or ROLE_PURPOSE[role],
            state=AgentState.CREATED,
            compute_budget=max(0.0, float(compute_budget)),
            created_at=now,
            updated_at=now,
            heartbeat_at=now,
        )
        self.save(agent)
        return agent

    def deploy(
        self,
        *,
        campaign_id: str,
        count: int,
        roles: Sequence[AgentRole] | None = None,
        compute_budget: float = 0.0,
    ) -> tuple[list[ResearchAgent], str]:
        """Create ``count`` agents, subject to what can actually be run.

        Returns the agents and a note about any shortfall. The note is not an
        error: asking for 32 on a 4-core machine is a reasonable thing to ask,
        and the honest answer is "here are 8, and here is why".
        """
        granted, note = capacity_for(requested=count)
        chosen = tuple(roles) if roles else default_roles(granted)
        created = [
            self.create(
                campaign_id=campaign_id,
                role=chosen[index % len(chosen)] if chosen else AgentRole.DISCOVERY,
                compute_budget=compute_budget,
            )
            for index in range(granted)
        ]
        return created, note

    def save(self, agent: ResearchAgent) -> ResearchAgent:
        agent.updated_at = _iso(None)
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO agents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    agent.agent_id,
                    agent.campaign_id,
                    str(agent.role),
                    agent.name,
                    agent.objective,
                    str(agent.state),
                    agent.current_task,
                    agent.current_claim,
                    agent.compute_budget,
                    agent.compute_spent,
                    agent.experiments,
                    agent.findings,
                    agent.errors,
                    agent.heartbeat_at,
                    agent.progress_at,
                    agent.last_result,
                    agent.last_error,
                    agent.created_at,
                    agent.updated_at,
                ),
            )
        return agent

    def get(self, agent_id: str) -> ResearchAgent | None:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
        return None if row is None else _to_agent(row)

    def list(
        self, campaign_id: str | None = None, *, state: AgentState | None = None
    ) -> builtins.list[ResearchAgent]:
        clauses: list[str] = []
        args: list[Any] = []
        if campaign_id:
            clauses.append("campaign_id=?")
            args.append(campaign_id)
        if state:
            clauses.append("state=?")
            args.append(str(state))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with closing(self._connect()) as db:
            rows = db.execute(f"SELECT * FROM agents {where} ORDER BY created_at", args).fetchall()
        return [_to_agent(row) for row in rows]

    def eligible(self, campaign_id: str) -> builtins.list[ResearchAgent]:
        """Agents this campaign can hand work to right now."""
        return [
            agent
            for agent in self.list(campaign_id)
            if agent.state in ELIGIBLE and not agent.exhausted()
        ]

    def set_state(
        self, agent_id: str, state: AgentState, *, task: str = "", error: str = ""
    ) -> ResearchAgent:
        agent = self.require(agent_id)
        agent.state = state
        if task:
            agent.current_task = task[:400]
        if error:
            agent.last_error = error[:600]
            agent.errors += 1
        agent.heartbeat_at = _iso(None)
        return self.save(agent)

    def beat(self, agent_id: str, *, task: str = "") -> ResearchAgent:
        agent = self.require(agent_id)
        agent.heartbeat_at = _iso(None)
        if task:
            agent.current_task = task[:400]
        return self.save(agent)

    def record(
        self,
        agent_id: str,
        *,
        experiments: int = 0,
        findings: int = 0,
        compute: float = 0.0,
        result: str = "",
    ) -> ResearchAgent:
        """Register work actually done. Only this moves the progress mark."""
        agent = self.require(agent_id)
        agent.experiments += int(experiments)
        agent.findings += int(findings)
        agent.compute_spent += float(compute)
        if result:
            agent.last_result = result[:600]
        now = _iso(None)
        agent.heartbeat_at = now
        if experiments or findings:
            agent.progress_at = now
        if agent.exhausted():
            agent.state = AgentState.COMPLETED
            agent.current_task = "compute budget spent"
        return self.save(agent)

    def require(self, agent_id: str) -> ResearchAgent:
        agent = self.get(agent_id)
        if agent is None:
            raise AgentError(f"No agent '{agent_id}'.")
        return agent

    def remove(self, agent_id: str) -> bool:
        with self._lock, closing(self._connect()) as db, db:
            db.execute("DELETE FROM claims WHERE agent_id=?", (agent_id,))
            return bool(db.execute("DELETE FROM agents WHERE agent_id=?", (agent_id,)).rowcount)

    def stop_all(self, campaign_id: str, *, reason: str = "campaign stopped") -> int:
        """Stop every agent that has not already finished, and free its claims.

        ``COMPLETED`` and ``FAILED`` are left alone: they are outcomes, and
        overwriting an outcome with "stopped" would lose the distinction between
        an agent that spent its budget and one that was told to stop.
        """
        agents = self.list(campaign_id)
        terminal = {AgentState.COMPLETED, AgentState.FAILED, AgentState.STOPPED}
        for agent in agents:
            if agent.state not in terminal:
                agent.state = AgentState.STOPPED
                agent.current_task = reason
                agent.current_claim = ""
                self.save(agent)
        self.release_all(campaign_id)
        return len(agents)

    def counts(self, campaign_id: str | None = None) -> dict[str, Any]:
        agents = self.list(campaign_id)
        by_state: Counter[str] = Counter(str(a.state) for a in agents)
        by_role: Counter[str] = Counter(str(a.role) for a in agents)
        return {
            "total": len(agents),
            "eligible": sum(1 for a in agents if a.state in ELIGIBLE),
            "stale": sum(1 for a in agents if a.stale()),
            "experiments": sum(a.experiments for a in agents),
            "findings": sum(a.findings for a in agents),
            "errors": sum(a.errors for a in agents),
            "by_state": {str(s): by_state.get(str(s), 0) for s in AgentState},
            "by_role": {str(r): by_role.get(str(r), 0) for r in AgentRole},
        }

    # ── claims ───────────────────────────────────────────────────────────────
    def claim(
        self,
        *,
        campaign_id: str,
        subject: str,
        agent_id: str,
        replica_of: str = "",
        lease_seconds: float = CLAIM_LEASE_SECONDS,
    ) -> Claim | None:
        """Take exclusive hold of ``subject``, or return ``None`` if held.

        The lease is what makes this safe unattended. An agent that dies holding
        a claim used to hold it until the process restarted; here the claim
        lapses on its own and the work becomes available again.
        """
        now = _now()
        expires = now + timedelta(seconds=max(1.0, float(lease_seconds)))
        claim_id = stable_id(
            "claim", {"campaign": campaign_id, "subject": subject, "replica": replica_of}
        )
        with self._lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM claims WHERE campaign_id=? AND subject=? AND replica_of=?",
                (campaign_id, subject, replica_of),
            ).fetchone()
            if row is not None:
                held = _to_claim(row)
                if held.agent_id == agent_id:
                    # Re-claiming your own work renews the lease.
                    db.execute(
                        "UPDATE claims SET expires_at=? WHERE claim_id=?",
                        (_iso(expires), held.claim_id),
                    )
                    return replace(held, expires_at=_iso(expires))
                if not held.expired(now=now):
                    return None
                db.execute("DELETE FROM claims WHERE claim_id=?", (held.claim_id,))
            db.execute(
                "INSERT INTO claims VALUES (?,?,?,?,?,?,?)",
                (
                    claim_id,
                    campaign_id,
                    subject,
                    agent_id,
                    replica_of,
                    _iso(now),
                    _iso(expires),
                ),
            )
        return Claim(
            claim_id=claim_id,
            campaign_id=campaign_id,
            subject=subject,
            agent_id=agent_id,
            replica_of=replica_of,
            claimed_at=_iso(now),
            expires_at=_iso(expires),
        )

    def release(self, claim_id: str) -> bool:
        with self._lock, closing(self._connect()) as db, db:
            return bool(
                db.execute("DELETE FROM claims WHERE claim_id=?", (claim_id,)).rowcount
            )

    def release_all(self, campaign_id: str) -> int:
        with self._lock, closing(self._connect()) as db, db:
            return int(
                db.execute("DELETE FROM claims WHERE campaign_id=?", (campaign_id,)).rowcount
            )

    def release_expired(self, campaign_id: str | None = None) -> int:
        """Drop lapsed leases. Safe to call at any time — the expiry is the test."""
        now = _iso(None)
        clause = " AND campaign_id=?" if campaign_id else ""
        args: tuple[Any, ...] = (now, campaign_id) if campaign_id else (now,)
        with self._lock, closing(self._connect()) as db, db:
            return int(
                db.execute(
                    f"DELETE FROM claims WHERE expires_at != '' AND expires_at < ?{clause}", args
                ).rowcount
            )

    def claims(self, campaign_id: str | None = None) -> builtins.list[dict[str, Any]]:
        clause = "WHERE campaign_id=?" if campaign_id else ""
        args: tuple[Any, ...] = (campaign_id,) if campaign_id else ()
        with closing(self._connect()) as db:
            rows = db.execute(
                f"SELECT * FROM claims {clause} ORDER BY claimed_at DESC", args
            ).fetchall()
        return [_to_claim(row).as_dict() for row in rows]

    def holder(self, campaign_id: str, subject: str) -> str | None:
        """Which agent holds ``subject``, ignoring lapsed leases."""
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM claims WHERE campaign_id=? AND subject=? AND replica_of=''",
                (campaign_id, subject),
            ).fetchone()
        if row is None:
            return None
        claim = _to_claim(row)
        return None if claim.expired() else claim.agent_id


def _to_agent(row: sqlite3.Row) -> ResearchAgent:
    return ResearchAgent(
        agent_id=row["agent_id"],
        campaign_id=row["campaign_id"],
        role=AgentRole(row["role"]),
        name=row["name"],
        objective=row["objective"],
        state=AgentState(row["state"]),
        current_task=row["current_task"],
        current_claim=row["current_claim"],
        compute_budget=float(row["compute_budget"] or 0.0),
        compute_spent=float(row["compute_spent"] or 0.0),
        experiments=int(row["experiments"] or 0),
        findings=int(row["findings"] or 0),
        errors=int(row["errors"] or 0),
        heartbeat_at=row["heartbeat_at"],
        progress_at=row["progress_at"] or "",
        last_result=row["last_result"] or "",
        last_error=row["last_error"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _to_claim(row: sqlite3.Row) -> Claim:
    return Claim(
        claim_id=row["claim_id"],
        campaign_id=row["campaign_id"],
        subject=row["subject"],
        agent_id=row["agent_id"],
        replica_of=row["replica_of"] or "",
        claimed_at=row["claimed_at"],
        expires_at=row["expires_at"] or "",
    )
