"""Removing the mode chooser did not widen what an assistant may do.

This is the check the whole migration rests on. The three modes carried
navigation *and* authority; navigation moved to `forge.product.navigation` and
authority moved to `forge.product.authority`, and the risk in that split is
that the second half quietly arrives more permissive than it left — an
assistant that could not start a campaign unattended in Normal mode being able
to, because nobody is standing in a mode any more.

So each profile is compared against the legacy pair it claims to stand for,
written out here rather than read back from the profile, across the whole input
space of the evaluator: every classified and unclassified action name, both
mutating and not, at all three risk levels, protected and not. That is a
stronger statement than enumerating the registry as it happens to be today.
"""

from __future__ import annotations

import pytest
from forge.modes.models import Stance, WorkspaceMode
from forge.modes.permissions import (
    AUTOMATION,
    CONSEQUENTIAL,
    PREPARATORY,
    ActionFacts,
    Actor,
    Ruling,
    evaluate,
)
from forge.product.authority import DEFAULT, PROFILES, AuthorityError, AuthorityProfile

#: The evaluator's whole input space, for the names that can change its answer.
SWEEP: list[ActionFacts] = [
    ActionFacts(name=name, mutating=mutating, risk=risk, protected=protected)
    for name in sorted(PREPARATORY | AUTOMATION | CONSEQUENTIAL | {"an_unclassified_verb"})
    for mutating in (True, False)
    for risk in ("safe", "confirm", "high")
    for protected in (True, False)
]

#: Each profile, and the `(mode, stance)` it is replacing. Spelled out rather
#: than taken from `to_mode()`, so this asserts the mapping instead of restating
#: it.
REPLACES: list[tuple[AuthorityProfile, WorkspaceMode, Stance | None]] = [
    (AuthorityProfile(), WorkspaceMode.NORMAL, None),
    (AuthorityProfile(unattended_work=True), WorkspaceMode.AI, Stance.HUMAN_IN_THE_LOOP),
    (
        AuthorityProfile(unattended_work=True, unattended_execution=True),
        WorkspaceMode.AI,
        Stance.AUTONOMOUS,
    ),
]


def _ruling(facts: ActionFacts, mode: WorkspaceMode, stance: Stance | None) -> Ruling:
    return evaluate(facts, actor=Actor.AI, mode=mode, stance=stance).ruling


@pytest.mark.parametrize(("profile", "mode", "stance"), REPLACES, ids=lambda v: str(v))
def test_each_profile_rules_exactly_as_the_mode_it_replaced(
    profile: AuthorityProfile, mode: WorkspaceMode, stance: Stance | None
) -> None:
    assert profile.to_mode() == (mode, stance)
    for facts in SWEEP:
        assert _ruling(facts, *profile.to_mode()) == _ruling(facts, mode, stance), facts.name


def test_nothing_reaches_the_book_without_the_second_explicit_grant() -> None:
    facts = ActionFacts(name="submit_orders", mutating=True, risk="safe")
    for profile in PROFILES:
        expected = Ruling.ALLOW if profile.unattended_execution else Ruling.REQUIRE_APPROVAL
        assert _ruling(facts, *profile.to_mode()) is expected


def test_every_reachable_configuration_is_offered_and_no_more() -> None:
    """Three profiles for three reachable configurations. Not two, not four."""
    assert len(PROFILES) == 3
    assert {p.to_mode() for p in PROFILES} == {
        (WorkspaceMode.NORMAL, None),
        (WorkspaceMode.AI, Stance.HUMAN_IN_THE_LOOP),
        (WorkspaceMode.AI, Stance.AUTONOMOUS),
    }


def test_the_default_is_the_posture_the_product_already_had() -> None:
    """"No mode entered" was AI mode on no stance, not Normal.

    `Actions.context()` says so in as many words, and it is what a headless
    agent run has always had. Defaulting to the narrowest profile would have
    been a silent *narrowing* -- safe in direction, and still a change nobody
    asked for, which would have stopped the research engine starting a campaign
    on its own.
    """
    # Compared by ruling rather than by tuple. The default names the stance
    # `human_in_the_loop` where `context()` left it `None`, and the evaluator
    # only ever asks whether the stance *is* `AUTONOMOUS`, so the two are the
    # same posture with one of them written down.
    assert DEFAULT.to_mode()[0] is WorkspaceMode.AI
    assert DEFAULT.to_mode()[1] is not Stance.AUTONOMOUS
    for facts in SWEEP:
        assert _ruling(facts, *DEFAULT.to_mode()) == _ruling(facts, WorkspaceMode.AI, None), (
            facts.name
        )
    assert not DEFAULT.unattended_execution, "the book is not reachable by default"


def test_prop_firm_enforced_identically_to_normal_so_collapsing_them_loses_nothing() -> None:
    """Three modes, two authorities. Prop Firm never differed in *enforcement*.

    It differed in `summarise`'s wording, which is copy. If this ever stops
    being true, collapsing them stops being safe, and this is where that is
    found out rather than in production.
    """
    for facts in SWEEP:
        assert _ruling(facts, WorkspaceMode.NORMAL, None) == _ruling(
            facts, WorkspaceMode.PROP_FIRM, None
        ), facts.name


def test_authority_only_ever_widens_in_the_direction_it_says_it_does() -> None:
    order = {Ruling.DENY: 0, Ruling.REQUIRE_APPROVAL: 1, Ruling.ALLOW: 2}
    for facts in SWEEP:
        rulings = [order[_ruling(facts, *p.to_mode())] for p in PROFILES]
        assert rulings == sorted(rulings), (
            f"{facts.name} is not monotonic across the profiles: {rulings}"
        )


def test_execution_without_unattended_work_is_refused_rather_than_normalised() -> None:
    with pytest.raises(AuthorityError):
        AuthorityProfile(unattended_execution=True)


@pytest.mark.parametrize(
    ("mode", "stance", "expected"),
    [
        (None, None, (True, False)),
        ("normal", None, (False, False)),
        ("prop_firm", None, (False, False)),
        ("ai", None, (True, False)),
        ("ai", "human_in_the_loop", (True, False)),
        ("ai", "autonomous", (True, True)),
    ],
)
def test_a_stored_session_migrates_to_the_authority_it_was_left_with(
    mode: str | None, stance: str | None, expected: tuple[bool, bool]
) -> None:
    """A session written before the chooser was removed comes back unchanged.

    Including the autonomous one. Silently narrowing it would be safe and still
    wrong: the operator opted in, and an opt-in that evaporates on upgrade is a
    setting nobody can rely on.
    """
    profile = AuthorityProfile.from_mode(mode, stance)
    assert (profile.unattended_work, profile.unattended_execution) == expected


def test_protected_controls_stay_denied_on_every_profile() -> None:
    """The escape route, closed at the only place it could open."""
    for name in ("enter_mode", "set_stance", "leave_mode", "set_fund_config"):
        facts = ActionFacts(name=name, mutating=True, risk="confirm", protected=True)
        for profile in PROFILES:
            assert _ruling(facts, *profile.to_mode()) is Ruling.DENY


def test_high_risk_stays_denied_on_every_profile() -> None:
    """There is no live path today. This is what keeps that true on the day there is."""
    facts = ActionFacts(name="place_live_order", mutating=True, risk="high")
    for profile in PROFILES:
        assert _ruling(facts, *profile.to_mode()) is Ruling.DENY
