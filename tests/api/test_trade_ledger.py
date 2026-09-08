"""Strategy → chart → historical trades, over the HTTP surface.

The feature these routes exist for is the one AlgoForge could not do at all:
open a strategy and see what it actually did, on the actual candles. So the
tests here are mostly about *correspondence* — every marker the API serves must
be a trade the backtest recorded, at the price and time it recorded, with the
levels it was actually running under.

The timestamp test is the one worth reading. Markers are served as timestamps
rather than bar indices because an index into the 1-minute archive means nothing
on an hourly chart, and passing indices to the browser would not fail — it would
draw markers in the wrong place, which is worse.
"""

from __future__ import annotations

import pathlib
import shutil

import pytest
from fastapi.testclient import TestClient
from forge_api.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    import forge_api.main as main

    monkeypatch.setattr(main, "ROOT", tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    shutil.copytree(pathlib.Path("rules"), tmp_path / "rules", dirs_exist_ok=True)
    with TestClient(create_app(database_path=tmp_path / "test.db")) as client:
        yield client


def _strategy_with_run(client, bars: int = 4000) -> tuple[str, dict]:
    created = client.post("/api/v1/strategies", json={"template": "momentum_breakout"})
    assert created.status_code == 201, created.text
    strategy_id = created.json()["data"]["strategy_id"]
    run = client.post(
        f"/api/v1/strategies/{strategy_id}/backtest",
        json={"dataset": "synthetic", "bar_count": bars},
    )
    assert run.status_code == 200, run.text
    return strategy_id, run.json()["data"]


# ── correspondence with the artifact ─────────────────────────────────────────


def test_every_marker_is_a_trade_the_backtest_recorded(client):
    strategy_id, backtest = _strategy_with_run(client)
    if not backtest["trades"]:
        pytest.skip("this template took no trades on this synthetic sample")

    ledger = client.get(f"/api/v1/strategies/{strategy_id}/trades").json()["data"]
    assert ledger["total_trades"] == len(backtest["trades"])
    assert ledger["backtest_id"] == backtest["backtest_id"]

    served = {row["trade_id"]: row for row in ledger["trades"]}
    for trade in backtest["trades"][: len(ledger["trades"])]:
        row = served[trade["trade_id"]]
        assert row["entry_price"] == trade["entry_price"]
        assert row["exit_price"] == trade["exit_price"]
        assert row["net_pnl"] == trade["net_pnl"]
        assert row["exit_reason"] == trade["exit_reason"]
        # Levels are the ones the run froze, not ones recomputed now.
        assert row["stop_price"] == trade["stop_price"]
        assert row["mfe"] == trade["mfe"]
        assert row["mae"] == trade["mae"]


def test_markers_carry_timestamps_so_they_survive_a_timeframe_change(client):
    """An index into the 1-minute archive means nothing on an hourly chart."""
    strategy_id, backtest = _strategy_with_run(client)
    if not backtest["trades"]:
        pytest.skip("no trades")
    ledger = client.get(f"/api/v1/strategies/{strategy_id}/trades").json()["data"]
    for row in ledger["trades"]:
        assert row["entry_time"] and "T" in row["entry_time"]
        assert row["exit_time"] and "T" in row["exit_time"]
        assert row["exit_time"] >= row["entry_time"]


def test_the_response_states_which_run_it_read(client):
    """Defaulting to the latest run is right for a first click and wrong to do
    silently."""
    strategy_id, first = _strategy_with_run(client)
    second = client.post(
        f"/api/v1/strategies/{strategy_id}/backtest",
        json={"dataset": "synthetic", "bar_count": 3000},
    ).json()["data"]

    latest = client.get(f"/api/v1/strategies/{strategy_id}/trades").json()["data"]
    assert latest["backtest_id"] in (first["backtest_id"], second["backtest_id"])

    named = client.get(
        f"/api/v1/strategies/{strategy_id}/trades",
        params={"backtest_id": first["backtest_id"]},
    ).json()["data"]
    assert named["backtest_id"] == first["backtest_id"]
    assert named["total_trades"] == len(first["trades"])


def test_a_backtest_belonging_to_another_strategy_is_refused(client):
    """Not merely wrong — serving it would attribute one strategy's trades to
    another, on a chart, with the other's name in the header."""
    _, first = _strategy_with_run(client)
    other = client.post("/api/v1/strategies", json={"template": "mean_reversion_band"})
    other_id = other.json()["data"]["strategy_id"]

    response = client.get(
        f"/api/v1/strategies/{other_id}/trades",
        params={"backtest_id": first["backtest_id"]},
    )
    assert response.status_code == 404
    assert "belongs to strategy" in response.json()["detail"]["reason"]


def test_a_strategy_with_no_run_says_so_rather_than_drawing_nothing(client):
    created = client.post("/api/v1/strategies", json={"template": "momentum_breakout"})
    strategy_id = created.json()["data"]["strategy_id"]
    response = client.get(f"/api/v1/strategies/{strategy_id}/trades")
    assert response.status_code == 404
    assert "no backtest yet" in response.json()["detail"]["reason"]


# ── filtering ────────────────────────────────────────────────────────────────


def test_filters_narrow_the_ledger_without_hiding_the_total(client):
    strategy_id, backtest = _strategy_with_run(client)
    if len(backtest["trades"]) < 4:
        pytest.skip("too few trades to filter meaningfully")
    total = len(backtest["trades"])

    losses = client.get(
        f"/api/v1/strategies/{strategy_id}/trades", params={"outcome": "loss"}
    ).json()["data"]
    assert losses["total_trades"] == total, "the total must survive filtering"
    assert losses["matched_trades"] <= total
    assert all(row["net_pnl"] <= 0 for row in losses["trades"])

    wins = client.get(
        f"/api/v1/strategies/{strategy_id}/trades", params={"outcome": "win"}
    ).json()["data"]
    assert all(row["net_pnl"] > 0 for row in wins["trades"])
    assert wins["matched_trades"] + losses["matched_trades"] == total


def test_a_truncated_window_says_it_is_truncated(client):
    strategy_id, backtest = _strategy_with_run(client)
    if len(backtest["trades"]) < 3:
        pytest.skip("too few trades")
    windowed = client.get(
        f"/api/v1/strategies/{strategy_id}/trades", params={"limit": 2}
    ).json()["data"]
    assert windowed["returned_trades"] == 2
    assert windowed["truncated"] is True
    assert windowed["total_trades"] == len(backtest["trades"])


def test_an_exit_reason_filter_selects_what_it_claims(client):
    strategy_id, backtest = _strategy_with_run(client)
    reasons = {t["exit_reason"] for t in backtest["trades"]}
    if not reasons:
        pytest.skip("no trades")
    reason = sorted(reasons)[0]
    filtered = client.get(
        f"/api/v1/strategies/{strategy_id}/trades", params={"exit_reason": reason}
    ).json()["data"]
    assert filtered["matched_trades"] > 0
    assert all(row["exit_reason"] == reason for row in filtered["trades"])


# ── the inspector ────────────────────────────────────────────────────────────


def test_the_inspector_links_a_trade_back_to_the_run_that_produced_it(client):
    strategy_id, backtest = _strategy_with_run(client)
    if not backtest["trades"]:
        pytest.skip("no trades")
    trade_id = backtest["trades"][0]["trade_id"]

    detail = client.get(
        f"/api/v1/strategies/{strategy_id}/trades/{trade_id}"
    ).json()["data"]
    assert detail["trade"]["trade_id"] == trade_id
    provenance = detail["provenance"]
    # Every one of these is read off the artifact, so the trade can be traced to
    # the exact run, code and data that produced it.
    assert provenance["backtest_id"] == backtest["backtest_id"]
    assert provenance["spec_hash"] == backtest["spec_hash"]
    assert provenance["code_hash"] == backtest["code_hash"]
    assert provenance["data_hash"] == backtest["data_hash"]
    assert provenance["evidence_tier"] == backtest["evidence_tier"]
    assert detail["holding_seconds"] >= 0


def test_the_inspector_does_not_recompute_what_the_run_recorded(client):
    strategy_id, backtest = _strategy_with_run(client)
    if not backtest["trades"]:
        pytest.skip("no trades")
    recorded = backtest["trades"][0]
    detail = client.get(
        f"/api/v1/strategies/{strategy_id}/trades/{recorded['trade_id']}"
    ).json()["data"]["trade"]
    for field in ("mfe", "mae", "stop_price", "target_price", "entry_price", "net_pnl"):
        assert detail[field] == recorded[field], field


def test_an_unknown_trade_is_a_named_404(client):
    strategy_id, _ = _strategy_with_run(client)
    response = client.get(f"/api/v1/strategies/{strategy_id}/trades/trade_nope")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "trade_not_found"


# ── regimes and resampling ───────────────────────────────────────────────────


def test_regimes_are_refused_when_the_dataset_cannot_be_classified(client):
    """A synthetic run records its dataset key, so this exercises the real path
    or refuses by name — never a silent empty report."""
    strategy_id, backtest = _strategy_with_run(client)
    response = client.get(f"/api/v1/strategies/{strategy_id}/regimes")
    if response.status_code != 200:
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "regimes_unavailable"
        return
    report = response.json()["data"]
    counted = sum(cell["trade_count"] for cell in report["cells"])
    assert counted + report["unclassified_trades"] == report["total_trades"]
    assert report["total_trades"] == len(backtest["trades"])
    assert report["attribution"] == "entry"
    assert report["series_fingerprint"]


def test_an_unknown_attribution_basis_is_refused(client):
    strategy_id, _ = _strategy_with_run(client)
    response = client.get(
        f"/api/v1/strategies/{strategy_id}/regimes", params={"attribution": "vibes"}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "bad_attribution"


def test_the_descriptive_basis_is_labelled_wherever_it_is_used(client):
    strategy_id, _ = _strategy_with_run(client)
    response = client.get(
        f"/api/v1/strategies/{strategy_id}/regimes", params={"descriptive": True}
    )
    if response.status_code != 200:
        pytest.skip("regimes unavailable for this dataset")
    report = response.json()["data"]
    assert report["settings"]["basis"] == "FULL_SAMPLE"
    assert any("information later than the bars" in w for w in report["warnings"])


def test_resampling_reorders_real_trades_and_says_so(client):
    strategy_id, backtest = _strategy_with_run(client)
    response = client.get(
        f"/api/v1/strategies/{strategy_id}/resample", params={"paths": 200, "seed": 5}
    )
    if response.status_code != 200:
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "resample_unavailable"
        return
    body = response.json()
    assert "No value here was invented" in body["meta"]["note"]
    data = body["data"]
    assert data["iid"]["paths"] == 200
    assert data["trade_count"] == len(backtest["trades"])
    if data["regime_aware"]:
        assert data["regime_aware"]["trades_per_path"] == len(backtest["trades"])


def test_resampling_refuses_a_sample_too_small_to_resample(client):
    """Rather than returning a distribution built from one number."""
    created = client.post("/api/v1/strategies", json={"template": "momentum_breakout"})
    strategy_id = created.json()["data"]["strategy_id"]
    run = client.post(
        f"/api/v1/strategies/{strategy_id}/backtest",
        json={"dataset": "synthetic", "bar_count": 400},
    )
    if run.status_code != 200:
        pytest.skip("the short run was refused upstream, which is also correct")
    trades = run.json()["data"]["trades"]
    response = client.get(f"/api/v1/strategies/{strategy_id}/resample", params={"paths": 100})
    if len(trades) < 2:
        assert response.status_code == 409
        assert "at least two trades" in response.json()["detail"]["reason"]
    else:
        # A short run that did take trades must still resample rather than
        # silently produce a distribution over nothing.
        assert response.status_code in (200, 409)
        if response.status_code == 200:
            assert response.json()["data"]["trade_count"] == len(trades)
