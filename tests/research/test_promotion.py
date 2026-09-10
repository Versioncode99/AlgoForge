"""Validation is reached when it is earned, and INCONCLUSIVE is not a failure."""

from __future__ import annotations

import pytest
from forge.research.frontier import FrontierState, SearchKind
from forge.research.information import estimate_cost, rank, value_of
from forge.research.promotion import (
    Candidate,
    Prerequisites,
    PromotionOutcome,
    PromotionQueue,
    PromotionState,
    assess,
    outcome_from_verdict,
)


class Gate:
    def __init__(self, gate: str, status: str, name: str = "") -> None:
        self.gate, self.status, self.name = gate, status, name or gate


def candidate(**kwargs) -> Candidate:
    base = {
        "strategy_id": "s1",
        "hypothesis_id": "hyp_1",
        "frontier_item_id": "fr_1",
        "experiment_id": "exp_1",
        "trades": 84,
        "net_pnl": 1_240.0,
        "grid_size": 9,
        "conformance_passed": True,
        "determinism_reproduced": True,
        "real_data": True,
    }
    return Candidate(**{**base, **kwargs})


def queue(tmp_path) -> PromotionQueue:
    return PromotionQueue(tmp_path / "promotion.db")


def test_a_candidate_that_meets_every_prerequisite_is_eligible() -> None:
    eligibility = assess(candidate())
    assert eligibility.eligible
    assert eligibility.reasons


def test_every_unmet_prerequisite_is_reported_not_just_the_first() -> None:
    eligibility = assess(
        candidate(
            trades=4,
            net_pnl=-10.0,
            grid_size=1,
            conformance_passed=None,
            determinism_reproduced=None,
            real_data=False,
        )
    )
    assert not eligibility.eligible
    assert len(eligibility.reasons) == 6


def test_synthetic_data_is_never_eligible() -> None:
    assert not assess(candidate(real_data=False)).eligible


def test_prerequisites_are_configurable() -> None:
    strict = Prerequisites(min_trades=200)
    assert not assess(candidate(trades=84), strict).eligible
    assert assess(candidate(trades=84), Prerequisites(min_trades=30)).eligible


def test_an_ineligible_candidate_is_not_queued(tmp_path) -> None:
    store = queue(tmp_path)
    bad = candidate(trades=2)
    assert store.enqueue("c1", bad, assess(bad)) is None
    assert store.pending("c1") == 0


def test_a_candidate_is_owed_one_run_not_two(tmp_path) -> None:
    store = queue(tmp_path)
    good = candidate()
    store.enqueue("c1", good, assess(good))
    store.enqueue("c1", good, assess(good))
    assert store.pending("c1") == 1


def test_claiming_marks_it_running_and_only_once(tmp_path) -> None:
    store = queue(tmp_path)
    good = candidate()
    store.enqueue("c1", good, assess(good))
    first = store.claim("c1")
    assert first is not None
    assert first["state"] == PromotionState.RUNNING
    assert store.claim("c1") is None


def test_the_highest_priority_entry_is_claimed_first(tmp_path) -> None:
    store = queue(tmp_path)
    for index, priority in enumerate((0.1, 0.9, 0.5)):
        item = candidate(strategy_id=f"s{index}")
        store.enqueue("c1", item, assess(item), priority=priority)
    claimed = store.claim("c1")
    assert claimed is not None
    assert claimed["strategy_id"] == "s1"


def test_a_decision_requires_a_reason(tmp_path) -> None:
    store = queue(tmp_path)
    good = candidate()
    entry = store.enqueue("c1", good, assess(good))
    assert entry is not None
    with pytest.raises(ValueError, match="stated reason"):
        store.decide(entry, PromotionOutcome.PASS, reasons=[])


def test_an_interrupted_run_is_released_back_to_the_queue(tmp_path) -> None:
    store = queue(tmp_path)
    good = candidate()
    store.enqueue("c1", good, assess(good))
    store.claim("c1")
    assert store.release_running("c1") == 1
    assert store.claim("c1") is not None


def test_outcomes_include_the_zeroes(tmp_path) -> None:
    counts = queue(tmp_path).outcomes("c1")
    assert set(counts) == {"PASS", "FAIL", "INCONCLUSIVE"}


def test_a_failed_gate_is_a_failure() -> None:
    outcome, reasons = outcome_from_verdict("FAIL", [Gate("G4", "FAIL", "Expectancy")])
    assert outcome is PromotionOutcome.FAIL
    assert "G4" in reasons[0]


def test_an_unmeasured_gate_is_inconclusive_not_a_failure() -> None:
    """A gap in the evidence must never prune a candidate."""
    outcome, reasons = outcome_from_verdict(
        "INCONCLUSIVE", [Gate("G5", "INCONCLUSIVE", "Deflated Sharpe")]
    )
    assert outcome is PromotionOutcome.INCONCLUSIVE
    assert outcome is not PromotionOutcome.FAIL
    assert "could not be measured" in reasons[0]


def test_a_pass_is_a_pass() -> None:
    outcome, reasons = outcome_from_verdict("PASS", [Gate("G0", "PASS")])
    assert outcome is PromotionOutcome.PASS
    assert reasons


def test_a_verdict_with_no_failing_gate_is_inconclusive() -> None:
    outcome, _ = outcome_from_verdict("REJECT", [Gate("G0", "PASS")])
    assert outcome is PromotionOutcome.INCONCLUSIVE


# ── information value ────────────────────────────────────────────────────────


def test_blocked_data_makes_the_expected_gain_exactly_zero() -> None:
    """Not small — zero. An experiment that cannot run teaches nothing."""
    value = value_of(
        novelty=1.0,
        state=FrontierState.UNTESTED,
        kind=SearchKind.FAMILY,
        prior_experiments=0,
        data_available=False,
        cost=estimate_cost(bars=250_000),
    )
    assert value.expected_gain == 0.0
    assert value.priority == 0.0


def test_repeated_experiments_against_one_question_decay() -> None:
    def value(prior: int) -> float:
        return value_of(
            novelty=1.0,
            state=FrontierState.UNTESTED,
            kind=SearchKind.PARAMETER,
            prior_experiments=prior,
            data_available=True,
            cost=estimate_cost(bars=100_000),
        ).expected_gain

    assert value(0) > value(1) > value(10)
    assert value(40) < value(0) * 0.01


def test_a_family_discovery_outranks_a_parameter_tweak_at_equal_cost() -> None:
    cost = estimate_cost(bars=100_000)

    def value(kind):
        return value_of(
            novelty=0.9,
            state=FrontierState.UNTESTED,
            kind=kind,
            prior_experiments=0,
            data_available=True,
            cost=cost,
        ).priority

    assert value(SearchKind.FAMILY) > value(SearchKind.PARAMETER)


def test_an_untested_question_outranks_a_settled_one() -> None:
    def value(state):
        return value_of(
            novelty=0.8,
            state=state,
            kind=SearchKind.HYPOTHESIS,
            prior_experiments=0,
            data_available=True,
            cost=estimate_cost(bars=100_000),
        ).priority

    assert value(FrontierState.UNTESTED) > value(FrontierState.VALIDATED)
    assert value(FrontierState.INCONCLUSIVE) > value(FrontierState.FAILED)


def test_validation_costs_more_than_a_screen() -> None:
    cheap = estimate_cost(bars=250_000, grid_size=9, includes_validation=False)
    dear = estimate_cost(bars=250_000, grid_size=9, includes_validation=True)
    assert dear.units > cheap.units


def test_ties_are_broken_towards_the_cheaper_experiment() -> None:
    shared = {
        "novelty": 0.8,
        "state": FrontierState.UNTESTED,
        "kind": SearchKind.HYPOTHESIS,
        "prior_experiments": 0,
        "data_available": True,
    }
    expensive = value_of(
        **shared, cost=estimate_cost(bars=250_000, grid_size=9, includes_validation=True)
    )
    cheap = value_of(
        **shared, cost=estimate_cost(bars=250_000, grid_size=9, includes_validation=True)
    )
    ordered = rank([("expensive", expensive), ("cheap", cheap)])
    assert [key for key, _ in ordered] == ["expensive", "cheap"]
