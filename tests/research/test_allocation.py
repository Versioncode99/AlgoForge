"""The budget model: a stated split, an adaptation that is bounded, a realised split."""

from __future__ import annotations

import random

import pytest
from forge.research.allocation import (
    DEFAULT_WEIGHTS,
    MAX_DRIFT,
    MIN_WEIGHT,
    Bucket,
    FrontierSignal,
    ResearchAllocation,
    adapt,
    realised,
)
from forge.research.frontier import FrontierState


def counts(**overrides: int) -> dict[str, int]:
    base = {str(state): 0 for state in FrontierState}
    base.update({str(FrontierState(k)): v for k, v in overrides.items()})
    return base


def test_the_default_split_is_the_one_the_research_model_asks_for() -> None:
    weights = ResearchAllocation.default().as_dict()
    assert weights["EXPLORE_HYPOTHESIS"] == pytest.approx(0.35)
    assert weights["DISCOVER_FAMILY"] == pytest.approx(0.25)
    assert weights["ADVANCE_PROMISING"] == pytest.approx(0.20)
    assert weights["REFINE_PARAMETERS"] == pytest.approx(0.10)
    assert weights["ROBUSTNESS"] == pytest.approx(0.10)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_an_unknown_bucket_is_refused_rather_than_ignored() -> None:
    """Silently ignoring it would run the default while the operator believed otherwise."""
    with pytest.raises(ValueError, match="Unknown research bucket"):
        ResearchAllocation.from_mapping({"explore": 0.6})


def test_configuration_is_normalised_and_nothing_is_starved() -> None:
    allocation = ResearchAllocation.from_mapping(
        {"EXPLORE_HYPOTHESIS": 0.9, "DISCOVER_FAMILY": 0.0}
    )
    weights = allocation.as_dict()
    assert sum(weights.values()) == pytest.approx(1.0)
    assert all(value >= MIN_WEIGHT * 0.9 for value in weights.values())


def test_the_draw_follows_the_weights() -> None:
    rng = random.Random(7)
    allocation = ResearchAllocation.default()
    drawn = [allocation.draw(rng) for _ in range(4000)]
    share = drawn.count(Bucket.EXPLORE_HYPOTHESIS) / len(drawn)
    assert 0.30 < share < 0.40
    # Every bucket is reachable: a bucket that never comes up is a bucket that
    # does not exist.
    assert set(drawn) == set(Bucket)


def test_a_deep_narrow_search_is_pulled_out_of_parameter_refinement() -> None:
    """The exact failure the module exists to correct."""
    signal = FrontierSignal(state_counts=counts(), experiments=60, mechanisms=2)
    advice = adapt(ResearchAllocation.default(), signal)
    weights = advice.allocation.as_dict()
    assert weights["REFINE_PARAMETERS"] < DEFAULT_WEIGHTS[Bucket.REFINE_PARAMETERS]
    assert weights["DISCOVER_FAMILY"] > DEFAULT_WEIGHTS[Bucket.DISCOVER_FAMILY]
    assert any("mechanisms" in reason for reason in advice.reasons)


def test_a_promising_backlog_pulls_budget_towards_advancing_it() -> None:
    signal = FrontierSignal(state_counts=counts(PROMISING=6))
    advice = adapt(ResearchAllocation.default(), signal)
    assert (
        advice.allocation.as_dict()["ADVANCE_PROMISING"] > DEFAULT_WEIGHTS[Bucket.ADVANCE_PROMISING]
    )
    assert any("promising" in reason for reason in advice.reasons)


def test_duplicate_proposals_pull_budget_out_of_family_discovery() -> None:
    signal = FrontierSignal(state_counts=counts(), duplicate_proposals=9, total_proposals=12)
    advice = adapt(ResearchAllocation.default(), signal)
    assert advice.allocation.as_dict()["DISCOVER_FAMILY"] < DEFAULT_WEIGHTS[Bucket.DISCOVER_FAMILY]


def test_adaptation_is_capped_so_no_bucket_can_take_over() -> None:
    signal = FrontierSignal(
        state_counts=counts(PROMISING=40),
        duplicate_proposals=100,
        total_proposals=100,
        validation_backlog=50,
        experiments=500,
        mechanisms=1,
    )
    advice = adapt(ResearchAllocation.default(), signal)
    for bucket, value in advice.allocation.weights.items():
        assert abs(value - DEFAULT_WEIGHTS[bucket]) <= MAX_DRIFT + 0.06, bucket
    assert sum(advice.allocation.weights.values()) == pytest.approx(1.0)


def test_no_tilt_still_produces_a_stated_reason() -> None:
    advice = adapt(ResearchAllocation.default(), FrontierSignal(state_counts=counts()))
    assert advice.reasons
    assert "no tilt" in advice.reasons[0]


def test_the_configured_allocation_is_never_overwritten() -> None:
    base = ResearchAllocation.default()
    before = base.as_dict()
    adapt(base, FrontierSignal(state_counts=counts(PROMISING=9), experiments=90, mechanisms=1))
    assert base.as_dict() == before


def test_realised_reports_what_was_actually_spent() -> None:
    spend = [Bucket.REFINE_PARAMETERS] * 9 + [Bucket.DISCOVER_FAMILY]
    shares = realised([str(b) for b in spend])
    assert shares["REFINE_PARAMETERS"] == pytest.approx(0.9)
    assert shares["DISCOVER_FAMILY"] == pytest.approx(0.1)
    # Buckets that consumed nothing are reported as zero, not omitted.
    assert shares["EXPLORE_HYPOTHESIS"] == 0.0


def test_realised_of_nothing_is_all_zeroes() -> None:
    assert set(realised([]).values()) == {0.0}
