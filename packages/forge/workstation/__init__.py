"""The workstation: panels, layouts, and the templates that seed them.

Separate from `forge.vault`, which owns where research is *stored*. This package
owns what the operator sees. Deleting everything here costs a screen
arrangement and nothing else.
"""

from forge.workstation.builder import (
    KNOWN_ROOTS,
    STYLE_TIMEFRAMES,
    describe,
    layout_for,
    markets_for,
    timeframe_for,
)
from forge.workstation.models import (
    EXECUTION_PANELS,
    GRID_COLUMNS,
    MAX_PANELS,
    MAX_ROWS,
    Panel,
    PanelKind,
    Workspace,
    WorkspaceProfile,
    new_panel_id,
    new_workspace_id,
)
from forge.workstation.store import (
    EXPORT_KIND,
    EXPORT_VERSION,
    MAX_VERSIONS,
    SCHEMA_VERSION,
    WorkspaceImportError,
    WorkspaceStore,
)
from forge.workstation.templates import TEMPLATES, WorkspaceTemplate, catalogue, template

__all__ = [
    "EXECUTION_PANELS",
    "EXPORT_KIND",
    "EXPORT_VERSION",
    "GRID_COLUMNS",
    "KNOWN_ROOTS",
    "MAX_PANELS",
    "MAX_ROWS",
    "MAX_VERSIONS",
    "SCHEMA_VERSION",
    "STYLE_TIMEFRAMES",
    "TEMPLATES",
    "Panel",
    "PanelKind",
    "Workspace",
    "WorkspaceImportError",
    "WorkspaceProfile",
    "WorkspaceStore",
    "WorkspaceTemplate",
    "catalogue",
    "describe",
    "layout_for",
    "markets_for",
    "new_panel_id",
    "new_workspace_id",
    "template",
    "timeframe_for",
]
