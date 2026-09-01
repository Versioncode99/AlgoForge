from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from forge.data.models import Bar, DataManifest
from forge.data.providers import LOCAL_FIXTURE
from forge.data.validation import validate_bars

ROOT = Path(__file__).resolve().parents[2]


def load_sample() -> list[Bar]:
    payload = json.loads((ROOT / "fixtures" / "market" / "mnq-sample.json").read_text("utf-8"))
    return [Bar.model_validate(item) for item in payload]


def test_sample_fixture_is_valid_and_reproducible() -> None:
    bars = load_sample()
    first = validate_bars(bars)
    second = validate_bars(bars)
    assert first.accepted
    assert first.content_hash == second.content_hash
    manifest = DataManifest.from_bars(LOCAL_FIXTURE, bars, ("SAMPLE_DATA", "UNCALIBRATED"))
    assert manifest.row_count == 3
    assert manifest.labels == ("SAMPLE_DATA", "UNCALIBRATED")


def test_duplicate_and_lookahead_timestamps_fail_g0() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    bad = Bar(
        symbol="MNQ",
        event_time=base,
        knowledge_time=base - timedelta(seconds=1),
        ingestion_time=base,
        open=1,
        high=2,
        low=0,
        close=1,
        volume=1,
        source="test",
    )
    receipt = validate_bars([bad, bad])
    assert not receipt.accepted
    assert "KNOWLEDGE_BEFORE_EVENT" in receipt.findings
    assert "DUPLICATE_BAR" in receipt.findings
