"""The experiment record and its lineage edges.

The table used to be four columns and a JSON blob, with no parent/child edge at
all — so a candidate produced by the `neighbourhood` policy, which starts from a
previous attempt and moves one parameter, had no recorded connection to the
attempt it came from.

The migration path matters as much as the feature: a workspace holds real
research and must not have to be deleted to gain a column.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from forge_api.experiments import Experiments


@pytest.fixture
def experiments(tmp_path: Path) -> Experiments:
    return Experiments(tmp_path / "experiments.db")


def _reserve(
    experiments: Experiments, value: float, parent: str | None = None, **extra: object
) -> str:
    key = experiments.reserve(
        "mnq-1m", "momentum_breakout", {"lookback": value}, parent_id=parent, **extra
    )
    assert key is not None
    return key


# ── the original contract, unchanged ─────────────────────────────────────────


def test_reserve_still_refuses_a_duplicate(experiments: Experiments) -> None:
    first = experiments.reserve("s", "t", {"a": 1.0})
    second = experiments.reserve("s", "t", {"a": 1.0})
    assert first is not None
    assert second is None


def test_the_same_configuration_is_one_experiment_whatever_its_parent(
    experiments: Experiments,
) -> None:
    """Two routes to one configuration must not inflate the trial count.

    The Deflated Sharpe deflates against how many things were tried; counting a
    configuration twice because it was reached twice would raise the hurdle
    against a search that did not actually happen.
    """
    root = _reserve(experiments, 10.0)
    assert (
        experiments.reserve(
            "mnq-1m", "momentum_breakout", {"lookback": 10.0}, parent_id=root
        )
        is None
    )
    assert experiments.count("mnq-1m") == 1


def test_recent_still_exposes_payload_keys(experiments: Experiments) -> None:
    key = _reserve(experiments, 10.0)
    experiments.finish(key, development_net=42.0)
    row = experiments.recent("mnq-1m")[0]
    assert row["template"] == "momentum_breakout"
    assert row["parameters"] == {"lookback": 10.0}
    assert row["development_net"] == 42.0
    assert row["id"] == key


# ── provenance ───────────────────────────────────────────────────────────────


def test_provenance_is_stored_in_queryable_columns(experiments: Experiments) -> None:
    key = _reserve(
        experiments,
        10.0,
        policy="exploration",
        family="breakout",
        hypothesis="momentum persists intraday",
        dataset="mnq-1m",
        data_version="abc123",
        seed=20260901,
    )
    row = experiments.get(key)
    assert row is not None
    assert row["policy"] == "exploration"
    assert row["family"] == "breakout"
    assert row["hypothesis"] == "momentum persists intraday"
    assert row["data_version"] == "abc123"
    assert row["seed"] == 20260901
    assert row["status"] == "reserved"
    assert row["created_at"]


def test_finish_promotes_known_fields_and_keeps_the_rest(
    experiments: Experiments,
) -> None:
    key = _reserve(experiments, 10.0)
    experiments.finish(
        key,
        status="rejected",
        verdict_id="verdict_1",
        failure_gate="G8",
        development_net=-12.0,
    )
    row = experiments.get(key)
    assert row is not None
    assert row["verdict_id"] == "verdict_1"
    assert row["failure_gate"] == "G8"
    assert row["finished_at"]
    # Not a column, so it lives on in the blob.
    assert row["development_net"] == -12.0


def test_finishing_an_unknown_key_is_a_no_op(experiments: Experiments) -> None:
    experiments.finish("nope", status="passed")
    assert experiments.get("nope") is None


# ── lineage ──────────────────────────────────────────────────────────────────


def test_a_child_records_its_parent(experiments: Experiments) -> None:
    root = _reserve(experiments, 10.0)
    child = _reserve(experiments, 20.0, parent=root)
    assert experiments.get(child)["parent_id"] == root  # type: ignore[index]
    assert [row["id"] for row in experiments.children(root)] == [child]


def test_ancestors_walk_from_parent_to_root(experiments: Experiments) -> None:
    root = _reserve(experiments, 10.0)
    middle = _reserve(experiments, 20.0, parent=root)
    leaf = _reserve(experiments, 30.0, parent=middle)
    assert [row["id"] for row in experiments.ancestors(leaf)] == [middle, root]
    assert experiments.ancestors(root) == []


def test_descendants_are_breadth_first_and_complete(experiments: Experiments) -> None:
    root = _reserve(experiments, 10.0)
    a = _reserve(experiments, 20.0, parent=root)
    b = _reserve(experiments, 30.0, parent=root)
    grandchild = _reserve(experiments, 40.0, parent=a)
    found = [row["id"] for row in experiments.descendants(root)]
    assert set(found) == {a, b, grandchild}
    assert found.index(grandchild) > found.index(a)


def test_roots_are_the_experiments_with_no_parent(experiments: Experiments) -> None:
    root = _reserve(experiments, 10.0)
    _reserve(experiments, 20.0, parent=root)
    assert [row["id"] for row in experiments.roots("mnq-1m")] == [root]


def test_lineage_returns_the_whole_line(experiments: Experiments) -> None:
    root = _reserve(experiments, 10.0)
    middle = _reserve(experiments, 20.0, parent=root)
    leaf = _reserve(experiments, 30.0, parent=middle)

    line = experiments.lineage(middle)
    assert line["experiment"]["id"] == middle
    assert [row["id"] for row in line["ancestors"]] == [root]
    assert [row["id"] for row in line["children"]] == [leaf]


def test_lineage_of_an_unknown_experiment_is_empty_not_an_error(
    experiments: Experiments,
) -> None:
    assert experiments.lineage("nope")["experiment"] is None


def test_a_broken_parent_link_does_not_hang_the_walk(experiments: Experiments) -> None:
    """A corrupted parent_id must terminate, not spin a worker thread."""
    key = _reserve(experiments, 10.0)
    with experiments.connect() as db:
        db.execute("UPDATE attempts SET parent_id=? WHERE id=?", ("missing", key))
    assert experiments.ancestors(key) == []


def test_a_self_referential_parent_terminates(experiments: Experiments) -> None:
    key = _reserve(experiments, 10.0)
    with experiments.connect() as db:
        db.execute("UPDATE attempts SET parent_id=? WHERE id=?", (key, key))
    assert experiments.ancestors(key) == []


# ── migration ────────────────────────────────────────────────────────────────


def test_an_old_database_opens_and_keeps_its_rows(tmp_path: Path) -> None:
    """The pre-lineage schema, with a real row in it."""
    path = tmp_path / "experiments.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE attempts "
            "(id TEXT PRIMARY KEY, scope TEXT, template TEXT, payload TEXT)"
        )
        db.execute(
            "INSERT INTO attempts VALUES (?, ?, ?, ?)",
            (
                "old_1",
                "mnq-1m",
                "momentum_breakout",
                json.dumps(
                    {
                        "id": "old_1",
                        "template": "momentum_breakout",
                        "parameters": {"lookback": 55.0},
                        "status": "backtested",
                        "development_net": 91.0,
                    }
                ),
            ),
        )

    experiments = Experiments(path)

    row = experiments.get("old_1")
    assert row is not None
    assert row["parameters"] == {"lookback": 55.0}
    assert row["development_net"] == 91.0
    # _merge omits null columns rather than carrying them as None.
    assert row.get("parent_id") is None
    assert experiments.count("mnq-1m") == 1
    assert [r["id"] for r in experiments.roots("mnq-1m")] == ["old_1"]

    # And it still accepts new work alongside the migrated row.
    child = experiments.reserve(
        "mnq-1m", "momentum_breakout", {"lookback": 60.0}, parent_id="old_1"
    )
    assert child is not None
    assert [r["id"] for r in experiments.children("old_1")] == [child]


def test_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "experiments.db"
    first = Experiments(path)
    key = first.reserve("s", "t", {"a": 1.0})
    reopened = Experiments(path)
    assert reopened.get(str(key)) is not None
