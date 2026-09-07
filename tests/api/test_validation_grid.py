"""The validation grid must be wide enough for the judge to use its evidence.

The engine used to hand run_validation exactly two configurations. G5 and G11
now refuse evidence that thin, so a two-point grid would make every candidate
INCONCLUSIVE forever. These tests pin that the grid actually clears the bar, on
the real shipped templates rather than a convenient fixture.
"""

from __future__ import annotations

import pytest
from forge.judge import MINIMUM_TRIAL_CONFIGURATIONS
from forge.research.validation import expand_grid
from forge.strategy import TEMPLATES
from forge_api.engine import _axis_points, _grid_size, _validation_grid


def _defaults(key: str) -> dict[str, float]:
    return {p.name: float(p.default) for p in TEMPLATES[key].parameters}


@pytest.mark.parametrize("key", sorted(TEMPLATES))
def test_every_shipped_template_reaches_the_judges_minimum(key: str) -> None:
    """If a template cannot reach it, that is a fact worth failing loudly on."""
    template = TEMPLATES[key]
    movable = [
        spec
        for spec in template.parameters
        if float(spec.high) > float(spec.low) and float(spec.step) > 0.0
    ]
    grid = _validation_grid(template, _defaults(key))
    if not movable:
        pytest.skip(f"{key} has no movable parameter")
    assert _grid_size(grid) >= MINIMUM_TRIAL_CONFIGURATIONS, (
        f"{key} yields only {_grid_size(grid)} configurations"
    )


@pytest.mark.parametrize("key", sorted(TEMPLATES))
def test_grid_size_matches_what_expand_grid_produces(key: str) -> None:
    """_grid_size is used to gate validation; it must not disagree with reality."""
    grid = _validation_grid(TEMPLATES[key], _defaults(key))
    assert _grid_size(grid) == len(expand_grid(grid))


@pytest.mark.parametrize("key", sorted(TEMPLATES))
def test_every_grid_point_is_inside_its_declared_range(key: str) -> None:
    template = TEMPLATES[key]
    grid = _validation_grid(template, _defaults(key))
    for spec in template.parameters:
        for value in grid.get(spec.name, []):
            assert float(spec.low) <= value <= float(spec.high), (
                f"{key}.{spec.name} produced {value} outside "
                f"[{spec.low}, {spec.high}]"
            )


@pytest.mark.parametrize("key", sorted(TEMPLATES))
def test_the_candidate_being_judged_is_in_its_own_grid(key: str) -> None:
    """The neighbourhood has to contain the point it is a neighbourhood of."""
    params = _defaults(key)
    grid = _validation_grid(TEMPLATES[key], params)
    for name, value in params.items():
        assert round(value, 6) in grid[name], f"{key}.{name} dropped its own value"


# ── the axis helper ──────────────────────────────────────────────────────────


class Spec:
    def __init__(self, name: str, low: float, high: float, step: float) -> None:
        self.name, self.low, self.high, self.step = name, low, high, step


def test_axis_points_stay_in_range_at_the_low_edge() -> None:
    points = _axis_points(Spec("x", 10.0, 100.0, 5.0), 10.0, 8)
    assert points[0] == 10.0
    assert all(10.0 <= value <= 100.0 for value in points)
    assert len(points) == 8


def test_axis_points_stay_in_range_at_the_high_edge() -> None:
    points = _axis_points(Spec("x", 10.0, 100.0, 5.0), 100.0, 8)
    assert points[-1] == 100.0
    assert all(10.0 <= value <= 100.0 for value in points)


def test_axis_points_are_centred_on_the_candidate() -> None:
    points = _axis_points(Spec("x", 0.0, 100.0, 1.0), 50.0, 5)
    assert points == [48.0, 49.0, 50.0, 51.0, 52.0]


def test_axis_points_are_sorted_and_unique() -> None:
    points = _axis_points(Spec("x", 0.0, 10.0, 1.0), 5.0, 7)
    assert points == sorted(points)
    assert len(points) == len(set(points))


def test_a_narrow_axis_yields_what_it_can_without_inventing_values() -> None:
    """Three legal settings cannot become eight. It must not fabricate any."""
    points = _axis_points(Spec("x", 1.0, 3.0, 1.0), 2.0, 8)
    assert points == [1.0, 2.0, 3.0]


def test_a_frozen_axis_returns_only_its_own_value() -> None:
    assert _axis_points(Spec("x", 5.0, 5.0, 1.0), 5.0, 8) == [5.0]
    assert _axis_points(Spec("x", 0.0, 10.0, 0.0), 5.0, 8) == [5.0]


def test_a_template_with_no_movable_parameter_gets_a_single_point_grid() -> None:
    class Frozen:
        parameters = (Spec("a", 1.0, 1.0, 0.0),)

    grid = _validation_grid(Frozen(), {"a": 1.0})
    assert _grid_size(grid) == 1
