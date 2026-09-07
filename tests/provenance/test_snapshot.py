"""Run snapshots: reproducible, not merely tamper-evident.

AlgoForge already stored a code_hash, so it could detect that a strategy had
changed since it was judged. It could not reconstruct the computation — the
judge's own source, the statistics module and the data identity were implicit.

Two questions are deliberately kept apart and tested separately:

* `verify()` — is the *record* undamaged?
* `drift()`  — have the *rules moved since*? A verdict from a judge that has
  since been edited is still an honest record of what that judge decided; it
  just cannot be compared with a fresh verdict as though the two agreed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from forge.provenance import CAPTURED_MODULES, RunSnapshots


@pytest.fixture
def snapshots(tmp_path: Path) -> RunSnapshots:
    return RunSnapshots(tmp_path / "runs")


VERDICT = {"verdict_id": "v1", "decision": "FAIL", "grade": "F"}
IDENTITY = {"dataset": "nq_1m_16y", "data_version": "abc", "seed": 20260901}


def _write(snapshots: RunSnapshots, run_id: str = "run_1", **extra: object):  # type: ignore[no-untyped-def]
    return snapshots.write(
        run_id=run_id,
        verdict=dict(VERDICT),
        identity=dict(IDENTITY),
        strategy_source="def signal(bars):\n    return 0\n",
        **extra,  # type: ignore[arg-type]
    )


# ── what a snapshot contains ─────────────────────────────────────────────────


def test_a_snapshot_captures_the_judge_that_produced_the_verdict(
    snapshots: RunSnapshots,
) -> None:
    """The point of the whole module: the rules are part of the record."""
    # Import them so they are in sys.modules and therefore capturable.
    import forge.judge.engine
    import forge.judge.statistics  # noqa: F401

    result = _write(snapshots)
    labels = {source.label for source in result.sources}
    assert "forge.judge.engine" in labels
    assert "forge.judge.statistics" in labels
    assert "strategy" in labels


def test_the_captured_source_is_readable_back_by_hash(snapshots: RunSnapshots) -> None:
    result = _write(snapshots)
    strategy = next(s for s in result.sources if s.label == "strategy")
    assert snapshots.source_text(strategy.sha256) == "def signal(bars):\n    return 0\n"


def test_identity_and_verdict_are_written_as_files(snapshots: RunSnapshots) -> None:
    result = _write(snapshots)
    assert json.loads((result.path / "verdict.json").read_text())["decision"] == "FAIL"
    identity = json.loads((result.path / "identity.json").read_text())
    assert identity["dataset"] == "nq_1m_16y"
    assert identity["snapshot_version"] == "1"
    assert identity["captured_at"]


def test_optional_sections_are_omitted_when_absent(snapshots: RunSnapshots) -> None:
    result = _write(snapshots)
    assert not (result.path / "evidence.json").exists()
    assert not (result.path / "preregistration.json").exists()


def test_optional_sections_are_written_when_present(snapshots: RunSnapshots) -> None:
    result = _write(
        snapshots,
        evidence={"trial_count": 9},
        preregistration={"content_hash": "abc"},
    )
    assert json.loads((result.path / "evidence.json").read_text())["trial_count"] == 9
    assert (result.path / "preregistration.json").exists()


def test_the_manifest_hashes_every_file_it_references(snapshots: RunSnapshots) -> None:
    result = _write(snapshots)
    manifest = snapshots.read(result.run_id)
    assert manifest is not None
    for name, meta in manifest["files"].items():
        assert (result.path / name).is_file(), name
        assert len(meta["sha256"]) == 64


# ── content addressing ───────────────────────────────────────────────────────


def test_identical_sources_are_stored_once_across_runs(snapshots: RunSnapshots) -> None:
    """A search judging thousands of candidates stores one unchanged judge once."""
    _write(snapshots, run_id="run_1")
    before = len(list(snapshots.sources.glob("*.py")))
    _write(snapshots, run_id="run_2")
    after = len(list(snapshots.sources.glob("*.py")))
    assert after == before


def test_a_different_strategy_adds_exactly_one_stored_source(
    snapshots: RunSnapshots,
) -> None:
    _write(snapshots, run_id="run_1")
    before = len(list(snapshots.sources.glob("*.py")))
    snapshots.write(
        run_id="run_2",
        verdict=dict(VERDICT),
        identity=dict(IDENTITY),
        strategy_source="def signal(bars):\n    return 1\n",
    )
    assert len(list(snapshots.sources.glob("*.py"))) == before + 1


# ── immutability ─────────────────────────────────────────────────────────────


def test_a_second_write_does_not_overwrite_the_first(snapshots: RunSnapshots) -> None:
    """Re-judging the same id would mean the first verdict was wrong. That is a
    second run, not a silent edit of the first."""
    first = _write(snapshots)
    second = snapshots.write(
        run_id="run_1",
        verdict={"verdict_id": "v2", "decision": "PASS", "grade": "A"},
        identity=dict(IDENTITY),
    )
    assert second.manifest_hash == first.manifest_hash
    assert json.loads((first.path / "verdict.json").read_text())["decision"] == "FAIL"


# ── verify ───────────────────────────────────────────────────────────────────


def test_an_untouched_snapshot_verifies(snapshots: RunSnapshots) -> None:
    result = _write(snapshots)
    report = snapshots.verify(result.run_id)
    assert report["intact"] is True
    assert report["changed"] == []


def test_an_edited_verdict_is_detected_and_named(snapshots: RunSnapshots) -> None:
    result = _write(snapshots)
    (result.path / "verdict.json").write_text('{"decision": "PASS"}', encoding="utf-8")
    report = snapshots.verify(result.run_id)
    assert report["intact"] is False
    assert {"kind": "file", "name": "verdict.json", "problem": "modified"} in report["changed"]


def test_a_deleted_file_is_detected(snapshots: RunSnapshots) -> None:
    result = _write(snapshots)
    (result.path / "identity.json").unlink()
    report = snapshots.verify(result.run_id)
    assert report["intact"] is False
    assert any(item["problem"] == "missing" for item in report["changed"])


def test_a_missing_stored_source_is_detected(snapshots: RunSnapshots) -> None:
    result = _write(snapshots)
    strategy = next(s for s in result.sources if s.label == "strategy")
    (snapshots.sources / f"{strategy.sha256}.py").unlink()
    report = snapshots.verify(result.run_id)
    assert report["intact"] is False
    assert any(item["kind"] == "source" for item in report["changed"])


def test_a_missing_snapshot_is_not_intact(snapshots: RunSnapshots) -> None:
    report = snapshots.verify("never_written")
    assert report["intact"] is False
    assert report["reason"] == "no snapshot"


def test_an_unreadable_manifest_reads_as_absent(snapshots: RunSnapshots) -> None:
    """Corruption must never be mistaken for an intact record."""
    result = _write(snapshots)
    (result.path / "manifest.json").write_text("{not json", encoding="utf-8")
    assert snapshots.read(result.run_id) is None
    assert snapshots.verify(result.run_id)["intact"] is False


# ── drift ────────────────────────────────────────────────────────────────────


def test_a_fresh_snapshot_has_not_drifted(snapshots: RunSnapshots) -> None:
    import forge.judge.engine  # noqa: F401

    result = _write(snapshots)
    report = snapshots.drift(result.run_id)
    assert report["comparable"] is True
    assert report["drifted"] == []


def test_drift_reports_a_judge_that_changed_since(snapshots: RunSnapshots) -> None:
    """Rewrite the recorded hash to stand in for the judge having been edited."""
    import forge.judge.engine  # noqa: F401

    result = _write(snapshots)
    manifest = snapshots.read(result.run_id)
    assert manifest is not None
    for item in manifest["sources"]:
        if item["label"] == "forge.judge.engine":
            item["sha256"] = "0" * 64
    (result.path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    report = snapshots.drift(result.run_id)
    assert report["comparable"] is False
    assert report["drifted"][0]["module"] == "forge.judge.engine"
    assert report["drifted"][0]["was"] == "0" * 64


def test_drift_of_a_missing_snapshot_is_not_comparable(snapshots: RunSnapshots) -> None:
    assert snapshots.drift("never_written")["comparable"] is False


def test_the_captured_module_list_is_all_real_modules() -> None:
    """A typo here would silently capture nothing."""
    import importlib

    for name in CAPTURED_MODULES:
        assert importlib.import_module(name) is not None
