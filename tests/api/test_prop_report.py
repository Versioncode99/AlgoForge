"""The successful path through the prop route, which nothing was exercising.

Both existing tests for `POST /strategies/{id}/prop` assert a *refusal* — no
backtest, or too few trading days. Neither ever reached a simulation, which is
how a complete `PropResult` type, four chart components and this whole route sat
in the repository unconnected to anything: nothing failed, because nothing ran.

What is checked here is that the payload a report would render is present, and
that every figure in it comes from the strategy's own trades rather than from
anywhere else.
"""

from __future__ import annotations

import pathlib
import shutil
from typing import Any

import pytest
from fastapi.testclient import TestClient
from forge.prop.engine import MIN_TRADING_DAYS
from forge_api.main import create_app

#: Enough one-minute synthetic bars to span the thirty trading days a prop
#: evaluation needs. Below this the route refuses, which is the behaviour the
#: other two tests cover.
BARS = 60_000


@pytest.fixture
def client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    import forge_api.main as main

    monkeypatch.setattr(main, "ROOT", tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    shutil.copytree(pathlib.Path("rules"), tmp_path / "rules", dirs_exist_ok=True)
    with TestClient(create_app(database_path=tmp_path / "test.db")) as client:
        yield client


@pytest.fixture
def simulated(client: TestClient) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    strategy_id = client.post("/api/v1/strategies", json={"template": "momentum_breakout"}).json()[
        "data"
    ]["strategy_id"]
    backtest = client.post(
        f"/api/v1/strategies/{strategy_id}/backtest",
        json={"dataset": "synthetic", "bar_count": BARS},
    )
    assert backtest.status_code == 200, backtest.text
    trades = backtest.json()["data"]["trades"]
    assert trades, "the fixture needs trades for there to be a daily series"

    rule_id = client.get("/api/v1/prop/rules").json()["data"][0]["rule_id"]
    response = client.post(
        f"/api/v1/strategies/{strategy_id}/prop", json={"rule_id": rule_id, "paths": 200}
    )
    assert response.status_code == 200, response.text
    return dict(response.json()["data"]), list(trades)


def test_the_report_carries_every_quantity_the_brief_asks_to_display(
    simulated: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    result, _ = simulated
    # pass, fail, expected value, payout, days to pass, drawdown distribution,
    # risk of ruin, percentile outcomes, phase, Monte Carlo equity paths.
    for field in (
        "pass_rate",
        "fail_count",
        "timeout_count",
        "mean_payout",
        "risk_of_ruin",
        "boundary_race",
        "target_reach_curve",
        "terminal_histogram",
        "return_drawdown_map",
        "tail_risk",
        "equity_paths",
        "failure_reasons",
        "labels",
        "trading_days",
    ):
        assert field in result, f"a report cannot be drawn without {field}"

    assert result["rule"]["phase"] in {"CHALLENGE", "FUNDED"}
    assert result["equity_paths"], "Monte Carlo paths are the point of the view"
    assert result["target_reach_curve"], "days-to-pass needs a curve"
    assert result["terminal_histogram"], "percentile outcomes need a distribution"
    assert result["return_drawdown_map"], "the drawdown distribution is per path"


def test_the_daily_series_is_the_strategy_s_own_trades(
    simulated: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    """Not a draw from a distribution — the actual fills, bucketed by exit day."""
    result, trades = simulated
    by_day: dict[str, float] = {}
    for trade in trades:
        day = str(trade["exit_time"])[:10]
        by_day[day] = by_day.get(day, 0.0) + float(trade["net_pnl"])
    expected = [by_day[day] for day in sorted(by_day)]

    assert result["trading_days"] == len(expected)
    assert len(result["daily_pnl"]) == len(expected)
    for observed, wanted in zip(result["daily_pnl"], expected, strict=True):
        assert observed == pytest.approx(wanted, abs=1e-6)
    assert sum(result["daily_pnl"]) == pytest.approx(
        sum(float(t["net_pnl"]) for t in trades), abs=1e-6
    )


def test_the_counts_are_consistent_with_each_other(
    simulated: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    result, _ = simulated
    assert result["pass_count"] + result["fail_count"] + result["timeout_count"] == (
        result["path_count"]
    )
    assert result["pass_rate"] == pytest.approx(result["pass_count"] / result["path_count"])
    assert result["risk_of_ruin"] == pytest.approx(result["fail_count"] / result["path_count"])
    assert result["interval_low"] <= result["pass_rate"] <= result["interval_high"]
    assert result["trading_days"] >= MIN_TRADING_DAYS


def test_the_assumptions_are_stated_and_describe_this_run(
    simulated: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    """A label that is always true is decoration; these have to say what happened."""
    result, _ = simulated
    labels = set(result["labels"])
    assert {"RESEARCH_ONLY", "BLOCK_BOOTSTRAP", "DAILY_SETTLEMENT_APPROXIMATION"} <= labels
    # Synthetic bars, so it must say so rather than claiming real data.
    assert "SYNTHETIC_DATA" in labels
    assert "REAL_DATA" not in labels
    assert any(item.startswith("EVIDENCE_TIER:") for item in labels)


def test_the_percentiles_are_ordered(
    simulated: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    tail = simulated[0]["tail_risk"]
    assert tail["terminal_p05"] <= tail["terminal_median"] <= tail["terminal_p95"]
    # Both are reported as positive loss magnitudes, so the conditional mean of
    # the tail is at least as large as the quantile that opens it. They are not
    # P&L figures, which is why the view has to label them as losses.
    assert tail["var_95"] >= 0.0 and tail["cvar_95"] >= 0.0
    assert tail["cvar_95"] >= tail["var_95"]


def test_days_to_pass_is_absent_rather_than_zero_when_nothing_passed(
    simulated: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    """A null and a zero are different claims and the view renders them apart."""
    race = simulated[0]["boundary_race"]
    if simulated[0]["pass_count"] == 0:
        assert race["target_days_median"] is None
    else:
        assert race["target_days_median"] is not None and race["target_days_median"] > 0
    if simulated[0]["fail_count"] == 0:
        assert race["loss_days_median"] is None
