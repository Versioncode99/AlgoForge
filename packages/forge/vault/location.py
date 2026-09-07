"""Where AlgoForge keeps everything it writes.

Every artifact the application produces — strategy code, backtest JSON, the
research library, the agent ledger, the engine's experiment memory — used to be
addressed as ``ROOT / "data" / ...`` against the repository checkout. That made
the repository and the workspace the same directory, so there was no way to put
the output somewhere the operator actually looks.

A :class:`Workspace` separates the two. The repository stays where the code is;
the workspace is wherever the operator points it, and by default that is an
Obsidian vault, so a strategy the engine wrote at 02:00 shows up as a note the
next time the vault is opened.

Resolution order, most specific first:

1. ``ALGOFORGE_VAULT`` in the environment — for tests and one-off runs.
2. ``config/storage.json`` in the repository — what the Settings tab writes.
3. The repository root itself — the pre-vault layout, so an existing checkout
   keeps working with no configuration at all.

The pointer file lives in the repository on purpose. It is the one thing that
cannot live in the workspace, because it is what says where the workspace is.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

# The visible folder inside a vault. Numbered to sort with the operator's own
# top-level folders rather than above or below all of them.
NOTES_FOLDER = "10 AlgoForge"

# Machine storage sits under a dot-directory: Obsidian hides those from the file
# explorer and the graph, so a 40 MB SQLite ledger never appears as a note.
STORE_FOLDER = ".store"

POINTER = Path("config") / "storage.json"


@dataclass(frozen=True)
class Workspace:
    """Resolved storage locations. Nothing else in the app builds these paths."""

    repo: Path
    root: Path
    vault_mode: bool

    @property
    def notes(self) -> Path:
        """Human-readable Markdown, or the repo's own ``artifacts`` in flat mode."""
        return self.root / NOTES_FOLDER if self.vault_mode else self.root / "artifacts" / "notes"

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
    """A directory is treated as a vault when it is one, or is empty enough to become one."""
    if (path / ".obsidian").is_dir():
        return True
    if (path / NOTES_FOLDER).is_dir():
        return True
    # A repository checkout is not a vault, whatever else is in it.
    return not (path / "pyproject.toml").exists()


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


def write_pointer(repo: Path, location: Path) -> None:
    path = pointer_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"location": str(location), "layout": "vault-v1"}, indent=2),
        encoding="utf-8",
    )


def resolve(repo: Path) -> Workspace:
    """The workspace this process should use. Cheap; safe to call repeatedly."""
    raw = os.getenv("ALGOFORGE_VAULT") or read_pointer(repo)
    if not raw:
        return Workspace(repo=repo, root=repo, vault_mode=False).ensure()
    root = Path(raw).expanduser()
    if not root.is_absolute():
        root = (repo / root).resolve()
    return Workspace(repo=repo, root=root, vault_mode=_looks_like_vault(root)).ensure()


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


def migrate(source: Workspace, target: Workspace, *, copy: bool = True) -> dict[str, object]:
    """Move existing output into a new workspace.

    Copy, not move, by default: relocating storage is not the moment to find out
    that the old copy was the only copy. The caller decides whether to clean up.
    """
    target.ensure()
    moved: list[str] = []
    skipped: list[str] = []
    pairs = [
        (source.strategies, target.strategies),
        (source.data, target.data),
        (source.templates, target.templates),
        (source.notes, target.notes),
    ]
    for src, dst in pairs:
        if not src.is_dir() or src.resolve() == dst.resolve():
            skipped.append(str(src))
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
