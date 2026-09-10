"""A failure has to be able to become a question, and some failures must not."""

from __future__ import annotations

import pytest
from forge.memory.research import FailureClass
from forge.research.followup import BARREN, Observation, derive, derive_many
from forge.research.frontier import SearchKind
from forge.research.hypotheses import (
    MIN_MECHANISM,
    MIN_PREDICTION,
    MIN_STATEMENT,
    HypothesisGraph,
)


def observation(**kwargs) -> Observation:
    base = {
        "family": "mean_reversion",
        "mechanism": "liquidity absorption",
        "hypothesis": "Price reverts after displacement.",
        "template": "vwap_sigma_reversion",
    }
    return Observation(**{**base, **kwargs})


def test_an_effect_confined_to_a_condition_produces_the_expected_question() -> None:
    """The research model's own example: level versus expansion."""
    followups = derive(
        observation(
            condition="low realised volatility",
            concentration="profitable only in the lowest volatility tercile",
        )
    )
    assert followups
    statements = " ".join(f.statement for f in followups)
    assert "rate of change" in statements
    assert "low realised volatility" in statements
    # One of them asks whether the condition is the mechanism rather than a filter.
    assert any(f.search_kind is SearchKind.MECHANISM for f in followups)


@pytest.mark.parametrize("failure", sorted(BARREN, key=str))
def test_a_barren_failure_suggests_nothing(failure) -> None:
    """A consumed holdout and a full disk say nothing about the market."""
    assert derive(observation(failure_class=failure)) == []


def test_no_trades_suggests_normalising_the_threshold() -> None:
    followups = derive(observation(failure_class=FailureClass.NO_TRADES))
    assert followups
    assert followups[0].search_kind is SearchKind.STRUCTURAL
    assert "realised" in followups[0].prediction or "dispersion" in followups[0].statement


def test_negative_expectancy_suggests_a_sign_error_not_more_tuning() -> None:
    followups = derive(observation(failure_class=FailureClass.NEGATIVE_EXPECTANCY))
    assert followups
    assert "invert" in followups[0].prediction.lower()
    assert followups[0].search_kind is SearchKind.MECHANISM


def test_a_walk_forward_failure_suggests_a_regime() -> None:
    followups = derive(observation(failure_class=FailureClass.WALK_FORWARD))
    assert followups
    assert "regime" in followups[0].statement.lower()


def test_selection_integrity_suggests_a_pre_committed_single_configuration() -> None:
    followups = derive(observation(failure_class=FailureClass.SELECTION_INTEGRITY))
    assert followups
    assert "pre-registered" in followups[0].prediction or "pre-committed" in (
        followups[0].statement
    )


def test_an_observation_with_no_structure_suggests_nothing() -> None:
    """An empty list is a real answer and is different from nobody asking."""
    assert derive(observation()) == []


def test_generation_is_deterministic() -> None:
    obs = observation(failure_class=FailureClass.RISK, condition="the New York open")
    assert [f.statement for f in derive(obs)] == [f.statement for f in derive(obs)]


def test_every_generated_question_clears_the_hypothesis_graph_bar(tmp_path) -> None:
    """A machine-written hypothesis does not get an easier bar than a human's."""
    graph = HypothesisGraph(tmp_path / "h.db")
    produced = 0
    for failure in FailureClass:
        for condition in ("", "volatility expansion"):
            for followup in derive(observation(failure_class=failure, condition=condition)):
                assert len(followup.statement) >= MIN_STATEMENT
                assert len(followup.prediction) >= MIN_PREDICTION
                assert len(followup.mechanism) >= MIN_MECHANISM
                node = graph.propose(
                    campaign_id="c1",
                    statement=followup.statement,
                    prediction=followup.prediction,
                    mechanism=followup.mechanism,
                    family=followup.family,
                    search_kind=followup.search_kind,
                    origin="failure-derived",
                )
                assert node.status.value == "UNTESTED"
                produced += 1
    assert produced > 5


def test_derive_many_deduplicates_across_a_batch() -> None:
    """Five candidates failing the same way should produce one question, not five."""
    batch = [observation(failure_class=FailureClass.NO_TRADES) for _ in range(5)]
    assert len(derive_many(batch)) == 1


def test_derive_many_respects_its_limit() -> None:
    batch = [
        observation(failure_class=failure, condition=f"condition {index}")
        for index, failure in enumerate(
            (
                FailureClass.NO_TRADES,
                FailureClass.RISK,
                FailureClass.ROBUSTNESS,
                FailureClass.NEGATIVE_EXPECTANCY,
            )
        )
    ]
    assert len(derive_many(batch, limit=3)) == 3
