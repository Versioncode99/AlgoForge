"""Freshness, tiers, service health, and the four invariants they exist for.

OpenTerminal's cache returns the last-known value when a provider fails and the
caller cannot tell. Its fallback chain returns the first success and the caller
never learns which link answered. Both are right for an indicative terminal and
both are fabrication in a system that decides whether evidence is admissible,
so the mechanism is kept and the silence is removed.

The four properties, stated as the brief states them:

* stale data cannot silently masquerade as fresh
* unavailable data cannot silently become admissible evidence
* a descent to a lower tier is never silent
* a service nobody has called is not healthy
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from forge.data.freshness import (
    NOT_EVIDENCE,
    RANK,
    TIER_MEANING,
    Freshness,
    Served,
    Tier,
    classify,
    unavailable,
)
from forge.data.services import (
    ABSENT_CAPABILITIES,
    FAILING_AFTER,
    ServiceRegistry,
    ServiceState,
    chain,
)

# ── the tier ladder ──────────────────────────────────────────────────────────


def test_every_tier_is_ranked_and_explained() -> None:
    """A tier a surface cannot describe is a tier that will be described wrongly."""
    assert set(RANK) == set(Tier)
    assert set(TIER_MEANING) == set(Tier)


def test_unavailable_ranks_below_everything() -> None:
    assert RANK[Tier.UNAVAILABLE] == 0
    assert all(RANK[tier] > 0 for tier in Tier if tier is not Tier.UNAVAILABLE)


# ── admissibility ────────────────────────────────────────────────────────────


def _fresh(tier: Tier = Tier.RESEARCH_GRADE) -> Served[int]:
    return Served(1, provider="databento", tier=tier, freshness=Freshness.FRESH)


def test_fresh_data_at_the_declared_tier_is_admissible() -> None:
    ok, reason = _fresh().admissible(Tier.RESEARCH_GRADE)
    assert ok and reason == ""


def test_a_lower_tier_is_blocked_not_warned() -> None:
    """§11: never silently switch to low-quality data while pretending nothing changed."""
    ok, reason = _fresh(Tier.INDICATIVE).admissible(Tier.RESEARCH_GRADE)
    assert not ok
    assert "INDICATIVE" in reason and "RESEARCH_GRADE" in reason
    assert "blocked rather than warned" in reason


def test_a_higher_tier_satisfies_a_lower_requirement() -> None:
    ok, _ = _fresh(Tier.EXECUTION_GRADE).admissible(Tier.INDICATIVE)
    assert ok


@pytest.mark.parametrize("state", sorted(NOT_EVIDENCE))
def test_no_state_in_not_evidence_is_admissible(state: Freshness) -> None:
    """The set is data so this can be a property rather than four tests."""
    served = Served(1, provider="p", tier=Tier.EXECUTION_GRADE, freshness=state)
    ok, reason = served.admissible(Tier.INDICATIVE)
    assert not ok, f"{state} was admitted as evidence"
    assert reason


def test_stale_data_is_usable_for_a_panel_and_not_for_a_result() -> None:
    """The distinction the whole envelope exists to carry.

    §12 permits an informational surface to render a cached value while a
    refresh is under way. It does not permit a backtest to rest on one.
    """
    served = Served(1, provider="p", tier=Tier.RESEARCH_GRADE, freshness=Freshness.STALE)
    assert served.usable
    assert not served.admissible(Tier.RESEARCH_GRADE)[0]


def test_nothing_returned_is_never_admissible_however_high_the_tier() -> None:
    served = unavailable("databento", "no credential is configured")
    assert not served.usable
    ok, reason = served.admissible(Tier.SYNTHETIC)
    assert not ok
    assert "no credential" in reason


def test_the_envelope_never_leaks_the_payload_into_the_record() -> None:
    """`as_dict` goes into audit rows and onto screens. The value does not."""
    payload = {"secret-looking": "x" * 40}
    served = Served(payload, provider="p", tier=Tier.INDICATIVE, freshness=Freshness.FRESH)
    described = served.as_dict()
    assert described["has_value"] is True
    assert "x" * 40 not in str(described)


# ── freshness over time ──────────────────────────────────────────────────────


def test_inside_the_ttl_is_fresh_and_past_it_is_not() -> None:
    now = datetime.now(UTC)
    ttl = timedelta(seconds=60)
    assert classify(retrieved_at=now, ttl=ttl, now=now) is Freshness.FRESH
    assert classify(retrieved_at=now - timedelta(seconds=90), ttl=ttl, now=now) is Freshness.STALE


def test_past_the_ttl_with_a_refresh_running_says_refreshing() -> None:
    """"Wait a moment" and "the source is down" are different things to show."""
    now = datetime.now(UTC)
    assert (
        classify(
            retrieved_at=now - timedelta(seconds=90),
            ttl=timedelta(seconds=60),
            refreshing=True,
            now=now,
        )
        is Freshness.REFRESHING
    )


# ── service health ───────────────────────────────────────────────────────────


def test_a_service_nobody_called_is_not_healthy() -> None:
    """Zero failures out of zero attempts is not a clean bill of health."""
    registry = ServiceRegistry()
    registry.declare("databento", Tier.RESEARCH_GRADE)
    service = registry.get("databento")
    assert service is not None
    assert service.state is ServiceState.NOT_OBSERVED
    assert "not been called" in service.explain()["what"]


def test_a_service_with_no_credential_is_unconfigured_not_failing() -> None:
    """Nothing is wrong with the service; something is missing here.

    Kept distinct because the remedies are completely different, and an
    operator sent to check connectivity for a missing API key is being sent to
    the wrong place.
    """
    registry = ServiceRegistry()
    registry.declare("fred", Tier.RESEARCH_GRADE, unconfigured="FRED_API_KEY is not set")
    service = registry.get("fred")
    assert service is not None
    assert service.state is ServiceState.UNCONFIGURED
    assert "FRED_API_KEY" in service.explain()["why"]


def test_one_failure_degrades_and_several_in_a_row_fail() -> None:
    registry = ServiceRegistry()
    registry.declare("databento", Tier.RESEARCH_GRADE)
    registry.record_success("databento", 10.0)
    registry.record_failure("databento", "503")
    assert registry.get("databento").state is ServiceState.DEGRADED
    for _ in range(FAILING_AFTER):
        registry.record_failure("databento", "503")
    assert registry.get("databento").state is ServiceState.FAILING


def test_a_success_clears_the_consecutive_failure_run() -> None:
    registry = ServiceRegistry()
    for _ in range(FAILING_AFTER):
        registry.record_failure("p", "boom")
    assert registry.get("p").state is ServiceState.FAILING
    registry.record_success("p", 5.0)
    assert registry.get("p").state is ServiceState.DEGRADED


def test_counters_say_they_are_observations_and_not_uptime() -> None:
    registry = ServiceRegistry()
    registry.declare("p", Tier.INDICATIVE)
    assert registry.snapshot()[0]["observed"] == "since this process started"


def test_the_snapshot_puts_the_broken_ones_first() -> None:
    registry = ServiceRegistry()
    registry.declare("healthy", Tier.INDICATIVE)
    registry.record_success("healthy", 1.0)
    registry.declare("quiet", Tier.INDICATIVE)
    registry.declare("broken", Tier.INDICATIVE)
    for _ in range(FAILING_AFTER):
        registry.record_failure("broken", "boom")
    assert next(row["name"] for row in registry.snapshot()) == "broken"


def test_every_unhealthy_state_carries_what_why_impact_and_remedy() -> None:
    """§13. A status light with no remedy is decoration."""
    registry = ServiceRegistry()
    registry.declare("a", Tier.INDICATIVE)
    registry.declare("b", Tier.INDICATIVE, unconfigured="no key")
    registry.record_failure("c", "boom")
    for row in registry.snapshot():
        assert set(row["explain"]) == {"what", "why", "impact", "remedy"}
        assert row["explain"]["what"]


def test_a_failing_call_is_recorded_and_still_raises() -> None:
    registry = ServiceRegistry()

    def boom() -> int:
        raise RuntimeError("503 from upstream")

    with pytest.raises(RuntimeError):
        registry.tracked("databento", boom)
    assert registry.get("databento").failed == 1
    assert "503" in registry.get("databento").last_error


# ── fallback chains ──────────────────────────────────────────────────────────


def test_the_chain_reports_which_link_answered() -> None:
    """OpenTerminal's `withFallback` returns the first success and says nothing."""
    served = chain(
        [
            ("databento", Tier.RESEARCH_GRADE, _raises),
            ("crypto-public", Tier.INDICATIVE, lambda: [1, 2, 3]),
        ],
        required=Tier.INDICATIVE,
    )
    assert served.provider == "crypto-public"
    assert served.value == [1, 2, 3]


def test_a_descent_across_a_tier_boundary_is_marked_degraded() -> None:
    served = chain(
        [
            ("databento", Tier.RESEARCH_GRADE, _raises),
            ("crypto-public", Tier.INDICATIVE, lambda: [1]),
        ],
        required=Tier.RESEARCH_GRADE,
    )
    assert served.freshness is Freshness.DEGRADED
    assert "below the RESEARCH_GRADE" in served.reason
    # Usable for a chart, refused for a result.
    assert served.usable
    assert not served.admissible(Tier.RESEARCH_GRADE)[0]


def test_a_descent_within_a_tier_is_not_degraded() -> None:
    served = chain(
        [("a", Tier.INDICATIVE, _raises), ("b", Tier.INDICATIVE, lambda: [1])],
        required=Tier.INDICATIVE,
    )
    assert served.freshness is Freshness.FRESH
    # The earlier failure is still carried, so the descent is observable.
    assert "a:" in served.reason


def test_a_chain_with_nothing_left_returns_unavailable_with_every_reason() -> None:
    served = chain(
        [("a", Tier.RESEARCH_GRADE, _raises), ("b", Tier.INDICATIVE, _raises)],
        required=Tier.INDICATIVE,
    )
    assert served.freshness is Freshness.UNAVAILABLE
    assert served.value is None
    assert "a:" in served.reason and "b:" in served.reason


def test_an_empty_chain_says_nothing_was_configured() -> None:
    served: Served[int] = chain([], required=Tier.INDICATIVE)
    assert served.freshness is Freshness.UNAVAILABLE
    assert "no sources were configured" in served.reason


def test_the_chain_order_is_the_order_given() -> None:
    """Deterministic, so the same outage produces the same descent twice."""
    for _ in range(5):
        served = chain(
            [("first", Tier.INDICATIVE, lambda: "a"), ("second", Tier.INDICATIVE, lambda: "b")],
            required=Tier.INDICATIVE,
        )
        assert served.provider == "first"


def test_the_chain_records_health_when_given_a_registry() -> None:
    registry = ServiceRegistry()
    chain(
        [("databento", Tier.RESEARCH_GRADE, _raises), ("public", Tier.INDICATIVE, lambda: 1)],
        required=Tier.INDICATIVE,
        registry=registry,
    )
    assert registry.get("databento").failed == 1
    assert registry.get("public").ok == 1


# ── what this build does not have ────────────────────────────────────────────


def test_absent_capabilities_are_named_rather_than_omitted() -> None:
    """An absent row reads as "fine"."""
    capabilities = {row["capability"] for row in ABSENT_CAPABILITIES}
    assert "Headline news" in capabilities
    for row in ABSENT_CAPABILITIES:
        assert {"what", "why", "impact", "remedy"} <= set(row)


def test_nothing_in_this_build_claims_to_be_execution_grade() -> None:
    """There is no venue feed and no order path. Claiming the tier would be the
    "call a public endpoint institutional" error the vocabulary exists to stop."""
    from forge.data.providers import PROVIDER_TIERS

    assert str(Tier.EXECUTION_GRADE) not in set(PROVIDER_TIERS.values())


def _raises() -> object:
    raise RuntimeError("upstream said no")
