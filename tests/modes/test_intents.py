"""The front door maps onto registered actions, not onto a language model."""

from __future__ import annotations

import pytest
from forge.modes.intents import INTENTS, Intent, catalogue, descriptor, for_mode
from forge.modes.models import MODES, WorkspaceMode


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

    def test_every_intent_names_sections_that_exist_in_its_modes(self) -> None:
        for item in INTENTS.values():
            for mode in item.modes:
                routes = {section.route for section in MODES[mode].sections}
                missing = [
                    route for route in item.sections_for(mode) if route not in routes
                ]
                assert not missing, (
                    f"{item.intent.value} sends a {mode.value} operator to {missing}, "
                    "which that mode does not have"
                )

    def test_every_intent_names_at_least_one_mode(self) -> None:
        for item in INTENTS.values():
            assert item.modes
            for mode in item.modes:
                assert item.sections_for(mode), f"{item.intent.value} has no route in {mode}"


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

    def test_prop_account_management_is_offered_only_in_prop_firm_mode(self) -> None:
        offered = {item.intent for item in for_mode(WorkspaceMode.PROP_FIRM)}
        assert Intent.MANAGE_PROP_ACCOUNTS in offered
        assert Intent.MANAGE_PROP_ACCOUNTS not in {
            item.intent for item in for_mode(WorkspaceMode.NORMAL)
        }

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

    def test_the_catalogue_can_be_filtered_by_mode(self) -> None:
        everything = catalogue()
        prop = catalogue(WorkspaceMode.PROP_FIRM)
        assert len(prop) < len(everything)
        assert all("prop_firm" in item["modes"] for item in prop)
