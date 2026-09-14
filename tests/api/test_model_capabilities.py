"""Capability before the call, and what a failure means after it.

Both exist to stop the same thing: a degraded result being mistaken for a real
one. A model that cannot produce structured output should not be *assigned* the
job, and an authentication failure should not be retried until it looks like a
slow network.
"""

from __future__ import annotations

import pytest
from forge_api.model_capabilities import (
    UNDECLARED,
    Capability,
    Failure,
    ModelCapabilities,
    backoff_seconds,
    capabilities_for,
    capable,
    classify,
    retryable,
)

CATALOGUE = [
    {
        "id": "big",
        "tier": "frontier",
        "capabilities": {
            "structured_output": True,
            "tool_calling": True,
            "streaming": True,
            "context_tokens": 200_000,
        },
    },
    {
        "id": "small",
        "tier": "economy",
        "capabilities": {
            "structured_output": False,
            "tool_calling": False,
            "streaming": True,
            "context_tokens": 8_000,
        },
    },
    # Flags on the entry itself rather than nested, which is how a provider
    # listing that predates the nested shape reads.
    {"id": "flat", "tier": "fast", "structured_output": False, "context_tokens": 32_000},
]


# ── the capability table ─────────────────────────────────────────────────────


def test_a_declared_model_reports_what_the_catalogue_says() -> None:
    found = capabilities_for("small", CATALOGUE)
    assert found.structured_output is False
    assert found.streaming is True
    assert found.context_tokens == 8_000


def test_flags_are_read_from_the_entry_when_not_nested() -> None:
    """A provider listing without a capabilities block still says something."""
    assert capabilities_for("flat", CATALOGUE).structured_output is False
    assert capabilities_for("flat", CATALOGUE).context_tokens == 32_000


def test_an_unlisted_model_is_uncharacterised_not_unusable() -> None:
    """Refusing by default would make a new model unusable rather than unknown."""
    assert capabilities_for("never-heard-of-it", CATALOGUE) == UNDECLARED
    assert UNDECLARED.structured_output is True


def test_undeclared_context_is_treated_as_short_not_unlimited() -> None:
    """A wrong 'yes' truncates a prompt silently; a wrong 'no' only routes a long
    job to a model that would have coped."""
    assert UNDECLARED.context_tokens == 0
    assert UNDECLARED.has(Capability.LONG_CONTEXT) is False


def test_long_context_is_a_threshold_not_a_guess() -> None:
    assert capabilities_for("big", CATALOGUE).has(Capability.LONG_CONTEXT) is True
    assert capabilities_for("small", CATALOGUE).has(Capability.LONG_CONTEXT) is False


def test_missing_names_every_gap_in_the_order_asked() -> None:
    gaps = capabilities_for("small", CATALOGUE).missing(
        [Capability.STRUCTURED_OUTPUT, Capability.STREAMING, Capability.TOOL_CALLING]
    )
    assert gaps == (Capability.STRUCTURED_OUTPUT, Capability.TOOL_CALLING)


def test_capable_is_the_question_a_router_actually_asks() -> None:
    assert capable("big", [Capability.STRUCTURED_OUTPUT], CATALOGUE) is True
    assert capable("small", [Capability.STRUCTURED_OUTPUT], CATALOGUE) is False
    assert capable("big", [], CATALOGUE) is True


@pytest.mark.parametrize("bogus", [None, "yes", [], {}])
def test_a_nonsense_context_value_reads_as_unknown(bogus: object) -> None:
    entry = [{"id": "odd", "capabilities": {"context_tokens": bogus}}]
    assert capabilities_for("odd", entry).context_tokens == 0


def test_a_boolean_context_value_is_not_a_token_count() -> None:
    """True would otherwise arrive as 1, which is a number and is nonsense."""
    entry = [{"id": "odd", "capabilities": {"context_tokens": True}}]
    assert capabilities_for("odd", entry).context_tokens == 0


def test_a_negative_context_value_is_clamped() -> None:
    entry = [{"id": "odd", "capabilities": {"context_tokens": -5}}]
    assert capabilities_for("odd", entry).context_tokens == 0


# ── the failure taxonomy ─────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [401, 402, 403])
def test_a_key_that_will_not_be_served_is_never_retried(status: int) -> None:
    """Retrying is how a systemic failure gets reported as a slow one."""
    assert classify(status) is Failure.NOT_ENTITLED
    assert retryable(Failure.NOT_ENTITLED) is False
    assert backoff_seconds(Failure.NOT_ENTITLED, 1) == 0.0


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_a_transient_failure_is_worth_another_attempt(status: int) -> None:
    assert classify(status) is Failure.TRANSIENT
    assert retryable(Failure.TRANSIENT) is True


def test_rate_limiting_is_transient_with_a_longer_wait_not_its_own_class() -> None:
    """A different parameter, not a different reaction -- which is the whole
    sizing rule this taxonomy is built on."""
    assert classify(429) is Failure.TRANSIENT
    assert backoff_seconds(Failure.TRANSIENT, 1, rate_limited=True) > backoff_seconds(
        Failure.TRANSIENT, 1
    )


@pytest.mark.parametrize(
    "message",
    ["Invalid API key provided", "Unauthorized", "You exceeded your quota", "billing hard limit"],
)
def test_a_refusal_in_words_is_read_when_the_status_did_not_say(message: str) -> None:
    assert classify(None, message) is Failure.NOT_ENTITLED


def test_a_safety_refusal_is_not_entitled_and_so_is_never_circumvented() -> None:
    assert classify(None, "blocked by content policy") is Failure.NOT_ENTITLED
    assert retryable(classify(None, "blocked by content policy")) is False


@pytest.mark.parametrize(
    "message",
    ["model does not support tool_choice", "unrecognized parameter: response_format"],
)
def test_an_unsupported_parameter_means_route_elsewhere(message: str) -> None:
    assert classify(None, message) is Failure.INCOMPATIBLE
    assert retryable(Failure.INCOMPATIBLE) is False


def test_a_precise_status_beats_the_word_list() -> None:
    """A provider that returns the right code is believed, even if its prose
    happens to contain a word from the other list."""
    assert classify(503, "invalid api key") is Failure.TRANSIENT


def test_an_unrecognised_failure_is_transient_because_the_alternatives_are_worse() -> None:
    """NOT_ENTITLED would stop work that might succeed; UNUSABLE would claim an
    output arrived when none did. One retry reveals which it was."""
    assert classify(None, "something went sideways") is Failure.TRANSIENT
    assert classify(418, "") is Failure.TRANSIENT


def test_backoff_grows_and_then_stops_growing() -> None:
    waits = [backoff_seconds(Failure.TRANSIENT, attempt) for attempt in range(1, 10)]
    assert waits == sorted(waits)
    assert waits[-1] <= 60.0


def test_backoff_is_zero_for_anything_not_worth_retrying() -> None:
    """So a caller that forgets to check `retryable` still does not hammer a
    provider that has refused it."""
    for failure in (Failure.NOT_ENTITLED, Failure.INCOMPATIBLE, Failure.UNUSABLE):
        assert backoff_seconds(failure, 1) == 0.0


def test_the_taxonomy_is_four_because_there_are_four_reactions() -> None:
    """Pinned deliberately: the sizing rule is the finding, and a fifth class
    added without a fifth behaviour is the thing it exists to prevent."""
    assert len(list(Failure)) == 4


def test_capabilities_compare_by_value() -> None:
    assert ModelCapabilities(True, True, True) == ModelCapabilities(True, True, True)


# ── role-level output bounds ─────────────────────────────────────────────────


def test_a_role_ceiling_narrows_but_can_never_widen() -> None:
    """A limit a settings edit can lift is not a safety limit."""
    from forge_api.model_routing import RoleRouting, RoutingSettings, output_ceiling

    settings = RoutingSettings(roles={"scout": RoleRouting(max_output_tokens=400)})
    assert output_ceiling(settings, "scout", 1600) == 400

    greedy = RoutingSettings(roles={"scout": RoleRouting(max_output_tokens=99_999)})
    assert output_ceiling(greedy, "scout", 1600) == 1600


def test_a_role_with_no_ceiling_uses_the_global_one() -> None:
    from forge_api.model_routing import RoutingSettings, output_ceiling

    assert output_ceiling(RoutingSettings(), "anything", 1600) == 1600


def test_a_nonsense_stored_ceiling_reads_as_unset() -> None:
    """A typo in one role must not stop every role loading."""
    from forge_api.model_routing import normalise

    settings = normalise(
        {"roles": {"scout": {"max_output_tokens": "lots"}}}, known_models=[]
    )
    assert settings.for_role("scout").max_output_tokens == 0
