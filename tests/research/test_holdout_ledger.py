from __future__ import annotations

import pytest
from forge.research import ResearchLedger


def test_holdout_can_be_consumed_once_per_lineage(tmp_path) -> None:
    ledger = ResearchLedger(tmp_path / "research.db")
    first = ledger.consume("opening_range_break", "split_123")
    assert first.lineage == "opening_range_break"

    with pytest.raises(ValueError, match="HOLDOUT_ALREADY_CONSUMED"):
        ledger.consume("opening_range_break", "split_456")

    saved = ledger.attach_result("opening_range_break", "backtest_holdout")
    assert saved.result_id == "backtest_holdout"
    assert ledger.get("opening_range_break") == saved
