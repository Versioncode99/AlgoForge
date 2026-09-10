"""The AI permission policy: what it allows, what it holds, and what it never permits.

The policy is the only thing standing between an agent's tool call and a
deterministic control, so these tests are written as adversarially as the rules
are. Several of them describe an escape route rather than a feature — the one
that matters most is `test_ai_cannot_widen_its_own_permissions`, because every
other rule here is worth exactly nothing if that one fails.
"""

from __future__ import annotations

import pytest
from forge.modes import MODE_ORDER, MODES, Stance, WorkspaceMode
from forge.modes.permissions import (
    AUTOMATION,
    CONSEQUENTIAL,
    PREPARATORY,
    ActionFacts,
    Actor,
    Ruling,
    evaluate,
    summarise,
)

SAFE_READ = ActionFacts("list_strategies", mutating=False, risk="safe")
PREP = ActionFacts("backtest_strategy", mutating=True, risk="safe")
AUTOMATE = ActionFacts("start_engine", mutating=True, risk="safe")
BOOK = ActionFacts("submit_orders", mutating=True, risk="safe")
PROTECTED = ActionFacts("set_fund_config", mutating=True, risk="safe", protected=True)
DESTRUCTIVE = ActionFacts("delete_workspace", mutating=True, risk="confirm")
LIVE = ActionFacts("place_live_order", mutating=True, risk="high")

STANCES: list[tuple[WorkspaceMode, Stance | None]] = [
    (mode, stance)
    for mode in MODE_ORDER
    for stance in (MODES[mode].stances or (None,))
]


def rule(facts: ActionFacts, mode: WorkspaceMode, stance: Stance | None = None) -> Ruling:
    return evaluate(facts, actor=Actor.AI, mode=mode, stance=stance).ruling


# ── what no configuration permits ────────────────────────────────────────────


@pytest.mark.parametrize(("mode", "stance"), STANCES, ids=lambda v: getattr(v, "value", "-"))
def test_protected_controls_are_denied_in_every_mode_and_stance(
    mode: WorkspaceMode, stance: Stance | None
) -> None:
    assert rule(PROTECTED, mode, stance) is Ruling.DENY


@pytest.mark.parametrize(("mode", "stance"), STANCES, ids=lambda v: getattr(v, "value", "-"))
def test_high_risk_is_denied_in_every_mode_and_stance(
    mode: WorkspaceMode, stance: Stance | None
) -> None:
    """There is no live path today. This is what keeps that true on the day there is."""
    assert rule(LIVE, mode, stance) is Ruling.DENY


def test_ai_cannot_widen_its_own_permissions() -> None:
    """The one escape route, closed at the only place it could open.

    An agent that could switch to the autonomous stance could grant itself
    everything that stance carries, so the actions that carry mode and stance
    are protected — and protection is checked before anything else about them.
    """
    for name in ("enter_mode", "set_stance", "leave_mode"):
        facts = ActionFacts(name, mutating=True, risk="safe", protected=True)
        for mode, stance in STANCES:
            judgement = evaluate(facts, actor=Actor.AI, mode=mode, stance=stance)
            assert judgement.ruling is Ruling.DENY
            assert "protected" in judgement.reason


def test_the_autonomous_stance_does_not_unlock_a_protected_control() -> None:
    """Autonomy moves the approval gate. It does not remove the deterministic ones."""
    autonomous = rule(PROTECTED, WorkspaceMode.HEDGE_FUND, Stance.AUTONOMOUS)
    supervised = rule(PROTECTED, WorkspaceMode.HEDGE_FUND, Stance.HUMAN_IN_THE_LOOP)
    assert autonomous is supervised is Ruling.DENY


# ── the four profiles are genuinely different ────────────────────────────────


def test_reading_is_free_everywhere() -> None:
    """An assistant that must ask permission to look cannot report honestly."""
    for mode, stance in STANCES:
        assert rule(SAFE_READ, mode, stance) is Ruling.ALLOW


def test_preparation_is_permitted_everywhere() -> None:
    """Requiring approval to run a backtest is not assistance."""
    for mode, stance in STANCES:
        assert rule(PREP, mode, stance) is Ruling.ALLOW


def test_automation_belongs_to_the_two_modes_that_exist_to_run_it() -> None:
    assert rule(AUTOMATE, WorkspaceMode.NORMAL) is Ruling.REQUIRE_APPROVAL
    assert rule(AUTOMATE, WorkspaceMode.PROP_FIRM) is Ruling.REQUIRE_APPROVAL
    assert rule(AUTOMATE, WorkspaceMode.AI) is Ruling.ALLOW
    assert rule(AUTOMATE, WorkspaceMode.HEDGE_FUND, Stance.HUMAN_IN_THE_LOOP) is Ruling.ALLOW


def test_reaching_the_book_needs_the_autonomous_stance_and_nothing_less() -> None:
    for mode in (WorkspaceMode.NORMAL, WorkspaceMode.PROP_FIRM, WorkspaceMode.AI):
        assert rule(BOOK, mode) is Ruling.REQUIRE_APPROVAL
    assert rule(BOOK, WorkspaceMode.HEDGE_FUND, Stance.HUMAN_IN_THE_LOOP) is Ruling.REQUIRE_APPROVAL
    assert rule(BOOK, WorkspaceMode.HEDGE_FUND, Stance.AUTONOMOUS) is Ruling.ALLOW


def test_the_four_modes_do_not_all_rule_the_same_way() -> None:
    """If every mode produced the same answers, the modes would be labels."""
    profiles = {
        (mode, stance): tuple(
            rule(facts, mode, stance) for facts in (PREP, AUTOMATE, BOOK, PROTECTED)
        )
        for mode, stance in STANCES
    }
    assert len(set(profiles.values())) >= 3, profiles


def test_destroying_work_needs_a_person_even_on_the_autonomous_stance() -> None:
    """Autonomy is about running the loop unattended, not deleting things unattended."""
    for mode, stance in STANCES:
        assert rule(DESTRUCTIVE, mode, stance) is Ruling.REQUIRE_APPROVAL


def test_an_unclassified_mutating_action_is_held_rather_than_allowed() -> None:
    """A new verb becomes available to AI when somebody lists it, not by default."""
    unknown = ActionFacts("some_new_verb", mutating=True, risk="safe")
    for mode, stance in STANCES:
        assert rule(unknown, mode, stance) is Ruling.REQUIRE_APPROVAL


# ── the operator is not governed by this ─────────────────────────────────────


def test_a_person_is_not_restricted_by_the_ai_policy() -> None:
    for facts in (SAFE_READ, PREP, AUTOMATE, BOOK, PROTECTED, DESTRUCTIVE, LIVE):
        for mode, stance in STANCES:
            judgement = evaluate(facts, actor=Actor.HUMAN, mode=mode, stance=stance)
            assert judgement.ruling is Ruling.ALLOW


# ── refusals name themselves ─────────────────────────────────────────────────


def test_every_refusal_carries_a_reason_naming_the_rule_that_fired() -> None:
    for facts in (AUTOMATE, BOOK, PROTECTED, DESTRUCTIVE, LIVE):
        for mode, stance in STANCES:
            judgement = evaluate(facts, actor=Actor.AI, mode=mode, stance=stance)
            if judgement.ruling is Ruling.ALLOW:
                continue
            assert len(judgement.reason) > 25, judgement
            assert judgement.as_dict()["mode"] == mode.value


def test_the_summary_says_something_different_for_each_stance() -> None:
    supervised = summarise(WorkspaceMode.HEDGE_FUND, Stance.HUMAN_IN_THE_LOOP)["summary"]
    autonomous = summarise(WorkspaceMode.HEDGE_FUND, Stance.AUTONOMOUS)["summary"]
    assert supervised != autonomous
    assert "approval" in str(supervised).lower()
    assert "kill switch" in str(autonomous).lower()


# ── the allowlists describe actions that exist ───────────────────────────────


def test_every_allowlisted_name_is_a_registered_action(tmp_path, monkeypatch) -> None:
    """An allowlist entry for a verb that no longer exists is worse than useless.

    It pre-authorises whatever takes that name next, which is how a renamed
    action silently inherits permission it was never granted.
    """
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    from forge_api.main import create_app

    app = create_app(tmp_path / "permissions.db")
    registered = set(app.state.actions.names())
    for name, listing in (
        ("PREPARATORY", PREPARATORY),
        ("AUTOMATION", AUTOMATION),
        ("CONSEQUENTIAL", CONSEQUENTIAL),
    ):
        missing = sorted(listing - registered)
        assert not missing, f"{name} allowlists actions that do not exist: {missing}"


def test_the_three_allowlists_do_not_overlap() -> None:
    """An action in two tiers is an action whose permission depends on rule order."""
    assert not PREPARATORY & AUTOMATION
    assert not PREPARATORY & CONSEQUENTIAL
    assert not AUTOMATION & CONSEQUENTIAL
