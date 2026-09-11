"""Copy groups: net-position convergence, sizing, reversals and health."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from forge.propdesk import (
    CopyGroup,
    CopyOutcome,
    CopyResolver,
    ExecutionMode,
    Follower,
    GroupPolicy,
    Leader,
    MappingPolicy,
    OrderRecord,
    OrderState,
    OrderType,
    Priority,
    RollWindow,
    Rounding,
    Side,
    SizingMethod,
    SizingPolicy,
    assess_health,
    default_catalogue,
)
from pydantic import ValidationError

NOW = datetime(2026, 9, 11, 15, 30, tzinfo=UTC)
LEADER = "leader"


@pytest.fixture
def resolver() -> CopyResolver:
    return CopyResolver(default_catalogue(), now=lambda: NOW)


def build_group(*followers: Follower, policy: GroupPolicy | None = None, **overrides):
    base: dict = {
        "group_id": "g1",
        "name": "Group",
        "leader": Leader(account_uid=LEADER),
        "followers": followers,
        "policy": policy or GroupPolicy(),
        "active": True,
        "owner_attested_by": "tester",
        "created_at": NOW,
        "updated_at": NOW,
    }
    return CopyGroup(**{**base, **overrides})


def follower(uid: str = "f1", **sizing) -> Follower:
    return Follower(account_uid=uid, sizing=SizingPolicy(**sizing))


# ── structure ────────────────────────────────────────────────────────────────


def test_a_leader_cannot_follow_its_own_group() -> None:
    with pytest.raises(ValidationError, match="cannot also be a follower"):
        build_group(follower(LEADER))


def test_a_follower_cannot_appear_twice() -> None:
    with pytest.raises(ValidationError, match="duplicate followers"):
        build_group(follower("f1"), follower("f1"))


def test_followers_can_be_added_and_removed_without_rebuilding_the_group() -> None:
    group = build_group(follower("f1"))
    grown = group.with_follower(follower("f2"))
    assert len(grown.followers) == 2
    assert len(grown.without_follower("f1").followers) == 1


# ── net-position convergence ─────────────────────────────────────────────────


def test_a_follower_converges_on_the_leaders_net_position(resolver) -> None:
    group = build_group(follower("f1", value=1.0))
    decisions = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=3, follower_positions={"f1": 0}
    )
    assert decisions[0].outcome is CopyOutcome.INTENT
    assert decisions[0].target_position == 3
    assert decisions[0].intent.quantity == 3


def test_the_same_leader_position_evaluated_twice_produces_one_order(resolver) -> None:
    """The whole duplicate-event defence, as arithmetic rather than a ledger.

    Two deliveries of one fill produce the same target twice, and applying a
    target twice is a no-op.
    """
    group = build_group(follower("f1", value=1.0))
    first = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=2, follower_positions={"f1": 0}
    )
    assert first[0].outcome is CopyOutcome.INTENT

    after = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=2, follower_positions={"f1": 2}
    )
    assert after[0].outcome is CopyOutcome.ALREADY_AT_TARGET


def test_four_partial_fills_do_not_accumulate_a_rounding_over_size(resolver) -> None:
    """Targets are computed from the leader's net position, not from deltas.

    A ceiling rule applied to each of four increments over-sizes four times; the
    same rule applied to the net position over-sizes once.
    """
    group = build_group(follower("f1", value=0.5, rounding=Rounding.CEIL))
    held = 0
    for leader_net in (1, 2, 3, 4):
        decisions = resolver.resolve(
            group=group,
            leader_root="MNQ",
            leader_position=leader_net,
            follower_positions={"f1": held},
        )
        decision = decisions[0]
        if decision.intent is not None:
            held += decision.intent.side.sign * decision.intent.quantity
    assert held == 2  # ceil(4 * 0.5), not the sum of four ceilings


def test_a_reduction_follows_the_leader_down(resolver) -> None:
    group = build_group(follower("f1", value=1.0))
    decision = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=1, follower_positions={"f1": 3}
    )[0]
    assert decision.intent.side is Side.SELL
    assert decision.intent.quantity == 2
    assert decision.intent.priority is Priority.FLATTEN


def test_a_flat_leader_flattens_its_followers_by_default(resolver) -> None:
    group = build_group(follower("f1", value=1.0))
    decision = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=0, follower_positions={"f1": 2}
    )[0]
    assert decision.target_position == 0
    assert decision.intent.quantity == 2


def test_a_group_that_does_not_flatten_leaves_the_follower_alone(resolver) -> None:
    group = build_group(
        follower("f1", value=1.0),
        policy=GroupPolicy(flatten_followers_when_leader_flat=False),
    )
    decision = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=0, follower_positions={"f1": 2}
    )[0]
    assert decision.outcome is CopyOutcome.SKIPPED_MODE
    assert decision.intent is None


# ── reversals ────────────────────────────────────────────────────────────────


def test_a_one_step_reversal_is_blocked_by_default(resolver) -> None:
    """The window in which one owner holds both sides of a correlated product."""
    group = build_group(follower("f1", value=1.0))
    decision = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=-2, follower_positions={"f1": 2}
    )[0]
    assert decision.outcome is CopyOutcome.BLOCKED_REVERSAL
    assert decision.intent is None
    assert "close to flat first" in decision.detail


def test_a_reversal_is_permitted_when_the_group_allows_it(resolver) -> None:
    group = build_group(
        follower("f1", value=1.0), policy=GroupPolicy(block_reversals=False)
    )
    decision = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=-2, follower_positions={"f1": 2}
    )[0]
    assert decision.outcome is CopyOutcome.INTENT
    assert decision.intent.quantity == 4


# ── sizing methods ───────────────────────────────────────────────────────────


def test_fixed_sizing_ignores_the_leaders_size_but_follows_its_direction(
    resolver,
) -> None:
    group = build_group(follower("f1", method=SizingMethod.FIXED, value=2))
    long = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=7, follower_positions={"f1": 0}
    )[0]
    short = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=-7, follower_positions={"f1": 0}
    )[0]
    assert long.target_position == 2
    assert short.target_position == -2


def test_equity_scaled_sizing_refuses_without_both_equities(resolver) -> None:
    """No size follows from an equity nobody stated."""
    group = build_group(follower("f1", method=SizingMethod.EQUITY_SCALED))
    decision = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=4, follower_positions={"f1": 0}
    )[0]
    assert decision.outcome is CopyOutcome.REFUSED_MAPPING
    assert "has not been reported" in decision.detail


def test_equity_scaled_sizing_scales_by_the_ratio(resolver) -> None:
    group = build_group(follower("f1", method=SizingMethod.EQUITY_SCALED))
    decision = resolver.resolve(
        group=group,
        leader_root="MNQ",
        leader_position=10,
        follower_positions={"f1": 0},
        leader_equity=150_000.0,
        follower_equity={"f1": 50_000.0},
    )[0]
    assert decision.target_position == 3


def test_a_cross_contract_follower_maps_into_its_own_product(resolver) -> None:
    group = build_group(
        follower(
            "f1",
            value=1.0,
            mapping=MappingPolicy.NOTIONAL_EQUIVALENT,
            target_root="MNQ",
        )
    )
    decision = resolver.resolve(
        group=group, leader_root="NQ", leader_position=1, follower_positions={"f1": 0}
    )[0]
    assert decision.intent.symbol == "MNQ"
    assert decision.intent.quantity == 10


def test_a_sizing_that_rounds_to_zero_is_named_rather_than_silent(resolver) -> None:
    group = build_group(follower("f1", value=0.2, rounding=Rounding.FLOOR))
    decision = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=1, follower_positions={"f1": 0}
    )[0]
    assert decision.outcome is CopyOutcome.ROUNDED_TO_ZERO


def test_a_per_order_cap_limits_one_order_without_changing_the_target(resolver) -> None:
    group = build_group(follower("f1", value=1.0, max_contracts_per_order=2))
    decision = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=5, follower_positions={"f1": 0}
    )[0]
    assert decision.target_position == 5
    assert decision.intent.quantity == 2
    assert "capped at 2" in decision.intent.reason


def test_a_position_cap_limits_the_target_itself(resolver) -> None:
    group = build_group(follower("f1", value=1.0, max_position_contracts=3))
    decision = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=9, follower_positions={"f1": 0}
    )[0]
    assert decision.target_position == 3


def test_a_risk_budget_policy_needs_a_stop_at_construction() -> None:
    with pytest.raises(ValidationError, match="needs a currency budget"):
        SizingPolicy(method=SizingMethod.RISK_BUDGET)


def test_a_cross_mapping_needs_a_target_product() -> None:
    with pytest.raises(ValidationError, match="needs a target product"):
        SizingPolicy(mapping=MappingPolicy.COUNT_CROSS)


# ── skips ────────────────────────────────────────────────────────────────────


def test_an_inactive_group_produces_no_intents(resolver) -> None:
    group = build_group(follower("f1"), active=False)
    decisions = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=3, follower_positions={}
    )
    assert {d.outcome for d in decisions} == {CopyOutcome.SKIPPED_INACTIVE}


def test_a_follower_switched_off_is_skipped_individually(resolver) -> None:
    group = build_group(
        follower("f1", value=1.0),
        Follower(account_uid="f2", mode=ExecutionMode.OFF),
    )
    decisions = resolver.resolve(
        group=group,
        leader_root="MNQ",
        leader_position=2,
        follower_positions={"f1": 0, "f2": 0},
    )
    outcomes = {d.follower_account_uid: d.outcome for d in decisions}
    assert outcomes["f1"] is CopyOutcome.INTENT
    assert outcomes["f2"] is CopyOutcome.SKIPPED_MODE


def test_a_product_outside_the_leaders_filter_is_skipped(resolver) -> None:
    group = build_group(
        follower("f1", value=1.0),
        leader=Leader(account_uid=LEADER, products=("ES",)),
    )
    decisions = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=2, follower_positions={"f1": 0}
    )
    assert {d.outcome for d in decisions} == {CopyOutcome.SKIPPED_PRODUCT}


def test_a_roll_window_blocks_the_whole_group() -> None:
    """Two providers disagreeing about the front month is a rejection at best."""
    catalogue = default_catalogue()
    catalogue.add_roll_window(
        RollWindow(root="MNQ", opens=NOW.date(), closes=NOW.date())
    )
    resolver = CopyResolver(catalogue, now=lambda: NOW)
    group = build_group(follower("f1", value=1.0))
    decisions = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=2, follower_positions={"f1": 0}
    )
    assert {d.outcome for d in decisions} == {CopyOutcome.BLOCKED_ROLL}


# ── mirrored working orders ──────────────────────────────────────────────────


def leader_order(**overrides) -> OrderRecord:
    base: dict = {
        "order_id": "lead-1",
        "account_uid": LEADER,
        "symbol": "MNQ",
        "side": Side.BUY,
        "quantity": 4,
        "order_type": OrderType.LIMIT,
        "limit_price": 19_950.0,
        "state": OrderState.WORKING,
        "created_at": NOW,
        "updated_at": NOW,
    }
    return OrderRecord(**{**base, **overrides})


def test_a_mirroring_follower_receives_the_leaders_working_order(resolver) -> None:
    mirrored = Follower(
        account_uid="f1",
        mode=ExecutionMode.MIRROR_ORDERS,
        sizing=SizingPolicy(value=1.0),
    )
    group = build_group(mirrored)
    decision = resolver.mirror_order(
        group=group, follower=mirrored, leader_order=leader_order()
    )
    assert decision.outcome is CopyOutcome.INTENT
    assert decision.intent.order_type is OrderType.LIMIT
    assert decision.intent.limit_price == 19_950.0


def test_a_fill_mirroring_follower_ignores_a_working_order(resolver) -> None:
    plain = Follower(account_uid="f1", sizing=SizingPolicy(value=1.0))
    decision = resolver.mirror_order(
        group=build_group(plain), follower=plain, leader_order=leader_order()
    )
    assert decision.outcome is CopyOutcome.SKIPPED_MODE
    assert "mirrors fills" in decision.detail


def test_a_terminal_leader_order_is_not_mirrored(resolver) -> None:
    mirrored = Follower(
        account_uid="f1",
        mode=ExecutionMode.MIRROR_ORDERS,
        sizing=SizingPolicy(value=1.0),
    )
    decision = resolver.mirror_order(
        group=build_group(mirrored),
        follower=mirrored,
        leader_order=leader_order(state=OrderState.FILLED),
    )
    assert decision.outcome is CopyOutcome.SKIPPED_MODE


def test_cancelling_a_mirror_is_prioritised_ahead_of_entries(resolver) -> None:
    mirrored = Follower(account_uid="f1", mode=ExecutionMode.MIRROR_ORDERS)
    intent = resolver.cancel_mirror(
        group=build_group(mirrored),
        follower=mirrored,
        follower_order_id="f-1",
        reason="the leader cancelled",
    )
    assert intent.priority is Priority.CANCEL


# ── health ───────────────────────────────────────────────────────────────────


def test_a_group_with_every_follower_at_target_is_healthy(resolver) -> None:
    group = build_group(follower("f1", value=1.0))
    decisions = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=2, follower_positions={"f1": 2}
    )
    health = assess_health(group=group, decisions=decisions)
    assert health.healthy is True
    assert health.diverged == 0


def test_a_follower_whose_connection_is_down_makes_the_group_unhealthy(
    resolver,
) -> None:
    group = build_group(follower("f1", value=1.0))
    decisions = resolver.resolve(
        group=group, leader_root="MNQ", leader_position=2, follower_positions={"f1": 2}
    )
    health = assess_health(
        group=group, decisions=decisions, unavailable_accounts=frozenset({"f1"})
    )
    assert health.healthy is False
    assert any("connection is not live" in issue for issue in health.issues)


def test_an_unattested_group_is_flagged() -> None:
    """Copying between different people's accounts is prohibited at every firm
    researched that addressed it, so somebody has to say the accounts are theirs."""
    group = build_group(follower("f1"), owner_attested_by="")
    health = assess_health(group=group, decisions=())
    assert any("one owner" in issue for issue in health.issues)


def test_an_active_group_with_nothing_replicating_is_flagged() -> None:
    group = build_group(Follower(account_uid="f1", mode=ExecutionMode.OFF))
    health = assess_health(group=group, decisions=())
    assert any("no follower is replicating" in issue for issue in health.issues)
