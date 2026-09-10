"""Research memory: knowledge, and never proof.

The store exists so a sentence an analysis produced can be found again six
months later. Everything dangerous about it comes from the same place — a
recorded claim with a provenance chain attached *looks* like a measurement, so
the store has to make it impossible for one to be anything else.

Three properties carry that:

1. A statement is promoted from what an analysis computed, verbatim. Nothing
   typed can enter.
2. Nothing here is evidence, and no path exists from a finding into a verdict.
3. A finding that turns out to be wrong is marked, not deleted, because a reader
   has to be able to tell "we never looked" from "we looked and were wrong".
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from forge.research.knowledge import (
    FindingStatus,
    KnowledgeError,
    KnowledgeStore,
    ResearchFinding,
)


def artifact(**overrides: Any) -> dict[str, Any]:
    """The shape `ArtifactStore.get` returns for a saved analysis."""
    payload: dict[str, Any] = {
        "artifact_id": "art_0001",
        "analysis": "by_volatility_percentile",
        "title": "Expectancy by volatility percentile",
        "content_hash": "c" * 16,
        "covered_trades": 512,
        "total_trades": 550,
        "findings": (
            "Expectancy collapses above the 90th volatility percentile.",
            "The 30-70 band holds 61% of gross profit.",
        ),
        "provenance": {
            "strategy_id": "strat_nq_london",
            "backtest_id": "bt_0042",
            "dataset_key": "nq_1m",
            "spec_hash": "s" * 16,
            "code_hash": "d" * 16,
            "data_hash": "e" * 16,
            "evidence_tier": "VALIDATION_OOS",
            "partition_name": "VALIDATION",
        },
        "is_evidence": False,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def store(tmp_path: Path) -> KnowledgeStore:
    return KnowledgeStore(tmp_path / "knowledge.db")


# ── nothing typed can enter ──────────────────────────────────────────────────


def test_a_statement_that_was_never_computed_cannot_be_promoted(
    store: KnowledgeStore,
) -> None:
    """The one that matters.

    A free-text claim carrying a real strategy id, a real backtest id and a real
    data hash would be indistinguishable, on the screen and in the database,
    from a sentence an analysis actually produced. So the statement has to match
    something the artifact says.
    """
    with pytest.raises(KnowledgeError, match="not one this artifact produced"):
        store.promote(artifact(), "This strategy is profitable in every regime.")
    assert store.count() == 0


def test_a_near_miss_is_still_a_miss(store: KnowledgeStore) -> None:
    """Paraphrase is the likely way this gets defeated, so it is checked."""
    with pytest.raises(KnowledgeError):
        store.promote(artifact(), "Expectancy collapses above the 90th percentile.")
    with pytest.raises(KnowledgeError):
        store.promote(artifact(), "expectancy collapses above the 90th volatility percentile.")


def test_surrounding_whitespace_is_not_a_different_claim(store: KnowledgeStore) -> None:
    finding = store.promote(
        artifact(), "  Expectancy collapses above the 90th volatility percentile.  "
    )
    assert finding.statement == "Expectancy collapses above the 90th volatility percentile."


def test_an_empty_statement_is_refused(store: KnowledgeStore) -> None:
    with pytest.raises(KnowledgeError, match="needs a statement"):
        store.promote(artifact(), "   ")


def test_an_artifact_with_no_id_cannot_be_cited(store: KnowledgeStore) -> None:
    payload = artifact()
    payload["artifact_id"] = ""
    payload["provenance"] = dict(payload["provenance"])
    with pytest.raises(KnowledgeError, match="no id"):
        store.promote(payload, payload["findings"][0])


# ── provenance travels with the sentence ─────────────────────────────────────


def test_the_whole_chain_is_recorded(store: KnowledgeStore) -> None:
    finding = store.promote(artifact(), artifact()["findings"][0], note="worth re-checking")
    assert finding.strategy_id == "strat_nq_london"
    assert finding.backtest_id == "bt_0042"
    assert finding.dataset_key == "nq_1m"
    assert finding.spec_hash and finding.code_hash and finding.data_hash
    assert finding.evidence_tier == "VALIDATION_OOS"
    assert finding.artifact_id == "art_0001"
    assert finding.artifact_content_hash == "c" * 16
    assert finding.analysis == "by_volatility_percentile"
    assert finding.note == "worth re-checking"


def test_the_sample_the_claim_rests_on_travels_with_it(store: KnowledgeStore) -> None:
    """Eleven trades and nine hundred are different claims in the same words."""
    finding = store.promote(artifact(), artifact()["findings"][0])
    assert finding.covered_trades == 512
    assert finding.total_trades == 550


def test_promoting_the_same_sentence_twice_is_one_finding(store: KnowledgeStore) -> None:
    first = store.promote(artifact(), artifact()["findings"][0])
    second = store.promote(artifact(), artifact()["findings"][0])
    assert first.finding_id == second.finding_id
    assert store.count() == 1


def test_the_same_sentence_from_a_different_run_is_a_different_finding(
    store: KnowledgeStore,
) -> None:
    """Two runs agreeing is worth more than one run saying it twice."""
    store.promote(artifact(), artifact()["findings"][0])
    other = artifact(artifact_id="art_0002")
    store.promote(other, other["findings"][0])
    assert store.count() == 2


# ── never evidence ───────────────────────────────────────────────────────────


def test_a_finding_declares_that_it_is_not_evidence(store: KnowledgeStore) -> None:
    finding = store.promote(artifact(), artifact()["findings"][0])
    assert finding.is_evidence is False
    assert finding.model_dump(mode="json")["is_evidence"] is False


def test_the_model_has_no_field_a_verdict_could_read() -> None:
    """No grade, no verdict, no pass — nothing shaped like a decision."""
    fields = set(ResearchFinding.model_fields)
    assert not fields & {"grade", "decision", "verdict", "gate", "passed", "score", "confidence"}


def _imported_modules(path: Path) -> set[str]:
    """Every module a file imports, read from its syntax rather than its text.

    Substring matching would trip over the word appearing in a docstring, which
    is exactly where these modules explain why they do not import each other.
    """
    import ast

    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_nothing_in_the_judge_imports_the_knowledge_store() -> None:
    """The separation is structural, so it is checked structurally.

    A judge that could read research memory would be a judge whose verdict
    depends on what somebody chose to write down — which is precisely the
    contamination the architecture forbids.
    """
    judge_dir = Path(__file__).resolve().parents[2] / "packages" / "forge" / "judge"
    for module in sorted(judge_dir.rglob("*.py")):
        imported = _imported_modules(module)
        assert not any("knowledge" in name for name in imported), (
            f"{module.name} imports the knowledge store"
        )
        assert not any("memory" in name for name in imported), (
            f"{module.name} imports research memory"
        )


def test_the_knowledge_store_never_reaches_for_the_judge() -> None:
    """And the reverse direction, so neither end can grow the link later."""
    module = (
        Path(__file__).resolve().parents[2] / "packages" / "forge" / "research" / "knowledge.py"
    )
    imported = _imported_modules(module)
    assert not any(name.startswith("forge.judge") for name in imported)


# ── wrong is recorded, not erased ────────────────────────────────────────────


def test_a_retraction_keeps_the_finding_and_states_the_reason(store: KnowledgeStore) -> None:
    finding = store.promote(artifact(), artifact()["findings"][0])
    retracted = store.retract(finding.finding_id, "the 2023 rerun contradicted it")
    assert retracted.status is FindingStatus.RETRACTED
    assert retracted.retraction_reason == "the 2023 rerun contradicted it"
    # Still there, still readable.
    assert store.get(finding.finding_id) is not None
    assert store.count() == 1


def test_a_retraction_without_a_reason_is_refused(store: KnowledgeStore) -> None:
    finding = store.promote(artifact(), artifact()["findings"][0])
    with pytest.raises(KnowledgeError, match="needs a reason"):
        store.retract(finding.finding_id, "  ")
    assert store.get(finding.finding_id).status is FindingStatus.STANDING  # type: ignore[union-attr]


def test_recall_does_not_hand_back_withdrawn_claims_by_default(store: KnowledgeStore) -> None:
    standing = store.promote(artifact(), artifact()["findings"][0])
    withdrawn = store.promote(artifact(), artifact()["findings"][1])
    store.retract(withdrawn.finding_id, "measured over the wrong partition")

    ids = {item.finding_id for item in store.recall()}
    assert ids == {standing.finding_id}
    # An audit asks for everything, and gets it.
    assert len(store.recall(status=None)) == 2


def test_a_finding_can_be_superseded_and_points_forward(store: KnowledgeStore) -> None:
    older = store.promote(artifact(), artifact()["findings"][0])
    newer_artifact = artifact(artifact_id="art_0002")
    newer = store.promote(newer_artifact, newer_artifact["findings"][0])
    updated = store.supersede(older.finding_id, newer.finding_id)
    assert updated.status is FindingStatus.SUPERSEDED
    assert updated.superseded_by == newer.finding_id
    assert {item.finding_id for item in store.recall()} == {newer.finding_id}


def test_a_finding_cannot_supersede_itself(store: KnowledgeStore) -> None:
    finding = store.promote(artifact(), artifact()["findings"][0])
    with pytest.raises(KnowledgeError, match="cannot supersede itself"):
        store.supersede(finding.finding_id, finding.finding_id)


def test_superseding_with_something_that_does_not_exist_is_refused(
    store: KnowledgeStore,
) -> None:
    finding = store.promote(artifact(), artifact()["findings"][0])
    with pytest.raises(KnowledgeError, match="to supersede it with"):
        store.supersede(finding.finding_id, "finding_nope")


# ── recall ───────────────────────────────────────────────────────────────────


def test_recall_filters_by_strategy_and_by_text(store: KnowledgeStore) -> None:
    store.promote(artifact(), artifact()["findings"][0])
    other = artifact(artifact_id="art_0003")
    other["provenance"] = {**other["provenance"], "strategy_id": "strat_other"}
    store.promote(other, other["findings"][1])

    assert len(store.recall(strategy_id="strat_nq_london")) == 1
    assert len(store.recall(strategy_id="strat_other")) == 1
    assert len(store.recall(query="volatility percentile")) == 1
    assert len(store.recall(query="gross profit")) == 1
    assert store.recall(query="nothing says this") == []


def test_findings_survive_a_reopen(tmp_path: Path) -> None:
    """Durable is the whole word in "durable memory"."""
    path = tmp_path / "knowledge.db"
    first = KnowledgeStore(path)
    finding = first.promote(artifact(), artifact()["findings"][0])

    reopened = KnowledgeStore(path)
    recovered = reopened.get(finding.finding_id)
    assert recovered is not None
    assert recovered.statement == finding.statement
    assert recovered.spec_hash == finding.spec_hash


def test_counts_are_reported_by_status(store: KnowledgeStore) -> None:
    a = store.promote(artifact(), artifact()["findings"][0])
    store.promote(artifact(), artifact()["findings"][1])
    store.retract(a.finding_id, "superseded by a longer window")
    assert store.counts() == {"STANDING": 1, "RETRACTED": 1}


def test_created_at_is_recorded_in_utc(store: KnowledgeStore) -> None:
    finding = store.promote(
        artifact(), artifact()["findings"][0], now=datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    )
    recovered = store.get(finding.finding_id)
    assert recovered is not None
    assert recovered.created_at == datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
