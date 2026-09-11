"""The funnel: every order climbs the ladder, and nothing routes around it.

These are the tests that matter most. If the desk could dispatch an intent that
had not cleared compatibility, the prop rule engine and the pre-trade gate, then
every other control in this package would be advisory.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from forge.prop.account import AccountState
from forge.propdesk import (
    CommandKind,
    ConnectionState,
    CopyResolver,
    NewsPolicy,
    OrderIntent,
    OrderType,
    Permission,
    Priority,
    PropProgramPolicy,
    Side,
    assess_news,
    cleared,
    default_catalogue,
    refused,
    summarise,
)
from forge.propdesk.news import EconomicEvent, Impact

NOW = datetime(2026, 9, 11, 15, 30, tzinfo=UTC)


def intent(
    account_uid: str,
    *,
    quantity: int = 1,
    side: Side = Side.BUY,
    kind: CommandKind = CommandKind.PLACE,
    priority: Priority = Priority.ENTRY,
    key: str = "k",
    symbol: str = "MNQ",
    **overrides,
) -> OrderIntent:
    base: dict = {
        "intent_id": f"i-{account_uid}-{key}",
        "kind": kind,
        "account_uid": account_uid,
        "idempotency_key": f"{account_uid}-{key}",
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "order_type": OrderType.MARKET,
        "priority": priority,
        "reference_price": 20_000.0,
        # A copied trade's evidence: the operator said both accounts are theirs.
        "attestation": "tester",
        "created_at": NOW,
    }
    return OrderIntent(**{**base, **overrides})


def stage(decision, name: str):
    return next(s for s in decision.stages if s.stage == name)


# ── the happy path ───────────────────────────────────────────────────────────


def test_a_clean_intent_climbs_every_rung_and_is_dispatched(
    desk, adapter, context_factory
) -> None:
    context = context_factory()
    decisions = desk.dispatch([intent(adapter.account_uid("F1"))], context)
    decision = decisions[0]
    assert decision.cleared is True
    assert decision.dispatched is True
    assert decision.acknowledgement is not None
    assert decision.acknowledgement.accepted is True
    assert {s.stage for s in decision.stages} == {
        "account_binding",
        "connection",
        "source_evidence",
        "compatibility",
        "cross_account_direction",
        "news_blackout",
        "account_rules",
        "pretrade_gate",
    }
    assert decision.gate is not None and decision.gate.allowed


def test_a_cleared_intent_carries_the_account_assessment_it_relied_on(
    desk, adapter, context_factory
) -> None:
    decisions = desk.dispatch([intent(adapter.account_uid("F1"))], context_factory())
    assert decisions[0].assessment is not None
    assert decisions[0].assessment.can_trade is True


# ── the ladder refuses ───────────────────────────────────────────────────────


def test_an_unknown_account_is_refused_before_anything_is_sent(desk, context_factory) -> None:
    decisions = desk.dispatch([intent("nobody")], context_factory())
    assert decisions[0].cleared is False
    assert decisions[0].dispatched is False
    assert "account_binding" in decisions[0].blocking_stages


def test_an_account_with_no_policy_is_refused_as_unknown(
    desk, adapter, context_factory
) -> None:
    """An unrecorded firm rule is not permission."""
    context = context_factory(policy=PropProgramPolicy())
    decisions = desk.dispatch([intent(adapter.account_uid("F1"))], context)
    assert decisions[0].cleared is False
    assert "compatibility" in decisions[0].blocking_stages
    assert stage(decisions[0], "compatibility").unknown is True


def test_an_account_whose_programme_prohibits_copying_is_refused(
    desk, adapter, context_factory
) -> None:
    context = context_factory(policy=PropProgramPolicy(copy_in=Permission.BLOCKED))
    decisions = desk.dispatch([intent(adapter.account_uid("F1"))], context)
    assert decisions[0].cleared is False
    assert "compatibility" in decisions[0].blocking_stages


def test_a_connection_that_is_not_live_refuses_a_new_entry(
    desk, adapter, context_factory
) -> None:
    context = context_factory(connection_state=ConnectionState.REAUTHORIZING)
    decisions = desk.dispatch([intent(adapter.account_uid("F1"))], context)
    assert decisions[0].cleared is False
    assert "connection" in decisions[0].blocking_stages


def test_an_account_in_breach_of_its_own_rules_is_refused(
    desk, adapter, context_factory, permissive_policy
) -> None:
    """The existing `forge.prop` engine, called — not a second rule engine."""
    breached = AccountState(
        as_of=NOW,
        balance=47_900.0,
        equity=47_900.0,
        high_water_balance=50_000.0,
        high_water_equity=50_000.0,
    )
    context = context_factory()
    uid = adapter.account_uid("F1")
    account = context.accounts[uid].model_copy(update={"state": breached})
    context = context.model_copy(update={"accounts": {**context.accounts, uid: account}})

    decisions = desk.dispatch([intent(uid)], context)
    assert decisions[0].cleared is False
    assert "account_rules" in decisions[0].blocking_stages
    assert decisions[0].assessment.can_trade is False


def test_an_account_with_no_recorded_state_cannot_be_assessed_or_traded(
    desk, adapter, context_factory
) -> None:
    context = context_factory()
    uid = adapter.account_uid("F1")
    account = context.accounts[uid].model_copy(update={"state": None, "rules": None})
    context = context.model_copy(update={"accounts": {**context.accounts, uid: account}})

    decisions = desk.dispatch([intent(uid)], context)
    assert decisions[0].cleared is False
    assert stage(decisions[0], "account_rules").unknown is True


def test_a_cross_account_hedge_is_refused_by_the_direction_guard(
    desk, adapter, context_factory
) -> None:
    """One owner, two accounts, opposite sides of one product group."""
    context = context_factory(positions={"F1": {"MNQ": 3}, "F2": {}})
    decisions = desk.dispatch(
        [intent(adapter.account_uid("F2"), side=Side.SELL)], context
    )
    assert decisions[0].cleared is False
    assert "cross_account_direction" in decisions[0].blocking_stages


def test_a_restricted_instrument_is_refused_by_the_pre_trade_gate(
    desk, adapter, context_factory
) -> None:
    """The existing gate, called with a context the desk assembled."""
    context = context_factory().model_copy(
        update={"instruments": {}, "market": {}}
    )
    decisions = desk.dispatch([intent(adapter.account_uid("F1"))], context)
    assert decisions[0].cleared is False
    assert "pretrade_gate" in decisions[0].blocking_stages


def test_the_kill_switch_in_the_existing_limit_set_refuses_everything(
    desk, adapter, context_factory
) -> None:
    from forge.risk.portfolio import PortfolioLimits

    context = context_factory().model_copy(
        update={"limits": PortfolioLimits(enabled=False)}
    )
    decisions = desk.dispatch([intent(adapter.account_uid("F1"))], context)
    assert decisions[0].cleared is False
    assert any("kill_switch" in reason or "disabled" in reason
               for reason in decisions[0].reasons)


def test_every_refusal_is_accumulated_rather_than_reported_one_at_a_time(
    desk, adapter, context_factory
) -> None:
    context = context_factory(
        policy=PropProgramPolicy(copy_in=Permission.BLOCKED),
        connection_state=ConnectionState.FAILED,
    )
    decisions = desk.dispatch([intent(adapter.account_uid("F1"))], context)
    assert {"connection", "compatibility"} <= set(decisions[0].blocking_stages)


# ── reducing exposure is always permitted ────────────────────────────────────


def test_a_flatten_survives_a_blocked_prop_rule(
    desk, adapter, context_factory
) -> None:
    """The moment the operator most needs to get out is not the moment to stop them."""
    context = context_factory(policy=PropProgramPolicy(copy_in=Permission.BLOCKED))
    decisions = desk.dispatch(
        [
            intent(
                adapter.account_uid("F1"),
                kind=CommandKind.FLATTEN,
                priority=Priority.FLATTEN,
                key="flat",
            )
        ],
        context,
    )
    assert decisions[0].cleared is True
    assert any("reduces exposure" in reason for reason in decisions[0].reasons)


def test_a_flatten_does_not_survive_an_unknown_account(desk, context_factory) -> None:
    """There is genuinely nowhere to send it."""
    decisions = desk.dispatch(
        [intent("nobody", kind=CommandKind.FLATTEN, priority=Priority.FLATTEN)],
        context_factory(),
    )
    assert decisions[0].cleared is False


def test_a_cancel_is_not_screened_as_a_new_order(desk, adapter, context_factory) -> None:
    decisions = desk.dispatch(
        [
            intent(
                adapter.account_uid("F1"),
                kind=CommandKind.CANCEL,
                priority=Priority.CANCEL,
                key="c",
            )
        ],
        context_factory(),
    )
    assert stage(decisions[0], "pretrade_gate").passed is True
    assert "reduces exposure" in stage(decisions[0], "pretrade_gate").detail


# ── news only tightens ───────────────────────────────────────────────────────


def test_a_blackout_window_refuses_a_new_entry(desk, adapter, context_factory) -> None:
    assessment = assess_news(
        policy=NewsPolicy(enabled=True, minutes_before=10, minutes_after=10),
        events=[
            EconomicEvent(
                event_id="e1",
                title="Consumer Price Index",
                at=NOW + timedelta(minutes=2),
                impact=Impact.HIGH,
                source="test",
            )
        ],
        at=NOW,
    )
    context = context_factory(news=assessment)
    decisions = desk.dispatch([intent(adapter.account_uid("F1"))], context)
    assert decisions[0].cleared is False
    assert "news_blackout" in decisions[0].blocking_stages


def test_a_blackout_does_not_prevent_getting_flat(desk, adapter, context_factory) -> None:
    assessment = assess_news(
        policy=NewsPolicy(enabled=True, minutes_before=10, minutes_after=10),
        events=[
            EconomicEvent(
                event_id="e1",
                title="FOMC Statement",
                at=NOW,
                impact=Impact.HIGH,
                source="test",
            )
        ],
        at=NOW,
    )
    decisions = desk.dispatch(
        [
            intent(
                adapter.account_uid("F1"),
                kind=CommandKind.FLATTEN,
                priority=Priority.FLATTEN,
                key="flat",
            )
        ],
        context_factory(news=assessment),
    )
    assert stage(decisions[0], "news_blackout").passed is True


def test_a_warn_policy_reports_without_refusing(desk, adapter, context_factory) -> None:
    assessment = assess_news(
        policy=NewsPolicy(enabled=True, action="warn", minutes_before=10),
        events=[
            EconomicEvent(
                event_id="e1",
                title="Consumer Price Index",
                at=NOW,
                impact=Impact.HIGH,
                source="test",
            )
        ],
        at=NOW,
    )
    decisions = desk.dispatch(
        [intent(adapter.account_uid("F1"))], context_factory(news=assessment)
    )
    assert decisions[0].cleared is True
    assert "warns rather than blocks" in stage(decisions[0], "news_blackout").detail


def test_news_can_never_clear_something_the_ladder_refused(
    desk, adapter, context_factory
) -> None:
    """There is no branch by which a calendar removes a restriction."""
    quiet = assess_news(policy=NewsPolicy(enabled=True), events=[], at=NOW)
    context = context_factory(
        policy=PropProgramPolicy(copy_in=Permission.BLOCKED), news=quiet
    )
    decisions = desk.dispatch([intent(adapter.account_uid("F1"))], context)
    assert decisions[0].cleared is False


# ── batches ──────────────────────────────────────────────────────────────────


def test_a_batch_threads_each_cleared_order_into_the_next(
    desk, adapter, context_factory
) -> None:
    """Eight followers taking one contract each is eight orders that each look
    trivial and together are not."""
    context = context_factory()
    uid = adapter.account_uid("F1")
    decisions = desk.dispatch(
        [intent(uid, key="a"), intent(uid, key="b", side=Side.SELL)], context
    )
    # The second order sees the first's position in the direction guard and the
    # gate, rather than an empty book.
    assert len(decisions) == 2
    assert decisions[0].cleared is True


def test_a_batch_summary_counts_refusals_by_stage(desk, adapter, context_factory) -> None:
    context = context_factory(policy=PropProgramPolicy())
    decisions = desk.dispatch(
        [intent(adapter.account_uid("F1")), intent("nobody", key="x")], context
    )
    summary = summarise(decisions)
    assert summary["screened"] == 2
    assert summary["cleared"] == 0
    assert "compatibility" in summary["refused_by_stage"]
    assert "account_binding" in summary["refused_by_stage"]


def test_cleared_and_refused_partition_a_batch(desk, adapter, context_factory) -> None:
    decisions = desk.dispatch(
        [intent(adapter.account_uid("F1")), intent("nobody", key="x")], context_factory()
    )
    assert len(cleared(decisions)) + len(refused(decisions)) == len(decisions)


# ── screening never sends ────────────────────────────────────────────────────


def test_screening_alone_sends_nothing(desk, adapter, context_factory) -> None:
    before = len(adapter.commands)
    desk.screen_all([intent(adapter.account_uid("F1"))], context_factory())
    assert len(adapter.commands) == before


def test_a_refused_intent_never_reaches_the_adapter(
    desk, adapter, context_factory
) -> None:
    """The claim the whole package rests on."""
    before = len(adapter.commands)
    context = context_factory(policy=PropProgramPolicy(copy_in=Permission.BLOCKED))
    desk.dispatch([intent(adapter.account_uid("F1"))], context)
    assert len(adapter.commands) == before


def test_the_desk_exposes_no_way_to_send_without_screening() -> None:
    """A caller holding an intent has exactly one thing it can do with it."""
    from forge.propdesk.desk import PropDesk

    public = {name for name in dir(PropDesk) if not name.startswith("_")}
    assert public == {"screen", "screen_all", "dispatch", "recent"}


# ── the copy engine reaches the desk and nothing else ────────────────────────


def test_a_copy_decision_becomes_an_order_only_through_the_desk(
    desk, adapter, context_factory, group
) -> None:
    resolver = CopyResolver(default_catalogue(), now=lambda: NOW)
    decisions = resolver.resolve(
        group=group,
        leader_root="MNQ",
        leader_position=2,
        follower_positions={
            adapter.account_uid("F1"): 0,
            adapter.account_uid("F2"): 0,
        },
        reference_price=20_000.0,
    )
    intents = [d.intent for d in decisions if d.intent is not None]
    assert len(intents) == 2

    results = desk.dispatch(intents, context_factory())
    assert all(r.cleared for r in results)
    assert all(r.acknowledgement and r.acknowledgement.accepted for r in results)


def test_a_follower_over_its_contract_cap_is_rejected_by_the_provider_and_recorded(
    desk, adapter, context_factory, group
) -> None:
    """F2's cap is two; the provider refuses and the record says why."""
    resolver = CopyResolver(default_catalogue(), now=lambda: NOW)
    decisions = resolver.resolve(
        group=group,
        leader_root="MNQ",
        leader_position=5,
        follower_positions={
            adapter.account_uid("F1"): 0,
            adapter.account_uid("F2"): 0,
        },
        reference_price=20_000.0,
    )
    results = desk.dispatch(
        [d.intent for d in decisions if d.intent], context_factory()
    )
    by_account = {r.intent.account_uid: r for r in results}
    capped = by_account[adapter.account_uid("F2")]
    assert capped.cleared is True  # the desk's own ladder had no objection
    assert capped.acknowledgement.accepted is False
    assert "maximum position limit" in capped.acknowledgement.reason


# ── the record ───────────────────────────────────────────────────────────────


def test_recent_decisions_are_kept_newest_first(desk, adapter, context_factory) -> None:
    context = context_factory()
    desk.dispatch([intent(adapter.account_uid("F1"), key="one")], context)
    desk.dispatch([intent(adapter.account_uid("F1"), key="two")], context)
    recent = desk.recent(10)
    assert recent[0].intent.idempotency_key.endswith("two")


def test_a_decision_serialises_with_its_blocking_stages(
    desk, adapter, context_factory
) -> None:
    decisions = desk.dispatch([intent("nobody")], context_factory())
    payload = decisions[0].as_dict()
    assert payload["cleared"] is False
    assert "account_binding" in payload["blocking_stages"]


# ── what backs an order ──────────────────────────────────────────────────────


def test_an_order_with_neither_a_strategy_nor_an_attestation_is_refused(
    desk, adapter, context_factory
) -> None:
    """Stricter than the gate alone, which has nothing to say about an intent
    that names no strategy."""
    decisions = desk.dispatch(
        [intent(adapter.account_uid("F1"), attestation="")], context_factory()
    )
    assert decisions[0].cleared is False
    assert "source_evidence" in decisions[0].blocking_stages


def test_a_strategy_order_still_needs_a_verdict_the_judge_passed(
    desk, adapter, context_factory
) -> None:
    """The attestation path is for copied trades and does not launder a strategy."""
    decisions = desk.dispatch(
        [intent(adapter.account_uid("F1"), attestation="", strategy_id="unjudged")],
        context_factory(),
    )
    assert decisions[0].cleared is False
    assert "pretrade_gate" in decisions[0].blocking_stages
    assert any("never been judged" in reason for reason in decisions[0].reasons)


def test_a_strategy_order_clears_once_the_judge_has_passed_it(
    desk, adapter, context_factory
) -> None:
    context = context_factory().model_copy(update={"verdicts": {"judged": "PASS"}})
    decisions = desk.dispatch(
        [intent(adapter.account_uid("F1"), attestation="", strategy_id="judged")],
        context,
    )
    assert decisions[0].cleared is True


def test_a_failed_strategy_is_refused_even_with_an_attestation(
    desk, adapter, context_factory
) -> None:
    """An attestation is evidence of ownership, not of a strategy's soundness."""
    context = context_factory().model_copy(update={"verdicts": {"bad": "FAIL"}})
    decisions = desk.dispatch(
        [intent(adapter.account_uid("F1"), strategy_id="bad", attestation="tester")],
        context,
    )
    assert decisions[0].cleared is False
    assert "pretrade_gate" in decisions[0].blocking_stages


def test_supplying_capital_engages_the_gates_notional_checks(
    desk, adapter, context_factory
) -> None:
    """An operator who supplies one is choosing a stricter, cash-portfolio measure."""
    context = context_factory().model_copy(update={"capital": 50_000.0})
    decisions = desk.dispatch([intent(adapter.account_uid("F1"))], context)
    assert decisions[0].cleared is False
    assert any("of capital" in reason for reason in decisions[0].reasons)
