"""Docking on the grid: splits that land, tabs that stay one rectangle, detaches that dissolve.

The bugs this guards against all render as painting faults and are data faults:
two panels drawn on top of each other, a tab bar with one tab in it, a stack
where nothing is showing, or a panel half off the grid after a refused edit.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from forge.workstation.docking import (
    MIN_SPAN,
    Along,
    DockingError,
    activate,
    detach,
    resize,
    split,
    stacks,
    tabs,
)
from forge.workstation.models import GRID_COLUMNS, Panel, PanelKind, Workspace


def panel(panel_id: str, *, x: int = 0, y: int = 0, width: int = 12, height: int = 8) -> Panel:
    return Panel(
        panel_id=panel_id, kind=PanelKind.CHART, x=x, y=y, width=width, height=height
    )


def workspace(*panels: Panel) -> Workspace:
    now = datetime.now(UTC)
    return Workspace(
        workspace_id="ws_test", name="Test", panels=panels, created_at=now, updated_at=now
    )


# ── splitting ────────────────────────────────────────────────────────────────


def test_splitting_sideways_halves_the_width_and_keeps_the_original_in_place() -> None:
    """The panel being split does not move: the new one appears beside it."""
    after = split(workspace(panel("chart")), "chart", Along.ROW, panel("book"))
    left, right = after.require("chart"), after.require("book")
    assert (left.x, left.width) == (0, 6)
    assert (right.x, right.width) == (6, 6)
    assert left.height == right.height == 8


def test_splitting_downward_halves_the_height() -> None:
    after = split(workspace(panel("chart")), "chart", Along.COLUMN, panel("risk"))
    top, bottom = after.require("chart"), after.require("risk")
    assert (top.y, top.height) == (0, 4)
    assert (bottom.y, bottom.height) == (4, 4)
    assert top.width == bottom.width == 12


def test_an_odd_span_gives_the_extra_unit_to_the_new_panel() -> None:
    after = split(workspace(panel("chart", width=7)), "chart", Along.ROW, panel("book"))
    assert after.require("chart").width == 3
    assert after.require("book").width == 4


def test_a_panel_too_narrow_to_split_is_refused_with_a_reason() -> None:
    with pytest.raises(DockingError, match="units wide"):
        split(workspace(panel("chart", width=MIN_SPAN * 2 - 1)), "chart", Along.ROW, panel("b"))


def test_a_refused_split_changes_nothing() -> None:
    """Immutability is the point: a rejected edit cannot half-apply."""
    before = workspace(panel("chart", width=3))
    with pytest.raises(DockingError):
        split(before, "chart", Along.ROW, panel("book"))
    assert before.require("chart").width == 3
    assert before.panel("book") is None


def test_a_split_never_runs_off_the_grid() -> None:
    after = split(workspace(panel("chart")), "chart", Along.ROW, panel("book"))
    for each in after.panels:
        assert each.x + each.width <= GRID_COLUMNS


# ── stacking ─────────────────────────────────────────────────────────────────


def test_stacking_puts_two_panels_in_one_rectangle() -> None:
    after = _two_stacked()
    left, right = after.require("chart"), after.require("book")
    assert (left.x, left.y, left.width, left.height) == (
        right.x,
        right.y,
        right.width,
        right.height,
    )
    assert left.stack == right.stack != ""


def test_the_arriving_tab_is_the_one_showing() -> None:
    """Dragging a panel onto another is a request to see it."""
    after = _two_stacked()
    assert after.require("book").active is True
    assert after.require("chart").active is False


def test_exactly_one_tab_shows_however_many_are_stacked() -> None:
    after = _two_stacked()
    after = _stack_onto(after, "risk", "chart")
    showing = [p.panel_id for p in tabs(after, after.require("chart").stack) if p.active]
    assert showing == ["risk"]


def test_a_panel_cannot_be_stacked_onto_itself() -> None:
    with pytest.raises(DockingError, match="itself"):
        _stack_onto(workspace(panel("chart")), "chart", "chart")


def test_restacking_a_tab_already_there_just_shows_it() -> None:
    after = _two_stacked()
    again = _stack_onto(after, "chart", "book")
    assert again.require("chart").active is True
    assert again.require("book").active is False
    assert len(tabs(again, again.require("chart").stack)) == 2


def test_activate_brings_a_tab_forward() -> None:
    after = activate(_two_stacked(), "chart")
    assert after.require("chart").active is True
    assert after.require("book").active is False


def test_activate_on_an_unstacked_panel_does_nothing() -> None:
    """A tab click should not have to check whether it was a tab."""
    before = workspace(panel("chart"))
    assert activate(before, "chart") == before


# ── the invariants, enforced by the model itself ─────────────────────────────


def test_a_stack_whose_members_disagree_about_geometry_is_refused() -> None:
    """This renders as panels drawn on top of each other. It is a data fault."""
    with pytest.raises(ValueError, match="different places"):
        workspace(
            panel("a", width=6).model_copy(update={"stack": "s", "active": True}),
            panel("b", x=6, width=6).model_copy(update={"stack": "s", "active": False}),
        )


def test_a_stack_with_nothing_showing_is_refused() -> None:
    with pytest.raises(ValueError, match="exactly one active"):
        workspace(
            panel("a").model_copy(update={"stack": "s", "active": False}),
            panel("b").model_copy(update={"stack": "s", "active": False}),
        )


def test_a_stack_with_two_showing_is_refused() -> None:
    with pytest.raises(ValueError, match="exactly one active"):
        workspace(
            panel("a").model_copy(update={"stack": "s", "active": True}),
            panel("b").model_copy(update={"stack": "s", "active": True}),
        )


def test_panels_written_before_docking_existed_still_load() -> None:
    """Every saved workspace has no stack field. None of them may break."""
    loaded = workspace(panel("chart"), panel("book", y=8))
    assert all(p.stack == "" and p.active for p in loaded.panels)
    assert stacks(loaded) == {}


# ── detaching ────────────────────────────────────────────────────────────────


def test_detaching_lands_the_panel_beside_the_tabs_it_came_from() -> None:
    after = detach(_two_stacked(), "book", Along.ROW)
    stayed, left = after.require("chart"), after.require("book")
    assert stayed.width == 6 and left.width == 6
    assert left.x == 6
    assert left.stack == ""


def test_detaching_the_second_to_last_dissolves_the_stack() -> None:
    """A stack of one is a panel. A tab bar with one tab is a bug."""
    after = detach(_two_stacked(), "book")
    assert after.require("chart").stack == ""
    assert after.require("chart").active is True
    assert stacks(after) == {}


def test_detaching_from_a_bigger_stack_keeps_it_a_stack() -> None:
    after = _stack_onto(_two_stacked(), "risk", "chart")
    after = detach(after, "risk")
    remaining = tabs(after, after.require("chart").stack)
    assert {p.panel_id for p in remaining} == {"chart", "book"}
    assert sum(1 for p in remaining if p.active) == 1


def test_detaching_the_visible_tab_leaves_something_showing() -> None:
    """Otherwise the rectangle goes blank and looks like a crash."""
    after = _stack_onto(_two_stacked(), "risk", "chart")
    assert after.require("risk").active is True
    after = detach(after, "risk")
    assert sum(1 for p in tabs(after, after.require("chart").stack) if p.active) == 1


def test_detaching_an_unstacked_panel_is_refused() -> None:
    with pytest.raises(DockingError, match="not stacked"):
        detach(workspace(panel("chart")), "chart")


# ── resizing ─────────────────────────────────────────────────────────────────


def test_resizing_takes_the_whole_stack_with_it() -> None:
    after = resize(_two_stacked(), "chart", 6, 4)
    assert (after.require("chart").width, after.require("chart").height) == (6, 4)
    assert (after.require("book").width, after.require("book").height) == (6, 4)


def test_resizing_past_the_grid_is_refused() -> None:
    with pytest.raises(DockingError, match="past column"):
        resize(workspace(panel("chart", x=6, width=6)), "chart", 8, 4)


def test_resizing_below_the_minimum_is_refused() -> None:
    with pytest.raises(DockingError, match="at least"):
        resize(workspace(panel("chart")), "chart", 1, 4)


# ── splitting a stack moves all of it ────────────────────────────────────────


def test_splitting_a_stacked_panel_moves_every_tab() -> None:
    """The stack is one rectangle. Tabs cannot be half-moved."""
    after = split(_two_stacked(), "chart", Along.ROW, panel("risk"))
    assert after.require("chart").width == 6
    assert after.require("book").width == 6
    assert after.require("risk").x == 6


def test_the_panel_added_by_a_split_is_never_part_of_a_stack() -> None:
    arriving = panel("risk").model_copy(update={"stack": "s"})
    after = split(_two_stacked(), "chart", Along.ROW, arriving)
    assert after.require("risk").stack == ""


# ── helpers ──────────────────────────────────────────────────────────────────


def _stack_onto(space: Workspace, moving: str, onto: str) -> Workspace:
    from forge.workstation.docking import stack as stack_op

    if space.panel(moving) is None:
        space = space.with_panel(panel(moving))
    return stack_op(space, moving, onto)


def _two_stacked() -> Workspace:
    return _stack_onto(workspace(panel("chart"), panel("book", y=8)), "book", "chart")
