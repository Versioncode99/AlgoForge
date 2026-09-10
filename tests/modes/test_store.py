"""The mode session: what is open, and what each mode remembers.

The per-mode workspace pointer is the point. Without it, switching from Hedge
Fund to Normal and back reopens whichever layout happened to be active globally,
which reads to the operator as their fund workspace having been replaced by a
chart.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from forge.modes import ModeStore, Stance, WorkspaceMode


@pytest.fixture
def store(tmp_path: Path) -> ModeStore:
    return ModeStore(tmp_path / "modes.db")


def test_nothing_is_open_before_a_choice_is_made(store: ModeStore) -> None:
    """The product opens on the chooser; defaulting would hide three of four."""
    session = store.session()
    assert session.mode is None and session.stance is None and session.workspace_id is None


def test_entering_a_mode_records_it_and_its_stance(store: ModeStore) -> None:
    store.enter(WorkspaceMode.HEDGE_FUND, Stance.AUTONOMOUS)
    session = store.session()
    assert session.mode is WorkspaceMode.HEDGE_FUND
    assert session.stance is Stance.AUTONOMOUS


def test_a_mode_with_no_stances_records_none(store: ModeStore) -> None:
    store.enter(WorkspaceMode.NORMAL)
    assert store.session().stance is None


def test_entering_the_fund_without_a_stance_takes_the_cautious_default(
    store: ModeStore,
) -> None:
    store.enter(WorkspaceMode.HEDGE_FUND)
    assert store.session().stance is Stance.HUMAN_IN_THE_LOOP


def test_a_stance_on_a_mode_that_has_none_is_refused(store: ModeStore) -> None:
    with pytest.raises(ValueError, match="no operating stances"):
        store.enter(WorkspaceMode.NORMAL, Stance.AUTONOMOUS)


def test_each_mode_remembers_its_own_layout_across_a_switch(store: ModeStore) -> None:
    store.enter(WorkspaceMode.NORMAL)
    store.remember_workspace(WorkspaceMode.NORMAL, "ws_normal")
    store.enter(WorkspaceMode.HEDGE_FUND)
    store.remember_workspace(WorkspaceMode.HEDGE_FUND, "ws_fund")

    assert store.session().workspace_id == "ws_fund"
    store.enter(WorkspaceMode.NORMAL)
    assert store.session().workspace_id == "ws_normal"
    # Switching away did not disturb what the other mode holds.
    assert store.workspace_for(WorkspaceMode.HEDGE_FUND) == "ws_fund"


def test_autonomy_is_never_resumed_implicitly(store: ModeStore) -> None:
    """A layout is remembered; a stance is asked for.

    Restoring the autonomous stance because somebody clicked back into the mode
    would mean the machine resumed reaching the book unattended without anybody
    saying so this time. The workspace pointer is a convenience; this is not.
    """
    store.enter(WorkspaceMode.HEDGE_FUND, Stance.AUTONOMOUS)
    assert store.session().stance is Stance.AUTONOMOUS
    store.enter(WorkspaceMode.NORMAL)
    store.enter(WorkspaceMode.HEDGE_FUND)
    assert store.session().stance is Stance.HUMAN_IN_THE_LOOP


def test_the_stance_can_be_changed_from_inside_the_mode(store: ModeStore) -> None:
    store.enter(WorkspaceMode.HEDGE_FUND)
    assert store.set_stance(WorkspaceMode.HEDGE_FUND, Stance.AUTONOMOUS) is Stance.AUTONOMOUS
    assert store.session().stance is Stance.AUTONOMOUS


def test_leaving_clears_what_is_open_and_forgets_nothing_else(store: ModeStore) -> None:
    store.enter(WorkspaceMode.PROP_FIRM)
    store.remember_workspace(WorkspaceMode.PROP_FIRM, "ws_prop")
    assert store.leave().mode is None
    assert store.workspace_for(WorkspaceMode.PROP_FIRM) == "ws_prop"


def test_a_deleted_layout_is_forgotten_by_every_mode_pointing_at_it(
    store: ModeStore,
) -> None:
    """Three modes pointing at a deleted workspace is three modes opening on nothing."""
    for mode in (WorkspaceMode.NORMAL, WorkspaceMode.AI):
        store.remember_workspace(mode, "shared")
    store.forget_workspace("shared")
    assert store.workspace_for(WorkspaceMode.NORMAL) is None
    assert store.workspace_for(WorkspaceMode.AI) is None


def test_an_unrecognised_stored_mode_lands_on_the_chooser_rather_than_raising(
    store: ModeStore,
) -> None:
    """A row written by a build that knew a mode this one does not."""
    import sqlite3

    with sqlite3.connect(store.path) as db:
        db.execute("INSERT OR REPLACE INTO mode_state VALUES ('mode', 'day_trading')")
    assert store.active_mode() is None


def test_an_unrecognised_stored_stance_falls_back_to_the_default_not_the_permissive_one(
    store: ModeStore,
) -> None:
    import sqlite3

    store.enter(WorkspaceMode.HEDGE_FUND)
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT OR REPLACE INTO mode_stance VALUES ('hedge_fund', 'unbounded')")
    assert store.stance_for(WorkspaceMode.HEDGE_FUND) is Stance.HUMAN_IN_THE_LOOP


def test_the_session_survives_a_restart(tmp_path: Path) -> None:
    """A restart is not a re-entry: the stance the operator chose is still in force."""
    path = tmp_path / "modes.db"
    ModeStore(path).enter(WorkspaceMode.HEDGE_FUND, Stance.AUTONOMOUS)
    reopened = ModeStore(path).session()
    assert reopened.mode is WorkspaceMode.HEDGE_FUND
    assert reopened.stance is Stance.AUTONOMOUS
