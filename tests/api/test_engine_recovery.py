"""What survives an engine that was killed rather than stopped.

Reservation is a content-derived primary key, which is what stops two workers
running the same configuration. The same property is what made an interrupted
run permanent: the claim outlived the process holding it, and every later
attempt at those parameters was declined as a duplicate of work that never
happened.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from forge_api.experiments import Experiments

PARAMS = {"lookback": 10.0}


def _reserve(experiments: Experiments, value: float = 10.0, **extra: object) -> str | None:
    return experiments.reserve("mnq-1m", "momentum_breakout", {"lookback": value}, **extra)


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "experiments.db"


@pytest.mark.parametrize("interrupted_at", ["reserved", "running"])
def test_a_claim_left_by_a_dead_process_can_be_searched_again(
    path: Path, interrupted_at: str
) -> None:
    """The two points a worker can die holding a claim."""
    first = Experiments(path)
    key = _reserve(first)
    assert key is not None
    if interrupted_at == "running":
        first.finish(key, status="running")

    # A new process. Nothing is in flight, whatever the rows still say.
    restarted = Experiments(path)
    assert restarted.reclaim_abandoned("mnq-1m") == 1

    again = _reserve(restarted)
    assert again == key, "the retry must be the same experiment, not a second one"
    assert restarted.get(key)["status"] == "reserved"
    assert restarted.get(key).get("finished_at") is None
    assert restarted.count("mnq-1m") == 1, "recovery must not inflate the trial count"


def test_a_finished_experiment_is_never_reclaimed(path: Path) -> None:
    """Recovery must not reopen work that actually completed."""
    experiments = Experiments(path)
    key = _reserve(experiments)
    assert key is not None
    experiments.finish(key, status="backtested", development_sharpe=1.2)

    assert experiments.reclaim_abandoned("mnq-1m") == 0
    assert _reserve(experiments) is None
    assert experiments.get(key)["status"] == "backtested"


def test_reclaim_does_not_reach_into_another_scope(path: Path) -> None:
    experiments = Experiments(path)
    mine = experiments.reserve("mnq-1m", "tpl", PARAMS)
    theirs = experiments.reserve("es-1m", "tpl", PARAMS)
    assert mine and theirs

    assert experiments.reclaim_abandoned("mnq-1m") == 1
    assert experiments.get(theirs)["status"] == "reserved"
    assert experiments.reserve("es-1m", "tpl", PARAMS) is None


def test_an_abandoned_claim_is_still_only_claimed_once(path: Path) -> None:
    """Recovery introduced a read-then-write, so the race is worth re-proving.

    Eight workers restart together and all propose the recovered candidate.
    Exactly one may get it.
    """
    experiments = Experiments(path)
    key = _reserve(experiments)
    assert key is not None
    experiments.finish(key, status="running")
    assert experiments.reclaim_abandoned("mnq-1m") == 1

    workers = 8
    start = threading.Barrier(workers)
    claimed: list[str] = []
    declined: list[int] = []
    unexpected: list[BaseException] = []

    def claim(index: int) -> None:
        try:
            start.wait()
            got = _reserve(experiments)
            (claimed if got is not None else declined).append(got or index)
        except BaseException as exc:
            unexpected.append(exc)

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert unexpected == []
    assert claimed == [key]
    assert len(declined) == workers - 1
    assert experiments.count("mnq-1m") == 1


def test_a_fresh_reservation_is_still_only_claimed_once(path: Path) -> None:
    """The same race on the ordinary path, which the same lock now covers."""
    experiments = Experiments(path)
    workers = 8
    start = threading.Barrier(workers)
    claimed: list[str] = []
    unexpected: list[BaseException] = []

    def claim() -> None:
        try:
            start.wait()
            got = _reserve(experiments)
            if got is not None:
                claimed.append(got)
        except BaseException as exc:
            unexpected.append(exc)

    threads = [threading.Thread(target=claim) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert unexpected == []
    assert len(claimed) == 1
    assert experiments.count("mnq-1m") == 1
