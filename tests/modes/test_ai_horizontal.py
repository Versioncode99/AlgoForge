"""AI as a horizontal capability, and AI as a permission boundary.

Doc 2 §2 wants AI horizontal rather than a separate environment.
`docs/AI_HORIZONTAL_DETERMINATION.md` argues that the capability already is and
the boundary cannot be, without weakening what an assistant may do unattended.
Both halves of that argument are asserted here, so the document fails with the
code rather than ageing quietly beside it.
"""

from __future__ import annotations

import pytest
from forge.modes.models import MODE_ORDER, Stance, WorkspaceMode, catalogue
from forge.modes.permissions import (
    AUTOMATION,
    CONSEQUENTIAL,
    ActionFacts,
    Actor,
    Ruling,
    evaluate,
)


def sections(mode_payload: dict[str, object]) -> list[dict[str, object]]:
    return list(mode_payload.get("sections", []))  # type: ignore[arg-type]


# ── the capability is horizontal ──────────────────────────────────────────────


def test_every_mode_can_reach_the_assistant() -> None:
    """A conversation is not something one environment has and the others do not."""
    for payload in catalogue():
        routes = {str(section.get("route")) for section in sections(payload)}
        assert "assistant" in routes, f"{payload.get('key')} cannot reach the assistant"


def test_every_mode_can_place_an_agent_panel() -> None:
    for payload in catalogue():
        kinds: set[str] = set()
        for section in sections(payload):
            kinds |= {str(kind) for kind in section.get("panel_kinds", ())}  # type: ignore[union-attr]
        assert "agent" in kinds, f"{payload.get('key')} cannot place an agent panel"


def test_there_is_one_action_registry_and_no_ai_only_verb(tmp_path, monkeypatch) -> None:
    """The interface and an assistant call the same implementation of everything."""
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    from forge_api.main import create_app

    app = create_app(tmp_path / "horizontal.db")
    names = set(app.state.actions._registry)
    assert names, "no actions registered at all"
    assert not {name for name in names if name.startswith("ai_")}, (
        "an ai-prefixed verb would be a second implementation reachable from one mode"
    )


# ── the mode is the boundary ──────────────────────────────────────────────────


def _facts(name: str) -> ActionFacts:
    return ActionFacts(name=name, mutating=True, risk="safe", protected=False)


@pytest.mark.parametrize("name", sorted(AUTOMATION))
def test_unattended_work_is_granted_by_the_mode_and_by_nothing_else(name: str) -> None:
    allowed = evaluate(_facts(name), actor=Actor.AI, mode=WorkspaceMode.AI, stance=None)
    assert allowed.ruling is Ruling.ALLOW

    for mode in (WorkspaceMode.NORMAL, WorkspaceMode.PROP_FIRM):
        held = evaluate(_facts(name), actor=Actor.AI, mode=mode, stance=None)
        assert held.ruling is Ruling.REQUIRE_APPROVAL, (
            f"{mode.value} grants '{name}' unattended, so AI mode is not the boundary"
        )


@pytest.mark.parametrize("name", sorted(CONSEQUENTIAL))
def test_the_book_is_reachable_only_in_ai_mode_on_the_autonomous_stance(name: str) -> None:
    allowed = evaluate(
        _facts(name), actor=Actor.AI, mode=WorkspaceMode.AI, stance=Stance.AUTONOMOUS
    )
    assert allowed.ruling is Ruling.ALLOW

    # Same mode, attended stance: held.
    attended = evaluate(
        _facts(name), actor=Actor.AI, mode=WorkspaceMode.AI, stance=Stance.HUMAN_IN_THE_LOOP
    )
    assert attended.ruling is Ruling.REQUIRE_APPROVAL

    # Every other mode, on any stance: held. This is the assertion that would
    # fail if autonomy became an axis orthogonal to the mode.
    for mode in (WorkspaceMode.NORMAL, WorkspaceMode.PROP_FIRM):
        for stance in (None, Stance.HUMAN_IN_THE_LOOP, Stance.AUTONOMOUS):
            held = evaluate(_facts(name), actor=Actor.AI, mode=mode, stance=stance)
            assert held.ruling is Ruling.REQUIRE_APPROVAL, (
                f"{mode.value}/{stance} reaches the book, which would widen what is "
                "reachable in exactly the direction Doc 1 §37 forbids"
            )


def test_removing_the_mode_would_remove_the_grants() -> None:
    """Stated as an assertion rather than as prose in the determination.

    If AI stops being one of the modes, the two rules that read it have nothing
    to read, and every automation and book-reaching action falls through to the
    unclassified default -- held. That is the sense in which the mode *is* the
    boundary.
    """
    assert WorkspaceMode.AI in set(MODE_ORDER)
    assert AUTOMATION, "no automation actions, so rule 7 grants nothing"
    assert CONSEQUENTIAL, "no consequential actions, so rule 8 grants nothing"
