"""Skips must be explainable, durable, and separable from each other.

The defect these replace: one counter called ``skipped_by_memory`` that summed
duplicates, an empty frontier, an exhausted campaign, a novelty collision and a
builder that raised — then reported the sum as compute saved.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from forge.research.skips import (
    REFUSED_BY_DEFAULT,
    NoveltyLevel,
    SkipKind,
    SkipLedger,
    admits,
    level_from_score,
    rank,
    retry_rule,
)


@pytest.fixture
def ledger(tmp_path: Path) -> SkipLedger:
    return SkipLedger(tmp_path / "skips.db")


# ── the novelty hierarchy ────────────────────────────────────────────────────
def test_the_bands_are_ordered_from_identical_to_new() -> None:
    assert rank(NoveltyLevel.EXACT_DUPLICATE) < rank(NoveltyLevel.NEAR_DUPLICATE)
    assert rank(NoveltyLevel.SAME_CONSTRUCTION) < rank(NoveltyLevel.SAME_MECHANISM)
    assert rank(NoveltyLevel.RELATED_HYPOTHESIS) < rank(NoveltyLevel.NOVEL_HYPOTHESIS)
    assert rank(NoveltyLevel.NOVEL_HYPOTHESIS) < rank(NoveltyLevel.NOVEL_FEATURE)


def test_an_identical_score_is_an_exact_duplicate() -> None:
    assert level_from_score(1.0) is NoveltyLevel.EXACT_DUPLICATE
    assert level_from_score(0.99) is NoveltyLevel.EXACT_DUPLICATE


def test_a_shared_mechanism_is_recognised_even_when_the_words_differ() -> None:
    """The case a single combined score cannot express."""
    level = level_from_score(0.50, mechanism=0.9, features=0.2)
    assert level is NoveltyLevel.SAME_MECHANISM


def test_a_distinct_feature_set_reads_as_a_novel_feature() -> None:
    assert level_from_score(0.2, mechanism=0.1, features=0.0) is NoveltyLevel.NOVEL_FEATURE


def test_refinement_is_not_refused_by_default() -> None:
    """The bug that made the engine refuse 94% of its own proposals.

    Testing new parameters inside a known construction is ordinary research.
    What bounds it is the budget allocation, not the duplicate gate.
    """
    assert NoveltyLevel.SAME_CONSTRUCTION not in REFUSED_BY_DEFAULT
    assert admits(NoveltyLevel.SAME_CONSTRUCTION)
    assert not admits(NoveltyLevel.EXACT_DUPLICATE)
    assert not admits(NoveltyLevel.NEAR_DUPLICATE)


def test_a_discovery_campaign_can_raise_the_floor() -> None:
    assert not admits(NoveltyLevel.SAME_CONSTRUCTION, floor=NoveltyLevel.NOVEL_HYPOTHESIS)
    assert admits(NoveltyLevel.NOVEL_MECHANISM, floor=NoveltyLevel.NOVEL_HYPOTHESIS)


def test_only_an_exact_duplicate_is_never_retryable() -> None:
    permitted, condition = retry_rule(NoveltyLevel.EXACT_DUPLICATE)
    assert permitted is False
    assert "trial count" in condition
    permitted, condition = retry_rule(NoveltyLevel.NEAR_DUPLICATE)
    assert permitted is True
    # And it says under what circumstances, which is what stops a failed
    # hypothesis from permanently barring a legitimate follow-up.
    assert "mechanism" in condition


# ── the ledger ───────────────────────────────────────────────────────────────
def test_a_skip_records_what_it_collided_with(ledger: SkipLedger) -> None:
    skip = ledger.record(
        campaign_id="c1",
        kind=SkipKind.EXPERIMENT_CLAIMED,
        level=NoveltyLevel.EXACT_DUPLICATE,
        subject="vol_normalized_momentum lookback=20",
        reason="the identical experiment is already claimed",
        matched="exp-abc",
        matched_kind="experiment",
        similarity=1.0,
        template="vol_normalized_momentum",
        parameters={"lookback": 20.0},
    )
    assert skip.matched == "exp-abc"
    assert skip.retry_permitted is False
    assert skip.saved_compute is True
    assert "exp-abc" in skip.describe()


def test_useful_and_wasted_skips_are_separated(ledger: SkipLedger) -> None:
    """An empty frontier saved nobody any compute and must not read as a win."""
    ledger.record(
        campaign_id="c1",
        kind=SkipKind.EXPERIMENT_CLAIMED,
        level=NoveltyLevel.EXACT_DUPLICATE,
        subject="a",
        reason="claimed",
    )
    ledger.record(
        campaign_id="c1",
        kind=SkipKind.FAILURE_REGION,
        level=NoveltyLevel.NEAR_DUPLICATE,
        subject="b",
        reason="near a recorded failure",
    )
    ledger.record(
        campaign_id="c1",
        kind=SkipKind.NO_ELIGIBLE_WORK,
        level=NoveltyLevel.NOVEL_HYPOTHESIS,
        subject="c",
        reason="the frontier had nothing eligible",
    )
    ledger.record(
        campaign_id="c1",
        kind=SkipKind.CAMPAIGN_EXHAUSTED,
        level=NoveltyLevel.NOVEL_HYPOTHESIS,
        subject="d",
        reason="budget reached",
    )
    counts = ledger.counts("c1")
    assert counts["total"] == 4
    assert counts["useful"] == 2
    assert counts["wasted"] == 1
    # Exhaustion is neither: it is a fact about the programme.
    assert counts["neutral"] == 1


def test_identical_refusals_collapse_onto_one_row(ledger: SkipLedger) -> None:
    """Eight workers refusing the same proposal is one fact, not eight."""
    for worker in range(8):
        ledger.record(
            campaign_id="c1",
            kind=SkipKind.NOT_NOVEL,
            level=NoveltyLevel.NEAR_DUPLICATE,
            subject="momentum with a trend gate",
            reason="restates an existing hypothesis",
            matched="hyp-1",
            template="t",
            parameters={"a": 1.0},
            worker_id=str(worker),
        )
    counts = ledger.counts("c1")
    assert counts["total"] == 8
    assert counts["distinct"] == 1


def test_counts_are_per_campaign(ledger: SkipLedger) -> None:
    ledger.record(
        campaign_id="c1", kind=SkipKind.NOT_NOVEL, level=NoveltyLevel.NEAR_DUPLICATE,
        subject="a", reason="x",
    )
    ledger.record(
        campaign_id="c2", kind=SkipKind.NOT_NOVEL, level=NoveltyLevel.NEAR_DUPLICATE,
        subject="b", reason="x",
    )
    assert ledger.counts("c1")["total"] == 1
    assert ledger.counts("c2")["total"] == 1
    assert ledger.counts()["total"] == 2


def test_retryable_skips_are_the_open_questions(ledger: SkipLedger) -> None:
    ledger.record(
        campaign_id="c1", kind=SkipKind.EXPERIMENT_CLAIMED,
        level=NoveltyLevel.EXACT_DUPLICATE, subject="exact", reason="claimed",
    )
    ledger.record(
        campaign_id="c1", kind=SkipKind.FAILURE_REGION,
        level=NoveltyLevel.NEAR_DUPLICATE, subject="near", reason="region",
    )
    retryable = ledger.retryable("c1")
    assert [row["subject"] for row in retryable] == ["near"]
    assert retryable[0]["retry_condition"]


def test_the_ledger_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "skips.db"
    first = SkipLedger(path)
    first.record(
        campaign_id="c1", kind=SkipKind.NOT_NOVEL, level=NoveltyLevel.NEAR_DUPLICATE,
        subject="a", reason="x",
    )
    # A different process, the same file. The old counter lived on the engine
    # object and reset to zero here, which is why it could never be reconciled
    # against the campaign's own progress row.
    second = SkipLedger(path)
    assert second.counts("c1")["total"] == 1


def test_every_kind_and_level_appears_in_the_counts(ledger: SkipLedger) -> None:
    """Zeroes are reported, so a missing category cannot look like an absent one."""
    counts = ledger.counts("c1")
    assert set(counts["by_kind"]) == {str(k) for k in SkipKind}
    assert set(counts["by_level"]) == {str(level) for level in NoveltyLevel}
