"""Background jobs with progress, for work too long to hold a request open.

A backtest over the full sixteen-year archive is 4.8 million bars and several
minutes of compute. Blocking an HTTP request for that long fails in every
direction at once: proxies drop it, the interface has nothing to show while it
waits, and cancelling means killing the browser tab.

So long work runs here instead. A job is submitted, gets an id back
immediately, and reports progress as it goes. The registry is deliberately
in-process and non-durable — jobs are recomputable by construction, and a
restart losing them is the correct behaviour rather than a limitation to work
around.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

JobStatus = Literal["QUEUED", "RUNNING", "DONE", "FAILED", "CANCELLED"]

# Jobs finish and nobody collects them. Keep the recent ones so a reloaded page
# can still find its result, and drop the rest rather than growing forever.
MAX_RETAINED_JOBS = 40


class Cancelled(Exception):
    """Raised inside a worker when the job has been asked to stop."""


@dataclass
class JobHandle:
    """What a running job can report back, and how it learns to stop."""

    job_id: str
    total: int
    _registry: JobRegistry
    _done: int = 0
    _note: str = ""

    def progress(self, done: int, note: str = "") -> None:
        """Publish progress and raise :class:`Cancelled` if asked to stop.

        Calling this is also how a worker becomes interruptible: there is no
        safe way to kill a thread from outside, so cancellation is cooperative
        and takes effect at the next checkpoint.
        """
        self._done = done
        if note:
            self._note = note
        self._registry._publish(self.job_id, done, self._note)
        if self._registry.is_cancelling(self.job_id):
            raise Cancelled(self.job_id)


@dataclass
class Job:
    job_id: str
    kind: str
    label: str
    status: JobStatus = "QUEUED"
    total: int = 0
    done: int = 0
    note: str = ""
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def fraction(self) -> float:
        if self.status == "DONE":
            return 1.0
        if self.total <= 0:
            return 0.0
        return min(1.0, self.done / self.total)

    @property
    def elapsed_seconds(self) -> float:
        if self.started_at is None:
            return 0.0
        return (self.finished_at or time.time()) - self.started_at

    @property
    def eta_seconds(self) -> float | None:
        """Remaining time from the rate achieved so far.

        Deliberately naive: a linear extrapolation is honest about being an
        estimate, and a cleverer model would still be wrong the moment the
        machine gets busy.
        """
        if self.status != "RUNNING" or self.done <= 0 or self.total <= 0:
            return None
        rate = self.done / max(self.elapsed_seconds, 1e-6)
        if rate <= 0:
            return None
        return max(0.0, (self.total - self.done) / rate)

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "note": self.note,
            "fraction": round(self.fraction, 6),
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "eta_seconds": None if self.eta_seconds is None else round(self.eta_seconds, 1),
            "error": self.error,
            "created_at": self.created_at,
        }


class JobRegistry:
    """Thread-safe registry of running and recently finished jobs."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._cancelling: set[str] = set()
        self._lock = threading.Lock()

    def submit(
        self,
        kind: str,
        label: str,
        total: int,
        work: Callable[[JobHandle], Any],
    ) -> Job:
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        job = Job(job_id=job_id, kind=kind, label=label, total=total)
        with self._lock:
            self._jobs[job_id] = job
            self._evict()
        handle = JobHandle(job_id=job_id, total=total, _registry=self)

        def run() -> None:
            with self._lock:
                job.status = "RUNNING"
                job.started_at = time.time()
            try:
                result = work(handle)
            except Cancelled:
                with self._lock:
                    job.status = "CANCELLED"
                    job.finished_at = time.time()
                    self._cancelling.discard(job_id)
                return
            except Exception as exc:
                with self._lock:
                    job.status = "FAILED"
                    job.error = f"{type(exc).__name__}: {exc}"
                    job.finished_at = time.time()
                    self._cancelling.discard(job_id)
                traceback.print_exc()
                return
            with self._lock:
                job.status = "DONE"
                job.result = result
                job.done = job.total
                job.finished_at = time.time()
                self._cancelling.discard(job_id)

        threading.Thread(target=run, name=f"job-{kind}", daemon=True).start()
        return job

    def _publish(self, job_id: str, done: int, note: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.done = done
                job.note = note

    def is_cancelling(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelling

    def cancel(self, job_id: str) -> bool:
        """Ask a job to stop. It ends at the worker's next progress checkpoint."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status in {"DONE", "FAILED", "CANCELLED"}:
                return False
            self._cancelling.add(job_id)
            return True

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[Job]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
        return jobs[:limit]

    def _evict(self) -> None:
        """Drop the oldest finished jobs. Caller holds the lock."""
        if len(self._jobs) <= MAX_RETAINED_JOBS:
            return
        finished = sorted(
            (job for job in self._jobs.values() if job.finished_at is not None),
            key=lambda item: item.finished_at or 0.0,
        )
        for job in finished[: len(self._jobs) - MAX_RETAINED_JOBS]:
            self._jobs.pop(job.job_id, None)


# One registry for the process. The API is a single-process desktop backend, so
# a module-level instance is the whole of the lifecycle management needed.
REGISTRY = JobRegistry()
