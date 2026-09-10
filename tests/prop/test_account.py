"""The live account rule engine: buffers, breaches, and what it refuses to guess.

Every test here is written against a rule set built in the test rather than
against a shipped one. That is the property under test as much as any number:
the engine knows no firm's contract, so a change to one firm's terms can never
change what this file asserts.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest
from forge.prop import (
    AccountRules,
    AccountState,
    ClosedTrade,
    CustomLimit,
    Level,
    Metric,
    SessionWindow,
    TrailMode,
    assess,
    loss_floor,
    state_from_trades,
)
from pydantic import ValidationError

NOW = datetime(2026, 3, 4, 15, 0, tzinfo=UTC)


def rules(**overrides: object) -> AccountRules:
    base: dict[str, object] = {
        "name": "50k Evaluation",
        "starting_balance": 50_000.0,
        "maximum_loss": 2_000.0,
        "daily_loss_limit": 1_000.0,
        "profit_target": 3_000.0,
        "trail_mode": TrailMode.END_OF_DAY,
        "floor_cap": 50_000.0,
    }
    return AccountRules(**{**base, **overrides})  # type: ignore[arg-type]


def state(**overrides: object) -> AccountState:
    base: dict[str, object] = {
        "as_of": NOW,
        "balance": 50_000.0,
        "equity": 50_000.0,
        "high_water_balance": 50_000.0,
        "high_water_equity": 50_000.0,
    }
    return AccountState(**{**base, **overrides})  # type: ignore[arg-type]


# ── the loss floor ───────────────────────────────────────────────────────────


def test_a_static_floor_never_moves() -> None:
    static = rules(trail_mode=TrailMode.STATIC, floor_cap=None)
    assert loss_floor(static, state(high_water_balance=55_000, high_water_equity=55_000)) == 48_000


def test_an_end_of_day_floor_ratchets_on_settled_balance() -> None:
    account = rules(floor_cap=None)
    assert loss_floor(account, state(high_water_balance=51_500, high_water_equity=52_000)) == 49_500


def test_an_intraday_floor_ratchets_on_the_highest_equity_touched() -> None:
    """The harsher reading, and the one operators most often misjudge."""
    account = rules(trail_mode=TrailMode.INTRADAY, floor_cap=None)
    peaked = state(high_water_balance=51_500, high_water_equity=52_000)
    eod = loss_floor(rules(floor_cap=None), peaked)
    intraday = loss_floor(account, peaked)
    assert intraday == 50_000
    assert intraday > eod


def test_the_floor_cap_stops_the_ratchet() -> None:
    capped = rules(floor_cap=50_000.0)
    assert loss_floor(capped, state(high_water_balance=60_000, high_water_equity=60_000)) == 50_000


def test_the_floor_never_falls_below_where_it_started() -> None:
    account = rules(floor_cap=None)
    assert loss_floor(account, state(high_water_balance=49_000, high_water_equity=49_000)) == 48_000


# ── the daily limit ──────────────────────────────────────────────────────────


def test_open_loss_counts_against_the_daily_limit() -> None:
    """Assessing the day on settled balance alone reports an account as comfortable
    while an open position takes it through the limit."""
    assessment = assess(rules(), state(equity=49_600, realised_today=-200, unrealised=-200))
    daily = assessment.status("daily_loss")
    assert daily is not None
    assert daily.observed == 400.0
    assert daily.buffer == 600.0


def test_a_winning_day_consumes_none_of_the_allowance() -> None:
    assessment = assess(rules(), state(equity=50_400, realised_today=400))
    daily = assessment.status("daily_loss")
    assert daily is not None and daily.observed == 0.0 and daily.level is Level.OK


def test_reaching_the_daily_limit_is_a_breach_and_stops_trading() -> None:
    """Contracts are written as "must not reach", so equality breaches."""
    assessment = assess(rules(), state(equity=49_000, realised_today=-1_000))
    daily = assessment.status("daily_loss")
    assert daily is not None and daily.level is Level.BREACH
    assert "daily_loss" in assessment.breaches
    assert assessment.can_trade is False


def test_headroom_moves_through_caution_and_warning_before_it_breaches() -> None:
    levels = [
        assess(rules(), state(equity=50_000 - loss, realised_today=-loss)).status("daily_loss")
        for loss in (100, 600, 850, 1_000)
    ]
    assert [status.level for status in levels if status] == [
        Level.OK, Level.CAUTION, Level.WARNING, Level.BREACH
    ]


# ── the drawdown ─────────────────────────────────────────────────────────────


def test_the_drawdown_row_reports_the_buffer_to_the_floor() -> None:
    assessment = assess(rules(), state(equity=50_600, high_water_balance=51_000,
                                       high_water_equity=51_000))
    drawdown = assessment.status("max_drawdown")
    assert drawdown is not None
    assert drawdown.limit == 49_000.0
    assert drawdown.buffer == pytest.approx(1_600.0)


def test_touching_the_floor_is_a_breach() -> None:
    assessment = assess(rules(), state(balance=48_000, equity=48_000))
    assert assessment.status("max_drawdown").level is Level.BREACH  # type: ignore[union-attr]
    assert assessment.can_trade is False


# ── size and session ─────────────────────────────────────────────────────────


def test_the_position_limit_can_be_asked_one_trade_ahead() -> None:
    account = rules(max_position_contracts=3)
    now = assess(account, state(open_contracts=2))
    ahead = assess(account, state(open_contracts=2), proposed_contracts=2)
    assert now.status("position_limit").level is not Level.BREACH  # type: ignore[union-attr]
    assert ahead.status("position_limit").level is Level.BREACH  # type: ignore[union-attr]
    assert ahead.can_trade is False


def test_a_flat_account_outside_its_session_is_warned_rather_than_breached() -> None:
    account = rules(session_windows=(SessionWindow(opens=time(13, 30), closes=time(20, 0)),))
    outside = assess(account, state(as_of=datetime(2026, 3, 4, 2, 0, tzinfo=UTC)))
    session = outside.status("session")
    assert session is not None and session.level is Level.WARNING
    assert outside.can_trade is True


def test_holding_a_position_outside_the_session_is_a_breach() -> None:
    account = rules(session_windows=(SessionWindow(opens=time(13, 30), closes=time(20, 0)),))
    outside = assess(
        account, state(as_of=datetime(2026, 3, 4, 2, 0, tzinfo=UTC), open_contracts=1)
    )
    assert outside.status("session").level is Level.BREACH  # type: ignore[union-attr]
    assert outside.can_trade is False


def test_a_session_window_may_wrap_midnight() -> None:
    overnight = SessionWindow(opens=time(22, 0), closes=time(4, 0))
    assert overnight.contains(time(23, 30))
    assert overnight.contains(time(1, 0))
    assert not overnight.contains(time(12, 0))


# ── absent is not compliant ──────────────────────────────────────────────────


def test_an_unconfigured_rule_reports_not_assessed_rather_than_ok() -> None:
    """A green row meaning "we never checked" is worse than no row."""
    bare = AccountRules(name="Bare", starting_balance=50_000, maximum_loss=2_000)
    assessment = assess(bare, state())
    for key in ("daily_loss", "profit_target", "position_limit", "session", "consistency"):
        status = assessment.status(key)
        assert status is not None and status.level is Level.NOT_ASSESSED, key
        assert status.detail, f"{key} says nothing about why it was not assessed"


def test_not_assessed_outranks_ok_in_the_summary() -> None:
    """An account with unchecked rules must not summarise as clean."""
    bare = AccountRules(name="Bare", starting_balance=50_000, maximum_loss=2_000)
    assert assess(bare, state()).level is Level.NOT_ASSESSED


def test_a_per_trade_risk_cap_with_no_reported_risk_is_not_assessed() -> None:
    capped = rules(max_risk_per_trade=250.0)
    status = assess(capped, state(risk_per_trade=None)).status("risk_per_trade")
    assert status is not None and status.level is Level.NOT_ASSESSED
    assert "does not report" in status.detail


# ── consistency and tenure ───────────────────────────────────────────────────


def test_consistency_measures_the_best_day_against_settled_profit() -> None:
    account = rules(consistency_share=0.4)
    assessment = assess(account, state(daily_profits=(400.0, -150.0, 550.0)))
    status = assessment.status("consistency")
    assert status is not None
    assert status.observed == pytest.approx(550 / 950, abs=1e-4)
    assert status.level is Level.BREACH


def test_consistency_with_no_profitable_day_is_not_assessed() -> None:
    account = rules(consistency_share=0.4)
    status = assess(account, state(daily_profits=(-100.0, -50.0))).status("consistency")
    assert status is not None and status.level is Level.NOT_ASSESSED


def test_being_early_in_a_minimum_tenure_does_not_stop_trading() -> None:
    """Refusing every order for the first week would be a rule read backwards."""
    account = rules(minimum_trading_days=10)
    assessment = assess(account, state(trading_days=2))
    assert assessment.status("trading_days").level is Level.BREACH  # type: ignore[union-attr]
    assert "trading_days" in assessment.breaches
    assert assessment.can_trade is True


# ── firm-specific limits ─────────────────────────────────────────────────────


def test_a_custom_limit_is_evaluated_against_a_measured_metric() -> None:
    account = rules(
        custom_limits=(
            CustomLimit(key="max_trades", label="Trades per day", metric=Metric.TRADES_TODAY,
                        comparison="max", value=5),
        )
    )
    # One of five leaves 80% of the allowance, which is comfortable. Three of
    # five leaves 40% and reads as CAUTION, which is the engine working: a
    # ceiling that only speaks at the moment it is reached speaks too late.
    assert assess(account, state(trades_today=1)).status("max_trades").level is Level.OK  # type: ignore[union-attr]
    assert assess(account, state(trades_today=3)).status("max_trades").level is Level.CAUTION  # type: ignore[union-attr]
    breached = assess(account, state(trades_today=5))
    assert breached.status("max_trades").level is Level.BREACH  # type: ignore[union-attr]
    assert breached.can_trade is False


def test_an_advisory_custom_limit_does_not_stop_trading() -> None:
    account = rules(
        custom_limits=(
            CustomLimit(key="min_days", label="Minimum days", metric=Metric.TRADING_DAYS,
                        comparison="min", value=10, advisory=True),
        )
    )
    assessment = assess(account, state(trading_days=1))
    assert "min_days" in assessment.breaches
    assert assessment.can_trade is True


def test_profit_is_measured_against_the_accounts_own_starting_balance() -> None:
    """A 50k account at 50,200 is 200 up, not 50,200 up."""
    account = rules(
        custom_limits=(
            CustomLimit(key="cap", label="Profit cap", metric=Metric.PROFIT,
                        comparison="max", value=1_000),
        )
    )
    status = assess(account, state(equity=50_200)).status("cap")
    assert status is not None and status.observed == pytest.approx(200.0)


# ── the rule set refuses incoherent configuration ────────────────────────────


def test_a_daily_limit_larger_than_the_drawdown_is_refused() -> None:
    with pytest.raises(ValidationError, match="single permitted day would end the account"):
        rules(daily_loss_limit=5_000.0)


def test_a_floor_cap_below_the_initial_floor_is_refused() -> None:
    with pytest.raises(ValidationError, match="could never reach it"):
        rules(floor_cap=40_000.0)


def test_duplicate_custom_limit_keys_are_refused() -> None:
    limit = CustomLimit(key="x", label="X", metric=Metric.TRADES_TODAY, comparison="max", value=1)
    with pytest.raises(ValidationError, match="duplicate custom limit keys"):
        rules(custom_limits=(limit, limit))


def test_a_high_water_equity_below_the_balance_high_water_is_refused() -> None:
    """Getting this wrong understates the floor, which is the dangerous direction."""
    with pytest.raises(ValidationError, match="cannot sit below"):
        state(high_water_balance=51_000, high_water_equity=50_000)


# ── deriving a state from trades that actually happened ──────────────────────


def test_a_strategys_trades_replay_into_an_account_state() -> None:
    account = rules()
    trades = [
        ClosedTrade(exit_time=datetime(2026, 3, day, 20, tzinfo=UTC), pnl=pnl, contracts=2)
        for day, pnl in ((1, 300.0), (2, -150.0), (3, 420.0))
    ]
    derived = state_from_trades(account, trades)
    assert derived.balance == 50_570.0
    assert derived.high_water_balance == 50_570.0
    assert derived.trading_days == 3
    # The last day is "today" for this replay, so it is not yet a settled day.
    assert derived.daily_profits == (300.0, -150.0)
    assert derived.largest_order_contracts == 2


def test_a_replay_invents_no_open_position() -> None:
    """Closed trades carry neither, and guessing would put fiction in the one
    panel whose whole job is to be trusted."""
    derived = state_from_trades(
        rules(), [ClosedTrade(exit_time=NOW, pnl=100.0)]
    )
    assert derived.open_contracts == 0
    assert derived.unrealised == 0.0
    assert derived.risk_per_trade is None
