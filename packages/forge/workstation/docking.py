"""Docking: split, stack, detach, resize, collapse — as operations on the grid.

Doc 2 asks for a docking model and names `DockNode` as a possible abstraction,
then says plainly not to introduce it if the current architecture already
provides an equivalent, and not to overbuild if the existing layout engine can be
extended cleanly. This module is the result of taking that seriously.

**Why there is no dock tree here.** The usual docking implementation is a tree of
split nodes with orientations and child weights, and leaves holding tabs. That
shape exists to *derive* rectangles, because the layout has none of its own.
`forge.workstation` already has rectangles: a 12-column grid in units that
survive a change of monitor, with resize and collapse already on `Panel`.
Replacing it with a tree would have meant a second layout model, a migration for
every saved workspace, and two things that could disagree about where a panel is
— which is the parallel system Doc 2 forbids.

So the tree's vocabulary is expressed as operations instead:

=================  =========================================================
Docking verb       How it is expressed here
=================  =========================================================
split horizontal   `split(..., Along.ROW)` — halve a rectangle's width and
                   put the new panel in the freed half
split vertical     `split(..., Along.COLUMN)` — the same down the height
tab / group        `stack()` — two panels share one rectangle, one draws
detach             `detach()` — pull a tab out into a rectangle of its own
reattach           `stack()` again, onto a different panel
resize             already `Panel.x/y/width/height`
collapse           already `Panel.collapsed`
=================  =========================================================

The one primitive the grid genuinely lacked is stacking, because a grid has no
way to say "these two rectangles overlap deliberately". That is `Panel.stack`,
and it is the only field this work added.

**Everything returns a new workspace.** A refused split leaves the caller holding
exactly what it passed in, so a rejected operation cannot half-apply and leave a
layout with a panel hanging off the grid.
"""

from __future__ import annotations

from enum import StrEnum

from forge.workstation.models import GRID_COLUMNS, MAX_ROWS, Panel, Workspace

#: Nothing useful is narrower than this, and a split that produces a one-column
#: sliver is a worse layout than a refused split with a reason.
MIN_SPAN = 2


class Along(StrEnum):
    """Which way a rectangle is cut.

    Named for the axis the two halves sit along rather than for the orientation
    of the divider, because "horizontal split" is ambiguous in every window
    manager and argued about in all of them.
    """

    #: Side by side. The divider is vertical; widths halve.
    ROW = "row"
    #: One above the other. The divider is horizontal; heights halve.
    COLUMN = "column"


class DockingError(ValueError):
    """A docking operation that cannot be performed, and why."""


def _stack_members(workspace: Workspace, stack: str) -> tuple[Panel, ...]:
    return tuple(panel for panel in workspace.panels if panel.stack and panel.stack == stack)


def split(
    workspace: Workspace,
    panel_id: str,
    along: Along,
    new_panel: Panel,
) -> Workspace:
    """Cut a panel's rectangle in two and put `new_panel` in the freed half.

    The existing panel keeps the first half, which is the half it is already
    drawn in — so the operator sees the new panel appear beside what they split
    rather than everything moving at once.

    Splitting a *stacked* panel splits the whole stack, because the stack is one
    rectangle: tabs cannot be half-moved. Every member follows.
    """
    target = workspace.require(panel_id)
    span = target.width if along is Along.ROW else target.height
    if span < MIN_SPAN * 2:
        axis = "wide" if along is Along.ROW else "tall"
        raise DockingError(
            f"'{panel_id}' is {span} units {axis}; a split needs {MIN_SPAN * 2} "
            f"so that neither half is narrower than {MIN_SPAN}"
        )
    keep = span // 2
    moved = span - keep

    first: dict[str, object]
    second: dict[str, object]
    if along is Along.ROW:
        first = {"width": keep}
        second = {"x": target.x + keep, "y": target.y, "width": moved, "height": target.height}
    else:
        first = {"height": keep}
        second = {"x": target.x, "y": target.y + keep, "width": target.width, "height": moved}

    # The whole stack moves, not just the panel that was named.
    family = _stack_members(workspace, target.stack) if target.stack else (target,)
    result = workspace
    for member in family:
        result = result.replacing_panel(member.model_copy(update=first))

    placed = new_panel.model_copy(update={**second, "stack": "", "active": True})
    return result.with_panel(placed)


def stack(workspace: Workspace, panel_id: str, onto: str) -> Workspace:
    """Make `panel_id` a tab of the rectangle `onto` occupies, and show it.

    The moved panel becomes active, because an operator who drags a panel onto
    another is asking to see it. The rest of the stack keeps its order.
    """
    if panel_id == onto:
        raise DockingError(f"'{panel_id}' cannot be stacked onto itself")
    moving = workspace.require(panel_id)
    host = workspace.require(onto)

    name = host.stack or f"stack_{host.panel_id}"
    if moving.stack and moving.stack == name:
        return activate(workspace, panel_id)

    result = workspace
    # The host joins its own stack if it was not in one.
    if not host.stack:
        result = result.replacing_panel(host.model_copy(update={"stack": name, "active": True}))

    # Everything already showing there stops showing; the arrival takes over.
    for member in _stack_members(result, name):
        if member.active:
            result = result.replacing_panel(member.model_copy(update={"active": False}))

    return result.replacing_panel(
        moving.model_copy(
            update={
                "stack": name,
                "active": True,
                "x": host.x,
                "y": host.y,
                "width": host.width,
                "height": host.height,
            }
        )
    )


def detach(workspace: Workspace, panel_id: str, along: Along = Along.ROW) -> Workspace:
    """Pull a tab out of its stack into a rectangle of its own.

    The rectangle is made by splitting the stack's, so a detached panel lands
    beside the tabs it came from rather than on top of something else. Detaching
    the last-but-one member dissolves the stack: a stack of one is a panel, and
    leaving the name behind would show a tab bar with a single tab in it.
    """
    panel = workspace.require(panel_id)
    if not panel.stack:
        raise DockingError(f"'{panel_id}' is not stacked, so there is nothing to detach it from")

    siblings = tuple(p for p in _stack_members(workspace, panel.stack) if p.panel_id != panel_id)
    span = panel.width if along is Along.ROW else panel.height
    if span < MIN_SPAN * 2:
        raise DockingError(
            f"the stack holding '{panel_id}' is {span} units across; detaching needs "
            f"{MIN_SPAN * 2} so neither side is narrower than {MIN_SPAN}"
        )
    keep = span // 2
    moved = span - keep

    stay: dict[str, object]
    leave: dict[str, object]
    if along is Along.ROW:
        stay = {"width": keep}
        leave = {"x": panel.x + keep, "y": panel.y, "width": moved, "height": panel.height}
    else:
        stay = {"height": keep}
        leave = {"x": panel.x, "y": panel.y + keep, "width": panel.width, "height": moved}

    result = workspace
    dissolving = len(siblings) == 1
    for sibling in siblings:
        update: dict[str, object] = dict(stay)
        if dissolving:
            update |= {"stack": "", "active": True}
        elif not any(s.active for s in siblings):
            # The detached panel was the visible one. Something has to show.
            update |= {"active": sibling.panel_id == siblings[0].panel_id}
        result = result.replacing_panel(sibling.model_copy(update=update))

    return result.replacing_panel(
        panel.model_copy(update={**leave, "stack": "", "active": True})
    )


def activate(workspace: Workspace, panel_id: str) -> Workspace:
    """Bring one tab of a stack to the front.

    A panel that is not stacked is already the only thing in its rectangle, so
    this is a no-op rather than an error — the caller is a tab click, and making
    it check first would put the same condition in every caller.
    """
    panel = workspace.require(panel_id)
    if not panel.stack:
        return workspace
    result = workspace
    for member in _stack_members(workspace, panel.stack):
        wanted = member.panel_id == panel_id
        if member.active != wanted:
            result = result.replacing_panel(member.model_copy(update={"active": wanted}))
    return result


def resize(workspace: Workspace, panel_id: str, width: int, height: int) -> Workspace:
    """Set a panel's size, taking its whole stack with it.

    The grid already stores the numbers; what this adds is the stack invariant,
    which a caller writing `replacing_panel` directly would have to remember and
    would eventually forget.
    """
    panel = workspace.require(panel_id)
    if width < MIN_SPAN or height < MIN_SPAN:
        raise DockingError(f"a panel is at least {MIN_SPAN} units on each side")
    if panel.x + width > GRID_COLUMNS:
        raise DockingError(
            f"width {width} puts '{panel_id}' past column {GRID_COLUMNS} from x={panel.x}"
        )
    if panel.y + height > MAX_ROWS:
        raise DockingError(f"height {height} puts '{panel_id}' past the last row")

    family = _stack_members(workspace, panel.stack) if panel.stack else (panel,)
    result = workspace
    for member in family:
        result = result.replacing_panel(
            member.model_copy(update={"width": width, "height": height})
        )
    return result


def tabs(workspace: Workspace, stack_name: str) -> tuple[Panel, ...]:
    """The tabs of one stack, in layout order. Empty if there is no such stack."""
    return _stack_members(workspace, stack_name)


def stacks(workspace: Workspace) -> dict[str, tuple[Panel, ...]]:
    """Every stack in the workspace, for a renderer deciding what to draw."""
    found: dict[str, list[Panel]] = {}
    for panel in workspace.panels:
        if panel.stack:
            found.setdefault(panel.stack, []).append(panel)
    return {name: tuple(members) for name, members in found.items()}
