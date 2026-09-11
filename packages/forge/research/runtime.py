"""What the engine is actually doing, as opposed to whether its threads exist.

The bug this module exists to fix: ``EngineState.running`` was a boolean set to
``True`` when the worker threads were created and back to ``False`` when the last
one left. It meant *"threads exist"*. The interface rendered it as **RUNNING**,
and a search that had exhausted its campaign, condemned every template it could
reach, or was refusing every proposal as a duplicate said RUNNING for as long as
it was left alone.

So the state is no longer a bit. It is a named state with a reason, derived from
three things the workers report as they go:

* a **heartbeat** per worker — when it last touched anything at all;
* a **progress mark** per worker — when it last did something that changed the
  research, which is a much smaller set of events than "touched anything";
* an **outcome stream** — what each cycle produced, so a run of identical
  barren outcomes can be recognised as a loop rather than as bad luck.

Nothing in here decides research. It observes, and it is allowed to say the
engine is idle. That is the whole point: the one thing it must never do is
report progress that did not happen.
"""

from __future__ import annotations

import threading
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

#: A worker that has not beaten within this many seconds is stale. Generous
#: because a single backtest over a 250k-bar series legitimately takes tens of
#: seconds and a worker in the middle of one is not stuck.
STALE_AFTER_SECONDS = 180.0

#: And one that has not beaten within this is presumed dead: its claims are
#: eligible for release. Deliberately several multiples of stale, because
#: releasing a claim a live worker still holds is the one failure mode worse
#: than holding a claim nobody is using.
DEAD_AFTER_SECONDS = 900.0

#: How long the whole engine may go without *progress* before the watchdog is
#: entitled to call it. Progress means a created candidate, a judged verdict, a
#: recorded finding — not a cycle that ended in a refusal.
NO_PROGRESS_SECONDS = 120.0

#: How many consecutive barren outcomes of the same kind constitute a loop.
LOOP_THRESHOLD = 25

#: How many recent outcomes to keep for diagnosis.
OUTCOME_WINDOW = 200


class RuntimeState(StrEnum):
    """The engine's actual condition.

    Ordered roughly from "starting" through "working" to "stopped", but the
    ordering is not meaningful to any code — the names are.
    """

    #: Threads created, data not yet loaded.
    STARTING = "STARTING"
    #: Workers are processing work. This is an *invariant*, not a label: at
    #: least one worker has made progress inside the no-progress window.
    RUNNING = "RUNNING"
    #: Deliberately held by the operator.
    PAUSED = "PAUSED"
    #: Alive, healthy, nothing to do, and nothing wrong. Distinct from BLOCKED:
    #: work would be picked up the moment it appeared.
    IDLE = "IDLE"
    #: Alive and asking for work, but the queue keeps coming back empty.
    WAITING_FOR_WORK = "WAITING_FOR_WORK"
    #: Blocked on a dataset: loading, missing, or refused by the data gate.
    WAITING_FOR_DATA = "WAITING_FOR_DATA"
    #: Blocked on an external model call or a specialist agent.
    WAITING_FOR_AGENT = "WAITING_FOR_AGENT"
    #: Something is preventing work that will not resolve on its own — a
    #: condemned template catalogue, a campaign whose capabilities the dataset
    #: cannot serve, a stuck lease.
    BLOCKED = "BLOCKED"
    #: The research space reachable from here has been searched out. A terminal
    #: condition for this configuration, not an error.
    EXHAUSTED = "EXHAUSTED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    #: An unhandled failure the engine could not continue past.
    ERROR = "ERROR"
    #: Reclaiming abandoned claims, reloading data, restarting a dead worker.
    RECOVERING = "RECOVERING"


#: The states in which it is honest to tell somebody their research is running.
WORKING_STATES = frozenset({RuntimeState.STARTING, RuntimeState.RUNNING, RuntimeState.RECOVERING})

#: The states that mean "alive but producing nothing", which is what the old
#: boolean hid.
STALLED_STATES = frozenset(
    {
        RuntimeState.IDLE,
        RuntimeState.WAITING_FOR_WORK,
        RuntimeState.WAITING_FOR_DATA,
        RuntimeState.WAITING_FOR_AGENT,
        RuntimeState.BLOCKED,
        RuntimeState.EXHAUSTED,
    }
)


class Outcome(StrEnum):
    """What one cycle produced.

    ``PROGRESS`` is the only value that resets the no-progress clock, and the
    only one a worker may report after actually changing something.
    """

    PROGRESS = "PROGRESS"
    #: Refused before any compute, because it had already been tried or was
    #: already known to fail. Cheap and legitimate — but not progress.
    DUPLICATE = "DUPLICATE"
    #: The director had nothing to propose: empty frontier, no eligible item.
    NO_WORK = "NO_WORK"
    #: Refused by the novelty gate.
    NOT_NOVEL = "NOT_NOVEL"
    #: A capability the dataset cannot serve.
    BLOCKED = "BLOCKED"
    #: The campaign reached a stopping criterion.
    EXHAUSTED = "EXHAUSTED"
    #: The cycle raised.
    ERROR = "ERROR"


#: Outcomes that produced nothing. Their *reasons* differ and the difference is
#: what the watchdog diagnoses from, so they are not collapsed into one value.
BARREN = frozenset(
    {
        Outcome.DUPLICATE,
        Outcome.NO_WORK,
        Outcome.NOT_NOVEL,
        Outcome.BLOCKED,
        Outcome.EXHAUSTED,
        Outcome.ERROR,
    }
)


def _now() -> float:
    return datetime.now(UTC).timestamp()


def _iso(stamp: float | None) -> str | None:
    return None if stamp is None else datetime.fromtimestamp(stamp, UTC).isoformat(
        timespec="seconds"
    )


@dataclass
class WorkerHeartbeat:
    """One worker's liveness, as it last reported it."""

    worker_id: str
    stage: str = "starting"
    #: Last time this worker reported anything at all.
    beat_at: float = field(default_factory=_now)
    #: Last time this worker produced *progress*.
    progress_at: float | None = None
    started_at: float = field(default_factory=_now)
    cycles: int = 0
    progressed: int = 0
    barren: int = 0
    errors: int = 0
    paused: bool = False
    #: What this worker is holding, so a dead worker's claim can be named.
    claim: str | None = None
    campaign_id: str | None = None
    agent_id: str | None = None
    last_outcome: Outcome | None = None
    last_reason: str = ""

    def age(self, *, now: float | None = None) -> float:
        return (now or _now()) - self.beat_at

    def stale(self, *, now: float | None = None) -> bool:
        return not self.paused and self.age(now=now) > STALE_AFTER_SECONDS

    def dead(self, *, now: float | None = None) -> bool:
        return not self.paused and self.age(now=now) > DEAD_AFTER_SECONDS

    def as_dict(self) -> dict[str, Any]:
        now = _now()
        return {
            "worker_id": self.worker_id,
            "stage": self.stage,
            "beat_at": _iso(self.beat_at),
            "progress_at": _iso(self.progress_at),
            "started_at": _iso(self.started_at),
            "seconds_since_beat": round(self.age(now=now), 1),
            "seconds_since_progress": (
                None if self.progress_at is None else round(now - self.progress_at, 1)
            ),
            "cycles": self.cycles,
            "progressed": self.progressed,
            "barren": self.barren,
            "errors": self.errors,
            "paused": self.paused,
            "stale": self.stale(now=now),
            "dead": self.dead(now=now),
            "claim": self.claim,
            "campaign_id": self.campaign_id,
            "agent_id": self.agent_id,
            "last_outcome": str(self.last_outcome) if self.last_outcome else None,
            "last_reason": self.last_reason,
        }


@dataclass(frozen=True)
class Diagnosis:
    """The watchdog's reading, and what it suggests doing about it."""

    state: RuntimeState
    reason: str
    #: A machine-readable handle so a caller can act without parsing prose.
    code: str
    #: What the orchestrator should try. Advisory: the watchdog never acts.
    remedy: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": str(self.state),
            "reason": self.reason,
            "code": self.code,
            "remedy": self.remedy,
            "detail": dict(self.detail),
        }


class RuntimeMonitor:
    """Heartbeats in, an honest state out.

    Thread-safe. Every worker calls :meth:`beat` as it moves through a cycle and
    :meth:`record` once at the end of one; everything else is derived.
    """

    def __init__(
        self,
        *,
        no_progress_seconds: float = NO_PROGRESS_SECONDS,
        loop_threshold: int = LOOP_THRESHOLD,
    ) -> None:
        self._lock = threading.RLock()
        self._workers: dict[str, WorkerHeartbeat] = {}
        self._outcomes: deque[tuple[float, Outcome, str]] = deque(maxlen=OUTCOME_WINDOW)
        self._no_progress_seconds = float(no_progress_seconds)
        self._loop_threshold = int(loop_threshold)
        self._state = RuntimeState.STOPPED
        self._reason = "not started"
        self._started_at: float | None = None
        self._last_progress_at: float | None = None
        self._stopping = False
        self._error: str | None = None
        # Set by the owner when something outside the workers is known to
        # block: a dataset that will not load, a model provider that is down.
        self._blockers: dict[str, str] = {}

    # ── lifecycle ────────────────────────────────────────────────────────────
    def starting(self, *, workers: int) -> None:
        with self._lock:
            self._workers.clear()
            self._outcomes.clear()
            self._blockers.clear()
            self._error = None
            self._stopping = False
            self._started_at = _now()
            self._last_progress_at = None
            self._state = RuntimeState.STARTING
            self._reason = f"starting {workers} worker(s)"

    def stopping(self) -> None:
        with self._lock:
            self._stopping = True
            self._state = RuntimeState.STOPPING
            self._reason = "stopping at the end of the current cycle"

    def stopped(self) -> None:
        with self._lock:
            self._stopping = False
            self._state = RuntimeState.STOPPED
            self._reason = "stopped"
            for beat in self._workers.values():
                beat.stage = "stopped"

    def failed(self, reason: str) -> None:
        with self._lock:
            self._error = reason
            self._state = RuntimeState.ERROR
            self._reason = reason

    def recovering(self, reason: str) -> None:
        with self._lock:
            self._state = RuntimeState.RECOVERING
            self._reason = reason

    # ── worker reports ───────────────────────────────────────────────────────
    def beat(
        self,
        worker_id: str,
        stage: str,
        *,
        claim: str | None = None,
        campaign_id: str | None = None,
        agent_id: str | None = None,
        paused: bool | None = None,
    ) -> None:
        """One worker is alive and at ``stage``."""
        with self._lock:
            beat = self._workers.get(worker_id)
            if beat is None:
                beat = WorkerHeartbeat(worker_id=worker_id)
                self._workers[worker_id] = beat
            beat.stage = stage
            beat.beat_at = _now()
            if claim is not None:
                beat.claim = claim or None
            if campaign_id is not None:
                beat.campaign_id = campaign_id or None
            if agent_id is not None:
                beat.agent_id = agent_id or None
            if paused is not None:
                beat.paused = bool(paused)

    def record(self, worker_id: str, outcome: Outcome, reason: str = "") -> None:
        """One cycle finished, and this is what it produced."""
        now = _now()
        with self._lock:
            beat = self._workers.setdefault(worker_id, WorkerHeartbeat(worker_id=worker_id))
            beat.beat_at = now
            beat.cycles += 1
            beat.last_outcome = outcome
            beat.last_reason = reason
            beat.claim = None
            if outcome is Outcome.PROGRESS:
                beat.progressed += 1
                beat.progress_at = now
                self._last_progress_at = now
            else:
                beat.barren += 1
                if outcome is Outcome.ERROR:
                    beat.errors += 1
            self._outcomes.append((now, outcome, reason))

    def blocker(self, key: str, reason: str | None) -> None:
        """Declare, or clear, something outside the workers that blocks work."""
        with self._lock:
            if reason:
                self._blockers[key] = reason
            else:
                self._blockers.pop(key, None)

    def forget(self, worker_id: str) -> None:
        with self._lock:
            self._workers.pop(worker_id, None)

    # ── derivation ───────────────────────────────────────────────────────────
    def diagnose(self, *, now: float | None = None) -> Diagnosis:
        """Read the heartbeats and say what is actually going on.

        The order of the checks is the order of authority: an explicit stop
        beats an error, an error beats a stall, and a stall beats the optimistic
        answer. Nothing here can return RUNNING unless a worker genuinely
        progressed inside the window.
        """
        now = now or _now()
        with self._lock:
            if self._state is RuntimeState.STOPPED:
                return Diagnosis(RuntimeState.STOPPED, self._reason, "stopped")
            if self._stopping:
                return Diagnosis(RuntimeState.STOPPING, self._reason, "stopping")
            if self._error:
                return Diagnosis(RuntimeState.ERROR, self._error, "error")

            live = [b for b in self._workers.values() if not b.paused]
            if self._workers and not live:
                return Diagnosis(
                    RuntimeState.PAUSED,
                    f"all {len(self._workers)} worker(s) are paused by the operator",
                    "paused",
                    remedy="resume a worker",
                )

            if self._blockers:
                key, reason = sorted(self._blockers.items())[0]
                state = (
                    RuntimeState.WAITING_FOR_DATA
                    if key.startswith("data")
                    else RuntimeState.WAITING_FOR_AGENT
                    if key.startswith("agent") or key.startswith("model")
                    else RuntimeState.BLOCKED
                )
                return Diagnosis(state, reason, f"blocked:{key}", remedy="clear the blocker")

            if self._state is RuntimeState.RECOVERING:
                return Diagnosis(RuntimeState.RECOVERING, self._reason, "recovering")

            if not self._workers:
                return Diagnosis(
                    RuntimeState.STARTING, self._reason or "no worker has reported yet", "starting"
                )

            dead = [b for b in live if b.dead(now=now)]
            if dead and len(dead) == len(live):
                names = ", ".join(sorted(b.worker_id for b in dead))
                return Diagnosis(
                    RuntimeState.ERROR,
                    f"every worker has stopped reporting: {names}",
                    "workers_dead",
                    remedy="restart the engine; claims held by these workers are releasable",
                    detail={"dead": sorted(b.worker_id for b in dead)},
                )

            # Nothing has ever progressed and we have only just started: that is
            # STARTING, not a stall.
            if self._last_progress_at is None:
                since_start = now - (self._started_at or now)
                if since_start <= self._no_progress_seconds:
                    return Diagnosis(
                        RuntimeState.STARTING,
                        f"started {since_start:.0f}s ago; no cycle has completed yet",
                        "starting",
                    )

            since_progress = now - (self._last_progress_at or self._started_at or now)
            if since_progress <= self._no_progress_seconds:
                stale = [b.worker_id for b in live if b.stale(now=now)]
                reason = f"{len(live)} worker(s) processing"
                if stale:
                    reason += f"; {len(stale)} stale ({', '.join(sorted(stale))})"
                return Diagnosis(
                    RuntimeState.RUNNING,
                    reason,
                    "running",
                    detail={"stale_workers": sorted(stale)},
                )

            return self._diagnose_stall(now, since_progress, live)

    def _diagnose_stall(
        self, now: float, since_progress: float, live: list[WorkerHeartbeat]
    ) -> Diagnosis:
        """No progress for longer than the window. Say *why*, precisely.

        Called with the lock held.
        """
        window = [entry for entry in self._outcomes if entry[0] >= now - max(since_progress, 1.0)]
        counts = Counter(outcome for _, outcome, _ in window)
        total = sum(counts.values())
        elapsed = f"{since_progress:.0f}s"

        # A run of identical barren outcomes at the tail is a loop, and which
        # outcome it is says what kind.
        tail: list[Outcome] = []
        for _, outcome, _ in reversed(self._outcomes):
            if outcome is Outcome.PROGRESS:
                break
            tail.append(outcome)
        looping = len(tail) >= self._loop_threshold
        dominant = Counter(tail).most_common(1)[0][0] if tail else None
        last_reason = next((r for _, _, r in reversed(self._outcomes) if r), "")

        detail: dict[str, Any] = {
            "seconds_without_progress": round(since_progress, 1),
            "barren_run": len(tail),
            "outcomes": {str(k): v for k, v in counts.items()},
            "last_reason": last_reason,
            "workers": len(live),
        }

        if total == 0:
            return Diagnosis(
                RuntimeState.IDLE,
                f"no cycle has completed in {elapsed} and no worker reported an outcome",
                "idle_no_outcomes",
                remedy="check worker heartbeats; a worker may be wedged inside a backtest",
                detail=detail,
            )

        if dominant is Outcome.EXHAUSTED or counts[Outcome.EXHAUSTED] >= max(1, total // 2):
            return Diagnosis(
                RuntimeState.EXHAUSTED,
                last_reason
                or f"every proposal in the last {total} cycles reported the campaign exhausted",
                "exhausted",
                remedy="start another campaign, widen the objective, or add a dataset",
                detail=detail,
            )

        if dominant is Outcome.ERROR or counts[Outcome.ERROR] >= max(1, total // 2):
            return Diagnosis(
                RuntimeState.ERROR,
                last_reason or f"{counts[Outcome.ERROR]} of the last {total} cycles raised",
                "cycle_errors",
                remedy="read the last error and fix the failing proposal path",
                detail=detail,
            )

        if dominant is Outcome.BLOCKED or counts[Outcome.BLOCKED] >= max(1, total // 2):
            return Diagnosis(
                RuntimeState.BLOCKED,
                last_reason
                or f"{counts[Outcome.BLOCKED]} of the last {total} cycles were blocked",
                "blocked",
                remedy="the dataset cannot serve a capability the campaign requires",
                detail=detail,
            )

        duplicates = counts[Outcome.DUPLICATE] + counts[Outcome.NOT_NOVEL]
        if looping and duplicates >= max(1, total // 2):
            return Diagnosis(
                RuntimeState.EXHAUSTED,
                f"{len(tail)} consecutive proposals in {elapsed} were already-tested or "
                "restatements of existing research — the frontier reachable from this "
                "objective is searched out",
                "duplicate_loop",
                remedy=(
                    "broaden the objective, allow another mechanism domain, enable literature "
                    "retrieval, or widen the parameter ranges"
                ),
                detail=detail,
            )
        if duplicates >= max(1, total // 2):
            return Diagnosis(
                RuntimeState.WAITING_FOR_WORK,
                f"{duplicates} of the last {total} proposals were duplicates or restatements; "
                f"nothing new has been produced in {elapsed}",
                "duplicate_pressure",
                remedy="re-tilt the research allocation towards discovery",
                detail=detail,
            )

        if counts[Outcome.NO_WORK] >= max(1, total // 2):
            return Diagnosis(
                RuntimeState.WAITING_FOR_WORK,
                f"the frontier returned no eligible item on {counts[Outcome.NO_WORK]} of the "
                f"last {total} cycles",
                "no_work",
                remedy="expand the frontier: generate hypotheses or add a campaign",
                detail=detail,
            )

        return Diagnosis(
            RuntimeState.IDLE,
            f"no progress in {elapsed} across {total} cycles",
            "idle",
            remedy="inspect the recent outcomes",
            detail=detail,
        )

    # ── read models ──────────────────────────────────────────────────────────
    def workers(self) -> list[dict[str, Any]]:
        with self._lock:
            return [beat.as_dict() for beat in sorted(self._workers.values(), key=_worker_sort)]

    def stale_workers(self, *, now: float | None = None) -> list[str]:
        now = now or _now()
        with self._lock:
            return sorted(b.worker_id for b in self._workers.values() if b.stale(now=now))

    def dead_workers(self, *, now: float | None = None) -> list[str]:
        now = now or _now()
        with self._lock:
            return sorted(b.worker_id for b in self._workers.values() if b.dead(now=now))

    def recent_outcomes(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            entries = list(self._outcomes)[-limit:]
        return [
            {"at": _iso(at), "outcome": str(outcome), "reason": reason}
            for at, outcome, reason in reversed(entries)
        ]

    def outcome_counts(self) -> dict[str, int]:
        with self._lock:
            counts = Counter(outcome for _, outcome, _ in self._outcomes)
        return {str(outcome): counts.get(outcome, 0) for outcome in Outcome}

    def snapshot(self) -> dict[str, Any]:
        """Everything an interface needs to answer "why isn't my research running?"."""
        diagnosis = self.diagnose()
        with self._lock:
            started, progress = self._started_at, self._last_progress_at
            blockers = dict(self._blockers)
        now = _now()
        return {
            "state": str(diagnosis.state),
            "reason": diagnosis.reason,
            "code": diagnosis.code,
            "remedy": diagnosis.remedy,
            "detail": diagnosis.detail,
            "working": diagnosis.state in WORKING_STATES,
            "stalled": diagnosis.state in STALLED_STATES,
            "started_at": _iso(started),
            "last_progress_at": _iso(progress),
            "seconds_without_progress": (
                None if progress is None and started is None else round(now - (progress or started or now), 1)
            ),
            "workers": self.workers(),
            "stale_workers": self.stale_workers(now=now),
            "dead_workers": self.dead_workers(now=now),
            "outcome_counts": self.outcome_counts(),
            "recent_outcomes": self.recent_outcomes(limit=25),
            "blockers": blockers,
        }


def _worker_sort(beat: WorkerHeartbeat) -> tuple[int, str]:
    """Numeric worker ids sort numerically; anything else sorts after, by name."""
    return (int(beat.worker_id), "") if beat.worker_id.isdigit() else (1 << 30, beat.worker_id)
