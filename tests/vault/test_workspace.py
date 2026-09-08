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
from forge.vault import (
    LAYOUT_APP,
    LAYOUT_VAULT,
    VaultMirror,
    Workspace,
    default_root,
    inspect,
    migrate,
    migrate_layout,
    read_pointer_layout,
    resolve,
    write_pointer,
)
from forge.vault.location import NOTES_FOLDER, STORE_FOLDER


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


def test_a_fresh_checkout_uses_the_application_data_directory(tmp_path: Path, monkeypatch):
    """A clone must not become a data directory just by being run once.

    That is how mutable state ends up beside the code — in a directory an
    updater may replace wholesale, or that a normal user cannot write to.
    """
    monkeypatch.delenv("ALGOFORGE_VAULT", raising=False)
    monkeypatch.delenv("ALGOFORGE_HOME", raising=False)
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    workspace = resolve(tmp_path)
    assert workspace.root == default_root()
    assert workspace.layout == LAYOUT_APP
    assert workspace.vault_mode is False


def test_a_checkout_that_already_holds_research_is_adopted(tmp_path: Path, monkeypatch):
    """An existing installation is never orphaned by the new default."""
    monkeypatch.delenv("ALGOFORGE_VAULT", raising=False)
    monkeypatch.delenv("ALGOFORGE_HOME", raising=False)
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "strategies" / "one").mkdir(parents=True)
    (tmp_path / "strategies" / "one" / "spec.json").write_text("{}", encoding="utf-8")

    workspace = resolve(tmp_path)
    assert workspace.root == tmp_path
    assert workspace.strategies == tmp_path / "strategies"
    assert workspace.data == tmp_path / "data"


def test_the_application_layout_needs_no_obsidian(tmp_path: Path):
    """The acceptance criterion: AlgoForge runs without a vault at all."""
    workspace = Workspace(repo=tmp_path, root=tmp_path / "app", vault_mode=False).ensure()
    assert workspace.obsidian is None
    assert workspace.layout == LAYOUT_APP
    assert workspace.notes == workspace.root / "research" / "notes"
    # Nothing the application owns lives inside a note vault.
    assert NOTES_FOLDER not in str(workspace.store)
    assert STORE_FOLDER not in str(workspace.store)
    for path in (workspace.strategies, workspace.data, workspace.exports, workspace.cache):
        assert path.is_dir()
        assert path.is_relative_to(workspace.root)


def test_cache_is_separable_from_evidence(tmp_path: Path):
    """Deleting the cache must never be able to destroy research."""
    workspace = Workspace(repo=tmp_path, root=tmp_path / "app", vault_mode=False).ensure()
    assert not workspace.artifacts.is_relative_to(workspace.cache)
    assert not workspace.strategies.is_relative_to(workspace.cache)
    assert not workspace.database.is_relative_to(workspace.cache)


def test_an_obsidian_export_is_a_destination_not_a_root(tmp_path: Path):
    vault = tmp_path / "MyVault"
    workspace = Workspace(
        repo=tmp_path, root=tmp_path / "app", vault_mode=False, obsidian=vault
    ).ensure()
    assert workspace.obsidian_export == vault
    # The application's own state stays outside it regardless.
    assert not workspace.store.is_relative_to(vault)
    assert not workspace.data.is_relative_to(vault)


def test_without_a_vault_the_export_still_has_somewhere_to_go(tmp_path: Path):
    workspace = Workspace(repo=tmp_path, root=tmp_path / "app", vault_mode=False).ensure()
    assert workspace.obsidian_export == workspace.exports / "obsidian"


def test_the_pointer_records_the_layout_it_means(tmp_path: Path, monkeypatch):
    """Sniffing the directory is ambiguous once two layouts exist."""
    monkeypatch.delenv("ALGOFORGE_VAULT", raising=False)
    monkeypatch.delenv("ALGOFORGE_HOME", raising=False)
    repo, target = tmp_path / "repo", tmp_path / "data"
    repo.mkdir()
    target.mkdir()

    write_pointer(repo, target, layout=LAYOUT_APP)
    assert read_pointer_layout(repo) == LAYOUT_APP
    assert resolve(repo).vault_mode is False

    write_pointer(repo, target, layout=LAYOUT_VAULT)
    assert resolve(repo).vault_mode is True


def test_the_legacy_vault_layout_still_resolves(tmp_path: Path, monkeypatch):
    """An existing installation's data is in it; breaking that would be worse
    than the architecture being replaced."""
    monkeypatch.delenv("ALGOFORGE_VAULT", raising=False)
    monkeypatch.delenv("ALGOFORGE_HOME", raising=False)
    repo, vault = tmp_path / "repo", tmp_path / "vault"
    repo.mkdir()
    (vault / ".obsidian").mkdir(parents=True)

    write_pointer(repo, vault, layout=LAYOUT_VAULT)
    workspace = resolve(repo)
    assert workspace.vault_mode is True
    assert workspace.store == vault / NOTES_FOLDER / STORE_FOLDER


def test_migration_carries_every_store_tree_not_a_hard_coded_four(tmp_path: Path):
    """Regression: `runs/` and `families/` were silently dropped.

    The pair list was written when the store held four trees. It grew two more
    and nobody updated it, so a real migration lost all 43 run snapshots — the
    reproducibility records, among the least replaceable things here. The
    verification step caught it; the copy should not have needed catching.
    """
    source = Workspace(repo=tmp_path, root=tmp_path / "from", vault_mode=False).ensure()
    for tree in ("strategies", "data", "templates", "families", "runs", "a_tree_added_later"):
        (source.store / tree).mkdir(parents=True, exist_ok=True)
        (source.store / tree / "thing.json").write_text("{}", encoding="utf-8")

    target = Workspace(repo=tmp_path, root=tmp_path / "to", vault_mode=False).ensure()
    migrate(source, target)

    for tree in ("strategies", "data", "templates", "families", "runs", "a_tree_added_later"):
        assert (target.store / tree / "thing.json").exists(), f"{tree} was not carried"


def test_migration_leaves_derived_trees_behind(tmp_path: Path):
    """Cache and logs rebuild themselves; copying them is pure cost."""
    source = Workspace(repo=tmp_path, root=tmp_path / "from", vault_mode=False).ensure()
    for tree in ("cache", "logs"):
        (source.store / tree).mkdir(parents=True, exist_ok=True)
        (source.store / tree / "junk.bin").write_bytes(b"x" * 32)

    target = Workspace(repo=tmp_path, root=tmp_path / "to", vault_mode=False).ensure()
    migrate(source, target)

    assert not (target.store / "cache" / "junk.bin").exists()
    assert not (target.store / "logs" / "junk.bin").exists()


def test_run_snapshots_survive_a_layout_migration(tmp_path: Path, monkeypatch):
    """End to end, through the verified path that refused before."""
    monkeypatch.delenv("ALGOFORGE_VAULT", raising=False)
    monkeypatch.delenv("ALGOFORGE_HOME", raising=False)
    repo, vault, target = tmp_path / "repo", tmp_path / "vault", tmp_path / "appdata"
    repo.mkdir()
    (vault / ".obsidian").mkdir(parents=True)
    write_pointer(repo, vault, layout=LAYOUT_VAULT)

    source = resolve(repo)
    snapshot = source.store / "runs" / "run_one"
    snapshot.mkdir(parents=True)
    (snapshot / "manifest.json").write_text("{}", encoding="utf-8")

    report = migrate_layout(repo, source, target)
    assert report["migrated"] is True, report.get("missing")
    assert report["before"]["runs"] == report["after"]["runs"] == 1
    assert (target / "runs" / "run_one" / "manifest.json").exists()


def test_migration_copies_verifies_and_only_then_switches(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ALGOFORGE_VAULT", raising=False)
    monkeypatch.delenv("ALGOFORGE_HOME", raising=False)
    repo, vault, target = tmp_path / "repo", tmp_path / "vault", tmp_path / "appdata"
    repo.mkdir()
    (vault / ".obsidian").mkdir(parents=True)
    write_pointer(repo, vault, layout=LAYOUT_VAULT)

    source = resolve(repo)
    (source.strategies / "one").mkdir(parents=True)
    (source.strategies / "one" / "spec.json").write_text("{}", encoding="utf-8")
    (source.data / "backtests").mkdir(parents=True, exist_ok=True)
    (source.data / "backtests" / "b1.json").write_text("{}", encoding="utf-8")

    report = migrate_layout(repo, source, target)
    assert report["migrated"] is True
    assert report["before"]["strategies"] == 1
    assert report["after"]["strategies"] == 1
    assert report["source_retained"] is True

    # The pointer moved, and the app now reads the application layout.
    assert read_pointer_layout(repo) == LAYOUT_APP
    assert resolve(repo).root == target
    assert (target / "strategies" / "one" / "spec.json").exists()
    # Copy, not move: the original is still a complete backup.
    assert (source.strategies / "one" / "spec.json").exists()


def test_a_failed_migration_leaves_the_pointer_alone(tmp_path: Path, monkeypatch):
    """Repointing the application at a half-copied workspace would be worse
    than never having run the migration."""
    monkeypatch.delenv("ALGOFORGE_VAULT", raising=False)
    monkeypatch.delenv("ALGOFORGE_HOME", raising=False)
    repo, vault, target = tmp_path / "repo", tmp_path / "vault", tmp_path / "appdata"
    repo.mkdir()
    (vault / ".obsidian").mkdir(parents=True)
    write_pointer(repo, vault, layout=LAYOUT_VAULT)
    source = resolve(repo)
    (source.strategies / "one").mkdir(parents=True)
    (source.strategies / "one" / "spec.json").write_text("{}", encoding="utf-8")

    def refuse(*args, **kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr("forge.vault.location.shutil.copytree", refuse)
    report = migrate_layout(repo, source, target)

    assert report["migrated"] is False
    assert report["missing"]
    assert read_pointer_layout(repo) == LAYOUT_VAULT
    assert resolve(repo).root == vault


def test_migrating_onto_itself_is_refused(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ALGOFORGE_VAULT", raising=False)
    monkeypatch.delenv("ALGOFORGE_HOME", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    source = Workspace(repo=repo, root=tmp_path / "app", vault_mode=False).ensure()
    report = migrate_layout(repo, source, tmp_path / "app")
    assert report["migrated"] is False
    assert "same store" in str(report["reason"])


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
