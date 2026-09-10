"""The pre-trade gate: every check, every refusal, and no way around it.

These are the tests that matter most in the repository. The gate is the boundary
between "the system decided something" and "the system did something", and every
assertion here describes a way an order could reach a venue that it must not.

Two properties are asserted throughout. Checks *accumulate* — an order blocked
for three reasons reports three, so its author does not discover them one round
trip at a time. And "could not be established" is never a pass on a check that
guards a downside: unknown borrow on a short sale is not permission.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from forge.execution import (
    BLOCKING_WHEN_UNKNOWN,
    CheckStatus,
    Decision,
    GateContext,
    InstrumentRule,
    MarketState,
    ProposedOrder,
    Side,
    screen,
    screen_all,
)
from forge.risk import PortfolioLimits

NOW = datetime(2026, 3, 4, 15, 0, tzinfo=UTC)


def context(**overrides: object) -> GateContext:
    base: dict[str, object] = {
        "now": NOW,
        "limits": PortfolioLimits(name="fund", max_leverage=2.0, max_position_weight=0.5),
        "capital": 10_000_000.0,
        "instruments": {
            "NQ": InstrumentRule(symbol="NQ", multiplier=20, adv_notional=5e10,
                                 max_order_contracts=10, max_position_contracts=20,
                                 shortable=True),
            "GC": InstrumentRule(symbol="GC", multiplier=100, adv_notional=3e10),
        },
        "market": {
            "NQ": MarketState(symbol="NQ", open=True, last_price=20_000,
                              last_price_at=NOW - timedelta(seconds=5)),
            "GC": MarketState(symbol="GC", open=True, last_price=2_400,
                              last_price_at=NOW - timedelta(seconds=5)),
        },
        "verdicts": {"good": "PASS", "bad": "FAIL"},
    }
    return GateContext(**{**base, **overrides})  # type: ignore[arg-type]


def order(**overrides: object) -> ProposedOrder:
    base: dict[str, object] = {
        "order_id": "o1",
        "symbol": "NQ",
        "side": Side.BUY,
        "quantity": 1,
        "strategy_id": "good",
        "reference_price": 20_000.0,
        "data_as_of": NOW,
        "created_at": NOW,
    }
    return ProposedOrder(**{**base, **overrides})  # type: ignore[arg-type]


def reasons(decision) -> str:
    return " | ".join(decision.reasons)


# ── the clean case ───────────────────────────────────────────────────────────


def test_a_clean_order_clears_and_carries_a_clearance_bound_to_itself() -> None:
    decision = screen(order(), context())
    assert decision.decision is Decision.ALLOW, reasons(decision)
    assert decision.clearance is not None
    assert decision.clearance.matches(order())
    assert decision.clearance.limits_name == "fund"


def test_every_check_runs_and_is_reported_even_when_the_order_clears() -> None:
    decision = screen(order(), context())
    names = {check.check for check in decision.checks}
    assert names >= {
        "kill_switch", "restricted_list", "instrument_eligibility", "validation_status",
        "market_state", "stale_data", "duplicate_order", "order_size", "position_limit",
        "borrow", "liquidity", "leverage", "concentration",
    }


# ── the refusals ─────────────────────────────────────────────────────────────


def test_the_kill_switch_refuses_everything() -> None:
    halted = context(limits=PortfolioLimits(name="fund", enabled=False))
    decision = screen(order(), halted)
    assert decision.decision is Decision.BLOCK
    assert "disabled" in reasons(decision)
    assert decision.clearance is None


def test_a_restricted_instrument_is_blocked_with_the_reason_verbatim() -> None:
    decision = screen(
        order(symbol="GC", reference_price=2_400.0),
        context(restricted={"GC": "under internal review"}),
    )
    assert decision.decision is Decision.BLOCK
    assert "under internal review" in reasons(decision)


def test_a_failed_strategy_cannot_trade() -> None:
    """The judge is authoritative at the gate too, not only in research."""
    decision = screen(order(strategy_id="bad"), context())
    assert decision.decision is Decision.BLOCK
    assert "verdict FAIL" in reasons(decision)


def test_a_never_judged_strategy_cannot_trade_either() -> None:
    decision = screen(order(strategy_id="unknown"), context())
    assert decision.decision is Decision.BLOCK
    assert "never been judged" in reasons(decision)


def test_a_closed_or_halted_market_blocks() -> None:
    closed = context(market={"NQ": MarketState(symbol="NQ", open=False, last_price=20_000,
                                               last_price_at=NOW)})
    assert screen(order(), closed).decision is Decision.BLOCK
    halted = context(market={"NQ": MarketState(symbol="NQ", halted=True, last_price=20_000,
                                               last_price_at=NOW)})
    assert "halted" in reasons(screen(order(), halted))


def test_a_stale_reference_price_blocks() -> None:
    decision = screen(order(data_as_of=NOW - timedelta(minutes=10)), context())
    assert decision.decision is Decision.BLOCK
    assert "old" in reasons(decision)


def test_a_price_stamped_in_the_future_is_not_treated_as_very_fresh() -> None:
    """A mis-stamped feed would otherwise pass the freshness check most easily."""
    decision = screen(order(data_as_of=NOW + timedelta(minutes=5)), context())
    assert decision.decision is Decision.BLOCK
    assert "future" in reasons(decision)


def test_an_identical_order_already_accepted_is_refused() -> None:
    proposed = order()
    seen = context(recent_fingerprints=(proposed.fingerprint(),))
    assert "identical" in reasons(screen(proposed, seen))


def test_an_order_above_its_per_order_cap_blocks() -> None:
    decision = screen(order(quantity=11), context())
    assert "per-order cap" in reasons(decision)


def test_a_projected_position_above_its_cap_blocks() -> None:
    decision = screen(order(quantity=5), context(positions={"NQ": 18}))
    assert "projected position 23 exceeds" in reasons(decision)


def test_going_short_an_instrument_of_unknown_borrow_is_blocked() -> None:
    """Unknown shortability is not permission, and the block says which check."""
    decision = screen(order(symbol="GC", side=Side.SELL, reference_price=2_400.0), context())
    assert decision.decision is Decision.BLOCK
    assert "borrow" in reasons(decision)
    borrow = next(c for c in decision.checks if c.check == "borrow")
    assert borrow.status is CheckStatus.NOT_APPLICABLE


def test_an_order_too_large_for_the_market_blocks() -> None:
    thin = context(
        instruments={"NQ": InstrumentRule(symbol="NQ", multiplier=20, adv_notional=1e6,
                                          shortable=True)},
    )
    decision = screen(order(quantity=1), thin)
    assert "average daily notional" in reasons(decision)


def test_the_leverage_ceiling_blocks_an_order_that_would_cross_it() -> None:
    loaded = context(current_gross=1.95)
    decision = screen(order(quantity=5), loaded)
    assert "leverage ceiling" in reasons(decision)


def test_the_single_name_ceiling_blocks_concentration() -> None:
    decision = screen(
        order(quantity=10),
        context(limits=PortfolioLimits(name="fund", max_position_weight=0.1, max_leverage=10)),
    )
    assert "single-name ceiling" in reasons(decision)


def test_reasons_accumulate_rather_than_stopping_at_the_first() -> None:
    decision = screen(
        order(strategy_id="bad", quantity=50, data_as_of=NOW - timedelta(hours=1)),
        context(restricted={"NQ": "review"}),
    )
    assert len(decision.reasons) >= 4, decision.reasons


# ── unknown is not a pass ────────────────────────────────────────────────────


def test_an_instrument_nobody_defined_is_blocked_rather_than_waved_through() -> None:
    decision = screen(order(symbol="ZN", reference_price=110.0), context())
    assert decision.decision is Decision.BLOCK
    assert "eligibility is unestablished" in reasons(decision)


def test_the_blocking_unknowns_are_the_ones_that_guard_a_downside() -> None:
    """Stated as data so the list is a rule rather than a habit."""
    assert frozenset(
        {"instrument_eligibility", "validation_status", "borrow", "market_state", "stale_data"}
    ) == BLOCKING_WHEN_UNKNOWN


def test_an_order_naming_no_strategy_is_blocked_for_want_of_evidence() -> None:
    decision = screen(order(strategy_id=""), context())
    assert decision.decision is Decision.BLOCK
    assert "names no strategy" in reasons(decision)


# ── a basket cannot do what one order could not ──────────────────────────────


def test_a_batch_threads_its_own_accepted_orders_through_the_limits() -> None:
    """Three orders each taking gross to 1.9 must not all clear against a 2.0 ceiling."""
    tight = context(
        limits=PortfolioLimits(name="fund", max_leverage=1.0, max_position_weight=1.0),
        capital=1_000_000.0,
    )
    batch = [order(order_id=f"o{index}", quantity=1) for index in range(4)]
    decisions = screen_all(batch, tight)
    # One NQ contract is 400,000 of a million, so at most two can clear.
    cleared = [decision for decision in decisions if decision.allowed]
    assert len(cleared) <= 2
    assert any("leverage" in reasons(decision) for decision in decisions if not decision.allowed)


def test_a_batch_refuses_its_own_duplicate() -> None:
    same = order()
    decisions = screen_all([same, same], context())
    assert decisions[0].allowed
    assert not decisions[1].allowed
    assert "identical" in reasons(decisions[1])


def test_a_batch_accumulates_positions_against_the_position_cap() -> None:
    # Concentration and leverage are set wide so the only thing that can stop the
    # third order is the contract cap this test is about.
    roomy = context(
        limits=PortfolioLimits(name="fund", max_leverage=100.0, max_position_weight=1.0)
    )
    batch = [order(order_id=f"o{index}", quantity=10) for index in range(4)]
    decisions = screen_all(batch, roomy)
    # Two lots of ten reach the twenty-contract cap; the third would cross it.
    assert sum(decision.allowed for decision in decisions) == 2
    assert "projected position 30 exceeds" in reasons(decisions[2])


# ── the clearance is bound to the order ──────────────────────────────────────


def test_the_fingerprint_changes_when_anything_about_the_order_changes() -> None:
    base = order()
    for change in ({"quantity": 2}, {"symbol": "GC"}, {"side": Side.SELL},
                   {"created_at": NOW + timedelta(seconds=1)}):
        assert base.fingerprint() != base.model_copy(update=change).fingerprint(), change


def test_a_clearance_does_not_match_an_order_edited_after_it_was_issued() -> None:
    decision = screen(order(), context())
    assert decision.clearance is not None
    assert not decision.clearance.matches(order(quantity=9))


def test_a_blocked_order_carries_no_clearance_at_all() -> None:
    decision = screen(order(strategy_id="bad"), context())
    assert decision.clearance is None


# ── the gate is a pure function of what it was handed ────────────────────────


def test_screening_the_same_order_twice_gives_the_same_answer() -> None:
    ctx = context()
    first, second = screen(order(), ctx), screen(order(), ctx)
    assert first.decision is second.decision
    assert first.reasons == second.reasons
    assert first.clearance and second.clearance
    assert first.clearance.fingerprint == second.clearance.fingerprint


def test_an_order_of_zero_or_negative_size_cannot_be_constructed() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        order(quantity=0)
