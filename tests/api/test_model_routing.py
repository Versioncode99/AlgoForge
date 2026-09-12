"""Which model answers, and whether the settings screen is telling the truth.

Two failures this is written against, and neither is about picking badly.

The first is a control that does nothing: a routing table covering nine
workflow roles while the research engine ran ten agent roles of its own meant
that choosing "a model per role" chose the model for none of the work the
campaigns actually did.

The second is a substitution nobody sees. The old `model_for` quietly returned a
default when the selected provider could not serve the configured model. The
substitution is often right; being invisible is what makes it a problem, because
the operator reads the settings screen and believes a frontier model answered.

So every test here checks either coverage or the explanation.
"""

from __future__ import annotations

import pytest
from forge.research.agents import AgentRole
from forge_api.model_routing import (
    AGENT_ROLE_KEYS,
    ROLE_KEYS,
    ROLES,
    ROLES_BY_KEY,
    Demand,
    RoleRouting,
    RoutingMode,
    RoutingSettings,
    mode_rows,
    normalise,
    resolve,
    role_rows,
    to_dict,
)

CATALOGUE = [
    {"id": "big-1", "label": "Big", "tier": "frontier"},
    {"id": "mid-1", "label": "Mid", "tier": "fast"},
    {"id": "small-1", "label": "Small", "tier": "economy"},
]
KNOWN = {entry["id"] for entry in CATALOGUE}


def _settings(**kwargs: object) -> RoutingSettings:
    base = RoutingSettings(
        mode=RoutingMode.HYBRID.value,
        default_model="mid-1",
        fallback_model="mid-1",
        roles={key: RoleRouting() for key in ROLE_KEYS},
    )
    for key, value in kwargs.items():
        setattr(base, key, value)
    return base


# ── coverage ─────────────────────────────────────────────────────────────────


def test_every_research_agent_role_can_be_assigned_a_model() -> None:
    """The gap this module exists to close.

    A role the engine can run and the settings cannot reach is a model choice
    the operator does not have; a role in settings the engine never runs is a
    control that does nothing. Both are asserted against here.
    """
    engine_roles = {str(role) for role in AgentRole}
    assert engine_roles <= set(AGENT_ROLE_KEYS)
    for name in engine_roles:
        assert AGENT_ROLE_KEYS[name] in ROLES_BY_KEY, name


def test_no_routing_role_is_unreachable_from_the_engine_or_the_workflow() -> None:
    workflow = {role.key for role in ROLES if role.kind == "workflow"}
    research = {role.key for role in ROLES if role.kind == "research"}
    assert research == set(AGENT_ROLE_KEYS.values())
    assert workflow, "the workflow half must not be empty"


def test_every_role_says_what_it_is_for_and_what_it_needs() -> None:
    for role in ROLES:
        assert role.label and len(role.detail) > 20, role.key
        assert role.demand in set(Demand)
        assert role.kind in ("workflow", "research")


def test_the_role_and_mode_tables_the_interface_renders_match_the_module() -> None:
    assert {row["key"] for row in role_rows()} == set(ROLE_KEYS)
    assert {row["key"] for row in mode_rows()} == set(RoutingMode)
    for row in mode_rows():
        assert len(row["detail"]) > 30, row["key"]


# ── the explanation ──────────────────────────────────────────────────────────


def test_an_assigned_model_is_used_and_the_reason_names_the_assignment() -> None:
    settings = _settings()
    settings.roles["agent_hypothesis"] = RoleRouting(model="big-1")
    decision = resolve(
        "agent_hypothesis", settings, provider="opencode_go", catalogue=CATALOGUE
    )
    assert decision.model == "big-1"
    assert decision.source == "assigned"
    assert not decision.substituted
    assert "assigned" in decision.reason


def test_a_substitution_is_reported_rather_than_made_quietly() -> None:
    """The failure this module was written against."""
    settings = _settings()
    settings.roles["agent_hypothesis"] = RoleRouting(model="not-on-this-provider")
    decision = resolve(
        "agent_hypothesis", settings, provider="deepseek", catalogue=CATALOGUE
    )
    assert decision.model == "mid-1"
    assert decision.substituted
    assert "not-on-this-provider" in decision.reason
    assert "deepseek" in decision.reason


def test_manual_routing_refuses_rather_than_substituting() -> None:
    """The mode that exists so a model comparison is not contaminated."""
    settings = _settings(mode=RoutingMode.MANUAL.value)
    settings.roles["agent_hypothesis"] = RoleRouting(model="absent")
    decision = resolve(
        "agent_hypothesis", settings, provider="opencode_go", catalogue=CATALOGUE
    )
    assert decision.model == ""
    assert not decision.available
    assert "does not substitute" in decision.reason


def test_hybrid_prefers_the_roles_own_fallback_over_the_global_one() -> None:
    settings = _settings()
    settings.roles["agent_regime"] = RoleRouting(model="absent", fallback="small-1")
    decision = resolve("agent_regime", settings, provider="opencode_go", catalogue=CATALOGUE)
    assert decision.model == "small-1"
    assert decision.source == "fallback"
    assert decision.substituted


def test_a_model_that_just_failed_is_not_asked_again() -> None:
    settings = _settings()
    settings.roles["chat"] = RoleRouting(model="big-1", fallback="small-1")
    decision = resolve(
        "chat",
        settings,
        provider="opencode_go",
        catalogue=CATALOGUE,
        unavailable=("big-1",),
    )
    assert decision.model == "small-1"
    assert "refused on the last call" in decision.reason


def test_a_disabled_role_is_not_called_and_says_so() -> None:
    settings = _settings()
    settings.roles["agent_reviewer"] = RoleRouting(model="big-1", enabled=False)
    decision = resolve("agent_reviewer", settings, provider="opencode_go", catalogue=CATALOGUE)
    assert decision.model == ""
    assert decision.source == "disabled"
    assert "switched off" in decision.reason


def test_an_unknown_role_names_the_roles_that_exist() -> None:
    with pytest.raises(KeyError, match="unknown routing role"):
        resolve("nonexistent", _settings(), provider="opencode_go", catalogue=CATALOGUE)


# ── smart routing stays inside the allowed list ──────────────────────────────


def test_smart_routing_picks_by_what_the_job_needs() -> None:
    settings = _settings(mode=RoutingMode.SMART.value)
    reasoning = resolve(
        "agent_hypothesis", settings, provider="opencode_go", catalogue=CATALOGUE
    )
    bulk = resolve("bulk", settings, provider="opencode_go", catalogue=CATALOGUE)
    assert reasoning.model == "big-1"
    assert bulk.model == "small-1"
    assert "reasoning" in reasoning.reason


def test_smart_routing_never_reaches_outside_the_allowed_list() -> None:
    """What stops "smart" meaning "whatever is most expensive"."""
    settings = _settings(mode=RoutingMode.SMART.value, allowed=["small-1"])
    decision = resolve(
        "agent_hypothesis", settings, provider="opencode_go", catalogue=CATALOGUE
    )
    assert decision.model == "small-1"


def test_smart_routing_with_nothing_left_says_so_rather_than_guessing() -> None:
    settings = _settings(mode=RoutingMode.SMART.value, allowed=["small-1"])
    decision = resolve(
        "bulk",
        settings,
        provider="opencode_go",
        catalogue=CATALOGUE,
        unavailable=("small-1",),
    )
    assert decision.model == ""
    assert "no allowed model" in decision.reason


# ── storage ──────────────────────────────────────────────────────────────────


def test_a_stored_model_that_no_longer_exists_is_treated_as_unset() -> None:
    """A provider change must not fail the load or leave a phantom assignment."""
    stored = {
        "mode": "hybrid",
        "default_model": "gone",
        "roles": {"chat": {"model": "gone", "fallback": "small-1"}},
    }
    settings = normalise(stored, known_models=KNOWN)
    assert settings.default_model == ""
    assert settings.for_role("chat").model == ""
    assert settings.for_role("chat").fallback == "small-1"


def test_a_role_the_system_cannot_run_without_stays_enabled() -> None:
    """A settings file must not be able to switch off hypothesis generation."""
    stored = {"roles": {key: {"enabled": False} for key in ROLE_KEYS}}
    settings = normalise(stored, known_models=KNOWN)
    for key in ROLE_KEYS:
        if not ROLES_BY_KEY[key].optional:
            assert settings.for_role(key).enabled, key


def test_an_unknown_mode_falls_back_rather_than_failing_the_load() -> None:
    assert normalise({"mode": "telepathy"}, known_models=KNOWN).mode == RoutingMode.HYBRID.value


def test_routing_survives_a_round_trip_through_storage() -> None:
    settings = _settings()
    settings.roles["agent_discovery"] = RoleRouting(
        model="big-1", fallback="small-1", critic="mid-1"
    )
    restored = normalise(to_dict(settings), known_models=KNOWN)
    entry = restored.for_role("agent_discovery")
    assert (entry.model, entry.fallback, entry.critic) == ("big-1", "small-1", "mid-1")
