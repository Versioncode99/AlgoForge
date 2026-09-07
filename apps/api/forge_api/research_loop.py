"""Continuous, bounded research intake and specialist hand-off.

The loop is deliberately separate from the strategy engine: stopping backtests
does not stop evidence discovery. It runs only while the local API is alive,
uses the existing daily model-call cap, and never promotes a strategy or burns
holdout data.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from forge.contracts.models import ApiEnvelope

from forge_api.activity import ActivityLog
from forge_api.agent_service import AgentService
from forge_api.jobs import REGISTRY, Job
from forge_api.settings_store import SettingsStore


class ResearchLoop:
    def __init__(
        self, agents: AgentService, settings: SettingsStore, log: ActivityLog, actions: Any
    ) -> None:
        self.agents = agents
        self.settings = settings
        self.log = log
        self.actions = actions
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._topic_index = 0
        self._cycles = 0
        self._sources_found = 0
        self._downstream_tasks = 0
        self._last_started: str | None = None
        self._last_finished: str | None = None
        self._next_run: str | None = None
        self._last_error: str | None = None
        self._in_flight = False

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._serve, name="continuous-research", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)

    def trigger(self) -> dict[str, Any]:
        current = self.settings.load().research_loop
        if not current.enabled:
            raise ValueError("Continuous research is paused in Settings")
        self._wake.set()
        return self.status()

    def status(self) -> dict[str, Any]:
        current = self.settings.load().research_loop
        with self._lock:
            thread_alive = bool(self._thread and self._thread.is_alive())
            return {
                "enabled": current.enabled,
                "running": thread_alive,
                "in_flight": self._in_flight,
                "interval_minutes": current.interval_minutes,
                "topics": list(current.topics),
                "topic_index": self._topic_index,
                "cycles": self._cycles,
                "sources_found": self._sources_found,
                "downstream_tasks": self._downstream_tasks,
                "last_started": self._last_started,
                "last_finished": self._last_finished,
                "next_run": self._next_run,
                "last_error": self._last_error,
                "scope": "Runs while the local API is open; no background cloud service.",
                "authority": (
                    "Research intake and specialist proposals only; "
                    "no promotion or holdout access."
                ),
            }

    def _serve(self) -> None:
        # Give application startup and short-lived tests a quiet window. The
        # operator can use Run now to bypass it.
        delay = 15.0
        while not self._stop.is_set():
            current = self.settings.load().research_loop
            wait_seconds = delay if current.enabled else 30.0
            with self._lock:
                self._next_run = (
                    datetime.now(UTC) + timedelta(seconds=wait_seconds)
                ).isoformat(timespec="seconds")
            woken = self._wake.wait(wait_seconds)
            self._wake.clear()
            if self._stop.is_set():
                break
            current = self.settings.load().research_loop
            if current.enabled:
                self._run_cycle(current.topics)
            delay = max(300.0, float(current.interval_minutes) * 60.0)
            if woken:
                # A manual run resets the cadence rather than causing a second
                # automatic run immediately afterwards.
                continue

    def _wait(self, job_id: str, timeout: float = 75.0) -> Job | None:
        deadline = time.monotonic() + timeout
        while not self._stop.wait(0.2):
            job = REGISTRY.get(job_id)
            if job and job.status in {"DONE", "FAILED", "CANCELLED"}:
                return job
            if time.monotonic() >= deadline:
                return job
        return REGISTRY.get(job_id)

    def _submit_and_wait(self, role: str, task: str) -> Job | None:
        try:
            submitted = self.agents.submit(role, task)
        except ValueError:
            return None
        return self._wait(str(submitted["job_id"]))

    def _run_cycle(self, topics: list[str]) -> None:
        if not topics:
            return
        topic = topics[self._topic_index % len(topics)]
        self._topic_index = (self._topic_index + 1) % len(topics)
        with self._lock:
            self._in_flight = True
            self._last_started = datetime.now(UTC).isoformat(timespec="seconds")
            self._last_error = None
        self.log.record("RESEARCH_LOOP", f"Evidence scan started: {topic}", "info")
        try:
            research = self._submit_and_wait("research", topic)
            if not research or research.status != "DONE" or not isinstance(research.result, dict):
                raise RuntimeError("Research scout did not complete")
            source_ids = [str(value) for value in research.result.get("source_ids", [])]
            self._sources_found += len(source_ids)
            evidence = ", ".join(source_ids[:8]) or "no new source IDs"
            hypothesis_task = (
                f"Review the latest research scan on '{topic}' (source IDs: {evidence}). "
                "Identify a falsifiable mechanism and counter-evidence. If it fits an existing "
                "template, emit a source-linked experiment proposal. Do not invent a new family "
                "without an economic mechanism and explicit data requirements."
            )
            hypothesis = self._submit_and_wait("hypothesis", hypothesis_task)
            if hypothesis:
                self._downstream_tasks += 1
            if hypothesis and isinstance(hypothesis.result, dict):
                candidate = hypothesis.result.get("family_candidate")
                if isinstance(candidate, dict):
                    arguments = {
                        key: candidate[key]
                        for key in ("key", "label", "description", "mechanism", "data_requirements")
                        if key in candidate
                    }
                    try:
                        created = self.actions.call("create_family", arguments)
                    except Exception as exc:
                        self.log.record(
                            "RESEARCH_LOOP",
                            f"Family candidate refused: {type(exc).__name__}: {exc}"[:400],
                            "warn",
                        )
                    else:
                        self.log.record(
                            "RESEARCH_LOOP",
                            f"Evidence-backed family registered: {created['created']}",
                            "pass" if created.get("runnable") else "warn",
                        )
            engineering_task = (
                f"Translate supported findings from '{topic}' (source IDs: {evidence}) into the "
                "existing executable template grid when possible. If capability or data is "
                "missing, report BLOCKED_DATA instead of fabricating a runnable strategy."
            )
            if self._submit_and_wait("strategy_code", engineering_task):
                self._downstream_tasks += 1
            self._cycles += 1
            self.log.record(
                "RESEARCH_LOOP",
                f"Cycle {self._cycles} complete: {len(source_ids)} references "
                "handed to specialists",
                "pass",
            )
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self.log.record("RESEARCH_LOOP", self._last_error, "fail")
        finally:
            with self._lock:
                self._in_flight = False
                self._last_finished = datetime.now(UTC).isoformat(timespec="seconds")


def build_research_loop_router(loop: ResearchLoop) -> APIRouter:
    router = APIRouter(prefix="/api/v1/research-loop", tags=["research-loop"])

    @router.get("", response_model=ApiEnvelope[dict[str, Any]])
    def status() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(data=loop.status())

    @router.post("/run", response_model=ApiEnvelope[dict[str, Any]])
    def run_now() -> ApiEnvelope[dict[str, Any]]:
        try:
            return ApiEnvelope(data=loop.trigger())
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
