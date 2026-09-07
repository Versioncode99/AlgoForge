"""Where output goes, and the Markdown mirror of it.

The pointer file is the one piece of configuration that cannot live in the thing
it configures, and the mirror is the one writer that must never be able to break
a backtest. Both properties are easy to lose in a refactor, so they are pinned
here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from forge.strategy import ParameterSpec, StrategySpec
from forge.vault import VaultMirror, Workspace, inspect, migrate, resolve, write_pointer
from forge.vault.location import NOTES_FOLDER


def make_spec(name: str = "Test Candidate") -> StrategySpec:
    return StrategySpec(
        strategy_id="test_candidate_0123456789",
        name=name,
        lineage="test_candidate",
        family="momentum",
        market="futures",
        symbol="MNQ.CME",
        template="momentum_breakout",
        hypothesis="A" * 60,
        falsifiable_prediction="B" * 40,
        parameters=(ParameterSpec(name="lookback", default=20, low=5, high=80, step=5),),
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def test_a_repo_with_no_pointer_keeps_the_pre_vault_layout(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ALGOFORGE_VAULT", raising=False)
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    workspace = resolve(tmp_path)
    assert workspace.vault_mode is False
    # Flat mode has to keep addressing the same directories an existing checkout
    # already uses, or a running instance loses sight of its own strategies.
    assert workspace.strategies == tmp_path / "strategies"
    assert workspace.data == tmp_path / "data"


def test_the_pointer_moves_storage_and_the_environment_beats_it(tmp_path: Path, monkeypatch):
    repo, vault, override = tmp_path / "repo", tmp_path / "vault", tmp_path / "override"
    for path in (repo, vault, override):
        path.mkdir()
    (repo / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    monkeypatch.delenv("ALGOFORGE_VAULT", raising=False)
    write_pointer(repo, vault)
    assert resolve(repo).root == vault
    assert json.loads((repo / "config" / "storage.json").read_text())["location"] == str(vault)

    monkeypatch.setenv("ALGOFORGE_VAULT", str(override))
    assert resolve(repo).root == override


def test_a_vault_keeps_notes_and_machine_storage_apart(tmp_path: Path):
    (tmp_path / ".obsidian").mkdir()
    workspace = Workspace(repo=tmp_path, root=tmp_path, vault_mode=True).ensure()
    assert workspace.notes == tmp_path / NOTES_FOLDER
    # Obsidian hides dot-directories, which is the whole reason a 1.8 GB backtest
    # store can live inside a note vault without appearing as 400,000 notes.
    assert workspace.store.name.startswith(".")
    assert workspace.strategies.is_relative_to(workspace.store)
    assert workspace.strategy_notes.is_relative_to(workspace.notes)


def test_inspect_refuses_the_repository_and_reports_what_it_found(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    report = inspect(tmp_path, str(tmp_path))
    assert report["usable"] is False
    assert any("repository" in problem for problem in report["problems"])

    fresh = tmp_path / "elsewhere"
    ready = inspect(tmp_path, str(fresh))
    assert ready["exists"] is False and ready["creatable"] is True and ready["usable"] is True


def test_migration_copies_output_but_leaves_the_purchased_market_cache(tmp_path: Path):
    repo, vault = tmp_path / "repo", tmp_path / "vault"
    (repo / "strategies" / "one").mkdir(parents=True)
    (repo / "strategies" / "one" / "spec.json").write_text("{}", encoding="utf-8")
    (repo / "data" / "market").mkdir(parents=True)
    (repo / "data" / "market" / "nq.parquet").write_bytes(b"expensive")
    (repo / "data" / "backtests").mkdir()
    vault.mkdir()

    source = Workspace(repo=repo, root=repo, vault_mode=False)
    target = Workspace(repo=repo, root=vault, vault_mode=True).ensure()
    report = migrate(source, target)

    assert (target.strategies / "one" / "spec.json").exists()
    assert (target.data / "backtests").exists()
    # Re-downloading a Databento archive costs money. A relocation must never be
    # the reason that happens, so the cache stays where it was bought.
    assert not (target.data / "market").exists()
    assert any("market" in item for item in report["left_behind"])
    # Copy, not move: the old copy is still there afterwards.
    assert (repo / "strategies" / "one" / "spec.json").exists()


def test_a_strategy_note_carries_the_hypothesis_and_the_paper_only_warning(tmp_path: Path):
    workspace = Workspace(repo=tmp_path, root=tmp_path, vault_mode=True).ensure()
    mirror = VaultMirror(workspace)
    path = mirror.strategy(make_spec(), source_titles=["Time Series Momentum"])
    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert 'family: "momentum"' in text
    assert "[[Time Series Momentum]]" in text
    # The mirror is read by a person in Obsidian, far from the app's own
    # disclaimers, so each note has to carry its own.
    assert "Paper only" in text
    assert mirror.status()["notes_written"] == 1


def test_a_note_that_cannot_be_written_is_skipped_not_raised(tmp_path: Path, monkeypatch):
    workspace = Workspace(repo=tmp_path, root=tmp_path, vault_mode=True).ensure()
    mirror = VaultMirror(workspace)

    def refuse(*args, **kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "write_text", refuse)
    assert mirror.strategy(make_spec()) is None
    assert mirror.status()["notes_skipped"] == 1
    assert "Permission denied" in str(mirror.status()["last_error"])


def test_a_disabled_mirror_writes_nothing(tmp_path: Path):
    workspace = Workspace(repo=tmp_path, root=tmp_path, vault_mode=True).ensure()
    mirror = VaultMirror(workspace, enabled=False)
    assert mirror.strategy(make_spec()) is None
    assert list(workspace.strategy_notes.glob("*.md")) == []


@pytest.mark.parametrize(
    "title",
    ["A/B: testing?", "x" * 300, "trailing dot.", 'quotes "and" pipes|'],
)
def test_note_names_survive_titles_windows_would_refuse(tmp_path: Path, title: str):
    workspace = Workspace(repo=tmp_path, root=tmp_path, vault_mode=True).ensure()
    mirror = VaultMirror(workspace)
    path = mirror.paper({"id": "p1", "title": title, "url": "https://example.org"})
    assert path is not None and path.exists()
