"""The frontier's job is to never lose the difference between kinds of silence."""

from __future__ import annotations

import pytest
from forge.research.frontier import (
    SCHEDULABLE_STATES,
    FrontierError,
    FrontierState,
    ResearchFrontier,
    SearchKind,
)

QUESTION = (
    "Does opening-range liquidity imbalance condition breakout continuation on NQ one-minute bars?"
)


def frontier(tmp_path) -> ResearchFrontier:
    return ResearchFrontier(tmp_path / "frontier.db")


def admit(store: ResearchFrontier, question: str = QUESTION, **kwargs):
    return store.admit(
        campaign_id="c1",
        question=question,
        family="session_structure",
        mechanism="Auction flow concentrates at the open",
        search_kind=SearchKind.HYPOTHESIS,
        **kwargs,
    )


def test_an_untested_question_is_not_a_failure(tmp_path) -> None:
    """The distinction the whole module exists for."""
    item = admit(frontier(tmp_path))
    assert item.state is FrontierState.UNTESTED
    assert item.state is not FrontierState.FAILED
    assert item.schedulable
    assert item.state in SCHEDULABLE_STATES


def test_a_question_needing_absent_data_is_blocked_not_failed(tmp_path) -> None:
    item = admit(
        frontier(tmp_path),
        question="Does resting depth imbalance predict the next trade's direction?",
        required_data=("L2_MBP",),
        missing_data=("L2_MBP",),
    )
    assert item.state is FrontierState.BLOCKED_BY_DATA
    assert item.missing_data == ("L2_MBP",)
    assert not item.schedulable
    # Nothing is claimed about whether it is true.
    assert "cannot serve" in item.reason


def test_blocked_data_cannot_be_admitted_as_untested(tmp_path) -> None:
    """A queue that will never reach an item must not contain it."""
    with pytest.raises(FrontierError, match=r"cannot be measured|not served"):
        admit(
            frontier(tmp_path),
            question="Does queue position predict passive fill quality on NQ?",
            missing_data=("L3_MBO",),
            state=FrontierState.UNTESTED,
        )


def test_the_same_question_admitted_twice_is_one_item(tmp_path) -> None:
    store = frontier(tmp_path)
    first, second = admit(store), admit(store)
    assert first.item_id == second.item_id
    assert len(store.list("c1")) == 1


def test_a_label_is_refused_as_a_question(tmp_path) -> None:
    with pytest.raises(FrontierError, match="20 characters"):
        admit(frontier(tmp_path), question="momentum")


def test_a_transition_needs_a_reason_that_is_not_the_state_name(tmp_path) -> None:
    store = frontier(tmp_path)
    item = admit(store)
    with pytest.raises(FrontierError, match="8 characters"):
        store.transition(item.item_id, FrontierState.FAILED, reason="FAILED")


def test_history_records_who_moved_it_and_on_what(tmp_path) -> None:
    store = frontier(tmp_path)
    item = admit(store)
    store.transition(
        item.item_id,
        FrontierState.PROMISING,
        reason="development net +1,240 over 84 trades",
        actor="engine",
        evidence=["bt_abc", "vd_def"],
    )
    store.transition(
        item.item_id,
        FrontierState.VALIDATED,
        reason="cleared the gate ladder on the holdout",
        evidence=["vd_ghi"],
    )
    history = store.history(item.item_id)
    assert [h["to"] for h in history] == ["UNTESTED", "PROMISING", "VALIDATED"]
    assert history[1]["evidence"] == ["bt_abc", "vd_def"]
    assert history[1]["actor"] == "engine"
    # Nothing is deleted: the original admission is still readable.
    assert history[0]["from"] == ""


def test_counts_include_the_states_with_nothing_in_them(tmp_path) -> None:
    """Absent keys make 'none' and 'not tracked' indistinguishable."""
    counts = frontier(tmp_path).counts("c1")
    assert set(counts) == {str(state) for state in FrontierState}
    assert all(value == 0 for value in counts.values())


def test_recording_an_experiment_never_changes_state(tmp_path) -> None:
    store = frontier(tmp_path)
    item = admit(store)
    store.record_experiment(item.item_id, 3)
    refreshed = store.get(item.item_id)
    assert refreshed is not None
    assert refreshed.experiments == 3
    assert refreshed.state is FrontierState.UNTESTED


def test_schedulable_excludes_settled_and_blocked(tmp_path) -> None:
    store = frontier(tmp_path)
    for index, state in enumerate(
        (FrontierState.FAILED, FrontierState.VALIDATED, FrontierState.EXHAUSTED)
    ):
        item = admit(store, question=f"{QUESTION} Variation number {index} of the same shape.")
        store.transition(item.item_id, state, reason="settled during the test")
    admit(store, question="A question nobody has looked at yet, phrased at some length.")
    schedulable = store.schedulable("c1")
    assert len(schedulable) == 1
    assert schedulable[0].state is FrontierState.UNTESTED


def test_inconclusive_is_still_schedulable(tmp_path) -> None:
    """Ran and could not say is not the same as disproven."""
    store = frontier(tmp_path)
    item = admit(store)
    store.transition(
        item.item_id, FrontierState.INCONCLUSIVE, reason="too few trials to deflate against"
    )
    assert [i.item_id for i in store.schedulable("c1")] == [item.item_id]


def test_state_survives_a_reopen(tmp_path) -> None:
    store = frontier(tmp_path)
    item = admit(store)
    store.transition(item.item_id, FrontierState.PROMISING, reason="positive development result")
    reopened = ResearchFrontier(tmp_path / "frontier.db").get(item.item_id)
    assert reopened is not None
    assert reopened.state is FrontierState.PROMISING
    assert reopened.question == QUESTION
