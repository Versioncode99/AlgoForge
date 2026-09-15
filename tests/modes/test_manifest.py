"""The three manifests: what they promise, and whether anything backs it.

A manifest is a promise made to two readers at once — the shell renders it, and
an agent is told it is the answer to "what can I do here". Both make it worth
asserting that every route has a view, every panel kind renders, and no mode
quietly offers a destination that does not exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from forge.modes import MODE_ORDER, MODES, Stance, WorkspaceMode, catalogue, descriptor
from forge.modes.models import parse_stance
from forge.product.navigation import DESTINATIONS, LEGACY_ROUTES, resolve
from forge.workstation import TEMPLATES, PanelKind

ROOT = Path(__file__).resolve().parents[2]
APP_TSX = ROOT / "apps" / "web" / "src" / "App.tsx"
INBOX_TS = ROOT / "apps" / "web" / "src" / "inbox.ts"


def test_three_modes_in_a_deliberate_order() -> None:
    """There were four. Hedge Fund was a product category, not a capability.

    Nothing it carried was deleted -- `test_the_book_loop_survived_its_mode`
    below is the check that the surfaces came across rather than going with it.
    """
    assert [mode.value for mode in MODE_ORDER] == ["normal", "prop_firm", "ai"]
    assert set(MODE_ORDER) == set(MODES)
    assert [entry["mode"] for entry in catalogue()] == [m.value for m in MODE_ORDER]


@pytest.mark.parametrize("mode", MODE_ORDER, ids=lambda m: m.value)
def test_every_mode_describes_itself_rather_than_naming_itself(mode: WorkspaceMode) -> None:
    """A name and a tagline are branding. The purpose is what a person chooses on."""
    spec = MODES[mode]
    assert spec.name.strip()
    assert spec.tagline.strip().endswith(".")
    assert len(spec.purpose) > 120, "a one-line purpose is a slogan, not a description"
    assert spec.sections, "a mode with no sections opens on nothing"


@pytest.mark.parametrize("mode", MODE_ORDER, ids=lambda m: m.value)
def test_route_ids_are_unique_and_labelled(mode: WorkspaceMode) -> None:
    """Two routes sharing an id makes the second unreachable by hash.

    `aria-current` also lands on both at once, so the rail highlights two rows
    and neither is wrong. This used to be asserted in the web tests against a
    navigation constant; the constant is gone and the manifest is the record.
    """
    routes = [section.route for section in MODES[mode].sections]
    duplicates = {route for route in routes if routes.count(route) > 1}
    assert not duplicates, f"{mode.value} declares {duplicates} twice"
    for section in MODES[mode].sections:
        assert section.label.strip(), f"{section.route} has no label"
        assert section.detail.strip(), f"{section.route} has no detail"
        assert section.group.strip(), f"{section.route} has no group"


def test_no_route_collides_with_the_skip_link() -> None:
    """The main landmark's id must not also be a destination.

    When it was "workspace" it was both, so the skip link set the hash, the hash
    listener read it as a route, and the one control built for keyboard and
    screen-reader users navigated them somewhere else instead of moving focus to
    the content. `apps/web/src/shell.test.ts` asserts the other half — that the
    landmark id is still this literal.
    """
    landmark = "main-content"
    for mode in MODE_ORDER:
        routes = {section.route for section in MODES[mode].sections}
        assert landmark not in routes, f"{mode.value} declares a route named {landmark}"


@pytest.mark.parametrize("mode", MODE_ORDER, ids=lambda m: m.value)
def test_every_panel_kind_a_mode_names_actually_exists(mode: WorkspaceMode) -> None:
    """A section offering a panel kind with nothing behind it is a fake surface."""
    known = {kind.value for kind in PanelKind}
    for section in MODES[mode].sections:
        unknown = set(section.panel_kinds) - known
        assert not unknown, f"{mode.value}/{section.route} names unknown panels: {unknown}"


@pytest.mark.parametrize("mode", MODE_ORDER, ids=lambda m: m.value)
def test_every_mode_seeds_a_template_that_exists(mode: WorkspaceMode) -> None:
    template = TEMPLATES.get(MODES[mode].workspace_template)
    assert template is not None, f"{mode.value} seeds a template that is not registered"
    assert template.panels, f"{mode.value} seeds an empty layout, so it opens on nothing"


def test_every_destination_and_tab_has_a_view_behind_it() -> None:
    """The shell must be able to render every destination the manifest offers.

    Read out of `App.tsx` rather than duplicated here: a list of routes in this
    file would be a second copy, and the point of the manifest is that there is
    one. It checks every *tab* as well as every destination, because a tab with
    nothing behind it is a row in a tab bar that opens a blank screen -- exactly
    the fake functionality the product rules refuse, and easier to introduce now
    that a destination can carry a dozen of them.
    """
    source = APP_TSX.read_text("utf-8")
    keyed = set(re.findall(r"'([a-z_]+)/([a-z_]+)':", source))
    bare = set(re.findall(r"^\s{4}([a-z_]+): <", source, re.M))
    for destination in DESTINATIONS:
        if not destination.tabs:
            assert destination.route in bare, (
                f"{destination.route} is in the rail with no view behind it"
            )
            continue
        for tab in destination.tabs:
            assert (destination.route, tab.tab) in keyed, (
                f"{destination.route}?tab={tab.tab} is offered with no view behind it"
            )


def test_a_link_written_for_any_mode_rail_still_lands_somewhere_real() -> None:
    """Every route the three mode manifests ever offered resolves to a screen.

    Links outlive navigation. An artifact card minted last week points at
    `#evidence`, a bookmark points at `#orchestrator`, and a saved workspace
    sidebar can name either -- so the compatibility map is checked against the
    destinations rather than trusted.
    """
    current = {d.route for d in DESTINATIONS}
    shadowed = sorted(set(LEGACY_ROUTES) & current)
    assert not shadowed, (
        f"{shadowed} are listed as legacy *and* exist as destinations, so the map entry is "
        "dead configuration that will silently stop matching"
    )
    for legacy, target in LEGACY_ROUTES.items():
        landed = resolve(legacy)
        assert landed.redirected, f"{legacy} is listed as legacy but resolved as current"
        assert landed.route in current, (
            f"{legacy} -> {target} names a destination that does not exist"
        )
        destination = next(d for d in DESTINATIONS if d.route == landed.route)
        if destination.tabs:
            assert destination.tab(landed.tab) is not None, (
                f"{legacy} -> {target} names a tab {landed.route} does not have"
            )


def test_every_place_the_inbox_can_send_somebody_exists() -> None:
    """The Open button on a finished job must land on a screen.

    It did not. `destination()` answered `'#prop'` for a finished prop matrix,
    and `prop` is a *panel kind*, not a route -- so the link resolved to no
    destination and the shell sent the reader to Home, which is the failure the
    function's own docstring says is worse than offering no button. `#actions`
    sent a finished mission to the action registry rather than to the missions
    screen.

    Neither was catchable where it was written: `inbox.test.ts` asserted the
    literal string `'#prop'`, so the assertion held whatever that string meant.
    Reachability can only be checked against the manifest, which lives here, so
    the links are read out of the TypeScript the same way `App.tsx` is above.
    """
    source = INBOX_TS.read_text("utf-8")
    body = source[source.index("export function destination") :]
    body = body[: body.index("\n}\n")]

    links = set(re.findall(r"format\('([a-z_]+)',\s*'([a-z_]+)'", body))
    assert links, "no destinations were found, so this test is checking nothing"

    # A bare hash literal is how the broken ones were spelled. There is no
    # reason for one here: a route and a tab name the screen unambiguously,
    # and a literal cannot be checked by the type system or by this test.
    literals = set(re.findall(r"return\s+'(#[^']*)'", body))
    assert not literals, (
        f"{sorted(literals)} are hand-written hashes rather than a named "
        "destination and tab; that is how '#prop' reached a released build"
    )

    current = {d.route for d in DESTINATIONS}
    for route, tab in sorted(links):
        assert route in current, f"the inbox opens '{route}', which is not a destination"
        destination = next(d for d in DESTINATIONS if d.route == route)
        assert destination.tab(tab) is not None, (
            f"the inbox opens '{route}?tab={tab}', a tab {route} does not have"
        )
        landed = resolve(f"{route}?tab={tab}")
        assert (landed.route, landed.tab) == (route, tab), (
            f"the inbox's '{route}?tab={tab}' resolves to "
            f"'{landed.route}?tab={landed.tab}' instead"
        )
        assert not landed.redirected, (
            f"the inbox mints '{route}?tab={tab}', which this product has to translate"
        )


def test_only_ai_has_stances_and_it_defaults_to_the_cautious_one() -> None:
    """The stance belongs to the mode whose purpose is unattended work.

    It used to belong to Hedge Fund, which was never the reason it existed:
    autonomy is a property of running without a person, not of being a fund.
    """
    for mode in (WorkspaceMode.NORMAL, WorkspaceMode.PROP_FIRM):
        assert MODES[mode].stances == ()
        assert MODES[mode].default_stance is None
    ai = MODES[WorkspaceMode.AI]
    assert ai.stances == (Stance.HUMAN_IN_THE_LOOP, Stance.AUTONOMOUS)
    # The default is the one where a person is still in the way. A default that
    # ran unattended would make the safer choice the one you have to remember.
    assert ai.default_stance is Stance.HUMAN_IN_THE_LOOP


def test_a_stance_on_a_mode_that_has_none_is_refused_rather_than_ignored() -> None:
    """Dropping it silently is how a request to run autonomously looks honoured."""
    with pytest.raises(ValueError, match="no operating stances"):
        parse_stance(WorkspaceMode.NORMAL, "autonomous")
    with pytest.raises(ValueError, match="no stance 'sideways'"):
        parse_stance(WorkspaceMode.AI, "sideways")
    assert parse_stance(WorkspaceMode.AI, None) is Stance.HUMAN_IN_THE_LOOP


def test_descriptor_refuses_an_unknown_mode_with_the_valid_set() -> None:
    with pytest.raises(KeyError, match="normal, prop_firm, ai"):
        descriptor("day_trading")


def test_every_mode_states_its_limitations() -> None:
    """A mode that cannot do something says so where the operator is standing."""
    for mode in MODE_ORDER:
        assert MODES[mode].limitations, f"{mode.value} claims no limitations at all"


def test_every_surface_the_removed_modes_carried_is_still_reachable() -> None:
    """"Remove the label, keep the capability" as a fact rather than an intention.

    Every route below had a view behind it under the mode navigation — the Hedge
    Fund loop that moved into Normal, the oversight surfaces that moved into AI,
    and the research and data screens the surviving modes carried. The mode rail
    is gone now and these are tabs, so the check is *reachability*: the link
    still resolves, and it resolves to a tab that exists. Something dropped in
    the move shows up here as a feature that quietly stopped existing, which is
    exactly the failure a product-hierarchy change invites.
    """
    carried = (
        # the deterministic book loop
        "portfolio", "risk", "gate", "execution", "operations", "book", "positions",
        "performance",
        # oversight, beside the actor it watches
        "orchestrator", "approvals", "audit", "actions", "activity",
        # research and data
        "data", "research", "lab", "memory", "validation", "evidence", "experiments",
        "lineage", "missions", "research_control", "pipeline", "campaigns",
        # the prop desk
        "account", "rules", "drawdown", "daily", "target", "desk", "allocation", "copy",
        "limits", "risk_management", "ai_management", "news", "desk_activity", "simulation",
        # everything else the rails offered
        "overview", "workspace", "charts", "trades", "strategies", "runs", "assistant",
        "agents", "settings",
    )
    by_route = {d.route: d for d in DESTINATIONS}
    for route in carried:
        landed = resolve(route)
        destination = by_route.get(landed.route)
        assert destination is not None, f"'{route}' no longer resolves to a destination"
        if destination.tabs:
            assert destination.tab(landed.tab) is not None, (
                f"'{route}' resolves to {landed.route}?tab={landed.tab}, which is not a tab"
            )


def test_the_chat_is_reachable_without_choosing_anything_first() -> None:
    """AI is horizontal. It was a mode, and a mode is the opposite of horizontal."""
    routes = {d.route for d in DESTINATIONS}
    assert "chat" in routes
    assert resolve("assistant").route == "chat", "the old AI Workspace link lost its home"
    chat = next(d for d in DESTINATIONS if d.route == "chat")
    assert "ai" not in chat.label.lower(), "Chat is a conversation, not a mode"


def test_the_rail_is_materially_shorter_than_the_three_it_replaced() -> None:
    """Sixty-two rail entries across three modes became nine.

    A number in a test is usually a smell. This one is the product requirement:
    the rail was the complexity, and a change that reorganised it without
    shortening it would pass every other check here.
    """
    assert len(DESTINATIONS) <= 10, f"the rail has grown back to {len(DESTINATIONS)} rows"
    assert len(LEGACY_ROUTES) > 40, "the compatibility map has lost links it used to carry"


def test_the_book_loop_admits_what_is_not_installed() -> None:
    """The disclaimers moved with the surfaces they disclaim."""
    text = " ".join(MODES[WorkspaceMode.NORMAL].limitations).lower()
    assert "simulated" in text
    assert "factor model" in text
    ai = " ".join(MODES[WorkspaceMode.AI].limitations).lower()
    assert "simulator" in ai, "an autonomously submitted order must say where it goes"
