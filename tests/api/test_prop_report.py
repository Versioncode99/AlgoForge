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


def test_the_failure_tally_covers_every_account_it_is_reported_against(
    simulated: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    """The panel says "N accounts" and the rows have to add up to N.

    They did not: the route counted reasons over `outcomes`, which the engine
    truncates to a sample, and rendered the tally under `fail_count`. A thousand
    accounts failing on maximum loss showed as 100.
    """
    result, _ = simulated
    if result["fail_count"] == 0:
        pytest.skip("this sample produced no failures to tally")
    assert sum(result["failure_reasons"].values()) == result["fail_count"]


def test_the_inspection_sample_is_named_as_a_sample(
    simulated: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    result, _ = simulated
    assert result["outcome_sample_size"] == len(result["sampled_terminal_balances"])
    assert result["outcome_sample_size"] <= result["path_count"]
    # The middle outcome is over every path, not over the sample.
    assert result["median_terminal"] == result["tail_risk"]["terminal_median"]


def test_the_fan_arrives_with_the_report_and_counts_every_account(
    simulated: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    """The band is over all paths; `equity_paths` beside it is a sample of 100."""
    result, _ = simulated
    fan = result["equity_fan"]
    assert fan, "the fan is the population view and the report cannot omit it"
    assert fan[0]["day"] == 0
    assert fan[0]["p05"] == fan[0]["p95"] == result["rule"]["starting_balance"]
    for point in fan:
        assert point["live"] + point["resolved"] == result["path_count"]
        assert point["p05"] <= point["p25"] <= point["median"] <= point["p75"] <= point["p95"]
    assert len(result["equity_paths"]) <= result["path_count"]


def test_the_payout_mean_arrives_with_its_distribution(
    simulated: tuple[dict[str, Any], list[dict[str, Any]]],
) -> None:
    """A headline mean with nothing beside it is the thing the brief rules out."""
    result, _ = simulated
    payout = result["payout"]
    assert payout["mean"] == result["mean_payout"]
    assert payout["p05"] <= payout["median"] <= payout["p95"] <= payout["best"]
    assert 0.0 <= payout["any_probability"] <= 1.0


@pytest.fixture
def journey(client: TestClient) -> dict[str, Any]:
    strategy_id = client.post("/api/v1/strategies", json={"template": "momentum_breakout"}).json()[
        "data"
    ]["strategy_id"]
    assert (
        client.post(
            f"/api/v1/strategies/{strategy_id}/backtest",
            json={"dataset": "synthetic", "bar_count": BARS},
        ).status_code
        == 200
    )
    rules = client.get("/api/v1/prop/rules").json()["data"]
    challenge = next(r for r in rules if r["phase"] == "CHALLENGE")
    funded = next(
        r for r in rules if r["phase"] == "FUNDED" and r["provider"] == challenge["provider"]
    )
    response = client.post(
        f"/api/v1/strategies/{strategy_id}/prop/journey",
        json={
            "challenge_rule_id": challenge["rule_id"],
            "funded_rule_id": funded["rule_id"],
            "paths": 100,
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json()["data"])


def test_the_journey_reports_both_legs_against_their_own_denominators(
    journey: dict[str, Any],
) -> None:
    assert journey["challenge"]["reached"] == journey["path_count"]
    assert journey["funded"]["reached"] == journey["challenge"]["cleared"]
    assert journey["funded"]["cleared"] <= journey["funded"]["reached"]
    assert 0.0 <= journey["payout_probability"] <= 1.0
    assert journey["payout_interval_low"] <= journey["payout_interval_high"]


def test_the_journey_states_that_it_resamples_the_same_days_twice(
    journey: dict[str, Any],
) -> None:
    labels = set(journey["labels"])
    assert "TWO_STAGE_RESAMPLE" in labels
    assert "FUNDED_LEG_RESAMPLES_THE_SAME_DAYS" in labels


def test_the_journey_refuses_two_providers(client: TestClient) -> None:
    """An account that starts at one firm and finishes at another does not exist."""
    strategy_id = client.post("/api/v1/strategies", json={"template": "momentum_breakout"}).json()[
        "data"
    ]["strategy_id"]
    client.post(
        f"/api/v1/strategies/{strategy_id}/backtest",
        json={"dataset": "synthetic", "bar_count": BARS},
    )
    rules = client.get("/api/v1/prop/rules").json()["data"]
    challenge = next(r for r in rules if r["phase"] == "CHALLENGE")
    other = next(
        r for r in rules if r["phase"] == "FUNDED" and r["provider"] != challenge["provider"]
    )
    response = client.post(
        f"/api/v1/strategies/{strategy_id}/prop/journey",
        json={
            "challenge_rule_id": challenge["rule_id"],
            "funded_rule_id": other["rule_id"],
            "paths": 100,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "provider_mismatch"


def test_the_journey_refuses_an_unknown_rule(client: TestClient) -> None:
    strategy_id = client.post("/api/v1/strategies", json={"template": "momentum_breakout"}).json()[
        "data"
    ]["strategy_id"]
    client.post(
        f"/api/v1/strategies/{strategy_id}/backtest",
        json={"dataset": "synthetic", "bar_count": BARS},
    )
    response = client.post(
        f"/api/v1/strategies/{strategy_id}/prop/journey",
        json={"challenge_rule_id": "nope", "funded_rule_id": "also-nope", "paths": 100},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["rule_id"] == "nope"


def test_the_journey_refuses_a_strategy_with_no_backtest(client: TestClient) -> None:
    """The same refusal the single-leg route gives, from the same guard."""
    strategy_id = client.post("/api/v1/strategies", json={"template": "momentum_breakout"}).json()[
        "data"
    ]["strategy_id"]
    rules = client.get("/api/v1/prop/rules").json()["data"]
    challenge = next(r for r in rules if r["phase"] == "CHALLENGE")
    funded = next(
        r for r in rules if r["phase"] == "FUNDED" and r["provider"] == challenge["provider"]
    )
    response = client.post(
        f"/api/v1/strategies/{strategy_id}/prop/journey",
        json={
            "challenge_rule_id": challenge["rule_id"],
            "funded_rule_id": funded["rule_id"],
            "paths": 100,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "no_backtest"
