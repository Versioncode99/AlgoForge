"""Where AlgoForge keeps everything it writes.

**AlgoForge owns its data. Obsidian is optional.**

An earlier design made an Obsidian vault the storage root, so machine state —
SQLite databases, generated strategy code, gigabytes of backtest JSON — lived
inside a note-taking application's folder, hidden under a dot-directory so it
would not appear as four hundred thousand notes. That is a useful trick for
personal research and the wrong architecture for a product: it conflates
human-readable documents with application persistence, and a customer who
installs AlgoForge should never have to know what Obsidian is.

So there are two independent locations now:

* the **data root**, owned by the application, at an OS-appropriate place;
* an optional **Obsidian export**, which is a destination the app writes *to*
  and never reads its own state from.

Two layouts exist, and a workspace knows which one it is:

``app-v2``
    The application-owned layout. ``strategies/``, ``data/``, ``templates/``,
    ``research/``, ``exports/``, ``logs/``, ``cache/`` directly under the root.

``vault-v1``
    The legacy Obsidian layout, where the store hid under
    ``<vault>/10 AlgoForge/.store``. Still fully supported, because an existing
    installation's data is in it, and a migration that broke on first launch
    would be worse than the architecture it replaced. Nothing new resolves to
    it; :func:`migrate_layout` moves an installation off it, by copying.

Resolution order, most specific first:

1. ``ALGOFORGE_HOME`` (or the older ``ALGOFORGE_VAULT``) in the environment —
   for tests, one-off runs, and portable installs.
2. ``config/storage.json`` in the repository, which records both the location
   and the layout — what the Settings tab writes.
3. The repository itself, **only if it already holds AlgoForge output**, so an
   existing checkout is adopted rather than orphaned.
4. Otherwise :func:`default_root` — the OS application-data directory. This is
   what a fresh install gets, and it requires no configuration and no Obsidian.

The pointer file lives in the repository on purpose. It is the one thing that
cannot live in the workspace, because it is what says where the workspace is.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# The visible folder inside a vault. Numbered to sort with the operator's own
# top-level folders rather than above or below all of them.
NOTES_FOLDER = "10 AlgoForge"

# Machine storage sits under a dot-directory: Obsidian hides those from the file
# explorer and the graph, so a 40 MB SQLite ledger never appears as a note.
STORE_FOLDER = ".store"

POINTER = Path("config") / "storage.json"

# The application-owned layout, and the legacy Obsidian one.
LAYOUT_APP = "app-v2"
LAYOUT_VAULT = "vault-v1"

# Directory name under the OS application-data location.
APP_DIRNAME = "AlgoForge"


def default_root() -> Path:
    """The OS's application-data directory for AlgoForge.

    Windows first, because that is where the product runs, but deliberately not
    Windows-only: hard-coding ``%LOCALAPPDATA%`` would make a later macOS or
    Linux build a rewrite rather than a port.

    Mutable user state never goes beside the executable. An installation
    directory can be read-only, replaced wholesale by an updater, or sit under
    ``Program Files`` where a normal user cannot write.
    """
    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if base:
            return Path(base) / APP_DIRNAME
        return Path.home() / "AppData" / "Local" / APP_DIRNAME
    # XDG on Linux; macOS conventionally uses Application Support, and
    # XDG_DATA_HOME is respected there by anything that sets it.
    xdg = os.getenv("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_DIRNAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIRNAME
    return Path.home() / ".local" / "share" / APP_DIRNAME


@dataclass(frozen=True)
class Workspace:
    """Resolved storage locations. Nothing else in the app builds these paths.

    ``obsidian`` is a place notes are *exported to*. It is never where the
    application keeps its own state, and it may be ``None`` — which is the
    normal case and must stay a fully working one.
    """

    repo: Path
    root: Path
    vault_mode: bool
    obsidian: Path | None = None

    @property
    def layout(self) -> str:
        return LAYOUT_VAULT if self.vault_mode else LAYOUT_APP

    @property
    def notes(self) -> Path:
        """Human-readable Markdown.

        In the legacy layout this is inside the vault. In the application
        layout it is the app's own ``research/notes``, and the vault — if one is
        configured at all — receives a *copy* through the export tree.
        """
        if self.vault_mode:
            return self.root / NOTES_FOLDER
        return self.root / "research" / "notes"

    # ── application-owned trees ──────────────────────────────────────────────
    @property
    def database(self) -> Path:
        """Structured state. The system of record for anything queryable."""
        return self.store / "data"

    @property
    def artifacts(self) -> Path:
        """Immutable evidence. Large, content-addressed, never in the database."""
        return self.store / "data"

    @property
    def exports(self) -> Path:
        """Interoperability copies: Markdown, Obsidian, generated code.

        Everything here is derived and may be deleted without losing research.
        """
        return self.store / "exports"

    @property
    def obsidian_export(self) -> Path:
        """Where an Obsidian export lands when no external vault is configured."""
        return self.obsidian if self.obsidian is not None else self.exports / "obsidian"

    @property
    def logs(self) -> Path:
        return self.store / "logs"

    @property
    def cache(self) -> Path:
        """Derived data that can be rebuilt. Safe to delete, by definition.

        If deleting a directory would destroy irreplaceable research state, it
        does not belong here — that is the whole distinction this property
        exists to make.
        """
        return self.store / "cache"

    @property
    def store(self) -> Path:
        """Machine storage: databases, generated code, cached bars."""
        return self.notes / STORE_FOLDER if self.vault_mode else self.root

    @property
    def strategies(self) -> Path:
        return self.store / "strategies"

    @property
    def data(self) -> Path:
        return self.store / "data"

    @property
    def templates(self) -> Path:
        """Operator- and agent-authored templates, as JSON plus guarded Python."""
        return self.store / "templates"

    # Named note folders. Kept as properties rather than string literals at each
    # call site so a rename is one edit.
    @property
    def strategy_notes(self) -> Path:
        return self.notes / "Strategies"

    @property
    def paper_notes(self) -> Path:
        return self.notes / "Research Papers"

    @property
    def backtest_notes(self) -> Path:
        return self.notes / "Backtests"

    @property
    def verdict_notes(self) -> Path:
        return self.notes / "Verdicts"

    @property
    def family_notes(self) -> Path:
        return self.notes / "Families"

    @property
    def mission_notes(self) -> Path:
        return self.notes / "Missions"

    def ensure(self) -> Workspace:
        for path in (
            self.strategies,
            self.data,
            self.templates,
            self.strategy_notes,
            self.paper_notes,
            self.backtest_notes,
            self.verdict_notes,
            self.family_notes,
            self.mission_notes,
        ):
            path.mkdir(parents=True, exist_ok=True)
        if not self.vault_mode:
            # Only the application layout owns these. Creating them inside a
            # vault would put more application furniture in someone's notes.
            for path in (self.exports, self.logs, self.cache):
                path.mkdir(parents=True, exist_ok=True)
        return self

    def counts(self) -> dict[str, int]:
        """How much of each thing is on disk. Cheap directory listings."""
        return {
            "strategies": _count(self.strategies, "*/spec.json"),
            "strategy_notes": _count(self.strategy_notes, "*.md"),
            "paper_notes": _count(self.paper_notes, "*.md"),
            "backtest_notes": _count(self.backtest_notes, "*.md"),
            "verdict_notes": _count(self.verdict_notes, "*.md"),
            "family_notes": _count(self.family_notes, "*.md"),
            "mission_notes": _count(self.mission_notes, "*.md"),
            "custom_templates": _count(self.templates, "*.json"),
        }

    def describe(self) -> dict[str, object]:
        """What the Settings tab renders."""
        return {
            "root": str(self.root),
            "vault_mode": self.vault_mode,
            "is_obsidian_vault": (self.root / ".obsidian").is_dir(),
            "notes": str(self.notes),
            "store": str(self.store),
            "exists": self.root.is_dir(),
            "writable": _writable(self.root),
            "counts": self.counts(),
            # Notes only, and the store is pruned rather than skipped file by
            # file. It sits underneath the notes, holds hundreds of thousands of
            # backtest artifacts, and is orders of magnitude larger — measuring
            # it would both mislabel the figure and make this endpoint slow.
            "note_bytes": _tree_bytes(self.notes, prune=self.store),
        }


def _count(path: Path, pattern: str) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for _ in path.glob(pattern))


def _tree_bytes(path: Path, *, prune: Path | None = None) -> int:
    """Total size below ``path``, not descending into ``prune``."""
    if not path.is_dir():
        return 0
    skip = prune.name if prune is not None and prune.parent == path else None
    total = 0
    for root, dirs, files in os.walk(path):
        if skip is not None and root == str(path):
            dirs[:] = [name for name in dirs if name != skip]
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def _writable(path: Path) -> bool:
    """Probe rather than guess. ``os.access`` lies on Windows network shares."""
    if not path.is_dir():
        return False
    probe = path / ".algoforge-write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        return False
    return True


def _looks_like_vault(path: Path) -> bool:
    """Is this directory *actually* an Obsidian vault, or already laid out as one?

    This used to return ``True`` for anything that was not a repository
    checkout, on the reasoning that an empty directory was "empty enough to
    become" a vault. That made the vault layout the default for every new
    location, which is the assumption being removed: a directory is not a vault
    because it is empty, it is a vault because Obsidian made it one.

    Only two things count now — Obsidian's own marker, and an existing
    AlgoForge vault layout. Everything else is an application data root.
    """
    return (path / ".obsidian").is_dir() or (path / NOTES_FOLDER).is_dir()


def pointer_path(repo: Path) -> Path:
    return repo / POINTER


def read_pointer(repo: Path) -> str | None:
    path = pointer_path(repo)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    location = raw.get("location")
    return str(location) if location else None


def read_pointer_layout(repo: Path) -> str | None:
    """The layout the pointer records, if it records one.

    Sniffing the directory is not good enough once two layouts exist: an
    application root that happens to sit inside a vault, or a vault that has
    been migrated and still has an ``.obsidian`` directory, would both be read
    wrongly. The pointer says which layout it means.
    """
    path = pointer_path(repo)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    layout = raw.get("layout")
    return str(layout) if layout in {LAYOUT_APP, LAYOUT_VAULT} else None


def write_pointer(repo: Path, location: Path, layout: str = LAYOUT_VAULT) -> None:
    """Record where the workspace is and which layout it uses.

    The layout defaults to ``vault-v1`` so that an existing caller that only
    passes a location keeps describing what it has always meant. Migration
    passes ``app-v2`` explicitly.
    """
    path = pointer_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"location": str(location), "layout": layout}, indent=2),
        encoding="utf-8",
    )


def _repo_holds_output(repo: Path) -> bool:
    """Does this checkout already contain AlgoForge's own output?

    Only then is the repository adopted as the workspace. A fresh clone must not
    become a data directory just by being run once — that is how mutable state
    ends up beside the code, which is exactly what the application-data location
    exists to prevent.
    """
    strategies = repo / "strategies"
    data = repo / "data"
    if any(strategies.glob("*/spec.json")):
        return True
    return any((data / name).is_dir() for name in ("backtests", "validation", "runtime"))


def resolve(repo: Path) -> Workspace:
    """The workspace this process should use. Cheap; safe to call repeatedly.

    See the module docstring for the order. The important change from the
    Obsidian-rooted design is the last step: a fresh installation now lands in
    the OS application-data directory and needs no configuration and no note
    application, rather than defaulting into the repository or a vault.
    """
    raw = os.getenv("ALGOFORGE_HOME") or os.getenv("ALGOFORGE_VAULT") or read_pointer(repo)
    if raw:
        root = Path(raw).expanduser()
        if not root.is_absolute():
            root = (repo / root).resolve()
        # An explicit layout wins. Without one — an environment override, or a
        # pointer written before layouts existed — fall back to sniffing, which
        # is what preserves an existing vault installation.
        layout = read_pointer_layout(repo) if raw == read_pointer(repo) else None
        vault_mode = _looks_like_vault(root) if layout is None else layout == LAYOUT_VAULT
        return Workspace(repo=repo, root=root, vault_mode=vault_mode).ensure()

    if _repo_holds_output(repo):
        # An existing checkout that already has research in it. Adopt it rather
        # than silently starting a second, empty workspace elsewhere.
        return Workspace(repo=repo, root=repo, vault_mode=False).ensure()

    return Workspace(repo=repo, root=default_root(), vault_mode=False).ensure()


def inspect(repo: Path, candidate: str) -> dict[str, object]:
    """Report on a location the operator is considering, without adopting it.

    Answering "is this a vault, does it already hold AlgoForge output, can I
    write to it" before the switch is what makes the switch safe to offer.
    """
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = (repo / path).resolve()
    exists = path.is_dir()
    problems: list[str] = []
    if not exists:
        if not path.parent.is_dir():
            problems.append(f"The parent folder {path.parent} does not exist.")
    elif not _writable(path):
        problems.append("This folder cannot be written to by the application.")
    if path == repo:
        problems.append("This is the repository itself, which is the pre-vault default.")
    probe = Workspace(repo=repo, root=path, vault_mode=_looks_like_vault(path))
    return {
        "path": str(path),
        "exists": exists,
        "creatable": not exists and path.parent.is_dir(),
        "writable": exists and _writable(path),
        "is_obsidian_vault": (path / ".obsidian").is_dir(),
        "vault_mode": probe.vault_mode,
        "already_initialised": probe.notes.is_dir(),
        "existing_strategies": _count(probe.strategies, "*/spec.json"),
        "problems": problems,
        "usable": not problems,
    }


# Store directories that are derived and not worth copying. Everything else in
# the store is migrated, including trees added after this function was written.
DERIVED_TREES = frozenset({"cache", "logs"})


def _migration_pairs(source: Workspace, target: Workspace) -> list[tuple[Path, Path]]:
    """Every tree a migration must carry, discovered rather than listed.

    This used to be a hard-coded list of four: strategies, data, templates and
    notes. The store grew ``families/`` and ``runs/`` afterwards and nobody
    updated the list, so a migration silently dropped all 43 run snapshots —
    the reproducibility records, which are among the least replaceable things
    the application holds.

    Enumerating the store instead means a tree added next year is carried
    without anyone remembering to add it here. Being exhaustive by default and
    naming the exceptions is the safer direction for a function whose failure
    mode is losing data quietly.
    """
    pairs: list[tuple[Path, Path]] = []
    if source.store.is_dir():
        for child in sorted(source.store.iterdir()):
            if not child.is_dir() or child.name in DERIVED_TREES:
                continue
            pairs.append((child, target.store / child.name))
    # Notes are a sibling of the store in the application layout and a parent of
    # it in the vault layout, so they are always added explicitly.
    pairs.append((source.notes, target.notes))
    return pairs


def migrate(source: Workspace, target: Workspace, *, copy: bool = True) -> dict[str, object]:
    """Move existing output into a new workspace.

    Copy, not move, by default: relocating storage is not the moment to find out
    that the old copy was the only copy. The caller decides whether to clean up.
    """
    target.ensure()
    moved: list[str] = []
    skipped: list[str] = []
    pairs = _migration_pairs(source, target)
    for src, dst in pairs:
        if not src.is_dir() or src.resolve() == dst.resolve():
            skipped.append(str(src))
            continue
        # The destination has to exist before anything is copied into it.
        # `ensure()` only creates the trees it knows about, so a store
        # directory added later — `runs`, `families` — had no target directory
        # and every `copy2` into it failed with a caught OSError. That is how
        # 43 run snapshots went missing quietly.
        try:
            dst.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            skipped.append(f"{dst} ({exc.strerror or type(exc).__name__})")
            continue
        for item in src.iterdir():
            # The store lives under notes; copying notes must not recurse into it.
            if item.name == STORE_FOLDER:
                continue
            # Purchased vendor archives stay with the checkout. Copying 100 MB+ of
            # Databento bars into a note vault buys nothing, and moving them is how
            # a relocation turns into a paid re-download.
            if item.name == "market" and src.name == "data":
                skipped.append(str(item))
                continue
            destination = dst / item.name
            if destination.exists():
                skipped.append(str(item))
                continue
            try:
                if item.is_dir():
                    shutil.copytree(item, destination)
                else:
                    shutil.copy2(item, destination)
                moved.append(str(item))
            except OSError as exc:
                skipped.append(f"{item} ({exc.strerror or type(exc).__name__})")
    return {
        "copied": len(moved),
        "skipped": len(skipped),
        "items": moved[:200],
        "left_behind": skipped[:200],
        "source": str(source.root),
        "target": str(target.root),
        "mode": "copy" if copy else "move",
    }


def inventory(workspace: Workspace) -> dict[str, int]:
    """What a workspace holds, in the units a migration must preserve.

    Counted rather than sampled, because the point of taking it before and
    after is that the two numbers have to be identical.
    """
    return {
        "strategies": _count(workspace.strategies, "*/spec.json"),
        "strategy_sources": _count(workspace.strategies, "*/strategy.py"),
        "backtests": _count(workspace.data / "backtests", "*.json"),
        "validation": _count(workspace.data / "validation", "*.json"),
        "conformance": _count(workspace.data / "conformance", "*.json"),
        "preregistrations": _count(workspace.data / "preregistrations", "*.json"),
        "runs": _count(workspace.store / "runs", "*/manifest.json"),
        "templates": _count(workspace.templates, "*.json"),
        "databases": _count(workspace.data, "*.db"),
    }


def migrate_layout(repo: Path, source: Workspace, target_root: Path) -> dict[str, object]:
    """Move an installation off the Obsidian layout onto the application one.

    Copy, verify, switch, retain — in that order, and the pointer is only
    rewritten if verification passed. A migration that half-succeeded and then
    repointed the application at the half would be worse than never having run.

    The original is left exactly where it was. The operator decides when to
    delete it, and until they do, they have a complete backup that predates the
    migration.
    """
    target = Workspace(repo=repo, root=target_root, vault_mode=False).ensure()
    if target.store.resolve() == source.store.resolve():
        return {
            "migrated": False,
            "reason": "source and target resolve to the same store",
            "source": str(source.root),
            "target": str(target.root),
        }

    before = inventory(source)
    report = migrate(source, target, copy=True)
    after = inventory(target)

    # Every count must have arrived. More in the target is fine — it may have
    # held something already — but fewer means something did not copy.
    missing = {
        name: {"source": count, "target": after.get(name, 0)}
        for name, count in before.items()
        if after.get(name, 0) < count
    }
    verified = not missing
    if verified:
        write_pointer(repo, target_root, layout=LAYOUT_APP)

    return {
        "migrated": verified,
        "verified": verified,
        "missing": missing,
        "before": before,
        "after": after,
        "copy_report": report,
        "source": str(source.root),
        "target": str(target.root),
        "source_retained": True,
        "note": (
            "The original workspace is untouched and still usable. Delete it only "
            "once you are satisfied with the migrated installation."
            if verified
            else "Verification failed; the pointer was NOT changed and the "
            "application still reads the original workspace."
        ),
    }
