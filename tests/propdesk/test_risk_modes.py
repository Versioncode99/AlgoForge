"""The three risk modes, their boundaries, and what the advisory layer may touch.

These tests are about containment rather than arithmetic. The arithmetic lives
in `test_risk_scaling.py`; here the questions are whether a boundary can be
exceeded, whether an unenforceable promise can be made, and whether a mode can
hold a permission it never consults.
"""

from __future__ import annotations

import importlib

import pytest
from forge.propdesk.consent import AI_RISK_DISCLOSURE
from forge.propdesk.risk import (
    MODE_DETAIL,
    MODE_PROMISE,
    PROHIBITIONS,
    AiCapability,
    ManualRisk,
    RiskBoundaries,
    RiskMode,
    RiskSettings,
    capability_catalogue,
    describe_modes,
    prohibition_catalogue,
)


def settings(**kwargs) -> RiskSettings:
    base = {"account_uid": "acct-1", "mode": RiskMode.MANUAL}
    base.update(kwargs)
    if base["mode"] is RiskMode.MANUAL and "manual" not in base:
        base["manual"] = ManualRisk(risk_fraction=0.1, max_contracts=2)
    return RiskSettings(**base)


class TestBoundaries:
    def test_a_minimum_above_the_maximum_is_refused(self) -> None:
        with pytest.raises(ValueError, match="is above maximum"):
            RiskBoundaries(minimum_fraction=0.3, maximum_fraction=0.1)

    def test_a_step_larger_than_the_daily_limit_is_refused(self) -> None:
        # Otherwise the step is unreachable after the first change of the day,
        # which makes the configuration quietly mean something else.
        with pytest.raises(ValueError, match="unreachable"):
            RiskBoundaries(max_step=0.1, max_daily_change=0.05)

    def test_no_boundary_can_express_having_no_ceiling(self) -> None:
        for field, value in (
            ("maximum_fraction", 0.0),
            ("max_step", 0.0),
            ("max_daily_change", 0.0),
            ("max_contracts", 0),
        ):
            with pytest.raises(ValueError):
                RiskBoundaries(**{field: value})

    def test_clamping_is_symmetric_and_returns_the_boundary_itself(self) -> None:
        boundaries = RiskBoundaries(minimum_fraction=0.05, maximum_fraction=0.2)
        assert boundaries.clamp(0.9) == 0.2
        assert boundaries.clamp(0.001) == 0.05
        assert boundaries.clamp(0.1) == 0.1

    def test_the_hash_changes_when_any_boundary_changes(self) -> None:
        # The audit record carries this, so a boundary edited between two
        # decisions is visible without diffing every field.
        first = RiskBoundaries()
        second = RiskBoundaries(max_step=0.03)
        assert first.boundaries_hash != second.boundaries_hash


class TestModes:
    def test_every_mode_has_a_promise_and_a_detail(self) -> None:
        for mode in RiskMode:
            assert MODE_PROMISE[mode]
            assert MODE_DETAIL[mode]

    def test_the_promises_are_the_ones_the_product_makes(self) -> None:
        assert MODE_PROMISE[RiskMode.MANUAL] == "I decide."
        assert MODE_PROMISE[RiskMode.ADAPTIVE] == "AlgoForge calculates."
        assert MODE_PROMISE[RiskMode.AI_MANAGED] == "AlgoForge manages within my boundaries."

    def test_manual_mode_requires_an_explicit_fraction(self) -> None:
        with pytest.raises(ValueError, match="requires an explicit risk fraction"):
            RiskSettings(account_uid="a", mode=RiskMode.MANUAL)

    def test_a_manual_fraction_outside_the_operators_own_boundaries_is_refused(self) -> None:
        boundaries = RiskBoundaries(minimum_fraction=0.05, maximum_fraction=0.15)
        with pytest.raises(ValueError, match="above"):
            settings(boundaries=boundaries, manual=ManualRisk(risk_fraction=0.5, max_contracts=1))
        with pytest.raises(ValueError, match="below"):
            settings(boundaries=boundaries, manual=ManualRisk(risk_fraction=0.01, max_contracts=1))

    def test_a_manual_contract_count_above_the_operators_ceiling_is_refused(self) -> None:
        with pytest.raises(ValueError, match="above your own ceiling"):
            settings(
                boundaries=RiskBoundaries(max_contracts=3),
                manual=ManualRisk(risk_fraction=0.1, max_contracts=9),
            )

    def test_only_manual_mode_is_not_automated(self) -> None:
        assert settings().automated is False
        assert settings(mode=RiskMode.ADAPTIVE, manual=None).automated is True


class TestCapabilities:
    def test_a_non_ai_mode_may_not_carry_ai_capabilities(self) -> None:
        # They would read as permissions and never be consulted, which is the
        # most dangerous shape a permission can take.
        with pytest.raises(ValueError, match="no advisory layer"):
            settings(
                mode=RiskMode.ADAPTIVE,
                manual=None,
                ai_capabilities=(AiCapability.POSITION_SIZING,),
            )

    def test_ai_mode_requires_an_acknowledged_disclosure(self) -> None:
        with pytest.raises(ValueError, match="acknowledged disclosure"):
            settings(mode=RiskMode.AI_MANAGED, manual=None)

    def test_may_is_false_for_a_capability_that_was_not_granted(self) -> None:
        configured = settings(
            mode=RiskMode.AI_MANAGED,
            manual=None,
            disclosure_version=AI_RISK_DISCLOSURE.version,
            ai_capabilities=(AiCapability.RISK_REDUCTION,),
        )
        assert configured.may(AiCapability.RISK_REDUCTION)
        assert not configured.may(AiCapability.RISK_INCREASE)

    def test_may_is_false_in_every_mode_but_ai_managed(self) -> None:
        adaptive = settings(mode=RiskMode.ADAPTIVE, manual=None)
        assert not adaptive.may(AiCapability.POSITION_SIZING)

    def test_every_capability_is_described_for_the_screen(self) -> None:
        described = {item["capability"] for item in capability_catalogue()}
        assert described == {capability.value for capability in AiCapability}
        assert all(item["label"] and item["detail"] for item in capability_catalogue())


class TestProhibitions:
    def test_every_prohibition_names_a_module_that_exists(self) -> None:
        # A prohibition with no mechanism beside it is a statement of intent.
        # This is the test that keeps the list honest.
        for prohibition in PROHIBITIONS:
            target = prohibition.enforced_by
            module_path = target
            while module_path:
                try:
                    importlib.import_module(module_path)
                    break
                except ModuleNotFoundError:
                    module_path, _, _ = module_path.rpartition(".")
            assert module_path, f"{target} names nothing importable"

    def test_every_prohibition_names_an_attribute_that_exists(self) -> None:
        for prohibition in PROHIBITIONS:
            parts = prohibition.enforced_by.split(".")
            module = None
            for index in range(len(parts), 0, -1):
                try:
                    module = importlib.import_module(".".join(parts[:index]))
                    remainder = parts[index:]
                    break
                except ModuleNotFoundError:
                    continue
            assert module is not None
            target = module
            for name in remainder:
                assert hasattr(target, name), f"{prohibition.enforced_by} has no {name}"
                target = getattr(target, name)

    def test_the_prohibitions_cover_every_thing_the_specification_names(self) -> None:
        statements = " | ".join(item.statement.lower() for item in PROHIBITIONS)
        for required in (
            "prop-firm limits",
            "maximum risk",
            "pre-trade gate",
            "unvalidated strategy",
            "blocked account",
            "g0-g13",
            "protected control",
        ):
            assert required in statements, f"nothing prohibits: {required}"

    def test_the_catalogue_renders_every_prohibition_with_its_mechanism(self) -> None:
        rendered = prohibition_catalogue()
        assert len(rendered) == len(PROHIBITIONS)
        assert all(
            item["statement"] and item["enforced_by"] and item["detail"] for item in rendered
        )


class TestDescription:
    def test_the_selector_describes_all_three_modes_in_order(self) -> None:
        described = describe_modes()
        assert [item["mode"] for item in described] == [m.value for m in RiskMode]

    def test_the_dictionary_form_carries_the_promise_the_screen_shows(self) -> None:
        payload = settings().as_dict()
        assert payload["promise"] == "I decide."
        assert payload["automated"] is False
        assert "boundaries_hash" in payload["boundaries"]
