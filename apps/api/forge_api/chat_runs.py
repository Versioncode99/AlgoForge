"""A chat turn as a durable run, rather than as one blocking request.

**What this replaces.** `POST /conversations/{id}/messages` wrote the question,
called the assistant, waited for the model, and returned the whole answer. Three
consequences, all of them visible to somebody using it:

* the question did not appear until the answer did, because one request carried
  both — a model taking nine seconds looked like an interface that had stopped;
* there was no way to stop it, because nothing existed to stop;
* a reload during the call lost the client's knowledge of it entirely. The turn
  was eventually written and the transcript recovered, which is the right
  backend behaviour and not something the reader could see.

**The shape.** A run is created and acknowledged immediately, executes on a
worker thread, and reports through typed events a client reads as a stream:

    accepted   the run exists, here is its id
    status     what it is doing now, in the operator's words
    delta      more of the answer
    source     something it read, with where it came from
    artifact   something reopenable that was produced
    completed  the finished turn, as the conversation stores it
    cancelled  stopped on request, with whatever had been produced
    failed     it did not finish, with the reason

**Why the events are buffered rather than pushed.** Every event a run has ever
emitted is kept until the run is collected, and the stream endpoint takes a
cursor. A client that reloads, or whose connection drops, reattaches and
receives the run from the beginning — which is what makes "recover after a
reload" a property of the protocol rather than a thing the client must
reconstruct from the transcript.

**What is honest about the streaming.** The provider clients in this build
return a completed message; none of them streams tokens. So `delta` carries the
answer in one piece, and a `status` event says so in as many words. The protocol
is where it belongs — a client written against it needs no change on the day a
provider streams — and the measurement in `docs/HORIZON_PERFORMANCE.md`
separates local overhead from provider time rather than reporting one number
that hides which is which.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Terminal statuses. A run in one of these will emit no further events.
FINISHED = frozenset({"completed", "cancelled", "failed"})

#: How long a finished run's events stay readable after it ends. Long enough for
#: a client that reloaded to reattach and see how it went; short enough that an
#: unattended process does not accumulate transcripts in memory.
RETAIN_SECONDS = 15 * 60

#: How long a stream waits for the next event before sending a keep-alive. A
#: proxy that closes an idle connection is indistinguishable from a run that
#: died, and the client's recovery from the two is different.
IDLE_TIMEOUT = 15.0


@dataclass
class Event:
    """One thing that happened, in the order it happened."""

    index: int
    kind: str
    data: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"index": self.index, "event": self.kind, "data": self.data}


@dataclass
class Run:
    """One turn in flight, and everything said about it so far."""

    run_id: str
    conversation_id: str
    question: str
    status: str = "accepted"
    text: str = ""
    error: str = ""
    created_at: str = ""
    finished_at: str = ""
    events: list[Event] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)
    #: Woken on every append, so a reader blocks rather than polls.
    pulse: threading.Condition = field(default_factory=threading.Condition)
    #: Monotonic time the run reached a terminal status, for collection.
    ended: float = 0.0
    #: Set under `pulse`, *after* the terminal event has been appended.
    #:
    #: Not `status in FINISHED`. Status is set first so a caller asking "is this
    #: still running?" gets the truth immediately, and a reader that treated it
    #: as end-of-stream returned in the window between the status flipping and
    #: the `completed` event being appended — which is a stream that ends one
    #: event short of the only event the reader was waiting for, sometimes.
    closed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "conversation_id": self.conversation_id,
            "status": self.status,
            "text": self.text,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class RunStore:
    """Where a run's outcome survives the process that produced it.

    Only the outcome. The event buffer is in memory and deliberately so: it is a
    delivery mechanism with a lifetime of minutes, and persisting every status
    line would make a chat message a dozen writes to answer a question nobody
    asks after the run has finished. What must survive is *that a run happened,
    how it ended, and what it produced* — and the turn itself is already in the
    conversation store, written before the model was called.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS chat_runs ("
                "run_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, "
                "question TEXT NOT NULL, status TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', "
                "error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, "
                "finished_at TEXT NOT NULL DEFAULT '')"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS chat_runs_by_conversation "
                "ON chat_runs(conversation_id, created_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def record(self, run: Run) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO chat_runs (run_id, conversation_id, question, status, text, "
                "error, created_at, finished_at) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET status=excluded.status, text=excluded.text, "
                "error=excluded.error, finished_at=excluded.finished_at",
                (
                    run.run_id,
                    run.conversation_id,
                    run.question,
                    run.status,
                    run.text,
                    run.error,
                    run.created_at,
                    run.finished_at,
                ),
            )

    def latest(self, conversation_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT * FROM chat_runs WHERE conversation_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        return None if row is None else dict(row)


class ChatRunner:
    """Accepts a question, answers it on a worker, and reports as it goes."""

    def __init__(self, chat: Any, store: RunStore, *, workers: int = 2) -> None:
        self.chat = chat
        self.store = store
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()
        # Two, not one: a second question while the first is still running is a
        # normal thing to do, and a pool of one turns it into a queue nobody
        # was told about. Not more than two, because each one is a model call on
        # the operator's allowance.
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="chat-run")

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self, conversation_id: str, message: str) -> Run:
        """Create a run and return immediately. The work happens on a worker."""
        run = Run(
            run_id=f"run_{uuid.uuid4().hex[:16]}",
            conversation_id=conversation_id,
            question=message,
            created_at=datetime.now(UTC).isoformat(),
        )
        with self._lock:
            self._collect()
            self._runs[run.run_id] = run
        self.store.record(run)
        self._emit(run, "accepted", {"run_id": run.run_id, "conversation_id": conversation_id})
        self._pool.submit(self._execute, run)
        return run

    def get(self, run_id: str) -> Run | None:
        with self._lock:
            return self._runs.get(run_id)

    def request_cancel(self, run_id: str) -> bool:
        """Ask a run to stop. Acknowledged immediately; observed at the next step.

        There is no way to interrupt a provider call that is already in flight,
        so "cancelled" means *the answer will not be written into the
        conversation* rather than *the request was withdrawn from the provider*.
        The distinction is stated in the event's own reason, because a control
        that claims to have stopped something it did not is worse than one that
        admits what it can do.
        """
        run = self.get(run_id)
        if run is None or run.status in FINISHED:
            return False
        run.cancel.set()
        self._emit(run, "status", {"stage": "cancelling", "detail": "Stopping…"})
        return True

    # ── the run ──────────────────────────────────────────────────────────────
    def _execute(self, run: Run) -> None:
        started = time.perf_counter()
        try:
            self._set_status(run, "running")
            self._emit(run, "status", {"stage": "reading", "detail": "Reading this instance…"})
            if run.cancel.is_set():
                return self._finish(
                    run, "cancelled", reason="stopped before the model was called"
                )

            outcome = self.chat.send(
                run.conversation_id, run.question, on_event=self._progress(run)
            )

            if run.cancel.is_set():
                # The turn is already written: it is what the assistant actually
                # did, and deleting it would leave a transcript that disagrees
                # with the audit log. The run is reported as cancelled and says
                # the answer arrived anyway.
                return self._finish(
                    run,
                    "cancelled",
                    reason="stopped after the provider had already replied; the turn was kept",
                    turn=outcome.get("turn"),
                )

            turn = outcome.get("turn") or {}
            run.text = str(turn.get("text", ""))
            self._emit(run, "delta", {"text": run.text})
            for artifact in turn.get("artifacts", []) or []:
                self._emit(run, "artifact", artifact)
            self._finish(
                run,
                "completed",
                turn=turn,
                extra={
                    "note": outcome.get("note"),
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                },
            )
        except Exception as exc:
            run.error = f"{type(exc).__name__}: {exc}"
            self._finish(run, "failed", reason=run.error)

    def _progress(self, run: Run) -> Any:
        """The callback the assistant reports through, as a bound closure."""

        def report(stage: str, detail: str, **extra: Any) -> bool:
            self._emit(run, "status", {"stage": stage, "detail": detail, **extra})
            # The return value is the cancel signal. The assistant checks it
            # between tool rounds, which is the only place it can stop.
            return not run.cancel.is_set()

        return report

    # ── events ───────────────────────────────────────────────────────────────
    def _emit(self, run: Run, kind: str, data: dict[str, Any]) -> None:
        with run.pulse:
            run.events.append(Event(index=len(run.events), kind=kind, data=data))
            run.pulse.notify_all()

    def _set_status(self, run: Run, status: str) -> None:
        run.status = status
        self.store.record(run)

    def _finish(
        self,
        run: Run,
        status: str,
        *,
        reason: str = "",
        turn: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        run.status = status
        run.error = reason if status == "failed" else run.error
        run.finished_at = datetime.now(UTC).isoformat()
        run.ended = time.monotonic()
        self.store.record(run)
        payload: dict[str, Any] = {"run_id": run.run_id, "status": status}
        if reason:
            payload["reason"] = reason
        if turn is not None:
            payload["turn"] = turn
        payload.update(extra or {})
        # The terminal event and the closed flag under one acquisition, so a
        # reader cannot observe the end of the stream before the event that says
        # how it ended.
        with run.pulse:
            run.events.append(Event(index=len(run.events), kind=status, data=payload))
            run.closed = True
            run.pulse.notify_all()

    def stream(self, run_id: str, cursor: int = 0) -> Iterator[dict[str, Any]]:
        """Every event from `cursor` onwards, then each new one as it arrives.

        Ends when the run reaches a terminal status and the reader has caught
        up. A keep-alive is yielded rather than nothing on an idle timeout,
        because a silent connection and a dead one look identical to a client.
        """
        run = self.get(run_id)
        if run is None:
            yield {"event": "failed", "data": {"reason": f"no run '{run_id}'"}, "index": 0}
            return
        position = max(0, cursor)
        while True:
            with run.pulse:
                while position >= len(run.events):
                    if run.closed:
                        return
                    if not run.pulse.wait(timeout=IDLE_TIMEOUT):
                        yield {"event": "keep-alive", "data": {}, "index": position}
                pending = [event.as_dict() for event in run.events[position:]]
                position = len(run.events)
            yield from pending

    def _collect(self) -> None:
        """Drop finished runs nobody is reading. Called under the lock."""
        now = time.monotonic()
        for run_id, run in list(self._runs.items()):
            if run.status in FINISHED and run.ended and now - run.ended > RETAIN_SECONDS:
                del self._runs[run_id]

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
