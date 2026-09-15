"""The sidebar: a composition the operator owns, not a property of the mode.

The complaint these tests answer is concrete. Navigation was a constant of the
`ModeDescriptor`, so wanting Prop Accounts beside Research Agents meant switching
modes — and switching modes swapped the rail, the layout and the context. A
workspace is now a composition of capabilities.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from forge.modes.models import MODES, WorkspaceMode
from forge.product.navigation import DESTINATIONS
from forge.workstation.models import Workspace, WorkspaceKind
from forge.workstation.sidebar import (
    CATALOGUE,
    MAX_GROUPS,
    Sidebar,
    SidebarError,
    default_sidebar_for,
    destinations,
    known,
    sidebar_from,
)


def _workspace(**changes) -> Workspace:
    now = datetime.now(UTC)
    fields = {"workspace_id": "w1", "name": "Desk", "created_at": now, "updated_at": now}
    return Workspace(**{**fields, **changes})


# ── the catalogue ────────────────────────────────────────────────────────────
def test_the_catalogue_is_the_union_of_every_mode() -> None:
    """This is the whole fix. Every destination, reachable from any workspace."""
    for mode in WorkspaceMode:
        for section in MODES[mode].sections:
            assert known(section.route), f"{section.route} is unreachable"


def test_a_destination_records_which_modes_offer_it() -> None:
    """Provenance, not restriction."""
    shared = [d for d in CATALOGUE.values() if len(d.modes) > 1]
    assert shared, "no destination is shared between modes"
    assert all(d.route for d in shared)


def test_the_catalogue_is_renderable() -> None:
    rows = destinations()
    assert len(rows) == len(CATALOGUE)
    assert all(row["label"] and row["detail"] for row in rows)


def test_an_unknown_route_cannot_enter_a_sidebar() -> None:
    """A rail item pointing at nothing is the dead control the rules refuse."""
    with pytest.raises(SidebarError, match="not a destination"):
        Sidebar().with_group("g", "G").with_item("no_such_screen", group_id="g")


# ── the built-in rails ───────────────────────────────────────────────────────
def test_every_workspace_starts_from_the_products_own_rail() -> None:
    """One navigation, so one default rail.

    It used to be one per mode, which is why creating a workspace asked which
    mode to start from — a question about navigation dressed as a question about
    what kind of trader you are.
    """
    rails = {mode: default_sidebar_for(mode) for mode in WorkspaceMode}
    for mode, rail in rails.items():
        assert set(rail.routes()) == {d.route for d in DESTINATIONS}, mode
    assert len({rail.routes() for rail in rails.values()}) == 1


def test_a_workspace_with_no_sidebar_falls_back_to_its_mode() -> None:
    """A layout saved before sidebars existed still opens with navigation."""
    workspace = _workspace(mode="prop_firm")
    assert workspace.sidebar is None
    assert set(workspace.rail().routes()) == {d.route for d in DESTINATIONS}


def test_a_workspace_with_no_mode_and_no_sidebar_is_empty_not_broken() -> None:
    assert _workspace().rail().groups == ()


# ── composition: the point of the exercise ───────────────────────────────────
def test_a_rail_saved_under_the_old_route_names_still_opens() -> None:
    """A workspace built before the mode navigation was replaced keeps working.

    Its items name `desk`, `charts`, `portfolio` — routes this product no longer
    has. They still name a *screen*, so they are translated on the way in and
    stored canonically, and every subsequent edit finds them under either
    spelling. Refusing them would have deleted somebody's arrangement to make a
    rename tidy.
    """
    rail = (
        Sidebar()
        .with_group("mine", "Mine")
        .with_item("desk", group_id="mine")
        .with_item("charts", group_id="mine")
        .with_item("portfolio", group_id="mine")
    )
    assert rail.routes() == (
        "propdesk:accounts", "markets:charts", "trading:portfolio",
    )
    # Found by the name it was saved under, as well as by the current one.
    assert rail.has("desk") and rail.has("propdesk:accounts")
    assert rail.without_item("charts").routes() == (
        "propdesk:accounts", "trading:portfolio",
    )


def test_one_workspace_can_hold_prop_and_research_without_switching_modes() -> None:
    workspace = (
        _workspace(name="My Prop Research", mode="prop_firm")
        .adding_sidebar_group("mine", "MY DESK")
        .adding_sidebar_item("propdesk:copy", group_id="mine")
        .adding_sidebar_item("propdesk:drawdown", group_id="mine")
        .adding_sidebar_item("research:experiments", group_id="mine")
        .adding_sidebar_item("markets:charts", group_id="mine")
    )
    routes = set(workspace.rail().routes())
    # A prop operator's four screens on one rail, two of which are tabs inside
    # different destinations. This used to be impossible twice over: the rail
    # was whichever mode was open, and a tab could not be named at all.
    assert {
        "propdesk:copy",
        "propdesk:drawdown",
        "research:experiments",
        "markets:charts",
    } <= routes
    # And the product's own nine are still there underneath it.
    assert {d.route for d in DESTINATIONS} <= routes


def test_a_workspace_can_be_composed_from_nothing() -> None:
    workspace = (
        _workspace(name="My Quant Desk")
        .adding_sidebar_group("trading", "MY TRADING")
        .adding_sidebar_item("markets:charts", group_id="trading")
        .adding_sidebar_item("propdesk:accounts", group_id="trading")
        .adding_sidebar_group("research", "RESEARCH")
        .adding_sidebar_item("campaigns", group_id="research")
    )
    rendered = workspace.rail().as_dict()
    assert [g["label"] for g in rendered["groups"]] == ["MY TRADING", "RESEARCH"]
    assert [i["route"] for i in rendered["groups"][0]["items"]] == [
        "markets:charts", "propdesk:accounts",
    ]


def test_the_first_item_into_an_empty_sidebar_gets_a_group() -> None:
    """An edit with an obvious meaning is not refused on a technicality."""
    workspace = _workspace().adding_sidebar_item("charts")
    assert workspace.rail().has("charts")


# ── editing ──────────────────────────────────────────────────────────────────
def test_add_remove_reorder_rename_group_collapse_pin_hide_all_work() -> None:
    workspace = (
        _workspace()
        .adding_sidebar_group("a", "Alpha")
        .adding_sidebar_group("b", "Beta")
        .adding_sidebar_item("charts", group_id="a")
        .adding_sidebar_item("desk", group_id="b")
    )
    assert workspace.rail().routes() == ("markets:charts", "propdesk:accounts")

    workspace = workspace.reordering_sidebar_groups(["b", "a"])
    assert [g.group_id for g in workspace.rail().groups] == ["b", "a"]

    workspace = workspace.renaming_sidebar_group("a", "ALPHA DESK")
    assert workspace.rail().require_group("a").label == "ALPHA DESK"

    workspace = workspace.collapsing_sidebar_group("a", True)
    assert workspace.rail().require_group("a").collapsed is True

    workspace = workspace.renaming_sidebar_item("charts", "NQ Chart")
    rendered = workspace.rail().as_dict()
    charts = next(
        i
        for g in rendered["groups"]
        for i in g["items"]
        if i["route"] == "markets:charts"
    )
    assert charts["label"] == "NQ Chart"
    assert charts["renamed"] is True

    workspace = workspace.pinning_sidebar_item("charts", True)
    assert workspace.rail().locate("charts") is not None

    workspace = workspace.hiding_sidebar_item("desk", True)
    assert workspace.rail().require_group("b").visible() == ()
    # Hidden, not gone: showing it restores its position.
    workspace = workspace.hiding_sidebar_item("desk", False)
    assert len(workspace.rail().require_group("b").visible()) == 1

    workspace = workspace.removing_sidebar_item("charts")
    assert not workspace.rail().has("charts")


def test_moving_an_item_between_groups_keeps_its_settings() -> None:
    workspace = (
        _workspace()
        .adding_sidebar_group("a", "A")
        .adding_sidebar_group("b", "B")
        .adding_sidebar_item("charts", group_id="a", label="NQ")
        .pinning_sidebar_item("charts", True)
        .moving_sidebar_item("charts", group_id="b")
    )
    rail = workspace.rail()
    assert rail.locate("markets:charts")[0] == "b"
    item = rail.require_group("b").items[0]
    assert item.label == "NQ"
    assert item.pinned is True


def test_adding_the_same_destination_twice_is_refused() -> None:
    """Refused rather than moved: add and move are different intentions."""
    workspace = _workspace().adding_sidebar_item("charts")
    with pytest.raises(SidebarError, match="already in this sidebar"):
        workspace.adding_sidebar_item("charts")


def test_editing_a_missing_group_names_what_exists() -> None:
    workspace = _workspace().adding_sidebar_group("a", "A")
    with pytest.raises(SidebarError, match="Groups: a"):
        workspace.renaming_sidebar_group("nope", "X")


def test_resetting_rebuilds_the_modes_rail() -> None:
    workspace = (
        _workspace(mode="normal")
        .adding_sidebar_group("mine", "MINE")
        .adding_sidebar_item("desk", group_id="mine")
    )
    assert workspace.sidebar is not None
    restored = workspace.with_default_sidebar()
    assert set(restored.rail().routes()) == {
        s.route for s in MODES[WorkspaceMode.NORMAL].sections
    }


def test_a_sidebar_is_bounded() -> None:
    sidebar = Sidebar()
    for index in range(MAX_GROUPS):
        sidebar = sidebar.with_group(f"g{index}", f"G{index}")
    with pytest.raises(ValueError, match="at most"):
        sidebar.with_group("one_more", "Too many")


# ── round trip ───────────────────────────────────────────────────────────────
def test_a_sidebar_survives_serialisation() -> None:
    original = (
        Sidebar()
        .with_group("trading", "MY TRADING")
        .with_item("charts", group_id="trading", label="NQ")
        .with_group("research", "RESEARCH")
        .with_item("campaigns", group_id="research")
        .collapsed_group("research", True)
    )
    restored = sidebar_from(original.model_dump(mode="json"))
    assert restored.routes() == original.routes()
    assert restored.require_group("research").collapsed is True
    assert restored.require_group("trading").items[0].label == "NQ"


def test_workspace_links_are_links_and_nothing_more() -> None:
    workspace = (
        _workspace()
        .linking_campaign("camp_1")
        .linking_account("acct_1")
        .described(description="Prop and research on one screen", icon="NQ")
        .pinning(True)
    )
    assert workspace.campaign_ids == ("camp_1",)
    assert workspace.account_ids == ("acct_1",)
    assert workspace.icon == "NQ"
    assert workspace.pinned is True
    assert workspace.kind is WorkspaceKind.USER_CREATED
    # Unlinking removes it without touching anything else.
    assert workspace.linking_campaign("camp_1", False).campaign_ids == ()
