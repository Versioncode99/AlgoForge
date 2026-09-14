"""Resume compatibility: the nine cases Doc 1 section 15 names, and the boundaries.

The property under test throughout is the one that makes the failure dangerous
rather than loud: a campaign that resumes under a changed configuration produces
a *plausible* continuation, so every assertion here is about whether the system
can tell the difference, not about whether it crashes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest
from forge.research.identity import (
    Compatibility,
    Identity,
    Verdict,
    compare,
    of,
    parse,
    vocabulary_version,
)


@dataclass(frozen=True)
class FakeCampaign:
    """Only the identity-bearing fields, plus the ones that must not count."""

    dataset: str = "nq-1m"
    symbol: str = "NQ"
    timeframe: str = "1m"
    universe: tuple[str, ...] = ("NQ",)
    start_date: str = "2018-01-01"
    end_date: str = "2024-01-01"
    allowed_capabilities: tuple[str, ...] = ("bars", "session")
    web_research: bool = False
    # Present so the test can change them and assert they do *not* move identity.
    seed: int = 7
    name: str = "Compression breakouts"
    description: str = ""
    priority: int = 50
    agent_target: int = 4
    tags: tuple[str, ...] = ()


def resumed(before: FakeCampaign, after: FakeCampaign) -> Compatibility:
    """Store an identity for `before`, then ask whether `after` may adopt it."""
    return compare(parse(of(before).as_json()), of(after))


# ── the nine scenarios ───────────────────────────────────────────────────────


def test_crash_and_restart_is_a_continuation() -> None:
    """Nothing changed but the process. The whole point of durable state."""
    campaign = FakeCampaign()
    assert resumed(campaign, campaign).verdict is Verdict.COMPATIBLE
    assert resumed(campaign, campaign).resumable


def test_changed_temporal_scope_is_not_a_continuation() -> None:
    """The case the temporal-integrity work exists for.

    Widening the window makes every earlier conclusion rest on bars the
    experiment never saw.
    """
    outcome = resumed(FakeCampaign(), FakeCampaign(start_date="2010-01-01"))
    assert outcome.verdict is Verdict.INCOMPATIBLE
    assert outcome.changed == ("scope",)
    assert not outcome.resumable
    assert "scope" in outcome.reason()


def test_changed_model_configuration_does_not_invalidate() -> None:
    """Deliberately excluded, and worth pinning so nobody adds it later.

    The experiment is run by the deterministic engine. Which model proposed the
    hypothesis does not change whether the backtest happened.
    """
    # Model configuration is not a campaign field at all -- that *is* the
    # assertion. Identity carries seven components and none of them is a model.
    assert "model" not in " ".join(of(FakeCampaign()).components)


def test_changed_agent_configuration_does_not_invalidate() -> None:
    """How many agents work the programme is capacity, not meaning."""
    outcome = resumed(FakeCampaign(agent_target=1), FakeCampaign(agent_target=32))
    assert outcome.verdict is Verdict.COMPATIBLE


def test_changed_research_plan_is_not_a_continuation() -> None:
    """A capability set that widens means the frontier was explored under a
    constraint that no longer holds."""
    outcome = resumed(
        FakeCampaign(), FakeCampaign(allowed_capabilities=("bars", "session", "book"))
    )
    assert outcome.verdict is Verdict.INCOMPATIBLE
    assert outcome.changed == ("capabilities",)


def test_changed_vocabulary_version_is_not_a_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"We already tried that" is a claim about a vocabulary that must still exist."""
    stored = parse(of(FakeCampaign()).as_json())
    assert stored is not None
    monkeypatch.setitem(
        __import__("forge.research.grammar", fromlist=["OBSERVABLES"]).OBSERVABLES,
        "invented_observable",
        object(),
    )
    outcome = compare(stored, of(FakeCampaign()))
    assert outcome.verdict is Verdict.INCOMPATIBLE
    assert outcome.changed == ("vocabulary",)


def test_partial_experiment_state_resumes_when_configuration_holds() -> None:
    """Progress is not identity. A half-finished programme is still the programme."""
    outcome = resumed(FakeCampaign(), FakeCampaign())
    assert outcome.resumable


def test_duplicate_resume_is_idempotent() -> None:
    """Comparing twice gives the same answer; nothing is consumed by asking."""
    campaign = FakeCampaign()
    stored = parse(of(campaign).as_json())
    first = compare(stored, of(campaign))
    second = compare(stored, of(campaign))
    assert first == second


def test_resume_after_completion_is_still_the_same_programme() -> None:
    """Identity says nothing about status.

    Whether a finished campaign *should* restart is a separate decision, made
    with the status in hand. Conflating the two here would mean a completed
    campaign could never be extended even under an unchanged configuration.
    """
    outcome = resumed(FakeCampaign(), FakeCampaign())
    assert outcome.verdict is Verdict.COMPATIBLE


# ── the components, individually ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "value", "component"),
    [
        ("dataset", "es-5m", "dataset"),
        ("symbol", "ES", "instrument"),
        ("timeframe", "5m", "instrument"),
        ("universe", ("NQ", "ES"), "universe"),
        ("end_date", "2025-06-01", "scope"),
        ("web_research", True, "external_research"),
    ],
)
def test_each_identity_component_moves_identity(
    field: str, value: object, component: str
) -> None:
    outcome = resumed(FakeCampaign(), replace(FakeCampaign(), **{field: value}))
    assert outcome.verdict is Verdict.INCOMPATIBLE
    assert outcome.changed == (component,)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seed", 999),
        ("name", "Renamed"),
        ("description", "notes"),
        ("priority", 99),
        ("agent_target", 64),
        ("tags", ("overnight",)),
    ],
)
def test_steering_and_labels_do_not_move_identity(field: str, value: object) -> None:
    """Over-binding teaches operators to work around the check."""
    outcome = resumed(FakeCampaign(), replace(FakeCampaign(), **{field: value}))
    assert outcome.verdict is Verdict.COMPATIBLE


def test_capability_order_is_not_a_change() -> None:
    """A set reordered in a request is the same set."""
    outcome = resumed(
        FakeCampaign(allowed_capabilities=("bars", "session")),
        FakeCampaign(allowed_capabilities=("session", "bars")),
    )
    assert outcome.verdict is Verdict.COMPATIBLE


def test_two_components_changing_are_both_named() -> None:
    outcome = resumed(FakeCampaign(), FakeCampaign(dataset="es-5m", symbol="ES"))
    assert outcome.changed == ("dataset", "instrument")


# ── legacy and malformed rows ────────────────────────────────────────────────


def test_campaign_with_no_recorded_identity_resumes() -> None:
    """Stranding existing programmes to close a hazard they are not in is the
    wrong trade for a store whose premise is that research is not a cache."""
    outcome = compare(None, of(FakeCampaign()))
    assert outcome.verdict is Verdict.UNKNOWN
    assert outcome.resumable


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        "[]",
        "null",
        '{"components": {}}',
        '{"digest": "abc"}',
        '{"components": [], "digest": "a"}',
    ],
)
def test_unreadable_identity_is_treated_as_absent_not_fatal(raw: str) -> None:
    """An unreadable column must not make a campaign unstartable forever."""
    assert parse(raw) is None
    assert compare(parse(raw), of(FakeCampaign())).resumable


def test_stored_identity_round_trips() -> None:
    identity = of(FakeCampaign())
    restored = parse(identity.as_json())
    assert restored is not None
    assert restored.digest == identity.digest
    assert dict(restored.components) == dict(identity.components)


def test_serialisation_is_stable() -> None:
    """The same identity always serialises alike, so a row does not churn."""
    assert of(FakeCampaign()).as_json() == of(FakeCampaign()).as_json()


# ── the digest itself ────────────────────────────────────────────────────────


def test_identity_is_deterministic_across_calls() -> None:
    assert of(FakeCampaign()).digest == of(FakeCampaign()).digest


def test_vocabulary_version_is_derived_not_declared() -> None:
    """Derived from the grammar, so adding an observable cannot be forgotten."""
    before = vocabulary_version()
    grammar = __import__("forge.research.grammar", fromlist=["SHAPES"])
    grammar.SHAPES["invented_shape"] = object()  # type: ignore[assignment]
    try:
        assert vocabulary_version() != before
    finally:
        del grammar.SHAPES["invented_shape"]
    assert vocabulary_version() == before


def test_digest_mismatch_with_unknown_component_shape_still_refuses() -> None:
    """An identity written by an older or newer build names something rather
    than reporting an empty change list."""
    stale = Identity(components={"retired_component": "x"}, digest="0" * 16)
    outcome = compare(stale, of(FakeCampaign()))
    assert outcome.verdict is Verdict.INCOMPATIBLE
    assert outcome.changed  # never empty
