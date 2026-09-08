"""Where workspaces live: SQLite, in the application's own data root.

Layouts are structured application state, so they belong in the database rather
than in files — the distinction the storage addendum draws. They are emphatically
*not* research: this database can be deleted and the operator loses their screen
arrangement, not a single artifact, verdict or holdout. That is the test for
whether something belongs here.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge.workstation.models import Panel, Workspace, WorkspaceProfile, new_workspace_id

#: Bumped when the stored shape changes in a way a reader must know about.
#: Rows carry it so an older row can be recognised rather than misread.
SCHEMA_VERSION = 1


class WorkspaceStore:
    """Durable workspaces, plus which one the operator had open."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS workspaces ("
                "workspace_id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                "template_key TEXT, profile TEXT, panels TEXT NOT NULL, "
                "schema_version INTEGER NOT NULL, "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS workspace_state ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    # ── reads ────────────────────────────────────────────────────────────────
    def get(self, workspace_id: str) -> Workspace | None:
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT * FROM workspaces WHERE workspace_id=?", (workspace_id,)
            ).fetchone()
        return None if row is None else _to_workspace(dict(row))

    def all_workspaces(self) -> list[Workspace]:
        with closing(self._connect()) as db, db:
            rows = db.execute("SELECT * FROM workspaces ORDER BY updated_at DESC").fetchall()
        return [_to_workspace(dict(row)) for row in rows]

    def summaries(self) -> list[dict[str, Any]]:
        """Enough to render a switcher without loading every panel.

        The same principle the artifact index follows: a list view reads a
        projection, not the whole record.
        """
        with closing(self._connect()) as db, db:
            rows = db.execute(
                "SELECT workspace_id, name, template_key, updated_at, "
                "json_array_length(panels) AS panel_count "
                "FROM workspaces ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def count(self) -> int:
        with closing(self._connect()) as db, db:
            return int(db.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0])

    # ── writes ───────────────────────────────────────────────────────────────
    def save(self, workspace: Workspace) -> Workspace:
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO workspaces VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(workspace_id) DO UPDATE SET "
                "name=excluded.name, template_key=excluded.template_key, "
                "profile=excluded.profile, panels=excluded.panels, "
                "schema_version=excluded.schema_version, updated_at=excluded.updated_at",
                (
                    workspace.workspace_id,
                    workspace.name,
                    workspace.template_key,
                    json.dumps(workspace.profile.model_dump(mode="json"))
                    if workspace.profile
                    else None,
                    json.dumps([p.model_dump(mode="json") for p in workspace.panels]),
                    SCHEMA_VERSION,
                    workspace.created_at.isoformat(),
                    workspace.updated_at.isoformat(),
                ),
            )
        return workspace

    def create(
        self,
        name: str,
        *,
        panels: tuple[Panel, ...] = (),
        profile: WorkspaceProfile | None = None,
        template_key: str | None = None,
    ) -> Workspace:
        now = datetime.now(UTC)
        workspace = Workspace(
            workspace_id=new_workspace_id(name, now),
            name=name,
            panels=panels,
            profile=profile,
            template_key=template_key,
            created_at=now,
            updated_at=now,
        )
        return self.save(workspace)

    def delete(self, workspace_id: str) -> bool:
        """Remove a layout. Returns whether there was one to remove.

        Deleting the active workspace clears the pointer rather than leaving it
        dangling at something that is gone.
        """
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            removed = db.execute(
                "DELETE FROM workspaces WHERE workspace_id=?", (workspace_id,)
            ).rowcount
            if removed:
                db.execute(
                    "DELETE FROM workspace_state WHERE key='active' AND value=?",
                    (workspace_id,),
                )
        return bool(removed)

    def clone(self, workspace_id: str, name: str) -> Workspace:
        source = self.get(workspace_id)
        if source is None:
            raise KeyError(workspace_id)
        return self.create(
            name,
            panels=source.panels,
            profile=source.profile,
            template_key=source.template_key,
        )

    # ── which one is open ────────────────────────────────────────────────────
    def active_id(self) -> str | None:
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT value FROM workspace_state WHERE key='active'").fetchone()
        return None if row is None else str(row["value"])

    def set_active(self, workspace_id: str) -> None:
        if self.get(workspace_id) is None:
            raise KeyError(workspace_id)
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO workspace_state VALUES ('active', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (workspace_id,),
            )

    def active(self) -> Workspace | None:
        """The open workspace, or `None` when there is genuinely none.

        Returns `None` rather than inventing one. A caller that needs a
        workspace to exist should say so by creating one from a template, which
        is a decision with a name attached rather than a silent side effect of
        a read.
        """
        workspace_id = self.active_id()
        if workspace_id is None:
            return None
        found = self.get(workspace_id)
        if found is None:
            # The pointer outlived its target. Report nothing open rather than
            # resurrecting a deleted layout.
            return None
        return found


def _to_workspace(row: dict[str, Any]) -> Workspace:
    profile = row.get("profile")
    return Workspace(
        workspace_id=str(row["workspace_id"]),
        name=str(row["name"]),
        template_key=row.get("template_key"),
        profile=WorkspaceProfile.model_validate(json.loads(profile)) if profile else None,
        panels=tuple(Panel.model_validate(item) for item in json.loads(row["panels"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )
