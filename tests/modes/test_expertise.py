"""Progressive disclosure: three depths over one engine, and no hidden refusals."""

from __future__ import annotations

import pytest
from forge.modes.expertise import (
    ALWAYS_VISIBLE,
    SURFACE_LABEL,
    SURFACE_LEVEL,
    ExpertiseLevel,
    Surface,
    catalogue,
    descriptor,
    shows,
    visible,
)


class TestTheLadder:
    def test_each_level_shows_at_least_as_much_as_the_one_below(self) -> None:
        guided = set(visible(ExpertiseLevel.GUIDED))
        advanced = set(visible(ExpertiseLevel.ADVANCED))
        quant = set(visible(ExpertiseLevel.QUANT))
        assert guided < advanced < quant

    def test_quant_shows_every_surface(self) -> None:
        assert set(visible(ExpertiseLevel.QUANT)) == set(Surface)

    def test_every_surface_declares_a_level_and_a_label(self) -> None:
        assert set(SURFACE_LEVEL) == set(Surface)
        assert set(SURFACE_LABEL) == set(Surface)


class TestNothingImportantIsHidden:
    def test_no_level_hides_a_refusal_or_a_limitation(self) -> None:
        # Simplifying a product by hiding its warnings makes its beginners less
        # safe, not less confused.
        for level in ExpertiseLevel:
            for surface in ALWAYS_VISIBLE:
                assert shows(level, surface), f"{level} hides {surface}"

    def test_the_always_visible_set_covers_what_a_beginner_most_needs(self) -> None:
        assert {
            Surface.REFUSAL_LADDER,
            Surface.LIMITATIONS,
            Surface.UNMEASURED,
            Surface.SIMULATION_NOTICE,
            Surface.FIRM_PERMISSIONS,
            Surface.DISCLOSURES,
        } <= ALWAYS_VISIBLE

    def test_guided_still_offers_the_risk_mode_selector(self) -> None:
        # Choosing how much control you want is the product's central promise.
        # Reserving it for experts would invert it.
        assert shows(ExpertiseLevel.GUIDED, Surface.RISK_MODE_SELECTOR)
        assert shows(ExpertiseLevel.GUIDED, Surface.WHY_ANSWERS)

    def test_boundaries_and_drivers_are_advanced_rather_than_quant(self) -> None:
        # A serious trader changing their own limits should not have to claim to
        # be a researcher.
        assert shows(ExpertiseLevel.ADVANCED, Surface.RISK_BOUNDARIES)
        assert shows(ExpertiseLevel.ADVANCED, Surface.RISK_DRIVERS)
        assert not shows(ExpertiseLevel.GUIDED, Surface.RISK_BOUNDARIES)

    def test_the_method_itself_is_reserved_for_quant(self) -> None:
        for surface in (
            Surface.TRIAL_MATRIX,
            Surface.CSCV_INTERNALS,
            Surface.CPCV_PATHS,
            Surface.DEFLATION_BENCHMARK,
            Surface.CALCULATION_TRACES,
        ):
            assert not shows(ExpertiseLevel.ADVANCED, surface)
            assert shows(ExpertiseLevel.QUANT, surface)


class TestDescription:
    def test_an_unknown_level_raises_with_the_valid_set(self) -> None:
        with pytest.raises(KeyError, match="guided, advanced, quant"):
            descriptor("expert")

    def test_a_level_can_be_described_from_its_string_form(self) -> None:
        assert descriptor("quant").level is ExpertiseLevel.QUANT

    def test_the_catalogue_marks_which_surfaces_are_always_visible(self) -> None:
        rendered = catalogue()
        assert [item["level"] for item in rendered] == [level.value for level in ExpertiseLevel]
        guided = rendered[0]
        always = {s["surface"] for s in guided["surfaces"] if s["always_visible"]}
        assert always == {surface.value for surface in ALWAYS_VISIBLE}

    def test_every_level_explains_what_it_is_for(self) -> None:
        for item in catalogue():
            assert item["label"] and item["detail"]
            assert "same engine" in item["detail"] or len(item["detail"]) > 40
