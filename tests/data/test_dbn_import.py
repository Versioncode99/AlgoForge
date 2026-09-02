from __future__ import annotations

import pandas as pd
import pytest
from forge.data.dbn_import import (
    DbnImportError,
    _front_month,
    _root_of,
    cached_batch,
    is_outright,
)


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [("GCZ1", "GC"), ("NQZ4", "NQ"), ("MNQH5", "MNQ"), ("NQ.c.0", "NQ"), ("GCV1", "GC")],
)
def test_contract_symbols_resolve_to_their_root(symbol: str, expected: str):
    assert _root_of(symbol) == expected


@pytest.mark.parametrize("spread", ["GCZ1-GCG2", "NQH5-NQM5", "GC:BF", "NQZ4 NQH5"])
def test_calendar_spreads_are_rejected(spread: str):
    """Spread prices are differences, not levels; one would corrupt the series."""
    assert is_outright(spread) is False


@pytest.mark.parametrize("outright", ["GCZ1", "NQZ4", "MNQH5"])
def test_outright_contracts_are_kept(outright: str):
    assert is_outright(outright) is True


def test_front_month_keeps_the_highest_volume_contract_per_minute():
    """A batch archive holds every expiry at once; only the liquid one belongs."""
    frame = pd.DataFrame(
        {
            "event_time": pd.to_datetime(
                ["2026-01-01T00:00Z", "2026-01-01T00:00Z", "2026-01-01T00:01Z"], utc=True
            ),
            "open": [1.0, 2.0, 3.0],
            "high": [1.0, 2.0, 3.0],
            "low": [1.0, 2.0, 3.0],
            "close": [1.0, 2.0, 3.0],
            "volume": [10.0, 500.0, 7.0],
        }
    )
    collapsed = _front_month(frame)
    assert len(collapsed) == 2, "one bar per timestamp"
    first = collapsed[collapsed["event_time"] == pd.Timestamp("2026-01-01T00:00Z")]
    assert float(first["volume"].iloc[0]) == 500.0, "kept the liquid contract"
    assert float(first["close"].iloc[0]) == 2.0


def test_missing_archive_is_reported_not_swallowed(tmp_path):
    from forge.data.dbn_import import import_batch

    with pytest.raises(DbnImportError, match="does not exist"):
        import_batch(tmp_path / "nope.zip", tmp_path, symbol="NQ", dataset_key="nq_test", root="NQ")


def test_cached_batch_returns_none_when_not_imported(tmp_path):
    assert cached_batch(tmp_path, "nq_1m_16y") is None


def test_cached_batch_finds_an_imported_archive(tmp_path):
    folder = tmp_path / "databento-batch"
    folder.mkdir(parents=True)
    (folder / "nq_1m_16y.parquet").write_bytes(b"")
    assert cached_batch(tmp_path, "nq_1m_16y") is not None
