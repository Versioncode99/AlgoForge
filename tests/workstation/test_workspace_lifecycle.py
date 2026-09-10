"""Workspaces as user-owned workstations: they persist, they have a history, they travel.

`tests/api/test_workspace_actions.py` pins the panel operations through the
action registry. These pin the lifecycle the store owns: what opens on a cold
start, what a version history records, and what an export carries.
"""

from __future__ import annotations

import json

import pytest
from forge.workstation import (
    EXPORT_KIND,
    MAX_VERSIONS,
    Panel,
    PanelKind,
    WorkspaceImportError,
    WorkspaceStore,
)


def store(tmp_path) -> WorkspaceStore:
    return WorkspaceStore(tmp_path / "workspaces.db")


def chart(index: int = 1, **kwargs) -> Panel:
    return Panel(
        panel_id=f"chart-{index}",
        kind=PanelKind.CHART,
        x=0,
        y=index - 1,
        width=6,
        height=1,
        settings={"symbol": "NQ", "timeframe": "1m"},
        **kwargs,
    )


# ── persistence across a restart ─────────────────────────────────────────────


def test_a_workspace_survives_reopening_the_store(tmp_path) -> None:
    first = store(tmp_path)
    workspace = first.create("NQ Research", panels=(chart(),))
    reopened = WorkspaceStore(tmp_path / "workspaces.db").get(workspace.workspace_id)
    assert reopened is not None
    assert reopened.name == "NQ Research"
    assert reopened.panels[0].settings["symbol"] == "NQ"


def test_restore_session_reopens_the_last_one_that_was_open(tmp_path) -> None:
    """Closing the app and reopening it should put you back where you were."""
    first = store(tmp_path)
    research = first.create("NQ Research")
    prop = first.create("Prop Challenge")
    first.set_active(prop.workspace_id)
    restored = WorkspaceStore(tmp_path / "workspaces.db").restore_session()
    assert restored is not None
    assert restored.workspace_id == prop.workspace_id
    assert restored.workspace_id != research.workspace_id


def test_restore_session_falls_back_to_the_default(tmp_path) -> None:
    first = store(tmp_path)
    research = first.create("NQ Research")
    first.create("Scratch")
    first.set_default(research.workspace_id)
    restored = WorkspaceStore(tmp_path / "workspaces.db").restore_session()
    assert restored is not None
    assert restored.workspace_id == research.workspace_id


def test_restore_session_invents_nothing_on_a_fresh_install(tmp_path) -> None:
    assert store(tmp_path).restore_session() is None


def test_opening_a_workspace_does_not_make_it_the_default(tmp_path) -> None:
    """The distinction the two pointers exist for."""
    workspaces = store(tmp_path)
    research = workspaces.create("NQ Research")
    scratch = workspaces.create("Scratch")
    workspaces.set_default(research.workspace_id)
    workspaces.set_active(scratch.workspace_id)
    assert workspaces.default_id() == research.workspace_id
    assert workspaces.active_id() == scratch.workspace_id


def test_a_dangling_active_pointer_reports_nothing_open(tmp_path) -> None:
    workspaces = store(tmp_path)
    workspace = workspaces.create("Temporary")
    workspaces.set_active(workspace.workspace_id)
    workspaces.delete(workspace.workspace_id)
    assert workspaces.active() is None
    assert workspaces.default_id() is None


def test_setting_a_default_that_does_not_exist_is_refused(tmp_path) -> None:
    with pytest.raises(KeyError):
        store(tmp_path).set_default("workspace_nope")


# ── version history ──────────────────────────────────────────────────────────


def test_every_meaningful_save_keeps_a_version(tmp_path) -> None:
    workspaces = store(tmp_path)
    workspace = workspaces.create("NQ Research")
    workspace = workspaces.save(workspace.with_panel(chart(1)), summary="added a chart")
    workspaces.save(workspace.with_panel(chart(2)), summary="added a second chart")
    history = workspaces.versions(workspace.workspace_id)
    assert [v["version"] for v in history] == [3, 2, 1]
    assert history[0]["summary"] == "added a second chart"
    assert history[0]["previous_version"] == 2


def test_identical_consecutive_saves_are_collapsed(tmp_path) -> None:
    """Dragging a panel must not produce forty entries that all say the same thing."""
    workspaces = store(tmp_path)
    workspace = workspaces.create("NQ Research", panels=(chart(),))
    for _ in range(10):
        workspaces.save(workspace)
    assert len(workspaces.versions(workspace.workspace_id)) == 1


def test_an_ai_edit_is_attributable(tmp_path) -> None:
    workspaces = store(tmp_path)
    workspace = workspaces.create("NQ Research")
    workspaces.save(
        workspace.with_panel(chart()), summary="added a chart for NQ", actor="ai"
    )
    latest = workspaces.versions(workspace.workspace_id)[0]
    assert latest["actor"] == "ai"
    assert latest["summary"] == "added a chart for NQ"


def test_a_save_with_no_summary_describes_what_the_layout_is(tmp_path) -> None:
    """Derived from the workspace, which is checkable; never a guess at intent."""
    workspaces = store(tmp_path)
    workspace = workspaces.create("NQ Research")
    workspaces.save(workspace.with_panel(chart(1)).with_panel(chart(2)))
    latest = workspaces.versions(workspace.workspace_id)[0]
    assert "2 panel" in latest["summary"]
    assert "chart" in latest["summary"]


def test_restoring_adds_a_version_rather_than_rewinding(tmp_path) -> None:
    workspaces = store(tmp_path)
    workspace = workspaces.create("NQ Research")
    workspace = workspaces.save(workspace.with_panel(chart(1)), summary="one chart")
    workspaces.save(workspace.with_panel(chart(2)), summary="two charts")

    restored = workspaces.restore(workspace.workspace_id, 2)
    assert len(restored.panels) == 1
    history = workspaces.versions(workspace.workspace_id)
    assert history[0]["version"] == 4
    assert "restored version 2" in history[0]["summary"]
    # The work in between is still readable, so the restore is itself undoable.
    assert any(v["summary"] == "two charts" for v in history)


def test_a_previous_version_can_be_duplicated_without_touching_the_original(
    tmp_path,
) -> None:
    workspaces = store(tmp_path)
    workspace = workspaces.create("NQ Research")
    workspace = workspaces.save(workspace.with_panel(chart(1)), summary="one chart")
    workspace = workspaces.save(workspace.with_panel(chart(2)), summary="two charts")

    copy = workspaces.duplicate_version(workspace.workspace_id, 2, "NQ Research — one chart")
    assert copy.workspace_id != workspace.workspace_id
    assert len(copy.panels) == 1
    assert len(workspaces.get(workspace.workspace_id).panels) == 2


def test_restoring_a_version_that_does_not_exist_is_refused(tmp_path) -> None:
    workspaces = store(tmp_path)
    workspace = workspaces.create("NQ Research")
    with pytest.raises(KeyError, match="no version"):
        workspaces.restore(workspace.workspace_id, 99)


def test_history_is_bounded(tmp_path) -> None:
    workspaces = store(tmp_path)
    workspace = workspaces.create("NQ Research")
    for index in range(MAX_VERSIONS + 12):
        workspace = workspaces.save(
            workspace.model_copy(update={"name": f"NQ Research {index}"}),
            summary=f"rename {index}",
        )
    assert len(workspaces.versions(workspace.workspace_id, limit=500)) <= MAX_VERSIONS


def test_deleting_a_workspace_takes_its_history_and_no_research(tmp_path) -> None:
    workspaces = store(tmp_path)
    workspace = workspaces.create("NQ Research", panels=(chart(),))
    workspaces.save(workspace.with_panel(chart(2)), summary="second chart")
    workspaces.delete(workspace.workspace_id)
    assert workspaces.versions(workspace.workspace_id) == []
    # Nothing in this module can reach an experiment, a verdict or a holdout.
    assert workspaces.get(workspace.workspace_id) is None


# ── export and import ────────────────────────────────────────────────────────


def test_a_workspace_round_trips_through_export_and_import(tmp_path) -> None:
    workspaces = store(tmp_path)
    workspace = workspaces.create(
        "NQ Research", panels=(chart(1), chart(2, collapsed=True)), template_key="research"
    )
    document = workspaces.export(workspace.workspace_id)
    assert document["kind"] == EXPORT_KIND
    # Serialisable as it stands: an export that needed a custom encoder would
    # not be a document.
    document = json.loads(json.dumps(document))

    imported = workspaces.import_workspace(document, name="NQ Research (imported)")
    assert imported.workspace_id != workspace.workspace_id
    assert imported.name == "NQ Research (imported)"
    assert len(imported.panels) == 2
    assert imported.panels[1].collapsed is True
    assert imported.template_key == "research"


def test_an_export_carries_no_identity_to_collide_with(tmp_path) -> None:
    workspaces = store(tmp_path)
    workspace = workspaces.create("NQ Research", panels=(chart(),))
    document = workspaces.export(workspace.workspace_id)
    assert "workspace_id" not in document


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not a dict", "JSON object"),
        ({"kind": "something.else"}, "not a workspace export"),
        ({"kind": EXPORT_KIND, "export_version": 99}, "newer version"),
        ({"kind": EXPORT_KIND, "export_version": 1}, "no panel list"),
    ],
)
def test_a_payload_that_is_not_a_workspace_is_refused_by_name(
    tmp_path, payload, message
) -> None:
    with pytest.raises(WorkspaceImportError, match=message):
        store(tmp_path).import_workspace(payload)


def test_an_import_cannot_introduce_a_panel_that_does_not_render(tmp_path) -> None:
    document = {
        "kind": EXPORT_KIND,
        "export_version": 1,
        "panels": [
            {"panel_id": "x-1", "kind": "holographic_orb", "x": 0, "y": 0, "width": 4, "height": 4}
        ],
    }
    with pytest.raises(WorkspaceImportError, match="could not be read"):
        store(tmp_path).import_workspace(document)


def test_an_import_cannot_introduce_a_panel_that_runs_off_the_grid(tmp_path) -> None:
    document = {
        "kind": EXPORT_KIND,
        "export_version": 1,
        "panels": [
            {"panel_id": "chart-1", "kind": "chart", "x": 10, "y": 0, "width": 8, "height": 4}
        ],
    }
    with pytest.raises(WorkspaceImportError, match="could not be read"):
        store(tmp_path).import_workspace(document)


# ── panel operations the model owns ──────────────────────────────────────────


def test_collapsing_keeps_the_geometry(tmp_path) -> None:
    workspaces = store(tmp_path)
    workspace = workspaces.create("NQ Research", panels=(chart(),))
    collapsed = workspace.collapsing("chart-1", True)
    panel = collapsed.require("chart-1")
    assert panel.collapsed is True
    assert (panel.x, panel.y, panel.width, panel.height) == (0, 0, 6, 1)
    assert collapsed.collapsing("chart-1", False).require("chart-1").collapsed is False


def test_reordering_moves_a_panel_in_the_draw_order(tmp_path) -> None:
    workspaces = store(tmp_path)
    workspace = workspaces.create("Desk", panels=(chart(1), chart(2), chart(3)))
    assert [p.panel_id for p in workspace.reordered("chart-1", 2).panels] == [
        "chart-2",
        "chart-3",
        "chart-1",
    ]
    # Out-of-range positions clamp rather than raise: "bring to front" is a
    # gesture, not an index the caller should have to compute.
    assert [p.panel_id for p in workspace.reordered("chart-1", 99).panels][-1] == "chart-1"
    assert next(iter(workspace.reordered("chart-3", -5).panels)).panel_id == "chart-3"


def test_reordering_an_unknown_panel_names_the_ones_that_exist(tmp_path) -> None:
    workspaces = store(tmp_path)
    workspace = workspaces.create("Desk", panels=(chart(1),))
    with pytest.raises(KeyError, match="chart-1"):
        workspace.reordered("chart-9", 0)
