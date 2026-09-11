"""The runtime monitor must never call an idle engine RUNNING.

These tests exist because the interface used to read a boolean that meant
"threads exist" and rendered it as RUNNING. Every case here is a situation in
which that boolean said RUNNING and nothing was happening.
"""

from __future__ import annotations

import pytest
from forge.research.runtime import (
    DEAD_AFTER_SECONDS,
    MAX_BACKOFF_SECONDS,
    STALE_AFTER_SECONDS,
    Outcome,
    RuntimeMonitor,
    RuntimeState,
)


@pytest.fixture
def monitor() -> RuntimeMonitor:
    m = RuntimeMonitor(no_progress_seconds=30.0, loop_threshold=5)
    m.starting(workers=2)
    return m


def _at(monitor: RuntimeMonitor, seconds: float) -> float:
    """A timestamp ``seconds`` after the monitor started."""
    return (monitor._started_at or 0.0) + seconds


def test_a_fresh_monitor_is_starting(monitor: RuntimeMonitor) -> None:
    assert monitor.diagnose().state is RuntimeState.STARTING


def test_progress_inside_the_window_is_running(monitor: RuntimeMonitor) -> None:
    monitor.beat("0", "backtesting")
    monitor.record("0", Outcome.PROGRESS, "judged one candidate")
    assert monitor.diagnose().state is RuntimeState.RUNNING


def test_duplicates_alone_are_never_running(monitor: RuntimeMonitor) -> None:
    """The 1,290 case. Eight workers, every cycle a duplicate, no progress."""
    for index in range(40):
        monitor.record(str(index % 2), Outcome.DUPLICATE, "already claimed")
    diagnosis = monitor.diagnose(now=_at(monitor, 120))
    assert diagnosis.state is not RuntimeState.RUNNING
    assert diagnosis.state is RuntimeState.EXHAUSTED
    assert diagnosis.code == "duplicate_loop"
    # And it says what to do about it rather than only that it is stuck.
    assert diagnosis.remedy


def test_a_short_run_of_duplicates_is_waiting_not_exhausted(monitor: RuntimeMonitor) -> None:
    """Below the loop threshold it is pressure, not a settled frontier."""
    monitor.record("0", Outcome.PROGRESS, "one real experiment")
    for _ in range(3):
        monitor.record("0", Outcome.DUPLICATE, "already claimed")
    diagnosis = monitor.diagnose(now=_at(monitor, 200))
    assert diagnosis.state is RuntimeState.WAITING_FOR_WORK
    assert diagnosis.code == "duplicate_pressure"


def test_an_empty_frontier_reads_as_waiting_for_work(monitor: RuntimeMonitor) -> None:
    for _ in range(10):
        monitor.record("0", Outcome.NO_WORK, "the frontier had nothing eligible")
    diagnosis = monitor.diagnose(now=_at(monitor, 120))
    assert diagnosis.state is RuntimeState.WAITING_FOR_WORK
    assert diagnosis.code == "no_work"


def test_an_exhausted_campaign_reads_as_exhausted(monitor: RuntimeMonitor) -> None:
    for _ in range(10):
        monitor.record("0", Outcome.EXHAUSTED, "experiment budget reached (200 experiments)")
    diagnosis = monitor.diagnose(now=_at(monitor, 120))
    assert diagnosis.state is RuntimeState.EXHAUSTED
    assert "budget" in diagnosis.reason


def test_repeated_cycle_errors_read_as_error(monitor: RuntimeMonitor) -> None:
    for _ in range(10):
        monitor.record("0", Outcome.ERROR, "ValueError: no such template")
    diagnosis = monitor.diagnose(now=_at(monitor, 120))
    assert diagnosis.state is RuntimeState.ERROR


def test_blocked_capability_reads_as_blocked(monitor: RuntimeMonitor) -> None:
    for _ in range(10):
        monitor.record("0", Outcome.BLOCKED, "requires ORDER_BOOK, which this dataset lacks")
    assert monitor.diagnose(now=_at(monitor, 120)).state is RuntimeState.BLOCKED


def test_a_declared_data_blocker_wins_over_everything(monitor: RuntimeMonitor) -> None:
    monitor.record("0", Outcome.PROGRESS, "fine")
    monitor.blocker("data", "nq_1m_16y: file not found")
    assert monitor.diagnose().state is RuntimeState.WAITING_FOR_DATA
    monitor.blocker("data", None)
    assert monitor.diagnose().state is RuntimeState.RUNNING


def test_all_workers_paused_reads_as_paused(monitor: RuntimeMonitor) -> None:
    monitor.beat("0", "paused", paused=True)
    monitor.beat("1", "paused", paused=True)
    assert monitor.diagnose().state is RuntimeState.PAUSED


def test_a_stale_worker_is_reported_but_does_not_stop_running(monitor: RuntimeMonitor) -> None:
    monitor.beat("0", "backtesting")
    monitor.record("0", Outcome.PROGRESS, "judged")
    monitor.beat("1", "backtesting")
    now = _at(monitor, STALE_AFTER_SECONDS + 10)
    # Worker 1 has not beaten since; worker 0 progressed a moment ago, so the
    # engine is genuinely working and the stale worker is a warning on the side.
    monitor._workers["0"].progress_at = now - 1
    monitor._workers["0"].beat_at = now - 1
    monitor._last_progress_at = now - 1
    diagnosis = monitor.diagnose(now=now)
    assert diagnosis.state is RuntimeState.RUNNING
    assert "1" in diagnosis.detail["stale_workers"]


def test_every_worker_dead_is_an_error(monitor: RuntimeMonitor) -> None:
    monitor.beat("0", "backtesting")
    monitor.beat("1", "backtesting")
    diagnosis = monitor.diagnose(now=_at(monitor, DEAD_AFTER_SECONDS + 60))
    assert diagnosis.state is RuntimeState.ERROR
    assert diagnosis.code == "workers_dead"


def test_no_outcomes_at_all_after_the_window_is_idle(monitor: RuntimeMonitor) -> None:
    """Workers beating but never finishing a cycle: wedged, not working."""
    monitor.beat("0", "backtesting")
    monitor.beat("1", "backtesting")
    diagnosis = monitor.diagnose(now=_at(monitor, 60))
    assert diagnosis.state is RuntimeState.IDLE
    assert diagnosis.code == "idle_no_outcomes"


def test_stopping_and_stopped_beat_every_other_reading(monitor: RuntimeMonitor) -> None:
    monitor.record("0", Outcome.PROGRESS, "judged")
    monitor.stopping()
    assert monitor.diagnose().state is RuntimeState.STOPPING
    monitor.stopped()
    assert monitor.diagnose().state is RuntimeState.STOPPED


def test_snapshot_answers_why_isnt_my_research_running() -> None:
    # A zero-second window so the snapshot, which reads the clock itself rather
    # than taking a `now`, sees the stall immediately.
    monitor = RuntimeMonitor(no_progress_seconds=0.0, loop_threshold=5)
    monitor.starting(workers=1)
    for _ in range(30):
        monitor.record("0", Outcome.DUPLICATE, "already claimed")
    snapshot = monitor.snapshot()
    assert snapshot["working"] is False
    assert snapshot["stalled"] is True
    assert snapshot["reason"]
    assert snapshot["remedy"]
    assert snapshot["outcome_counts"]["DUPLICATE"] == 30
    assert snapshot["recent_outcomes"][0]["outcome"] == "DUPLICATE"


def test_progress_resets_the_clock(monitor: RuntimeMonitor) -> None:
    for _ in range(30):
        monitor.record("0", Outcome.DUPLICATE, "already claimed")
    assert monitor.diagnose(now=_at(monitor, 120)).state is RuntimeState.EXHAUSTED
    monitor.record("0", Outcome.PROGRESS, "a genuinely new candidate")
    assert monitor.diagnose().state is RuntimeState.RUNNING


# ── pacing ───────────────────────────────────────────────────────────────────
def test_a_working_engine_is_never_slowed(monitor: RuntimeMonitor) -> None:
    monitor.beat("0", "backtesting")
    monitor.beat("1", "backtesting")
    monitor.record("0", Outcome.PROGRESS, "judged")
    assert monitor.backoff(4.0) == 4.0


def test_one_unlucky_round_is_not_a_stall(monitor: RuntimeMonitor) -> None:
    """Every worker deserves a turn before the engine decides it is stuck."""
    monitor.beat("0", "x")
    monitor.beat("1", "x")
    monitor.record("0", Outcome.DUPLICATE, "already claimed")
    assert monitor.backoff(4.0) == 4.0


def test_a_long_barren_run_backs_off_and_is_bounded(monitor: RuntimeMonitor) -> None:
    monitor.beat("0", "x")
    monitor.beat("1", "x")
    for _ in range(400):
        monitor.record("0", Outcome.EXHAUSTED, "experiment budget reached")
    backoff = monitor.backoff(1.0)
    assert backoff > 1.0
    assert backoff <= MAX_BACKOFF_SECONDS


def test_progress_restores_full_rate_immediately(monitor: RuntimeMonitor) -> None:
    """Recovery must not take as long as the stall did."""
    monitor.beat("0", "x")
    monitor.beat("1", "x")
    for _ in range(400):
        monitor.record("0", Outcome.DUPLICATE, "already claimed")
    assert monitor.backoff(1.0) > 1.0
    monitor.record("0", Outcome.PROGRESS, "a real candidate")
    assert monitor.backoff(1.0) == 1.0


def test_backing_off_does_not_change_what_is_reported(monitor: RuntimeMonitor) -> None:
    """Pacing is not reporting. The state and the reason are the same either way."""
    monitor.beat("0", "x")
    monitor.beat("1", "x")
    for _ in range(400):
        monitor.record("0", Outcome.EXHAUSTED, "experiment budget reached")
    before = monitor.diagnose(now=_at(monitor, 600))
    assert monitor.backoff(1.0) > 1.0
    after = monitor.diagnose(now=_at(monitor, 600))
    assert before.state is after.state
    assert before.reason == after.reason
