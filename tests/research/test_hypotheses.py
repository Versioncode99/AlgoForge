"""The hypothesis graph: falsifiability at the door, lineage on disk."""

from __future__ import annotations

import pytest
from forge.research.frontier import SearchKind
from forge.research.hypotheses import (
    EdgeKind,
    HypothesisError,
    HypothesisGraph,
    HypothesisStatus,
)

STATEMENT = (
    "Opening-range breaks continue when the break occurs on above-median volume, because "
    "the auction did not absorb the flow that produced it."
)
PREDICTION = (
    "Low-volume breaks must underperform high-volume breaks over the same horizon, or the "
    "absorption explanation is abandoned."
)
MECHANISM = (
    "The opening auction clears at a single price; interest that could not clear there is "
    "worked in the continuous session."
)


def graph(tmp_path) -> HypothesisGraph:
    return HypothesisGraph(tmp_path / "hyp.db")


def propose(store: HypothesisGraph, statement: str = STATEMENT, **kwargs):
    return store.propose(
        campaign_id="c1",
        statement=statement,
        prediction=kwargs.pop("prediction", PREDICTION),
        mechanism=kwargs.pop("mechanism", MECHANISM),
        family="session_structure",
        search_kind=SearchKind.HYPOTHESIS,
        **kwargs,
    )


def test_a_hypothesis_starts_untested(tmp_path) -> None:
    assert propose(graph(tmp_path)).status is HypothesisStatus.UNTESTED


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("statement", "momentum works", "statement needs"),
        ("prediction", "it works", "abandon"),
        ("mechanism", "flow", "mechanism"),
    ],
)
def test_a_claim_that_cannot_be_wrong_is_refused(tmp_path, field, value, message) -> None:
    store = graph(tmp_path)
    kwargs = {"statement": STATEMENT, "prediction": PREDICTION, "mechanism": MECHANISM}
    kwargs[field] = value
    with pytest.raises(HypothesisError, match=message):
        store.propose(
            campaign_id="c1",
            family="session_structure",
            search_kind=SearchKind.HYPOTHESIS,
            **kwargs,
        )


def test_a_blocked_hypothesis_is_not_queued_as_untested(tmp_path) -> None:
    node = propose(
        graph(tmp_path),
        statement="Resting depth imbalance predicts the direction of the next aggressive trade.",
        required_data=("L2_MBP",),
        missing_data=("L2_MBP",),
    )
    assert node.status is HypothesisStatus.BLOCKED_BY_DATA


def test_the_same_claim_twice_is_one_node(tmp_path) -> None:
    store = graph(tmp_path)
    first, second = propose(store), propose(store)
    assert first.hypothesis_id == second.hypothesis_id
    assert len(store.list("c1")) == 1


def test_a_derived_hypothesis_carries_an_edge_back_to_its_parent(tmp_path) -> None:
    store = graph(tmp_path)
    parent = propose(store)
    child = propose(
        store,
        statement=(
            "The opening-range effect depends on the rate of change of volume rather than "
            "its level, because repositioning is a transition and not a state."
        ),
        parent_id=parent.hypothesis_id,
        origin="failure-derived",
    )
    assert child.parent_id == parent.hypothesis_id
    # A derived hypothesis is a question, never a conclusion.
    assert child.status is HypothesisStatus.UNTESTED
    edges = store.edges(child.hypothesis_id, kind=EdgeKind.DERIVED_FROM)
    assert edges and edges[0]["target"] == parent.hypothesis_id


def test_a_hypothesis_cannot_be_its_own_parent(tmp_path) -> None:
    store = graph(tmp_path)
    node = propose(store)
    with pytest.raises(HypothesisError, match="cannot link to itself"):
        store.link(node.hypothesis_id, EdgeKind.DERIVED_FROM, node.hypothesis_id)


def test_an_internal_edge_must_point_at_a_hypothesis(tmp_path) -> None:
    store = graph(tmp_path)
    node = propose(store)
    with pytest.raises(HypothesisError, match="not one"):
        store.link(node.hypothesis_id, EdgeKind.CONTRADICTS, "hyp_does_not_exist")


def test_an_unknown_parent_is_refused(tmp_path) -> None:
    with pytest.raises(HypothesisError, match="does not exist"):
        propose(graph(tmp_path), parent_id="hyp_nope")


def test_ancestry_and_descendants_walk_the_chain(tmp_path) -> None:
    store = graph(tmp_path)
    root = propose(store)
    current = root
    for depth in range(3):
        current = propose(
            store,
            statement=(
                f"Derivation number {depth} of the opening-range claim, testing whether the "
                "effect survives conditioning on a different market state entirely."
            ),
            parent_id=current.hypothesis_id,
        )
    assert len(store.ancestry(current.hypothesis_id)) == 3
    assert len(store.descendants(root.hypothesis_id)) == 3
    assert store.ancestry(current.hypothesis_id)[-1].hypothesis_id == root.hypothesis_id


def test_lineage_answers_where_it_came_from_and_what_tested_it(tmp_path) -> None:
    store = graph(tmp_path)
    node = propose(store)
    store.link(node.hypothesis_id, EdgeKind.TESTED_BY, "exp_1")
    store.link(node.hypothesis_id, EdgeKind.RESULT, "vd_1", note="failed G12")
    store.link(node.hypothesis_id, EdgeKind.SOURCED_FROM, "arxiv_x")
    lineage = store.lineage(node.hypothesis_id)
    assert lineage["experiments"] == ["exp_1"]
    assert lineage["results"] == ["vd_1"]
    assert lineage["sources"] == ["arxiv_x"]
    assert lineage["hypothesis"]["falsifiable_prediction"] == PREDICTION


def test_distinct_mechanisms_separates_depth_from_breadth(tmp_path) -> None:
    """A hundred trials of one idea is one mechanism, and must count as one."""
    store = graph(tmp_path)
    for index in range(6):
        propose(
            store,
            statement=(
                f"Variation {index}: opening-range continuation measured against a different "
                "volume threshold each time, with the same underlying explanation."
            ),
        )
    assert len(store.list("c1")) == 6
    assert store.distinct_mechanisms("c1") == 1


def test_status_reflects_evidence_and_is_never_evidence(tmp_path) -> None:
    store = graph(tmp_path)
    node = propose(store)
    store.set_status(node.hypothesis_id, HypothesisStatus.REFUTED)
    refreshed = store.get(node.hypothesis_id)
    assert refreshed is not None
    assert refreshed.status is HypothesisStatus.REFUTED
    # There is no route from a hypothesis into the judge's input.
    assert not hasattr(refreshed, "is_evidence")


def test_the_graph_survives_a_reopen(tmp_path) -> None:
    store = graph(tmp_path)
    node = propose(store)
    reopened = HypothesisGraph(tmp_path / "hyp.db").get(node.hypothesis_id)
    assert reopened is not None
    assert reopened.statement == STATEMENT
