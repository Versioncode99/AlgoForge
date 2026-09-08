"""Where the workspace lives, and how to move it.

The location is a pointer file in the repository, not a setting inside the
workspace — a setting that lives in the thing it configures cannot be read
before the thing is found.

Changing it takes effect on the next start. Nothing here relocates a running
process's open SQLite handles: the honest sequence is copy, switch the pointer,
restart. Pretending otherwise would silently split the ledger across two roots.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from forge.contracts.models import ApiEnvelope
from forge.vault import (
    LAYOUT_VAULT,
    VaultMirror,
    Workspace,
    default_root,
    inspect,
    inventory,
    migrate,
    migrate_layout,
    resolve,
    write_pointer,
)
from pydantic import BaseModel, Field

from forge_api.activity import ActivityLog, Level


class LocationRequest(BaseModel):
    path: str = Field(min_length=2, max_length=400)


class MigrateLayoutRequest(BaseModel):
    """Where to migrate to. Empty means the OS application-data directory."""

    path: str = Field(default="", max_length=400)


class SwitchRequest(BaseModel):
    path: str = Field(min_length=2, max_length=400)
    # Copying is the default because the alternative is discovering that the old
    # copy was the only copy.
    migrate_existing: bool = True
    create_if_missing: bool = True


def build_storage_router(
    workspace: Workspace,
    mirror: VaultMirror,
    log: ActivityLog,
    library: Any,
    store: Any,
    families: Any,
    research: Any,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/storage", tags=["storage"])
    repo = workspace.repo

    def payload() -> dict[str, Any]:
        described = workspace.describe()
        return {
            **described,
            "repo": str(repo),
            "pointer": str(repo / "config" / "storage.json"),
            "mirror": mirror.status(),
            "folders": [
                {"name": "Strategies", "path": str(workspace.strategy_notes), "kind": "notes"},
                {"name": "Research Papers", "path": str(workspace.paper_notes), "kind": "notes"},
                {"name": "Backtests", "path": str(workspace.backtest_notes), "kind": "notes"},
                {"name": "Verdicts", "path": str(workspace.verdict_notes), "kind": "notes"},
                {"name": "Families", "path": str(workspace.family_notes), "kind": "notes"},
                {"name": "Missions", "path": str(workspace.mission_notes), "kind": "notes"},
                {"name": "Strategy code", "path": str(workspace.strategies), "kind": "store"},
                {"name": "Databases", "path": str(workspace.data), "kind": "store"},
                {"name": "Custom templates", "path": str(workspace.templates), "kind": "store"},
            ],
            "stays_in_repo": [
                {
                    "name": "Prop rule sets",
                    "path": str(repo / "rules"),
                    "why": "Version-controlled contracts; a rule set is code, not output.",
                },
                {
                    "name": "Operator settings",
                    "path": str(repo / "config"),
                    "why": "Holds the pointer to the workspace, so it cannot live inside it.",
                },
                {
                    "name": "Market data cache",
                    "path": str(repo / "data" / "market"),
                    "why": (
                        "Purchased vendor archives. Left in place so relocating the "
                        "workspace can never cost a paid Databento download."
                    ),
                },
            ],
        }

    @router.get("", response_model=ApiEnvelope[dict[str, Any]])
    def read_storage() -> ApiEnvelope[dict[str, Any]]:
        return ApiEnvelope(
            data=payload(),
            meta={"restart_required_after_change": True},
        )

    @router.post("/inspect", response_model=ApiEnvelope[dict[str, Any]])
    def inspect_location(body: LocationRequest) -> ApiEnvelope[dict[str, Any]]:
        """Report on a candidate folder without adopting it."""
        return ApiEnvelope(data=inspect(repo, body.path))

    @router.get("/layout", response_model=ApiEnvelope[dict[str, Any]])
    def read_layout() -> ApiEnvelope[dict[str, Any]]:
        """Which storage layout this installation is on, and what it holds."""
        return ApiEnvelope(
            data={
                "layout": workspace.layout,
                "root": str(workspace.root),
                "store": str(workspace.store),
                "application_default": str(default_root()),
                "obsidian_required": False,
                "obsidian_export": str(workspace.obsidian_export),
                "inventory": inventory(workspace),
                "migration_available": workspace.layout == LAYOUT_VAULT,
            },
            meta={
                "note": (
                    "AlgoForge owns its data. The vault layout keeps working, but it "
                    "stores databases and gigabytes of backtest JSON inside a "
                    "note-taking application's folder. Migrating copies everything to "
                    "an application-owned location and leaves the original in place."
                )
            },
        )

    @router.post("/migrate-layout", response_model=ApiEnvelope[dict[str, Any]])
    def migrate_to_app_layout(body: MigrateLayoutRequest) -> ApiEnvelope[dict[str, Any]]:
        """Move this installation off the Obsidian layout. Copies; never moves.

        The pointer is rewritten only if every counted item arrived. A partial
        copy that repointed the application at itself would be worse than never
        having run.
        """
        target = Path(body.path).expanduser() if body.path else default_root()
        if not target.is_absolute():
            raise HTTPException(422, {"code": "relative_path", "detail": str(target)})
        report = migrate_layout(repo, workspace, target)
        level: Level = "pass" if report["migrated"] else "fail"
        log.record(
            "STORAGE",
            f"layout migration to {target} — {'verified' if report['migrated'] else 'refused'}",
            level,
        )
        if not report["migrated"]:
            raise HTTPException(409, {"code": "migration_not_verified", **report})
        return ApiEnvelope(
            data=report,
            meta={
                "restart_required_after_change": True,
                "original_retained": True,
                "note": (
                    "The original workspace is untouched and still complete. Delete it "
                    "only once you are satisfied with the migrated installation."
                ),
            },
        )

    @router.post("/switch", response_model=ApiEnvelope[dict[str, Any]])
    def switch_location(body: SwitchRequest) -> ApiEnvelope[dict[str, Any]]:
        report = inspect(repo, body.path)
        target = Path(str(report["path"]))
        if not report["exists"]:
            if not body.create_if_missing or not report["creatable"]:
                raise HTTPException(
                    422,
                    {
                        "code": "location_unavailable",
                        "detail": f"{target} does not exist and cannot be created.",
                        "report": report,
                    },
                )
            try:
                target.mkdir(parents=True)
            except OSError as exc:
                raise HTTPException(
                    422,
                    {"code": "cannot_create", "detail": str(exc), "report": report},
                ) from exc
        elif report["problems"]:
            raise HTTPException(422, {"code": "location_refused", "report": report})

        moved: dict[str, Any] = {"copied": 0, "skipped": 0}
        if body.migrate_existing:
            destination = Workspace(
                repo=repo, root=target, vault_mode=bool(report["vault_mode"])
            ).ensure()
            moved = migrate(workspace, destination)

        write_pointer(repo, target)
        log.record(
            "STORAGE",
            f"workspace pointed at {target} ({moved.get('copied', 0)} items copied) — "
            "restart to use it",
            "warn",
        )
        return ApiEnvelope(
            data={
                "location": str(target),
                "migration": moved,
                "active_location": str(workspace.root),
                "restart_required": True,
            },
            meta={
                "note": (
                    "The pointer is written. The running process keeps using the old "
                    "location until AlgoForge restarts, so the ledger is never split "
                    "across two roots mid-run."
                )
            },
        )

    @router.post("/reindex", response_model=ApiEnvelope[dict[str, Any]])
    def reindex(backfill: bool = True) -> ApiEnvelope[dict[str, Any]]:
        """Rewrite the vault notes from what is on disk now.

        Mirroring happens as work completes, so a vault attached to an instance
        that has already run 400 strategies would otherwise start empty. This
        walks the existing store once and writes the notes that were never
        written — a job, not a request, when the library is large.
        """
        written = {"strategies": 0, "papers": 0, "backtests": 0, "families": 0}
        if backfill:
            for family in families.all():
                if mirror.family(family.as_dict()):
                    written["families"] += 1
            # Read the library once: four hundred strategies each re-querying it
            # would turn a linear walk into a quadratic one.
            known = {str(item["id"]): item for item in research.list()}
            for item in known.values():
                if mirror.paper(item):
                    written["papers"] += 1
            for spec in library.list_specs():
                titles = [str(known[sid]["title"]) for sid in spec.research_sources if sid in known]
                if mirror.strategy(spec, source_titles=titles):
                    written["strategies"] += 1
                latest = store.latest(spec.strategy_id)
                if latest and mirror.backtest(latest, strategy_name=spec.name):
                    written["backtests"] += 1

        counts = workspace.counts()
        mirror.index(counts)
        log.record(
            "STORAGE",
            f"vault reindexed — {written['strategies']} strategy notes, "
            f"{written['papers']} paper notes, {written['backtests']} backtest notes",
            "pass",
        )
        return ApiEnvelope(
            data={
                "written": written,
                "counts": counts,
                "notes_root": str(workspace.notes),
            },
            meta={"note": "Notes are rewritten, never merged. Hand edits to them are replaced."},
        )

    return router


def resolve_workspace(repo: Path) -> Workspace:
    """Entry point used by the app factory. Kept here so main stays declarative."""
    return resolve(repo)
