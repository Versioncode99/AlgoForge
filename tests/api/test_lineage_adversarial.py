"""Attacks on experiment provenance and lineage.

Two things here are evidence rather than bookkeeping. `preregistration_hash` is
what G1 reads to decide whether the claim being judged is the claim that was
frozen before the numbers existed. `parent_id` is what answers "where did this
come from?". Both are fixed when the experiment is reserved, and an outcome
that can rewrite either is not an outcome — it is a second draft of the record.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from forge_api.experiments import Experiments


@pytest.fixture
def experiments(tmp_path: Path) -> Experiments:
    return Experiments(tmp_path / "experiments.db")


def _reserve(experiments: Experiments, value: float, **extra: object) -> str:
    key = experiments.reserve("mnq-1m", "momentum_breakout", {"lookback": value}, **extra)
    assert key is not None
    return key


# ── provenance cannot be rewritten by an outcome ─────────────────────────────


def test_finish_cannot_rewrite_the_preregistration_hash(
    experiments: Experiments,
) -> None:
    """The "run it, dislike it, rewrite the claim" move, via the outcome writer."""
    key = _reserve(experiments, 10.0, preregistration_hash="FROZEN")

    with pytest.raises(ValueError, match="provenance"):
        experiments.finish(key, preregistration_hash="REWRITTEN")

    row = experiments.get(key)
    assert row is not None
    assert row["preregistration_hash"] == "FROZEN"


def test_finish_cannot_manufacture_a_hash_for_an_unregistered_experiment(
    experiments: Experiments,
) -> None:
    """The payload blob is the second route, and it is the more dangerous one.

    `_merge` lets the blob supply any field whose column is NULL, so an
    experiment that was never pre-registered could acquire a hash it never had
    — evidence created from nothing rather than altered.
    """
    key = _reserve(experiments, 10.0)
    assert experiments.get(key).get("preregistration_hash") is None

    with pytest.raises(ValueError, match="provenance"):
        experiments.finish(key, preregistration_hash="INVENTED")

    assert experiments.get(key).get("preregistration_hash") is None


def test_finish_cannot_redirect_a_lineage_edge(experiments: Experiments) -> None:
    root = _reserve(experiments, 10.0)
    child = _reserve(experiments, 20.0, parent_id=root)

    with pytest.raises(ValueError, match="provenance"):
        experiments.finish(child, parent_id=None)

    assert experiments.get(child)["parent_id"] == root


def test_finish_still_records_outcomes(experiments: Experiments) -> None:
    """The guard must not stop `finish` doing its actual job."""
    key = _reserve(experiments, 10.0)
    experiments.finish(
        key,
        status="passed",
        development_net=42.0,
        development_sharpe=1.5,
        backtest_id="backtest_1",
    )
    row = experiments.get(key)
    assert row["status"] == "passed"
    assert row["development_net"] == 42.0
    assert row["development_sharpe"] == 1.5
    assert row["backtest_id"] == "backtest_1"
    assert row["finished_at"]


# ── the graph is acyclic by construction ─────────────────────────────────────


def test_an_edge_cannot_point_at_an_experiment_that_does_not_exist(
    experiments: Experiments,
) -> None:
    """A dangling parent is not a lineage; it is a claim about one."""
    with pytest.raises(ValueError, match="does not exist"):
        experiments.reserve(
            "mnq-1m", "momentum_breakout", {"lookback": 10.0}, parent_id="NO_SUCH_ATTEMPT"
        )


def test_an_experiment_declines_to_be_its_own_parent(experiments: Experiments) -> None:
    """A neighbourhood step that clamped back to where it started."""
    root = _reserve(experiments, 10.0)
    assert (
        experiments.reserve(
            "mnq-1m", "momentum_breakout", {"lookback": 10.0}, parent_id=root
        )
        is None
    )
    assert experiments.get(root).get("parent_id") is None
    assert experiments.count("mnq-1m") == 1


def test_a_cycle_cannot_be_built_through_the_api(experiments: Experiments) -> None:
    """A -> B -> A, attempted every way the class allows.

    Edges may only point at rows that already exist, and no existing edge can be
    redirected afterwards, so every edge points strictly backwards in insertion
    order. That is what makes the graph acyclic — not the read-side guards.
    """
    root = _reserve(experiments, 10.0)
    child = _reserve(experiments, 20.0, parent_id=root)

    # Close the loop by redirecting the root at its own child.
    with pytest.raises(ValueError, match="provenance"):
        experiments.finish(root, parent_id=child)

    assert experiments.get(root).get("parent_id") is None
    assert [row["id"] for row in experiments.roots("mnq-1m")] == [root]


def test_a_cycle_written_directly_into_the_database_cannot_hang_a_reader(
    experiments: Experiments, tmp_path: Path
) -> None:
    """The read guards still matter: this class is not the only writer of the file.

    The lineage is wrong and nothing can make it right, but a corrupted
    `parent_id` must not spin a worker thread forever.
    """
    root = _reserve(experiments, 10.0)
    child = _reserve(experiments, 20.0, parent_id=root)

    with sqlite3.connect(tmp_path / "experiments.db") as db:
        db.execute("UPDATE attempts SET parent_id=? WHERE id=?", (child, root))

    assert len(experiments.ancestors(root)) <= 2
    assert len(experiments.descendants(root)) <= 2
    # Every node in a cycle has a parent, so the whole line drops out of roots().
    # Honest, and visibly broken, rather than an invented root.
    assert experiments.roots("mnq-1m") == []
