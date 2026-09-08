"""The workstation: panels, layouts, and the templates that seed them.

Separate from `forge.vault`, which owns where research is *stored*. This package
owns what the operator sees. Deleting everything here costs a screen
arrangement and nothing else.
"""

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
from forge.workstation.store import SCHEMA_VERSION, WorkspaceStore
from forge.workstation.templates import TEMPLATES, WorkspaceTemplate, catalogue, template

__all__ = [
    "EXECUTION_PANELS",
    "GRID_COLUMNS",
    "MAX_PANELS",
    "MAX_ROWS",
    "SCHEMA_VERSION",
    "TEMPLATES",
    "Panel",
    "PanelKind",
    "Workspace",
    "WorkspaceProfile",
    "WorkspaceStore",
    "WorkspaceTemplate",
    "catalogue",
    "new_panel_id",
    "new_workspace_id",
    "template",
]
