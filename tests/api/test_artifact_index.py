"""The durable artifact index: what makes a restart cheap, and what keeps it honest.

The property that matters is not "the index is fast" but "the index is never the
reason an answer is wrong". Every test here either proves a read is correct
without a warm index, or proves the index refuses to serve something stale.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from forge_api.activity import PARTIAL_WRITE_GRACE_SECONDS, BacktestStore
from forge_api.artifact_index import PROJECTION_VERSION, ArtifactIndex, project


def artifact(
    backtest_id: str,
    strategy_id: str,
    *,
    trades: int = 3,
    net_pnl: float = 100.0,
    finished: str = "2026-09-01T00:00:00Z",
    tier: str = "VALIDATION_OOS",
) -> dict[str, Any]:
    return {
        "calculation_version": "contract-units-v2",
        "backtest_id": backtest_id,
        "strategy_id": strategy_id,
        "spec_hash": "a" * 64,
        "code_hash": "b" * 64,
        "data_hash": "c" * 64,
        "parameters": {"sma_period": 30.0, "entry_atr": 1.5},
        "bar_count": 4000,
        "trades": [
            {
                "trade_id": f"trade_{index}",
                "direction": 1,
                "entry_decision_index": index,
                "entry_index": index + 1,
                "exit_decision_index": index + 4,
                "exit_index": index + 5,
                "entry_time": "2026-01-01T00:00:00Z",
                "exit_time": "2026-01-01T00:05:00Z",
                "entry_price": 100.0,
                "exit_price": 101.0,
                "gross_pnl": 10.0,
                "costs": 1.0,
                "net_pnl": 9.0,
                "bars_held": 4,
                "exit_reason": "signal",
            }
            for index in range(trades)
        ],
        "equity": [0.0, 9.0, 18.0],
        "net_pnl": net_pnl,
        "gross_pnl": net_pnl + 10.0,
        "total_costs": 10.0,
        "win_rate": 0.6,
        "max_drawdown": 25.0,
        "lookahead_clean": True,
        "labels": ["REAL_DATA", "VALIDATION_OOS"],
        "evidence_tier": tier,
        "dataset_key": "mnq-1m",
        "partition_name": "VALIDATION",
        "split_receipt": {"split_id": "split-1"},
        "started_at": "2026-09-01T00:00:00Z",
        "finished_at": finished,
    }


def write(root: Path, payload: dict[str, Any]) -> Path:
    path = root / f"{payload['backtest_id']}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


class FakeResult:
    """The narrow part of BacktestResult that BacktestStore.save touches."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.backtest_id = payload["backtest_id"]
        self.strategy_id = payload["strategy_id"]

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        return self._payload


# ── projection ───────────────────────────────────────────────────────────────


def test_projection_carries_every_field_a_list_row_uses() -> None:
    row = project(artifact("backtest_1", "alpha"))
    assert row["strategy_id"] == "alpha"
    assert row["trade_count"] == 3
    assert row["net_pnl"] == 100.0
    assert row["split_id"] == "split-1"
    assert row["evidence_tier"] == "VALIDATION_OOS"
    assert row["labels"] == ["REAL_DATA", "VALIDATION_OOS"]
    assert row["parameters"] == {"sma_period": 30.0, "entry_atr": 1.5}


def test_projection_never_carries_the_trade_ledger() -> None:
    """The whole point: a projection must not be a second copy of the artifact."""
    row = project(artifact("backtest_1", "alpha", trades=500))
    assert "trades" not in row
    assert "equity" not in row
    assert row["trade_count"] == 500


def test_projection_of_a_legacy_artifact_reports_legacy_rather_than_defaulting() -> None:
    payload = artifact("backtest_1", "alpha")
    del payload["calculation_version"]
    del payload["evidence_tier"]
    row = project(payload)
    assert row["calculation_version"] == "legacy-price-points"
    assert row["evidence_tier"] == "LEGACY_IN_SAMPLE"


def test_projection_survives_a_missing_split_receipt() -> None:
    payload = artifact("backtest_1", "alpha")
    payload["split_receipt"] = None
    assert project(payload)["split_id"] is None


# ── index store ──────────────────────────────────────────────────────────────


def test_index_round_trips(tmp_path: Path) -> None:
    index = ArtifactIndex(tmp_path / "i.db")
    index.put_many([("a.json", 10, project(artifact("backtest_a", "alpha")))])
    loaded = index.load_all()
    assert loaded["a.json"][0] == 10
    assert loaded["a.json"][1]["strategy_id"] == "alpha"


def test_index_forgets_rows(tmp_path: Path) -> None:
    index = ArtifactIndex(tmp_path / "i.db")
    index.put_many([("a.json", 10, project(artifact("backtest_a", "alpha")))])
    index.forget(["a.json"])
    assert index.load_all() == {}


def test_a_projection_version_bump_discards_the_old_rows(tmp_path: Path) -> None:
    """Merging two projection shapes would serve rows missing fields silently."""
    path = tmp_path / "i.db"
    index = ArtifactIndex(path)
    index.put_many([("a.json", 10, project(artifact("backtest_a", "alpha")))])
    assert index.count() == 1

    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA user_version = {PROJECTION_VERSION + 1}")
    db.close()

    assert ArtifactIndex(path).count() == 0


def test_a_damaged_row_is_skipped_not_raised(tmp_path: Path) -> None:
    path = tmp_path / "i.db"
    index = ArtifactIndex(path)
    index.put_many(
        [
            ("a.json", 10, project(artifact("backtest_a", "alpha"))),
            ("b.json", 10, project(artifact("backtest_b", "beta"))),
        ]
    )
    with sqlite3.connect(path) as db:
        db.execute("UPDATE artifacts SET projection = ? WHERE name = ?", ("{not json", "a.json"))
    db.close()
    loaded = ArtifactIndex(path).load_all()
    assert "a.json" not in loaded
    assert "b.json" in loaded


# ── store behaviour ──────────────────────────────────────────────────────────


def test_a_cold_store_still_answers_correctly(tmp_path: Path) -> None:
    """Correctness must not depend on the backfill having run."""
    root = tmp_path / "backtests"
    root.mkdir()
    write(root, artifact("backtest_a", "alpha", trades=7))
    write(root, artifact("backtest_b", "beta", trades=2))

    store = BacktestStore(root)
    count, latest = store.list_summary("alpha")
    assert count == 1
    assert latest is not None and latest["trade_count"] == 7


def test_a_restart_reads_the_index_rather_than_the_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "backtests"
    root.mkdir()
    write(root, artifact("backtest_a", "alpha", trades=7))
    BacktestStore(root)._ready()

    # Corrupt the artifact. A second store that re-parsed it would lose the row;
    # one reading the durable index still has the projection.
    (root / "backtest_a.json").write_text("{ truncated", encoding="utf-8")
    revived = BacktestStore(root)
    count, latest = revived.list_summary("alpha")
    assert count == 1
    assert latest is not None and latest["trade_count"] == 7


def test_an_artifact_rewritten_at_a_different_size_is_re_read(tmp_path: Path) -> None:
    """Artifacts are immutable; one that moved is stale evidence, not evidence."""
    root = tmp_path / "backtests"
    root.mkdir()
    write(root, artifact("backtest_a", "alpha", trades=7))
    BacktestStore(root)._ready()

    write(root, artifact("backtest_a", "alpha", trades=11))
    _, latest = BacktestStore(root).list_summary("alpha")
    assert latest is not None and latest["trade_count"] == 11


def test_a_deleted_artifact_leaves_the_index(tmp_path: Path) -> None:
    root = tmp_path / "backtests"
    root.mkdir()
    write(root, artifact("backtest_a", "alpha"))
    write(root, artifact("backtest_b", "alpha", finished="2026-09-02T00:00:00Z"))
    store = BacktestStore(root)
    assert store.list_summary("alpha")[0] == 2

    (root / "backtest_b.json").unlink()
    revived = BacktestStore(root)
    assert revived.list_summary("alpha")[0] == 1
    assert revived.list_summary("alpha")[1]["backtest_id"] == "backtest_a"


def test_save_indexes_without_re_reading(tmp_path: Path) -> None:
    root = tmp_path / "backtests"
    root.mkdir()
    store = BacktestStore(root)
    store.save(FakeResult(artifact("backtest_a", "alpha", trades=4)))

    assert store.list_summary("alpha")[1]["trade_count"] == 4
    # And it is durable: a fresh store sees it without parsing anything.
    assert BacktestStore(root).list_summary("alpha")[1]["trade_count"] == 4


def test_newest_run_wins_regardless_of_write_order(tmp_path: Path) -> None:
    root = tmp_path / "backtests"
    root.mkdir()
    store = BacktestStore(root)
    store.save(FakeResult(artifact("backtest_new", "alpha", finished="2026-09-05T00:00:00Z")))
    store.save(FakeResult(artifact("backtest_old", "alpha", finished="2026-09-01T00:00:00Z")))
    assert store.list_summary("alpha")[1]["backtest_id"] == "backtest_new"


def test_projections_for_returns_every_run_newest_first(tmp_path: Path) -> None:
    root = tmp_path / "backtests"
    root.mkdir()
    write(root, artifact("backtest_a", "alpha", finished="2026-09-01T00:00:00Z"))
    write(root, artifact("backtest_b", "alpha", finished="2026-09-03T00:00:00Z"))
    rows = BacktestStore(root).projections_for("alpha")
    assert [row["backtest_id"] for row in rows] == ["backtest_b", "backtest_a"]


def test_projections_match_the_full_artifacts_they_replace(tmp_path: Path) -> None:
    """`projections_for` replaced `for_strategy` on the list paths. They must agree."""
    root = tmp_path / "backtests"
    root.mkdir()
    for index in range(5):
        write(
            root,
            artifact(
                f"backtest_{index}",
                "alpha",
                trades=index + 1,
                net_pnl=float(index * 10),
                finished=f"2026-09-0{index + 1}T00:00:00Z",
            ),
        )
    store = BacktestStore(root)
    full = store.for_strategy("alpha")
    projected = store.projections_for("alpha")
    assert len(full) == len(projected)
    for whole, row in zip(full, projected, strict=True):
        assert row["backtest_id"] == whole["backtest_id"]
        assert row["trade_count"] == len(whole["trades"])
        assert row["net_pnl"] == whole["net_pnl"]
        assert row["parameters"] == whole["parameters"]
        assert row["split_id"] == whole["split_receipt"]["split_id"]


def test_an_unparseable_artifact_is_absent_not_favourable(tmp_path: Path) -> None:
    root = tmp_path / "backtests"
    root.mkdir()
    (root / "backtest_broken.json").write_text("{ truncated", encoding="utf-8")
    write(root, artifact("backtest_ok", "alpha"))
    store = BacktestStore(root)
    assert store.count() == 1
    assert store.list_summary("alpha")[0] == 1


def test_a_damaged_artifact_is_recorded_once_not_retried_forever(tmp_path: Path) -> None:
    """A file that cannot be parsed must not re-enter the queue on every read.

    Found on the reference workspace: one artifact had its first 32 KB
    overwritten by another process. Because nothing recorded the failure, it sat
    in `pending` permanently and every strategy listing spawned a thread pool to
    fail at it again — 0.27s of pure retry across 399 rows.
    """
    root = tmp_path / "backtests"
    root.mkdir()
    (root / "backtest_broken.json").write_bytes(b'{"sessionId":"x"}' + b"\x00" * 64)
    write(root, artifact("backtest_ok", "alpha"))

    store = BacktestStore(root)
    store._ready()
    assert store.status()["pending"] == 0
    assert store.status()["unreadable"] == 1
    assert store.status()["ready"] is True

    # And a restart does not retry it either.
    revived = BacktestStore(root)
    revived._scan()
    assert revived._pending == []
    assert [row["name"] for row in revived.damaged()] == ["backtest_broken.json"]
    assert revived.damaged()[0]["reason"].startswith("DATA_CORRUPTED")


def test_a_repaired_artifact_is_retried(tmp_path: Path) -> None:
    """Written off at one size, not written off forever."""
    root = tmp_path / "backtests"
    root.mkdir()
    (root / "backtest_a.json").write_text("{ truncated", encoding="utf-8")
    BacktestStore(root)._ready()

    write(root, artifact("backtest_a", "alpha", trades=5))
    revived = BacktestStore(root)
    assert revived.list_summary("alpha")[1]["trade_count"] == 5
    assert revived.status()["unreadable"] == 0


def test_a_json_file_that_is_not_a_backtest_is_a_schema_error(tmp_path: Path) -> None:
    root = tmp_path / "backtests"
    root.mkdir()
    (root / "notes.json").write_text('{"hello": "world"}', encoding="utf-8")
    store = BacktestStore(root)
    store._ready()
    assert store.count() == 0
    assert store.damaged()[0]["reason"].startswith("SCHEMA_ERROR")


def test_status_reports_progress_honestly(tmp_path: Path) -> None:
    root = tmp_path / "backtests"
    root.mkdir()
    write(root, artifact("backtest_a", "alpha"))
    store = BacktestStore(root)
    cold = store.status()
    assert cold["scanned"] is False
    assert cold["ready"] is False

    store._ready()
    warm = store.status()
    assert warm["indexed"] == 1
    assert warm["pending"] == 0
    assert warm["ready"] is True


def test_warm_is_idempotent_and_leaves_the_store_ready(tmp_path: Path) -> None:
    root = tmp_path / "backtests"
    root.mkdir()
    for index in range(20):
        write(root, artifact(f"backtest_{index}", f"strategy_{index % 4}"))
    store = BacktestStore(root)
    store.warm()
    store.warm()
    assert store.count() == 20
    assert store.status()["ready"] is True


def test_index_database_is_not_mistaken_for_an_artifact(tmp_path: Path) -> None:
    root = tmp_path / "backtests"
    root.mkdir()
    write(root, artifact("backtest_a", "alpha"))
    store = BacktestStore(root)
    store._ready()
    assert (root / "_index.db").exists()
    assert store.count() == 1


@pytest.mark.parametrize("strategy_id", ["unknown", ""])
def test_a_strategy_with_no_runs_reports_none(tmp_path: Path, strategy_id: str) -> None:
    root = tmp_path / "backtests"
    root.mkdir()
    write(root, artifact("backtest_a", "alpha"))
    store = BacktestStore(root)
    assert store.list_summary(strategy_id) == (0, None)
    assert store.projections_for(strategy_id) == []
    assert store.latest_projection(strategy_id) is None


def test_a_scan_racing_with_writers_does_not_delete_their_work(tmp_path: Path) -> None:
    """The scan walks the directory outside the lock, because it is the slow
    part and the engine's writers must not block on it. That means artifacts
    saved during the walk are absent from the listing and present in the index,
    and treating those as vanished deleted just-written runs.

    Measured before the fix: 142 of 150 artifacts survived six concurrent
    writers, self-healing only on restart, and silently under-reporting a
    strategy's runs until then.
    """
    root = tmp_path / "backtests"
    root.mkdir()
    store = BacktestStore(root)
    failures: list[str] = []

    def writer(worker: int) -> None:
        try:
            for index in range(25):
                store.save(FakeResult(artifact(f"backtest_w{worker}_{index}", "alpha")))
        except Exception as exc:
            failures.append(f"{type(exc).__name__}: {exc}")

    def scanner() -> None:
        try:
            for _ in range(40):
                store._scan()
                store.count()
        except Exception as exc:
            failures.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=writer, args=(w,)) for w in range(6)]
    threads += [threading.Thread(target=scanner) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    on_disk = len(list(root.glob("*.json")))
    assert on_disk == 150
    assert store.count() == on_disk, "the index lost artifacts a concurrent scan raced with"
    assert BacktestStore(root).count() == on_disk


def test_concurrent_writers_queue_rather_than_race_the_busy_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writers must not depend on SQLite's busy handler to take turns.

    `BacktestStore.save` calls `put_many` outside its own lock, deliberately, so
    a database write does not block another worker's in-memory bookkeeping. That
    left the writes with no serialisation at all: six engine workers each opened
    a connection and fought for the write lock.

    SQLite's busy handler has no fairness guarantee, so under steady contention
    one connection can be starved past *any* timeout. That is what
    "database is locked" was on a loaded Windows runner — not a timeout too
    short, but a connection that never won.

    The timeout is squeezed to a millisecond here, which is what makes this a
    test of the queueing rather than of the machine's speed: with the busy
    handler as the only defence this fails outright, and with the writes queued
    it cannot fail however slow the filesystem is.
    """
    root = tmp_path / "backtests"
    root.mkdir()
    store = BacktestStore(root)
    monkeypatch.setattr(
        ArtifactIndex,
        "_connect",
        lambda self: sqlite3.connect(self.path, timeout=0.001),
    )
    failures: list[str] = []

    def writer(worker: int) -> None:
        try:
            for index in range(25):
                store.save(FakeResult(artifact(f"backtest_w{worker}_{index}", "alpha")))
        except Exception as exc:
            failures.append(f"{type(exc).__name__}: {exc}")

    def scanner() -> None:
        try:
            for _ in range(40):
                store._scan()
                store.count()
        except Exception as exc:
            failures.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=writer, args=(w,)) for w in range(6)]
    threads += [threading.Thread(target=scanner) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == [], "a writer lost the race for the write lock"
    assert store.count() == 150


def test_a_genuinely_deleted_artifact_is_still_forgotten(tmp_path: Path) -> None:
    """The race fix must not stop real deletions being noticed."""
    root = tmp_path / "backtests"
    root.mkdir()
    write(root, artifact("backtest_a", "alpha"))
    write(root, artifact("backtest_b", "alpha", finished="2026-09-02T00:00:00Z"))
    store = BacktestStore(root)
    assert store.count() == 2

    (root / "backtest_b.json").unlink()
    store._scan()
    assert store.count() == 1


def test_a_stale_temporary_is_swept_and_a_fresh_one_is_left_alone(tmp_path: Path) -> None:
    """`save` writes to a temporary and renames. A process killed between the
    two leaves the temporary behind forever, invisible because nothing reads
    them, accumulating in a directory that already holds thousands of files.

    Deleting a *live* writer's temporary would be far worse, hence the grace.
    """
    root = tmp_path / "backtests"
    root.mkdir()
    stale = root / "backtest_interrupted.tmp"
    stale.write_text('{"backtest_id": "x"', encoding="utf-8")
    aged = time.time() - PARTIAL_WRITE_GRACE_SECONDS - 60
    os.utime(stale, (aged, aged))

    fresh = root / "backtest_inflight.tmp"
    fresh.write_text('{"backtest_id": "y"', encoding="utf-8")

    store = BacktestStore(root)
    store._ready()
    assert not stale.exists(), "an hour-old temporary is residue and should be swept"
    assert fresh.exists(), "a temporary written seconds ago may still be in flight"


def test_a_temporary_beside_a_completed_artifact_is_kept(tmp_path: Path) -> None:
    """If the artifact landed, the rename is the writer's business, not ours."""
    root = tmp_path / "backtests"
    root.mkdir()
    write(root, artifact("backtest_a", "alpha"))
    companion = root / "backtest_a.tmp"
    companion.write_text("partial", encoding="utf-8")
    aged = time.time() - PARTIAL_WRITE_GRACE_SECONDS - 60
    os.utime(companion, (aged, aged))

    BacktestStore(root)._ready()
    assert companion.exists()


@pytest.mark.parametrize(
    "identity",
    [
        {"nested": "object"},
        ["a", "list"],
        42,
        "",
        "   ",
        None,
    ],
)
def test_a_non_string_identity_is_a_schema_error(tmp_path: Path, identity: Any) -> None:
    """A truthiness check let a nested object through, and `_regroup`
    stringified it into a grouping key like "{'nested': 'object'}" — an
    artifact filed under a strategy that cannot exist."""
    root = tmp_path / "backtests"
    root.mkdir()
    payload = artifact("backtest_weird", "alpha")
    payload["strategy_id"] = identity
    (root / "backtest_weird.json").write_text(json.dumps(payload), encoding="utf-8")

    store = BacktestStore(root)
    store._ready()
    assert store.count() == 0
    assert store.damaged()[0]["reason"].startswith("SCHEMA_ERROR")


def test_finished_at_ordering_uses_the_recorded_timestamp(tmp_path: Path) -> None:
    """Not file mtime: artifacts are copied between workspaces."""
    root = tmp_path / "backtests"
    root.mkdir()
    later = datetime(2026, 9, 9, tzinfo=UTC).isoformat().replace("+00:00", "Z")
    earlier = datetime(2026, 9, 2, tzinfo=UTC).isoformat().replace("+00:00", "Z")
    write(root, artifact("backtest_a", "alpha", finished=later))
    write(root, artifact("backtest_b", "alpha", finished=earlier))
    assert BacktestStore(root).list_summary("alpha")[1]["backtest_id"] == "backtest_a"
