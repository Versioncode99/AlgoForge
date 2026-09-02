from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from forge.data.models import Bar
from forge.research import chronological_split


def bars(count: int) -> list[Bar]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return [
        Bar(
            symbol="MNQ.CME",
            event_time=start + timedelta(minutes=i),
            knowledge_time=start + timedelta(minutes=i),
            ingestion_time=start + timedelta(minutes=i, seconds=1),
            open=20_000 + i,
            high=20_001 + i,
            low=19_999 + i,
            close=20_000.5 + i,
            volume=1000,
            source="fixture",
        )
        for i in range(count)
    ]


def test_split_is_chronological_purged_non_overlapping_and_stable() -> None:
    source = bars(3000)
    first = chronological_split(source, warmup_bars=100)
    second = chronological_split(source, warmup_bars=100)

    assert first.receipt == second.receipt
    assert first.receipt.development.end_index + 100 == first.receipt.validation.start_index
    assert first.receipt.validation.end_index + 100 == first.receipt.holdout.start_index
    assert first.development[-1].event_time < first.validation[0].event_time
    assert first.validation[-1].event_time < first.holdout[0].event_time
    assert first.receipt.validation.evidence_tier == "VALIDATION_OOS"
    assert first.receipt.holdout.evidence_tier == "HOLDOUT"


def test_split_refuses_short_or_unsorted_data() -> None:
    with pytest.raises(ValueError, match="INSUFFICIENT_SPLIT_BARS"):
        chronological_split(bars(400), warmup_bars=100)
    unsorted = bars(2000)
    unsorted[3], unsorted[4] = unsorted[4], unsorted[3]
    with pytest.raises(ValueError, match="strictly chronological"):
        chronological_split(unsorted, warmup_bars=50)
