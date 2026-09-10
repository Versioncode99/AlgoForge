"""Which mode is open, on which stance, and which layout each mode remembers.

The per-mode workspace pointer is the point of this table. Without it, switching
from Hedge Fund to Normal and back reopens whichever layout happened to be
active globally, which reads to the operator as their fund workspace having been
replaced by a chart. Each mode keeps its own pointer, so leaving a mode and
returning to it is a *return*, not a reset — and switching cannot corrupt the
layout of the mode being left, because switching does not write to it.

Nothing here is research. Deleting this database costs the operator their choice
of opening screen and their four layout pointers, and nothing else: the layouts
themselves live in `forge.workstation.store`, and the evidence lives further
away still.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forge.modes.models import MODES, Stance, WorkspaceMode, parse_stance

#: Bumped when the stored shape changes in a way a reader must know about.
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ModeSession:
    """What the operator has open.

    `mode` is `None` before a choice has been made, and that is a real state
    rather than a missing one: the home screen is where the product opens, and
    defaulting to Normal would mean nobody ever sees the four options.
    """

    mode: WorkspaceMode | None
    stance: Stance | None
    workspace_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value if self.mode else None,
            "stance": self.stance.value if self.stance else None,
            "workspace_id": self.workspace_id,
        }


class ModeStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS mode_state ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS mode_workspace ("
                "mode TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, "
                "schema_version INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS mode_stance ("
                "mode TEXT PRIMARY KEY, stance TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    # ── the open mode ────────────────────────────────────────────────────────
    def active_mode(self) -> WorkspaceMode | None:
        value = self._state("mode")
        if value is None:
            return None
        try:
            return WorkspaceMode(value)
        except ValueError:
            # A row written by a build that knew a mode this one does not. Report
            # nothing open rather than raising: the operator lands on the home
            # screen and picks again, which is recoverable, and a 500 is not.
            return None

    def enter(self, mode: WorkspaceMode, stance: Stance | None = None) -> ModeSession:
        """Open a mode, validating the stance against it.

        Called on every entry, not only the first, so a stance change is the
        same operation as a mode change and there is one code path holding the
        validation.
        """
        resolved = parse_stance(mode, stance.value if stance else None)
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO mode_state VALUES ('mode', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (mode.value,),
            )
            if resolved is not None:
                db.execute(
                    "INSERT INTO mode_stance VALUES (?, ?) "
                    "ON CONFLICT(mode) DO UPDATE SET stance=excluded.stance",
                    (mode.value, resolved.value),
                )
        return self.session()

    def leave(self) -> ModeSession:
        """Return to the home screen without forgetting anything.

        The per-mode pointers and stances are left exactly where they are: this
        clears which mode is open, not what the modes contain.
        """
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM mode_state WHERE key='mode'")
        return self.session()

    # ── stance ───────────────────────────────────────────────────────────────
    def stance_for(self, mode: WorkspaceMode) -> Stance | None:
        available = MODES[mode].stances
        if not available:
            return None
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT stance FROM mode_stance WHERE mode=?", (mode.value,)
            ).fetchone()
        if row is None:
            return available[0]
        try:
            stored = Stance(str(row["stance"]))
        except ValueError:
            return available[0]
        # A stance the mode no longer offers falls back to the default rather
        # than being honoured. The failure mode this prevents is the important
        # one: an unrecognised stance must never read as the permissive one.
        return stored if stored in available else available[0]

    def set_stance(self, mode: WorkspaceMode, stance: Stance) -> Stance:
        resolved = parse_stance(mode, stance.value)
        if resolved is None:
            raise ValueError(f"{mode.value} mode has no operating stances")
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO mode_stance VALUES (?, ?) "
                "ON CONFLICT(mode) DO UPDATE SET stance=excluded.stance",
                (mode.value, resolved.value),
            )
        return resolved

    # ── each mode's layout ───────────────────────────────────────────────────
    def workspace_for(self, mode: WorkspaceMode) -> str | None:
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT workspace_id FROM mode_workspace WHERE mode=?", (mode.value,)
            ).fetchone()
        return None if row is None else str(row["workspace_id"])

    def remember_workspace(self, mode: WorkspaceMode, workspace_id: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO mode_workspace VALUES (?, ?, ?) "
                "ON CONFLICT(mode) DO UPDATE SET workspace_id=excluded.workspace_id",
                (mode.value, workspace_id, SCHEMA_VERSION),
            )

    def forget_workspace(self, workspace_id: str) -> None:
        """Drop pointers to a layout that has been deleted.

        Every mode holding it, not just the active one — a deleted workspace
        that three modes still point at is three modes that open to nothing.
        """
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM mode_workspace WHERE workspace_id=?", (workspace_id,))

    # ── reads ────────────────────────────────────────────────────────────────
    def session(self) -> ModeSession:
        mode = self.active_mode()
        if mode is None:
            return ModeSession(mode=None, stance=None, workspace_id=None)
        return ModeSession(
            mode=mode,
            stance=self.stance_for(mode),
            workspace_id=self.workspace_for(mode),
        )

    def _state(self, key: str) -> str | None:
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT value FROM mode_state WHERE key=?", (key,)).fetchone()
        return None if row is None else str(row["value"])
