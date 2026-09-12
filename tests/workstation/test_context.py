"""Context propagation, and the property that makes it safe.

`link_group` used to be a label attached to nothing: the field's docstring
claimed panels sharing a group followed each other's symbol, `Workspace.linked`
wrote the tag, nothing read it, and the workspace view drew the group's name as
a badge on the panel header. These tests pin the behaviour the badge was
already asserting, and pin the one decision that makes it reversible -- that
resolution never writes to a panel.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from forge.workstation.context import (
    DEFAULT_GROUP,
    MAX_GROUPS,
    Facet,
    WorkstationContext,
    groups_in,
    resolve,
    resolve_all,
)
from forge.workstation.models import Panel, PanelKind, Workspace
from pydantic import ValidationError


def _panel(panel_id: str, x: int, *, group: str | None = None, **settings: str) -> Panel:
    return Panel(
        panel_id=panel_id,
        kind=PanelKind.CHART,
        x=x,
        y=0,
        width=4,
        height=6,
        settings=dict(settings),
        link_group=group,
    )


def _workspace(*panels: Panel) -> Workspace:
    now = datetime.now(UTC)
    return Workspace(
        workspace_id="w1", name="NQ Lab", created_at=now, updated_at=now, panels=panels
    )


# ── the context itself ───────────────────────────────────────────────────────


def test_an_unset_context_is_empty_and_nothing_is_inferred() -> None:
    context = WorkstationContext()
    assert context.empty
    assert set(context.facets().values()) == {""}


def test_clearing_a_facet_leaves_the_others() -> None:
    context = WorkstationContext(instrument="NQ", timeframe="1m", campaign_id="c1")
    cleared = context.cleared(Facet.INSTRUMENT)
    assert cleared.instrument == ""
    assert cleared.timeframe == "1m"
    assert cleared.campaign_id == "c1"


def test_facets_are_separate_fields_and_not_one_subject() -> None:
    """An account is not a strategy, and a campaign is not an instrument.

    Conflating them is the category error the previous phase found in the
    interface, where the governing workspace was labelled a mode.
    """
    context = WorkstationContext(
        instrument="NQ", campaign_id="c1", strategy_id="s1", account_id="a1"
    )
    facets = context.facets()
    assert facets["instrument"] == "NQ"
    assert facets["campaign"] == "c1"
    assert facets["strategy"] == "s1"
    assert facets["account"] == "a1"


# ── resolution ───────────────────────────────────────────────────────────────


def test_a_panel_in_no_group_shows_its_own_symbol() -> None:
    panel = _panel("chart-1", 0, symbol="ES", timeframe="5m")
    resolved = resolve(panel, {DEFAULT_GROUP: WorkstationContext(instrument="NQ")})
    assert resolved.symbol == "ES"
    assert resolved.source == "panel"
    assert resolved.group is None


def test_a_panel_in_a_group_follows_that_group() -> None:
    panel = _panel("chart-1", 0, symbol="ES", timeframe="5m", group="a")
    resolved = resolve(panel, {"a": WorkstationContext(instrument="NQ", timeframe="1m")})
    assert (resolved.symbol, resolved.timeframe) == ("NQ", "1m")
    assert resolved.source == "context"


def test_a_group_whose_context_sets_nothing_does_not_blank_the_panel() -> None:
    panel = _panel("chart-1", 0, symbol="ES", timeframe="5m", group="a")
    resolved = resolve(panel, {})
    assert resolved.symbol == "ES"
    assert resolved.source == "panel"


def test_two_groups_look_at_different_instruments() -> None:
    """Which is what §55's "Link Group A" means, and why one global symbol is not enough."""
    resolved = {
        item.panel_id: item
        for item in resolve_all(
            (
                _panel("chart-1", 0, symbol="ES", group="a"),
                _panel("chart-2", 4, symbol="ES", group="b"),
                _panel("chart-3", 8, symbol="ES"),
            ),
            {"a": WorkstationContext(instrument="NQ"), "b": WorkstationContext(instrument="GC")},
        )
    }
    assert resolved["chart-1"].symbol == "NQ"
    assert resolved["chart-2"].symbol == "GC"
    assert resolved["chart-3"].symbol == "ES"


def test_resolution_reports_where_the_value_came_from() -> None:
    """An operator seeing MNQ must be able to tell whether it is pinned or followed."""
    resolved = resolve_all(
        (_panel("a", 0, symbol="MNQ", group="g"), _panel("b", 4, symbol="MNQ")),
        {"g": WorkstationContext(instrument="MNQ")},
    )
    assert [item.source for item in resolved] == ["context", "panel"]


def test_groups_in_lists_only_groups_panels_actually_name() -> None:
    assert groups_in(
        (_panel("a", 0, group="x"), _panel("b", 4), _panel("c", 8, group="x"))
    ) == ("x",)


# ── the property that makes it reversible ────────────────────────────────────


def test_setting_a_context_writes_nothing_to_any_panel() -> None:
    """The decision the whole design rests on.

    A model that wrote the new symbol across the group would destroy the pinned
    value the moment a group member changed, and could not be undone.
    """
    workspace = _workspace(_panel("chart-1", 0, symbol="MNQ", timeframe="5m", group="a"))
    before = workspace.require("chart-1").settings
    after = workspace.with_context(WorkstationContext(instrument="NQ", timeframe="1m"), "a")
    assert after.require("chart-1").settings == before


def test_unlinking_reveals_the_symbol_the_panel_was_always_pinned_to() -> None:
    workspace = _workspace(_panel("chart-1", 0, symbol="MNQ", timeframe="5m", group="a"))
    workspace = workspace.with_context(WorkstationContext(instrument="NQ"), "a")
    assert resolve(workspace.require("chart-1"), workspace.contexts).symbol == "NQ"

    unlinked = workspace.linked(None, ("chart-1",))
    assert resolve(unlinked.require("chart-1"), unlinked.contexts).symbol == "MNQ"


def test_a_context_set_on_one_group_does_not_touch_another() -> None:
    workspace = _workspace(
        _panel("chart-1", 0, symbol="ES", group="a"), _panel("chart-2", 4, symbol="CL", group="b")
    )
    workspace = workspace.with_context(WorkstationContext(instrument="NQ"), "a")
    resolved = {
        item.panel_id: item.symbol
        for item in resolve_all(workspace.panels, workspace.contexts)
    }
    assert resolved == {"chart-1": "NQ", "chart-2": "CL"}


# ── bounds ───────────────────────────────────────────────────────────────────


def test_an_empty_context_is_not_stored() -> None:
    """Storing it would make "has a context" and "sets nothing" differ in the
    record and look identical on screen."""
    workspace = _workspace(_panel("chart-1", 0, group="a"))
    assert workspace.with_context(WorkstationContext(), "a").contexts == {}


def test_setting_a_context_to_empty_removes_it() -> None:
    workspace = _workspace(_panel("chart-1", 0, group="a"))
    workspace = workspace.with_context(WorkstationContext(instrument="NQ"), "a")
    assert "a" in workspace.contexts
    assert workspace.with_context(WorkstationContext(), "a").contexts == {}


def test_the_group_ceiling_fires_on_a_mutation_not_only_at_construction() -> None:
    """`model_copy` does not re-run validators.

    The previous phase shipped exactly this bug in the sidebar: every mutation
    used `model_copy`, so the duplicate-route check only ever ran when the
    workspace was first built. This is the regression test for the same mistake
    in a new field.
    """
    workspace = _workspace(_panel("chart-1", 0))
    with pytest.raises(ValidationError):
        for index in range(MAX_GROUPS + 1):
            workspace = workspace.with_context(
                WorkstationContext(instrument="NQ"), f"group-{index}"
            )


def test_clearing_a_group_leaves_the_workspace_context() -> None:
    workspace = _workspace(_panel("chart-1", 0, group="a"))
    workspace = workspace.with_context(WorkstationContext(instrument="ES"))
    workspace = workspace.with_context(WorkstationContext(instrument="NQ"), "a")
    workspace = workspace.without_context("a")
    assert workspace.context_for().instrument == "ES"
    assert workspace.context_for("a").empty


def test_a_workspace_saved_before_contexts_existed_still_opens() -> None:
    """Empty contexts resolve to "no context", so those panels show what they always showed."""
    workspace = _workspace(_panel("chart-1", 0, symbol="NQ", timeframe="1m", group="a"))
    assert workspace.contexts == {}
    assert resolve(workspace.require("chart-1"), workspace.contexts).symbol == "NQ"


# ── persistence ──────────────────────────────────────────────────────────────


def _store(tmp_path):
    from forge.workstation.store import WorkspaceStore

    return WorkspaceStore(tmp_path / "workspaces.db")


def test_a_context_survives_a_round_trip(tmp_path) -> None:
    store = _store(tmp_path)
    created = store.create("NQ Lab", panels=(_panel("chart-1", 0, symbol="ES", group="a"),))
    store.save(created.with_context(WorkstationContext(instrument="NQ", timeframe="1m"), "a"))

    reopened = store.get(created.workspace_id)
    assert reopened is not None
    assert reopened.context_for("a").instrument == "NQ"
    assert resolve(reopened.require("chart-1"), reopened.contexts).symbol == "NQ"


def test_restoring_a_layout_does_not_discard_the_context(tmp_path) -> None:
    """A version records the layout. Everything else comes from the workspace as it stands.

    Before this, `version()` rebuilt a workspace from the layout columns alone,
    so `restore` wrote back a workspace with no sidebar and no context — an
    operator restoring yesterday's panel arrangement lost the rail they had
    spent the afternoon building.
    """
    store = _store(tmp_path)
    created = store.create("NQ Lab", panels=(_panel("chart-1", 0, symbol="ES", group="a"),))
    # A second version, so there is an earlier layout to go back to, and a
    # context set on the workspace as it stands now.
    with_two = created.model_copy(
        update={"panels": (*created.panels, _panel("chart-2", 4, symbol="CL"))}
    )
    store.save(
        with_two.with_context(WorkstationContext(instrument="NQ"), "a"), summary="two panels"
    )

    restored = store.restore(created.workspace_id, 1)
    assert len(restored.panels) == 1, "the layout did not go back"
    assert restored.context_for("a").instrument == "NQ", "the context was discarded"


def test_a_context_travels_with_an_export(tmp_path) -> None:
    store = _store(tmp_path)
    created = store.create("NQ Lab", panels=(_panel("chart-1", 0, symbol="ES", group="a"),))
    store.save(created.with_context(WorkstationContext(instrument="NQ"), "a"))

    document = store.export(created.workspace_id)
    imported = store.import_workspace(document, name="Copy")
    assert imported.context_for("a").instrument == "NQ"
    assert resolve(imported.require("chart-1"), imported.contexts).symbol == "NQ"


def test_an_export_written_before_contexts_existed_still_imports(tmp_path) -> None:
    store = _store(tmp_path)
    created = store.create("NQ Lab", panels=(_panel("chart-1", 0, symbol="ES"),))
    document = store.export(created.workspace_id)
    document.pop("contexts")
    imported = store.import_workspace(document, name="Old export")
    assert imported.contexts == {}
