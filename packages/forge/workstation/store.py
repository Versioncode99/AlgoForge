"""Where workspaces live: SQLite, in the application's own data root.

Layouts are structured application state, so they belong in the database rather
than in files — the distinction the storage addendum draws. They are emphatically
*not* research: this database can be deleted and the operator loses their screen
arrangement, not a single artifact, verdict or holdout. That is the test for
whether something belongs here. The reverse holds too, and is the more important
half: deleting a workspace never touches an experiment, a verdict or a holdout.

**Active and default are different things.** *Active* is which workspace is open
right now and moves whenever the operator switches. *Default* is which one opens
on a cold start and only changes when they say so. Conflating them means a
workspace opened once to check something becomes the one that greets them every
morning.

**Every meaningful save keeps a version.** A layout someone spent an afternoon
arranging is worth an undo that survives a restart, and an edit made by the
agent is worth being able to see and reverse. Versions record what changed, who
changed it, and what came before, and identical consecutive saves are collapsed
so that dragging a panel does not produce forty entries.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge.contracts.hashing import content_hash
from forge.workstation.models import Panel, Workspace, WorkspaceProfile, new_workspace_id

#: Bumped when the stored shape changes in a way a reader must know about.
#: Rows carry it so an older row can be recognised rather than misread.
SCHEMA_VERSION = 1

#: How many versions of one workspace are kept. Deep enough to undo an
#: afternoon's work, shallow enough that a workspace edited all day does not
#: grow without bound.
MAX_VERSIONS = 60

#: What an exported workspace file says it is. Checked on import, so a JSON file
#: that is not a workspace is refused by name rather than by traceback.
EXPORT_KIND = "algoforge.workspace"
EXPORT_VERSION = 1


class WorkspaceImportError(ValueError):
    """An exported payload could not be imported. The message says why."""


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
            db.execute(
                "CREATE TABLE IF NOT EXISTS workspace_versions ("
                "version_id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, "
                "version INTEGER NOT NULL, name TEXT NOT NULL, template_key TEXT, "
                "profile TEXT, panels TEXT NOT NULL, summary TEXT NOT NULL, "
                "actor TEXT NOT NULL, previous_version INTEGER, digest TEXT NOT NULL, "
                "schema_version INTEGER NOT NULL, at TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS workspace_versions_ws "
                "ON workspace_versions(workspace_id, version)"
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
    def save(
        self,
        workspace: Workspace,
        *,
        summary: str = "",
        actor: str = "operator",
        record_version: bool = True,
    ) -> Workspace:
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
        if record_version:
            self._record_version(workspace, summary=summary, actor=actor)
        return workspace

    # ── version history ──────────────────────────────────────────────────────
    def _record_version(
        self, workspace: Workspace, *, summary: str, actor: str
    ) -> int | None:
        """Keep this state as a version, unless it is the one already on top.

        Identical consecutive states are collapsed. Dragging a panel produces a
        stream of saves whose content settles on one arrangement, and forty
        entries that all say the same thing are not a history — they are a way
        of making the real edits impossible to find.
        """
        payload = [p.model_dump(mode="json") for p in workspace.panels]
        digest = content_hash(
            {"name": workspace.name, "panels": payload, "template": workspace.template_key}
        )
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute(
                "SELECT version, digest FROM workspace_versions WHERE workspace_id=? "
                "ORDER BY version DESC LIMIT 1",
                (workspace.workspace_id,),
            ).fetchone()
            if latest is not None and str(latest["digest"]) == digest:
                return None
            previous = int(latest["version"]) if latest is not None else None
            version = (previous or 0) + 1
            db.execute(
                "INSERT INTO workspace_versions (workspace_id, version, name, template_key, "
                "profile, panels, summary, actor, previous_version, digest, schema_version, at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    workspace.workspace_id,
                    version,
                    workspace.name,
                    workspace.template_key,
                    json.dumps(workspace.profile.model_dump(mode="json"))
                    if workspace.profile
                    else None,
                    json.dumps(payload),
                    summary or _describe(workspace),
                    actor,
                    previous,
                    digest,
                    SCHEMA_VERSION,
                    datetime.now(UTC).isoformat(),
                ),
            )
            db.execute(
                "DELETE FROM workspace_versions WHERE workspace_id=? AND version <= ?",
                (workspace.workspace_id, version - MAX_VERSIONS),
            )
        return version

    def versions(self, workspace_id: str, *, limit: int = MAX_VERSIONS) -> list[dict[str, Any]]:
        """This workspace's history, newest first."""
        with closing(self._connect()) as db, db:
            rows = db.execute(
                "SELECT version, name, summary, actor, previous_version, at, "
                "json_array_length(panels) AS panel_count "
                "FROM workspace_versions WHERE workspace_id=? ORDER BY version DESC LIMIT ?",
                (workspace_id, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def version(self, workspace_id: str, version: int) -> Workspace | None:
        """One historical state, reconstructed as a workspace.

        Carries the *current* identity and creation time: this is what the
        workspace looked like then, not a different workspace.
        """
        current = self.get(workspace_id)
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT * FROM workspace_versions WHERE workspace_id=? AND version=?",
                (workspace_id, int(version)),
            ).fetchone()
        if row is None or current is None:
            return None
        profile = row["profile"]
        return Workspace(
            workspace_id=workspace_id,
            name=str(row["name"]),
            template_key=row["template_key"],
            profile=WorkspaceProfile.model_validate(json.loads(profile)) if profile else None,
            panels=tuple(Panel.model_validate(item) for item in json.loads(row["panels"])),
            created_at=current.created_at,
            updated_at=datetime.fromisoformat(str(row["at"])),
        )

    def restore(self, workspace_id: str, version: int, *, actor: str = "operator") -> Workspace:
        """Put a previous state back, as a new version on top.

        Never rewinds the history. Restoring version 3 onto a workspace at
        version 9 produces version 10, so the intervening work is still
        readable and the restore itself can be undone.
        """
        historical = self.version(workspace_id, version)
        if historical is None:
            raise KeyError(f"no version {version} of workspace '{workspace_id}'")
        return self.save(
            historical.model_copy(update={"updated_at": datetime.now(UTC)}),
            summary=f"restored version {version}",
            actor=actor,
        )

    def duplicate_version(self, workspace_id: str, version: int, name: str) -> Workspace:
        """Copy a previous state into a new workspace, leaving this one alone."""
        historical = self.version(workspace_id, version)
        if historical is None:
            raise KeyError(f"no version {version} of workspace '{workspace_id}'")
        return self.create(
            name,
            panels=historical.panels,
            profile=historical.profile,
            template_key=historical.template_key,
            summary=f"duplicated from version {version} of '{historical.name}'",
        )

    def create(
        self,
        name: str,
        *,
        panels: tuple[Panel, ...] = (),
        profile: WorkspaceProfile | None = None,
        template_key: str | None = None,
        summary: str = "",
        actor: str = "operator",
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
        return self.save(workspace, summary=summary or "created", actor=actor)

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
                    "DELETE FROM workspace_state WHERE key IN ('active','default') AND value=?",
                    (workspace_id,),
                )
                # The layout is gone, so its history describes nothing. Research
                # artifacts are untouched: none of them live here.
                db.execute(
                    "DELETE FROM workspace_versions WHERE workspace_id=?", (workspace_id,)
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
            summary=f"duplicated from '{source.name}'",
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

    # ── which one opens on a cold start ──────────────────────────────────────
    def default_id(self) -> str | None:
        with closing(self._connect()) as db, db:
            row = db.execute("SELECT value FROM workspace_state WHERE key='default'").fetchone()
        return None if row is None else str(row["value"])

    def set_default(self, workspace_id: str) -> None:
        """Mark the workspace that opens on a cold start.

        Separate from `set_active` on purpose: opening a workspace to look at
        something must not silently make it the one that greets the operator
        every morning.
        """
        if self.get(workspace_id) is None:
            raise KeyError(workspace_id)
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO workspace_state VALUES ('default', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (workspace_id,),
            )

    def clear_default(self) -> None:
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM workspace_state WHERE key='default'")

    def restore_session(self) -> Workspace | None:
        """What to open when the application starts.

        The last one open, then the default, then nothing. Preferring the last
        open one is the behaviour every workstation has: closing the app and
        reopening it should put you back where you were, and the default is the
        fallback for a genuinely cold start.

        Returns `None` rather than inventing a workspace. A caller that needs
        one should create it from a template — a decision with a name attached
        rather than a silent side effect of a read.
        """
        for workspace_id in (self.active_id(), self.default_id()):
            if not workspace_id:
                continue
            found = self.get(workspace_id)
            if found is not None:
                self.set_active(found.workspace_id)
                return found
        return None

    # ── export and import ────────────────────────────────────────────────────
    def export(self, workspace_id: str) -> dict[str, Any]:
        """A workspace as a portable document.

        Carries no ids that would collide on the way in: the importing
        installation mints its own. Carries no research and no credentials —
        there are none here to carry, which is the point of keeping layouts
        separate from everything else.
        """
        workspace = self.get(workspace_id)
        if workspace is None:
            raise KeyError(workspace_id)
        return {
            "kind": EXPORT_KIND,
            "export_version": EXPORT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "name": workspace.name,
            "template_key": workspace.template_key,
            "profile": workspace.profile.model_dump(mode="json") if workspace.profile else None,
            "panels": [p.model_dump(mode="json") for p in workspace.panels],
        }

    def import_workspace(
        self, payload: Any, *, name: str | None = None, actor: str = "operator"
    ) -> Workspace:
        """Create a workspace from an exported document.

        Every field is validated by the same models the rest of the store uses,
        so an import cannot introduce a panel kind that does not render or a
        geometry that does not fit the grid. A payload that is not a workspace
        is refused by name.
        """
        if not isinstance(payload, dict):
            raise WorkspaceImportError("A workspace export is a JSON object.")
        if payload.get("kind") != EXPORT_KIND:
            raise WorkspaceImportError(
                f"This is not a workspace export: expected kind '{EXPORT_KIND}', "
                f"got '{payload.get('kind')}'."
            )
        if int(payload.get("export_version", 0)) > EXPORT_VERSION:
            raise WorkspaceImportError(
                f"This export was written by a newer version (export_version "
                f"{payload.get('export_version')}); this build reads up to {EXPORT_VERSION}."
            )
        raw_panels = payload.get("panels")
        if not isinstance(raw_panels, list):
            raise WorkspaceImportError("The export has no panel list.")
        try:
            panels = tuple(Panel.model_validate(item) for item in raw_panels)
            profile = (
                WorkspaceProfile.model_validate(payload["profile"])
                if payload.get("profile")
                else None
            )
        except Exception as exc:
            raise WorkspaceImportError(f"The export could not be read: {exc}") from exc
        chosen = (name or str(payload.get("name") or "")).strip() or "Imported workspace"
        return self.create(
            chosen[:120],
            panels=panels,
            profile=profile,
            template_key=payload.get("template_key"),
            summary="imported",
            actor=actor,
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


def _describe(workspace: Workspace) -> str:
    """A change summary derived from the workspace rather than asserted.

    Used when a caller does not supply one. It says what the layout *is*, which
    is checkable, rather than guessing what changed, which is not.
    """
    if not workspace.panels:
        return "empty layout"
    kinds: dict[str, int] = {}
    for panel in workspace.panels:
        kinds[panel.kind.value] = kinds.get(panel.kind.value, 0) + 1
    parts = ", ".join(
        f"{count}x {kind}" if count > 1 else kind for kind, count in sorted(kinds.items())
    )
    return f"{len(workspace.panels)} panel(s): {parts}"


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
