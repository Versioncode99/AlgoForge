"""Attacks on the burn-once holdout: one lineage, many simultaneous claimants.

A lineage's holdout is the only sealed evidence it will ever have. Spending it
twice does not merely duplicate work — the second run is chosen *after* seeing
the first, which turns the holdout into another tuning round while it still
carries the authority of sealed data. So the property under test is not "the
ledger usually notices", it is "exactly one caller can ever win".
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import forge
import pytest
from forge.research import ResearchLedger

# A second AlgoForge instance is a separate process against the same file, so
# the guarantee has to come from SQLite's locking, not from a Python lock.
CHILD = """
import sys
from pathlib import Path
from forge.research import ResearchLedger

ledger = ResearchLedger(Path(sys.argv[1]))
try:
    ledger.consume("LINEAGE", "split")
    print("WON")
except ValueError:
    print("LOST")
"""


@pytest.fixture
def ledger(tmp_path: Path) -> ResearchLedger:
    return ResearchLedger(tmp_path / "research.db")


def test_only_one_of_sixteen_threads_can_consume_a_holdout(
    ledger: ResearchLedger,
) -> None:
    """Two judge calls on one lineage, arriving together."""
    claimants = 16
    start = threading.Barrier(claimants)
    won: list[int] = []
    lost: list[int] = []
    unexpected: list[BaseException] = []

    def claim(index: int) -> None:
        try:
            start.wait()
            ledger.consume("LINEAGE", "split")
            won.append(index)
        except ValueError:
            lost.append(index)
        except BaseException as exc:  # a locked database would land here
            unexpected.append(exc)

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(claimants)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert unexpected == []
    assert len(won) == 1
    assert len(lost) == claimants - 1


def test_separate_lineages_do_not_contend(ledger: ResearchLedger) -> None:
    """Serialising writers must not make one lineage block another.

    The lock is there to make double-consumption impossible, not to make the
    engine's workers queue behind each other for unrelated holdouts.
    """
    count = 12
    start = threading.Barrier(count)
    consumed: list[int] = []
    unexpected: list[BaseException] = []

    def claim(index: int) -> None:
        try:
            start.wait()
            ledger.consume(f"LINEAGE_{index}", "split")
            consumed.append(index)
        except BaseException as exc:
            unexpected.append(exc)

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert unexpected == []
    assert len(consumed) == count


def test_a_crash_after_consuming_does_not_release_the_holdout(tmp_path: Path) -> None:
    """Dying mid-run must not hand the lineage a second look at sealed data.

    The result is never attached, so the row keeps `result_id` NULL. That is the
    honest record: the holdout was spent, and what it bought was lost.
    """
    path = tmp_path / "research.db"
    ResearchLedger(path).consume("LINEAGE", "split")

    restarted = ResearchLedger(path)
    spent = restarted.get("LINEAGE")
    assert spent is not None
    assert spent.result_id is None

    with pytest.raises(ValueError):
        restarted.consume("LINEAGE", "split")


def test_a_result_cannot_attach_to_a_lineage_that_never_consumed(
    ledger: ResearchLedger,
) -> None:
    with pytest.raises(KeyError):
        ledger.attach_result("NEVER_CONSUMED", "backtest_1")


def test_only_one_of_several_processes_can_consume_a_holdout(tmp_path: Path) -> None:
    """Two AlgoForge instances against one workspace."""
    path = tmp_path / "research.db"
    ResearchLedger(path)  # create the schema before the children race for it
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")

    # The children are plain interpreters: they do not inherit pytest's
    # `pythonpath` setting, so `forge` has to be put on their path explicitly.
    packages = str(Path(forge.__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONPATH": packages}

    processes = [
        subprocess.Popen(
            [sys.executable, str(script), str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for _ in range(4)
    ]
    outcomes = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=120)
        assert process.returncode == 0, stderr
        outcomes.append(stdout.strip())

    assert outcomes.count("WON") == 1
    assert outcomes.count("LOST") == len(processes) - 1
