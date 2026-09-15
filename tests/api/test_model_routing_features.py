"""One global default, four feature overrides, and one resolver behind all of it.

Routing was already rich — modes, per-role fallbacks, an allow-list, a decision
with a reason — and the problem was never capability. It was that the *screen*
offered a nineteen-row role matrix as the first thing anybody saw, and that two
of the three callers read a flat copy of the table instead of the table.

So these tests are about the two halves of that: a feature layer somebody can
actually reason about, and every caller reaching the same resolver.
"""

from __future__ import annotations

from typing import Any

import pytest
from forge_api.model_routing import (
    EVIDENCE_CRITICAL_ROLES,
    FEATURE_FOR_ROLE,
    FEATURE_KEYS,
    FEATURES,
    ROLE_KEYS,
    ROLES_BY_KEY,
    RoutingSettings,
    normalise,
    resolve,
    to_dict,
)

CATALOGUE: list[dict[str, Any]] = [
    {"id": "frontier-1", "tier": "frontier"},
    {"id": "fast-1", "tier": "fast"},
    {"id": "economy-1", "tier": "economy"},
]
KNOWN = {entry["id"] for entry in CATALOGUE}


def settings(**raw: Any) -> RoutingSettings:
    return normalise(raw, known_models=KNOWN)


def pick(role: str, config: RoutingSettings, **kwargs: Any) -> Any:
    return resolve(role, config, provider="test", catalogue=CATALOGUE, **kwargs)


# ── the feature layer ────────────────────────────────────────────────────────


def test_four_features_cover_every_role_exactly_once() -> None:
    """A role in no feature cannot be reached from the simple screen.

    A role in two is worse: its model would depend on which feature was written
    last, which is a setting whose effect you cannot predict from the screen.
    """
    assert [feature.key for feature in FEATURES] == ["chat", "research", "strategy", "fast"]
    covered = [role for feature in FEATURES for role in feature.roles]
    assert sorted(covered) == sorted(ROLE_KEYS)
    assert len(covered) == len(set(covered)), "a role is claimed by two features"


def test_every_feature_names_roles_that_exist() -> None:
    for feature in FEATURES:
        unknown = [role for role in feature.roles if role not in ROLES_BY_KEY]
        assert not unknown, f"{feature.key} names roles the engine does not run: {unknown}"


# ── resolution ───────────────────────────────────────────────────────────────


def test_the_global_default_answers_when_nothing_overrides_it() -> None:
    config = settings(default_model="fast-1")
    for role in ROLE_KEYS:
        decision = pick(role, config)
        assert decision.model == "fast-1", role
        assert decision.source == "default"


@pytest.mark.parametrize("feature", FEATURE_KEYS)
def test_a_feature_override_wins_over_the_global_default(feature: str) -> None:
    config = settings(default_model="fast-1", features={feature: "frontier-1"})
    owned = next(f for f in FEATURES if f.key == feature).roles
    for role in owned:
        decision = pick(role, config)
        assert decision.model == "frontier-1", role
        assert decision.source == "feature"
        assert feature.replace("_", " ") in decision.reason.lower() or (
            next(f for f in FEATURES if f.key == feature).label.lower() in decision.reason.lower()
        )
    # And nothing else moved.
    for role in set(ROLE_KEYS) - set(owned):
        assert pick(role, config).model == "fast-1", role


def test_a_role_assignment_wins_over_its_feature() -> None:
    """Somebody who opened the advanced panel and named a model meant it."""
    config = settings(
        default_model="economy-1",
        features={"research": "fast-1"},
        roles={"agent_falsification": {"model": "frontier-1"}},
    )
    assert pick("agent_falsification", config).model == "frontier-1"
    assert pick("agent_falsification", config).source == "assigned"
    assert pick("agent_validation", config).model == "fast-1"


def test_the_fast_route_is_not_used_for_evidence_critical_judgement() -> None:
    """A judge answered by the cheap model is evidence nobody chose the model for.

    The separation is asserted rather than trusted to the table: setting Fast
    tasks to the economy model must not move validation, falsification, review,
    risk or the post-mortem.
    """
    config = settings(default_model="frontier-1", features={"fast": "economy-1"})
    for role in EVIDENCE_CRITICAL_ROLES:
        assert FEATURE_FOR_ROLE[role] != "fast", f"{role} is routed by the fast feature"
        assert pick(role, config).model == "frontier-1", role


# ── validation ───────────────────────────────────────────────────────────────


def test_a_feature_override_naming_a_model_the_provider_does_not_serve_is_dropped() -> None:
    """Read as unset, which falls through to the default and says so.

    Honoured would be worse: the screen would show a frontier model beside a
    feature that a cheap one is actually answering.
    """
    config = settings(default_model="fast-1", features={"chat": "a-model-that-left"})
    assert "chat" not in config.features
    decision = pick("chat", config)
    assert decision.model == "fast-1"
    assert decision.source == "default"


def test_a_feature_override_outside_the_allowed_list_is_dropped() -> None:
    config = settings(
        default_model="fast-1",
        allowed=["fast-1", "economy-1"],
        features={"research": "frontier-1"},
    )
    assert "research" not in config.features
    assert pick("agent_validation", config).model == "fast-1"


def test_an_unavailable_model_is_reported_rather_than_substituted_in_manual_mode() -> None:
    config = settings(mode="manual", default_model="fast-1", features={"chat": "frontier-1"})
    decision = pick("chat", config, unavailable=["frontier-1"])
    assert decision.model == ""
    assert decision.source == "none"
    assert "does not substitute" in decision.reason


def test_a_fallback_is_named_in_the_reason_rather_than_applied_silently() -> None:
    config = settings(
        mode="hybrid",
        default_model="economy-1",
        fallback_model="fast-1",
        features={"chat": "frontier-1"},
    )
    decision = pick("chat", config, unavailable=["frontier-1"])
    assert decision.model == "economy-1"
    assert decision.substituted is True
    assert "frontier-1" in decision.reason
    assert "economy-1" in decision.reason


def test_nothing_available_at_all_says_so_rather_than_picking_anything() -> None:
    config = settings(mode="hybrid", features={"chat": "frontier-1"})
    decision = pick(
        "chat", config, unavailable=["frontier-1", "fast-1", "economy-1"]
    )
    assert decision.model == ""
    assert decision.source == "none"


# ── migration and round trip ─────────────────────────────────────────────────


def test_settings_written_before_features_existed_load_unchanged() -> None:
    """The stored shape gains a key; nothing that was there changes meaning."""
    legacy = {
        "mode": "hybrid",
        "default_model": "fast-1",
        "fallback_model": "economy-1",
        "allowed": ["fast-1", "economy-1", "frontier-1"],
        "roles": {"chat": {"model": "frontier-1", "fallback": "", "enabled": True}},
    }
    config = normalise(legacy, known_models=KNOWN)
    assert config.features == {}
    assert pick("chat", config).model == "frontier-1"
    assert pick("bulk", config).model == "fast-1"


def test_the_features_survive_a_serialisation_round_trip() -> None:
    config = settings(
        default_model="fast-1", features={"chat": "frontier-1", "fast": "economy-1"}
    )
    again = normalise(to_dict(config), known_models=KNOWN)
    assert again.features == config.features
    assert to_dict(again) == to_dict(config)


def test_an_unknown_feature_in_a_stored_file_is_ignored_rather_than_raising() -> None:
    config = settings(default_model="fast-1", features={"telepathy": "frontier-1"})
    assert set(config.features) <= set(FEATURE_KEYS)


# ── the one-way migration off the flat map ───────────────────────────────────


def test_a_legacy_file_carries_its_choices_across_once(tmp_path: Any) -> None:
    """An operator's existing per-role choices survive; the shipped ones do not.

    The distinction is the whole migration. A flat entry equal to what this
    build ships was written into the copy at save time, not chosen — reading it
    back as an assignment would pin every role on every existing installation
    and make the feature overrides do nothing at all.
    """
    import json

    from forge_api.settings_store import DEFAULT_ROUTING, SettingsStore

    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "ai": {
                    "provider": "opencode_go",
                    # One deliberate choice, and one that is just the default.
                    "routing": {
                        "chat": "glm-5.3",
                        "orchestrator": DEFAULT_ROUTING["orchestrator"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    store = SettingsStore(path)
    routing = store.load().ai.model_routing

    assert routing.for_role("chat").model == "glm-5.3", "a real choice was dropped"
    assert routing.for_role("orchestrator").model == "", (
        "the shipped default was migrated as though somebody had chosen it"
    )
    assert routing.for_role("orchestrator").recommended == DEFAULT_ROUTING["orchestrator"]
    assert routing.flat_migrated is True


def test_the_migration_does_not_run_twice(tmp_path: Any) -> None:
    """Re-applying it would let the deprecated copy govern behaviour forever."""
    import json

    from forge_api.settings_store import SettingsStore

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"ai": {"routing": {"chat": "glm-5.3"}}}), encoding="utf-8")
    store = SettingsStore(path)
    first = store.load()
    assert first.ai.model_routing.for_role("chat").model == "glm-5.3"

    # The operator clears the assignment and sets a feature override instead.
    first.ai.model_routing.roles["chat"].model = ""
    first.ai.model_routing.features["chat"] = "deepseek-v4-pro"
    store.save(first)

    second = store.load()
    assert second.ai.model_routing.for_role("chat").model == "", (
        "the flat map re-imposed the cleared assignment"
    )
    assert second.ai.model_routing.features["chat"] == "deepseek-v4-pro"


def test_a_fresh_installation_uses_the_shipped_recommendation_not_a_blank(
    tmp_path: Any,
) -> None:
    """Nothing regresses for somebody who never opens the routing screen."""
    from forge_api.providers import catalog_for
    from forge_api.settings_store import DEFAULT_ROUTING, SettingsStore

    store = SettingsStore(tmp_path / "fresh.json")
    current = store.load()
    catalogue = catalog_for(current.ai.provider)
    for role, expected in DEFAULT_ROUTING.items():
        decision = resolve(
            role, current.ai.model_routing, provider=current.ai.provider, catalogue=catalogue
        )
        assert decision.model == expected, role
        assert decision.source == "recommended"
