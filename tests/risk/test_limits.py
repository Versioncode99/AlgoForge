"""Risk limits: refusal is the default, and every breach is reported at once.

The layer is consulted by the lifecycle rather than by execution code, because
the execution half does not exist. That ordering is the only safe one while it
does not: limits that exist before there is anything to limit cannot be
forgotten when there is.
"""

from __future__ import annotations

import pytest
from forge.risk import ExecutionIntent, RiskProfile, RiskRefusal, evaluate


def profile(**overrides: object) -> RiskProfile:
    base: dict[str, object] = {
        "name": "conservative",
        "max_position_contracts": 2,
        "max_order_contracts": 1,
        "max_daily_loss": 250.0,
        "max_drawdown": 500.0,
        "max_open_orders": 4,
        "instruments": ("MNQ.CME",),
    }
    return RiskProfile(**{**base, **overrides})  # type: ignore[arg-type]


def intent(**overrides: object) -> ExecutionIntent:
    base: dict[str, object] = {
        "strategy_id": "demo",
        "instrument": "MNQ.CME",
        "contracts": 1,
        "live": False,
    }
    return ExecutionIntent(**{**base, **overrides})  # type: ignore[arg-type]


def test_a_conforming_intent_is_allowed() -> None:
    decision = evaluate(profile(), intent())
    assert decision.allowed is True
    assert decision.reasons == ()


# ── the kill switch ──────────────────────────────────────────────────────────


def test_a_disabled_profile_refuses_everything() -> None:
    decision = evaluate(profile(enabled=False), intent())
    assert decision.allowed is False
    assert "kill switch" in decision.reasons[0]


def test_the_refusal_names_the_profile_so_why_nothing_runs_has_an_answer() -> None:
    with pytest.raises(RiskRefusal, match="conservative"):
        evaluate(profile(enabled=False), intent()).raise_if_refused()


# ── paper and live are separate permissions ──────────────────────────────────


def test_a_paper_profile_refuses_live() -> None:
    decision = evaluate(profile(), intent(live=True))
    assert decision.allowed is False
    assert any("does not permit live" in reason for reason in decision.reasons)


def test_live_is_off_by_default() -> None:
    assert RiskProfile(name="default").allow_live is False


def test_a_live_only_profile_refuses_paper() -> None:
    decision = evaluate(profile(allow_paper=False, allow_live=True), intent())
    assert any("does not permit paper" in reason for reason in decision.reasons)


# ── the limits themselves ────────────────────────────────────────────────────


def test_an_instrument_outside_the_permitted_set_is_refused() -> None:
    decision = evaluate(profile(), intent(instrument="ES.CME"))
    assert any("not in the permitted set" in reason for reason in decision.reasons)


def test_an_empty_instrument_set_permits_any_instrument() -> None:
    assert evaluate(profile(instruments=()), intent(instrument="ES.CME")).allowed is True


def test_an_oversized_order_is_refused() -> None:
    decision = evaluate(profile(), intent(contracts=5))
    assert any("max_order_contracts" in reason for reason in decision.reasons)


def test_the_projected_position_is_what_is_limited_not_the_order() -> None:
    """One more contract on top of two is a position of three."""
    decision = evaluate(profile(max_order_contracts=3), intent(contracts=1, current_position=2))
    assert any("projected position 3" in reason for reason in decision.reasons)


def test_a_short_position_counts_by_magnitude() -> None:
    decision = evaluate(profile(max_order_contracts=3), intent(contracts=1, current_position=-2))
    assert any("projected position 3" in reason for reason in decision.reasons)


def test_open_orders_at_the_ceiling_refuse_another() -> None:
    decision = evaluate(profile(), intent(open_orders=4))
    assert any("max_open_orders" in reason for reason in decision.reasons)


def test_the_daily_loss_limit_is_a_magnitude() -> None:
    """Losses arrive as negative realised P&L; the limit is stated positively."""
    assert evaluate(profile(), intent(realised_today=-249.0)).allowed is True
    decision = evaluate(profile(), intent(realised_today=-250.0))
    assert any("max_daily_loss" in reason for reason in decision.reasons)


def test_profit_never_trips_the_loss_limit() -> None:
    assert evaluate(profile(), intent(realised_today=10_000.0)).allowed is True


def test_the_drawdown_limit_trips_at_the_ceiling() -> None:
    assert evaluate(profile(), intent(drawdown=499.0)).allowed is True
    assert evaluate(profile(), intent(drawdown=500.0)).allowed is False


def test_a_zero_size_intent_is_refused() -> None:
    assert any(
        "must be positive" in reason for reason in evaluate(profile(), intent(contracts=0)).reasons
    )


# ── refusals accumulate ──────────────────────────────────────────────────────


def test_every_breach_is_reported_at_once() -> None:
    """Fixing one breach should not require discovering the next by retrying."""
    decision = evaluate(
        profile(),
        intent(instrument="ES.CME", contracts=9, live=True, drawdown=900.0, open_orders=7),
    )
    assert decision.allowed is False
    assert len(decision.reasons) >= 5


# ── a profile with no ceiling is not a profile ───────────────────────────────


@pytest.mark.parametrize(
    "field",
    [
        "max_position_contracts",
        "max_order_contracts",
        "max_daily_loss",
        "max_drawdown",
        "max_open_orders",
    ],
)
def test_a_non_positive_limit_is_refused_at_construction(field: str) -> None:
    with pytest.raises(RiskRefusal, match=field):
        profile(**{field: 0})


def test_an_unnamed_profile_is_refused() -> None:
    with pytest.raises(RiskRefusal, match="named"):
        profile(name="  ")


def test_a_profile_is_content_addressed() -> None:
    assert profile().profile_id == profile().profile_id
    assert profile().profile_id != profile(max_daily_loss=100.0).profile_id


def test_the_defaults_are_conservative() -> None:
    """A profile constructed with nothing but a name must still be restrictive."""
    default = RiskProfile(name="bare")
    assert default.max_position_contracts == 1
    assert default.allow_live is False
    assert evaluate(default, intent(contracts=2)).allowed is False
