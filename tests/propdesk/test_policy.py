"""Firm permissions, four-valued, where unknown is never permission."""

from __future__ import annotations

from datetime import UTC, date, datetime

from forge.propdesk import (
    POLICY_QUESTIONS,
    CompatibilityReport,
    DirectionGuard,
    Permission,
    PropProgramPolicy,
    StrategyRequirements,
    UseCase,
    evaluate_compatibility,
    worst,
)
from forge.propdesk.policy import OrderOrigin

NOW = datetime(2026, 9, 11, 15, 30, tzinfo=UTC)
TODAY = NOW.date()


# ── the four values ──────────────────────────────────────────────────────────


def test_only_allowed_permits_automatic_action() -> None:
    assert Permission.ALLOWED.permits_automatic_action is True
    for value in (
        Permission.BLOCKED,
        Permission.UNKNOWN,
        Permission.REQUIRES_CONFIRMATION,
    ):
        assert value.permits_automatic_action is False


def test_the_worst_of_nothing_is_unknown_not_allowed() -> None:
    """An empty check list means nothing was checked."""
    assert worst(()) is Permission.UNKNOWN


def test_unknown_outranks_requires_confirmation() -> None:
    """A question nobody asked is worse than one somebody answered."""
    assert (
        worst((Permission.REQUIRES_CONFIRMATION, Permission.UNKNOWN))
        is Permission.UNKNOWN
    )
    assert worst((Permission.UNKNOWN, Permission.BLOCKED)) is Permission.BLOCKED


# ── defaults ─────────────────────────────────────────────────────────────────


def test_an_unedited_policy_permits_nothing_automatically() -> None:
    """The honest starting position: nobody has read a contract yet."""
    policy = PropProgramPolicy()
    assert policy.automation is Permission.UNKNOWN
    assert policy.copy_in is Permission.UNKNOWN
    assert policy.copy_out is Permission.UNKNOWN
    assert policy.algorithmic_allocation is Permission.UNKNOWN


def test_hedging_and_third_party_copying_default_to_blocked() -> None:
    """Prohibited at every researched firm that addressed them."""
    policy = PropProgramPolicy()
    assert policy.cross_account_hedging is Permission.BLOCKED
    assert policy.third_party_copy is Permission.BLOCKED


def test_no_firm_rules_are_shipped() -> None:
    """AlgoForge asserts nothing about what any named firm's contract says."""
    assert PropProgramPolicy().firm_label == ""


def test_every_policy_question_names_a_real_field() -> None:
    fields = set(PropProgramPolicy.model_fields)
    assert {key for key, _ in POLICY_QUESTIONS} <= fields


# ── evaluation ───────────────────────────────────────────────────────────────


def test_no_recorded_policy_is_unknown_and_refuses() -> None:
    report = evaluate_compatibility(
        account_uid="a", use_case=UseCase.COPY_FOLLOWER, policy=None, at=NOW
    )
    assert report.verdict is Permission.UNKNOWN
    assert report.permits_automatic_action is False
    assert "not permission" in report.reasons[0].detail


def test_a_recorded_permission_allows_the_matching_use_case() -> None:
    policy = PropProgramPolicy(copy_in=Permission.ALLOWED)
    report = evaluate_compatibility(
        account_uid="a", use_case=UseCase.COPY_FOLLOWER, policy=policy, at=NOW
    )
    assert report.verdict is Permission.ALLOWED


def test_each_use_case_reads_its_own_permission() -> None:
    """Copying a human's trades is a different question from running a bot."""
    policy = PropProgramPolicy(
        copy_in=Permission.ALLOWED,
        automation=Permission.BLOCKED,
        algorithmic_allocation=Permission.UNKNOWN,
    )
    verdicts = {
        use_case: evaluate_compatibility(
            account_uid="a", use_case=use_case, policy=policy, at=NOW
        ).verdict
        for use_case in (
            UseCase.COPY_FOLLOWER,
            UseCase.STRATEGY_AUTOMATION,
            UseCase.ALGORITHMIC_ALLOCATION,
        )
    }
    assert verdicts[UseCase.COPY_FOLLOWER] is Permission.ALLOWED
    assert verdicts[UseCase.STRATEGY_AUTOMATION] is Permission.BLOCKED
    assert verdicts[UseCase.ALGORITHMIC_ALLOCATION] is Permission.UNKNOWN


def test_a_policy_outside_its_effective_window_reads_as_unknown() -> None:
    """Firm rules change; a stale policy may no longer describe the contract."""
    policy = PropProgramPolicy(
        copy_in=Permission.ALLOWED, review_by=date(2026, 1, 1)
    )
    report = evaluate_compatibility(
        account_uid="a", use_case=UseCase.COPY_FOLLOWER, policy=policy, at=NOW
    )
    assert report.verdict is Permission.UNKNOWN
    assert any(r.check == "policy_currency" for r in report.reasons)


def test_an_account_the_provider_has_locked_is_blocked() -> None:
    report = evaluate_compatibility(
        account_uid="a",
        use_case=UseCase.COPY_FOLLOWER,
        policy=PropProgramPolicy(copy_in=Permission.ALLOWED),
        account_can_trade=False,
        at=NOW,
    )
    assert report.verdict is Permission.BLOCKED


# ── where orders originate ───────────────────────────────────────────────────


def test_hosted_execution_is_blocked_by_a_personal_device_rule() -> None:
    policy = PropProgramPolicy(
        copy_in=Permission.ALLOWED, order_origin=OrderOrigin.PERSONAL_DEVICE_ONLY
    )
    report = evaluate_compatibility(
        account_uid="a",
        use_case=UseCase.COPY_FOLLOWER,
        policy=policy,
        hosted_execution=True,
        at=NOW,
    )
    assert report.verdict is Permission.BLOCKED
    assert any("own device" in r.detail for r in report.reasons)


def test_hosted_execution_with_no_recorded_rule_is_unknown_not_fine() -> None:
    report = evaluate_compatibility(
        account_uid="a",
        use_case=UseCase.COPY_FOLLOWER,
        policy=PropProgramPolicy(copy_in=Permission.ALLOWED),
        hosted_execution=True,
        at=NOW,
    )
    assert report.verdict is Permission.UNKNOWN


def test_local_execution_asks_no_origin_question() -> None:
    report = evaluate_compatibility(
        account_uid="a",
        use_case=UseCase.COPY_FOLLOWER,
        policy=PropProgramPolicy(
            copy_in=Permission.ALLOWED, order_origin=OrderOrigin.PERSONAL_DEVICE_ONLY
        ),
        hosted_execution=False,
        at=NOW,
    )
    assert report.verdict is Permission.ALLOWED


# ── strategy requirements ────────────────────────────────────────────────────


def test_a_product_not_on_the_permitted_list_is_blocked() -> None:
    policy = PropProgramPolicy(
        automation=Permission.ALLOWED, permitted_products=("MNQ",)
    )
    report = evaluate_compatibility(
        account_uid="a",
        use_case=UseCase.STRATEGY_AUTOMATION,
        policy=policy,
        requirements=StrategyRequirements(products=("CL",)),
        at=NOW,
    )
    assert report.verdict is Permission.BLOCKED


def test_no_recorded_product_list_means_unknown_not_everything() -> None:
    policy = PropProgramPolicy(automation=Permission.ALLOWED)
    report = evaluate_compatibility(
        account_uid="a",
        use_case=UseCase.STRATEGY_AUTOMATION,
        policy=policy,
        requirements=StrategyRequirements(products=("MNQ",)),
        at=NOW,
    )
    assert report.verdict is Permission.UNKNOWN


def test_an_explicitly_prohibited_product_is_blocked_even_if_also_permitted() -> None:
    policy = PropProgramPolicy(
        automation=Permission.ALLOWED,
        permitted_products=("MNQ", "CL"),
        prohibited_products=("CL",),
    )
    report = evaluate_compatibility(
        account_uid="a",
        use_case=UseCase.STRATEGY_AUTOMATION,
        policy=policy,
        requirements=StrategyRequirements(products=("CL",)),
        at=NOW,
    )
    assert report.verdict is Permission.BLOCKED


def test_an_order_type_the_provider_lacks_blocks_the_strategy() -> None:
    report = evaluate_compatibility(
        account_uid="a",
        use_case=UseCase.STRATEGY_AUTOMATION,
        policy=PropProgramPolicy(
            automation=Permission.ALLOWED, permitted_products=("MNQ",)
        ),
        requirements=StrategyRequirements(
            products=("MNQ",), order_types=("stop_limit",)
        ),
        capability_order_types=("market", "limit"),
        capability_max_contracts=5,
        at=NOW,
    )
    assert report.verdict is Permission.BLOCKED
    assert any("does not offer stop_limit" in r.detail for r in report.reasons)


def test_undiscovered_capability_is_unknown_rather_than_assumed() -> None:
    report = evaluate_compatibility(
        account_uid="a",
        use_case=UseCase.STRATEGY_AUTOMATION,
        policy=PropProgramPolicy(
            automation=Permission.ALLOWED, permitted_products=("MNQ",)
        ),
        requirements=StrategyRequirements(products=("MNQ",), order_types=("market",)),
        capability_order_types=(),
        capability_max_contracts=5,
        at=NOW,
    )
    assert report.verdict is Permission.UNKNOWN


def test_the_binding_contract_cap_is_the_smaller_of_those_known() -> None:
    report = evaluate_compatibility(
        account_uid="a",
        use_case=UseCase.STRATEGY_AUTOMATION,
        policy=PropProgramPolicy(
            automation=Permission.ALLOWED, permitted_products=("MNQ",)
        ),
        requirements=StrategyRequirements(products=("MNQ",), max_contracts=4),
        capability_order_types=("market",),
        capability_max_contracts=10,
        rules_max_contracts=3,
        at=NOW,
    )
    assert report.verdict is Permission.BLOCKED
    assert any("cap is 3" in r.detail for r in report.reasons)


def test_no_known_cap_at_all_is_unknown() -> None:
    report = evaluate_compatibility(
        account_uid="a",
        use_case=UseCase.STRATEGY_AUTOMATION,
        policy=PropProgramPolicy(
            automation=Permission.ALLOWED, permitted_products=("MNQ",)
        ),
        requirements=StrategyRequirements(products=("MNQ",)),
        capability_order_types=("market",),
        at=NOW,
    )
    assert report.verdict is Permission.UNKNOWN


# ── confirmation ─────────────────────────────────────────────────────────────


def test_an_unconfirmed_requires_confirmation_does_not_permit_action() -> None:
    report = evaluate_compatibility(
        account_uid="a",
        use_case=UseCase.COPY_FOLLOWER,
        policy=PropProgramPolicy(copy_in=Permission.REQUIRES_CONFIRMATION),
        at=NOW,
    )
    assert report.verdict is Permission.REQUIRES_CONFIRMATION
    assert report.permits_automatic_action is False


def test_a_confirmed_requires_confirmation_permits_action() -> None:
    report = evaluate_compatibility(
        account_uid="a",
        use_case=UseCase.COPY_FOLLOWER,
        policy=PropProgramPolicy(copy_in=Permission.REQUIRES_CONFIRMATION),
        at=NOW,
    ).model_copy(update={"confirmed_by": "the operator", "confirmed_at": NOW})
    assert report.permits_automatic_action is True


def test_confirming_a_blocked_verdict_changes_nothing() -> None:
    """A confirmation settles a question; it does not overrule a prohibition."""
    report = evaluate_compatibility(
        account_uid="a",
        use_case=UseCase.COPY_FOLLOWER,
        policy=PropProgramPolicy(copy_in=Permission.BLOCKED),
        at=NOW,
    ).model_copy(update={"confirmed_by": "the operator", "confirmed_at": NOW})
    assert report.permits_automatic_action is False


def test_a_report_cites_the_policy_version_it_relied_on() -> None:
    policy = PropProgramPolicy(copy_in=Permission.ALLOWED)
    report = evaluate_compatibility(
        account_uid="a", use_case=UseCase.COPY_FOLLOWER, policy=policy, at=NOW
    )
    assert report.policy_version == policy.version

    edited = policy.model_copy(update={"copy_in": Permission.BLOCKED})
    assert edited.version != policy.version


# ── the cross-account direction guard ────────────────────────────────────────


def guard() -> DirectionGuard:
    return DirectionGuard({"NQ": "NASDAQ100", "MNQ": "NASDAQ100", "ES": "SP500"})


def test_going_short_a_micro_while_another_account_is_long_the_mini_is_blocked() -> None:
    """The rule as the firms write it: correlated products count as one exposure."""
    reason = guard().check(
        positions={"a": {"NQ": 2}, "b": {}},
        account_uid="b",
        symbol="MNQ",
        delta=-1,
        policy=None,
    )
    assert reason.verdict is Permission.BLOCKED
    assert "mini against its own micro" in reason.detail


def test_the_same_direction_across_accounts_is_allowed() -> None:
    reason = guard().check(
        positions={"a": {"NQ": 2}, "b": {}},
        account_uid="b",
        symbol="MNQ",
        delta=3,
        policy=None,
    )
    assert reason.verdict is Permission.ALLOWED


def test_an_unrelated_product_does_not_trigger_the_guard() -> None:
    reason = guard().check(
        positions={"a": {"NQ": 2}, "b": {}},
        account_uid="b",
        symbol="ES",
        delta=-1,
        policy=None,
    )
    assert reason.verdict is Permission.ALLOWED


def test_a_policy_permitting_hedging_downgrades_the_block_to_a_confirmation() -> None:
    policy = PropProgramPolicy(cross_account_hedging=Permission.ALLOWED)
    reason = guard().check(
        positions={"a": {"NQ": 2}, "b": {}},
        account_uid="b",
        symbol="MNQ",
        delta=-1,
        policy=policy,
    )
    assert reason.verdict is Permission.REQUIRES_CONFIRMATION
    assert reason.blocking is True


def test_an_opposite_position_within_one_account_is_also_caught() -> None:
    reason = guard().check(
        positions={"a": {"NQ": 2}},
        account_uid="a",
        symbol="MNQ",
        delta=-3,
        policy=None,
    )
    assert reason.verdict is Permission.BLOCKED


def test_a_zero_delta_asks_nothing() -> None:
    reason = guard().check(
        positions={"a": {"NQ": 2}}, account_uid="a", symbol="MNQ", delta=0, policy=None
    )
    assert reason.verdict is Permission.ALLOWED


def test_an_unmapped_symbol_is_its_own_group() -> None:
    """Two unknown products are not silently treated as the same exposure."""
    subject = DirectionGuard({})
    assert subject.group("ZZ") == "ZZ"
    reason = subject.check(
        positions={"a": {"YY": 1}}, account_uid="a", symbol="ZZ", delta=-1, policy=None
    )
    assert reason.verdict is Permission.ALLOWED


# ── reports ──────────────────────────────────────────────────────────────────


def test_a_report_serialises_with_its_computed_permission() -> None:
    report = evaluate_compatibility(
        account_uid="a",
        use_case=UseCase.COPY_FOLLOWER,
        policy=PropProgramPolicy(copy_in=Permission.ALLOWED),
        at=NOW,
    )
    assert isinstance(report, CompatibilityReport)
    assert report.as_dict()["permits_automatic_action"] is True
