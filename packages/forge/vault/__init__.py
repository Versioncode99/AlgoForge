"""Where AlgoForge stores what it makes, and the Markdown mirror of it."""

from forge.vault.location import (
    NOTES_FOLDER,
    STORE_FOLDER,
    Workspace,
    inspect,
    migrate,
    read_pointer,
    resolve,
    write_pointer,
)
from forge.vault.mirror import VaultMirror, safe_stem

__all__ = [
    "NOTES_FOLDER",
    "STORE_FOLDER",
    "VaultMirror",
    "Workspace",
    "inspect",
    "migrate",
    "read_pointer",
    "resolve",
    "safe_stem",
    "write_pointer",
]
