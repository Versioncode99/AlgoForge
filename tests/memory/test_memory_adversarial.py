"""Attacks on research memory: poisoning it, racing it, and contaminating it.

Research memory decides what the engine stops exploring. Every property here is
about the *destructive* direction — a wrongly recorded failure deletes candidates
that were never tested, and does it silently. The asymmetry is the whole point:
failing to prune costs one re-run, pruning wrongly costs a region.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import pytest
from forge.memory import (
    FailureClass,
    ResearchMemory,
    classify_gate,
    classify_reason,
    reach_of,
)


@dataclass(frozen=True)
class Spec:
    name: str
    low: float
    high: float


RANGES = (Spec("lookback", 0.0, 100.0), Spec("threshold", 0.0, 100.0))

# The classes that say nothing about the parameter region.
NON_RESEARCH = (
    FailureClass.DATA,
    FailureClass.BOOKKEEPING,
    FailureClass.INFRASTRUCTURE,
)


@pytest.fixture
def memory(tmp_path: Path) -> ResearchMemory:
    return ResearchMemory(tmp_path / "research_memory.db")


def test_concurrent_writers_keep_the_chain_intact(memory: ResearchMemory) -> None:
    """Eight workers writing at once must not fork the hash chain.

    Regression: the tail row was read outside the write transaction, so every
    worker linked to the same predecessor. The chain forked during ordinary
    operation, `verify_chain` reported tampering that never happened, and real
    tampering became indistinguishable from routine concurrency.
    """
    workers = 8
    per_worker = 6
    errors: list[BaseException] = []
    start = threading.Barrier(workers)

    def writer(index: int) -> None:
        try:
            start.wait()
            for step in range(per_worker):
                memory.record(
                    scope="scope",
                    template="template",
                    failure_class=FailureClass.NEGATIVE_EXPECTANCY,
                    reason=f"worker {index} step {step}",
                    parameters={"lookback": float(index * 10 + step), "threshold": float(step)},
                    ranges=RANGES,
                )
        except BaseException as exc:  # reported to the assertions, not swallowed
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert memory.total("scope") == workers * per_worker
    assert memory.verify_chain() == {
        "intact": True,
        "entries": workers * per_worker,
        "broken_at": None,
    }


def test_infrastructure_wording_never_reaches_a_pruning_class() -> None:
    """A broken machine phrased in the vocabulary of a research result.

    Regression: `classify_reason` matched "no trades" before considering that
    the sentence also said the dataset was unavailable, so a vendor outage
    pruned a parameter region at reach 0.08.
    """
    outages = (
        "dataset unavailable: no trades could be loaded",
        "market data connection reset; no trades",
        "timed out reading bars, no trades produced",
        "artifact corrupt, no trades recoverable",
        "permission denied opening dataset, no trades",
    )
    for reason in outages:
        assert reach_of(classify_reason(reason)) is None, reason


def test_a_genuine_no_trades_result_still_prunes() -> None:
    """The fix must not disarm the class it guards.

    These are the engine's own stop messages for a run that executed correctly
    and produced nothing.
    """
    assert classify_reason("produced no trades") is FailureClass.NO_TRADES
    assert classify_reason("burn-once holdout produced no trades") is FailureClass.NO_TRADES


def test_inconclusive_is_never_evidence() -> None:
    """Absence of measurement must not become a research conclusion."""
    for number in range(14):
        gate = f"G{number}"
        for status in ("INCONCLUSIVE", "PASS", "WARN", "SKIPPED", ""):
            assert classify_gate(gate, status) is None, (gate, status)


def test_non_research_failures_never_prune_even_at_zero_distance(
    memory: ResearchMemory,
) -> None:
    """The strongest possible case for pruning, which must still not prune.

    Identical parameters, so distance is 0 on every axis. Only the class stands
    between a disk error and a deleted region.
    """
    params = {"lookback": 50.0, "threshold": 50.0}
    for failure in NON_RESEARCH:
        memory.record(
            scope="scope",
            template="template",
            failure_class=failure,
            reason=f"{failure} occurred",
            parameters=params,
            ranges=RANGES,
        )
    assert (
        memory.prune(
            scope="scope", template="template", parameters=params, ranges=RANGES
        )
        is None
    )


def test_a_failure_cannot_prune_across_scope_or_template(memory: ResearchMemory) -> None:
    """Template-wide reach means this template, not every template."""
    params = {"lookback": 50.0, "threshold": 50.0}
    memory.record(
        scope="scope_a",
        template="template_a",
        # The widest-reaching class there is: if anything leaks, this does.
        failure_class=FailureClass.LOOKAHEAD,
        reason="lookahead in template_a",
        parameters=params,
        ranges=RANGES,
    )

    def prune(scope: str, template: str) -> object | None:
        return memory.prune(
            scope=scope, template=template, parameters=params, ranges=RANGES
        )

    assert prune("scope_b", "template_a") is None
    assert prune("scope_a", "template_b") is None
    assert prune("scope_a", "template_a") is not None
