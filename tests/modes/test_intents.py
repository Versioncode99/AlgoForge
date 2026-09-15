"""The front door maps onto registered actions, not onto a language model."""

from __future__ import annotations

import pytest
from forge.modes.intents import INTENTS, Intent, catalogue, descriptor, for_mode
from forge.modes.models import WorkspaceMode
from forge.product.navigation import DESTINATIONS, resolve


class TestTheMapIsReal:
    def test_every_intent_names_registered_actions(self, tmp_path, monkeypatch) -> None:
        # An intent offering a verb that was renamed is a dead end the operator
        # discovers by clicking it.
        monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
        from forge_api.main import create_app

        registered = set(create_app(tmp_path / "intents.db").state.actions.names())
        for item in INTENTS.values():
            missing = [name for name in item.actions if name not in registered]
            assert not missing, f"{item.intent.value} names unregistered actions: {missing}"

    def test_every_intent_lands_on_a_destination_that_exists(self) -> None:
        """A front-door button that opens a blank screen is worse than no button.

        This used to be checked per mode, because the same intent landed in
        different places depending on which rail was drawn. There is one
        navigation now, so the check is against the destinations themselves --
        including the tab, since a link naming a tab a destination does not have
        lands on its default and quietly shows the reader something else.
        """
        by_route = {d.route: d for d in DESTINATIONS}
        for item in INTENTS.values():
            assert item.links, f"{item.intent.value} goes nowhere"
            for link in item.links:
                landed = resolve(link)
                destination = by_route.get(landed.route)
                assert destination is not None, f"{item.intent.value} names '{link}'"
                if destination.tabs:
                    assert destination.tab(landed.tab) is not None, (
                        f"{item.intent.value} names '{link}', which is not a tab of "
                        f"{landed.route}"
                    )

    def test_no_intent_reaches_a_destination_by_redirect(self) -> None:
        """The map names current links, not links that still happen to resolve.

        A legacy route in this file would keep working and would stop being
        maintained, which is how a front door comes to point at the shape the
        product used to have.
        """
        for item in INTENTS.values():
            stale = [link for link in item.links if resolve(link).redirected]
            assert not stale, f"{item.intent.value} still names the old routes {stale}"


class TestTheOfferedSet:
    def test_the_specifications_seven_intents_are_all_present(self) -> None:
        labels = {item.label.lower() for item in INTENTS.values()}
        assert labels == {
            "find a strategy",
            "test an idea",
            "analyse a strategy",
            "improve a strategy",
            "manage prop accounts",
            "run a strategy",
            "research the market",
        }

    def test_prop_account_management_is_offered_without_choosing_a_mode_first(self) -> None:
        """The Prop Desk is a destination everybody has.

        It used to be a mode, so this intent used to be hidden from anybody who
        had not declared themselves a prop trader before opening the product.
        Hiding a destination that exists is not simplification; it is a second
        navigation nobody can see.
        """
        assert Intent.MANAGE_PROP_ACCOUNTS in {item.intent for item in INTENTS.values()}
        for link in descriptor(Intent.MANAGE_PROP_ACCOUNTS).links:
            assert resolve(link).route == "propdesk"

    def test_every_mode_offers_at_least_one_intent(self) -> None:
        for mode in WorkspaceMode:
            assert for_mode(mode), f"{mode.value} has no front door"


class TestHonesty:
    def test_the_execution_intents_say_execution_is_simulated(self) -> None:
        # The caveat belongs at the front door, not three screens in.
        for intent in (Intent.RUN_A_STRATEGY, Intent.MANAGE_PROP_ACCOUNTS):
            assert "simulat" in descriptor(intent).caveat.lower()

    def test_the_literature_intent_does_not_imply_a_papers_result_transfers(self) -> None:
        caveat = descriptor(Intent.FIND_A_STRATEGY).caveat.lower()
        assert "will not report it as validated" in caveat

    def test_the_improvement_intent_names_the_multiple_testing_cost(self) -> None:
        caveat = descriptor(Intent.IMPROVE_A_STRATEGY).caveat.lower()
        assert "raises the bar" in caveat and "g5" in caveat

    def test_no_intent_promises_a_result(self) -> None:
        for item in INTENTS.values():
            text = f"{item.detail} {item.caveat}".lower()
            for banned in ("profitable", "guarantee", "will make", "best strategy"):
                assert banned not in text, f"{item.intent.value} promises: {banned}"


class TestLookup:
    def test_an_unknown_intent_raises_with_the_valid_set(self) -> None:
        with pytest.raises(KeyError, match="find_a_strategy"):
            descriptor("become_rich")

    def test_the_catalogue_still_answers_a_mode_but_no_longer_narrows_by_one(self) -> None:
        """The parameter survives; what it used to filter does not.

        A client built against `/modes/intents?mode=prop_firm` keeps working,
        and gets every intent -- which is the truth now, because every
        destination is reachable from everywhere.
        """
        everything = catalogue()
        assert catalogue(WorkspaceMode.PROP_FIRM) == everything
        assert all("prop_firm" in item["modes"] for item in everything)

    def test_an_unknown_mode_is_still_refused_rather_than_ignored(self) -> None:
        with pytest.raises(ValueError, match="day_trading"):
            catalogue("day_trading")
