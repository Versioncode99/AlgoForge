"""The four manifests: what they promise, and whether anything backs it.

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
from forge.workstation import TEMPLATES, PanelKind

ROOT = Path(__file__).resolve().parents[2]
APP_TSX = ROOT / "apps" / "web" / "src" / "App.tsx"


def test_four_modes_in_a_deliberate_order() -> None:
    assert [mode.value for mode in MODE_ORDER] == ["normal", "prop_firm", "ai", "hedge_fund"]
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


@pytest.mark.parametrize("mode", MODE_ORDER, ids=lambda m: m.value)
def test_every_route_has_a_view_behind_it(mode: WorkspaceMode) -> None:
    """The shell must be able to render every destination the manifest offers.

    Read out of `App.tsx` rather than duplicated here: a list of routes in this
    file would be a third copy, and the point of the manifest is that there is
    one.
    """
    source = APP_TSX.read_text("utf-8")
    per_mode = set(re.findall(r"'([a-z_]+)/([a-z_]+)':", source))
    shared = set(re.findall(r"^\s{4}([a-z_]+): <", source, re.M))
    for section in MODES[mode].sections:
        assert (mode.value, section.route) in per_mode or section.route in shared, (
            f"{mode.value}/{section.route} appears in the rail with no view behind it"
        )


def test_only_hedge_fund_has_stances_and_it_defaults_to_the_cautious_one() -> None:
    for mode in (WorkspaceMode.NORMAL, WorkspaceMode.PROP_FIRM, WorkspaceMode.AI):
        assert MODES[mode].stances == ()
        assert MODES[mode].default_stance is None
    fund = MODES[WorkspaceMode.HEDGE_FUND]
    assert fund.stances == (Stance.HUMAN_IN_THE_LOOP, Stance.AUTONOMOUS)
    # The default is the one where a person is still in the way. A default that
    # ran unattended would make the safer choice the one you have to remember.
    assert fund.default_stance is Stance.HUMAN_IN_THE_LOOP


def test_a_stance_on_a_mode_that_has_none_is_refused_rather_than_ignored() -> None:
    """Dropping it silently is how a request to run autonomously looks honoured."""
    with pytest.raises(ValueError, match="no operating stances"):
        parse_stance(WorkspaceMode.NORMAL, "autonomous")
    with pytest.raises(ValueError, match="no stance 'sideways'"):
        parse_stance(WorkspaceMode.HEDGE_FUND, "sideways")
    assert parse_stance(WorkspaceMode.HEDGE_FUND, None) is Stance.HUMAN_IN_THE_LOOP


def test_descriptor_refuses_an_unknown_mode_with_the_valid_set() -> None:
    with pytest.raises(KeyError, match="normal, prop_firm, ai, hedge_fund"):
        descriptor("day_trading")


def test_every_mode_states_its_limitations() -> None:
    """A mode that cannot do something says so where the operator is standing."""
    for mode in MODE_ORDER:
        assert MODES[mode].limitations, f"{mode.value} claims no limitations at all"


def test_the_fund_mode_admits_what_is_not_installed() -> None:
    text = " ".join(MODES[WorkspaceMode.HEDGE_FUND].limitations).lower()
    assert "simulated" in text
    assert "factor model" in text
    assert "point-in-time" in text
