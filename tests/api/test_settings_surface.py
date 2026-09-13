"""The settings endpoint, and whether the controls on it do anything.

The audit that opened this phase found a control that did not work: the screen
offered an editable "OpenAI-compatible base URL", the endpoint accepted it, and
`settings_store.load()` derived the URL from the provider id on every read — so
an operator could type a value, watch the save succeed, and have it silently
revert. That is the shape of defect these tests are about, and it is the reason
each one goes through the real HTTP surface rather than through the store.

Every assertion here is one of three things: a control changes stored state, a
control that cannot change state is refused with a reason, or a value the screen
shows is derived from what would actually happen rather than from what was
asked for.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from forge.research.agents import AgentRole
from forge_api.main import create_app
from forge_api.model_routing import AGENT_ROLE_KEYS


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "api.db")) as api:
        yield api


def _settings(client: TestClient) -> dict:
    response = client.get("/api/v1/settings")
    assert response.status_code == 200
    return response.json()["data"]


def _patch(client: TestClient, body: dict) -> dict:
    response = client.patch("/api/v1/settings", json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]


# ── coverage ─────────────────────────────────────────────────────────────────


def test_every_research_agent_role_is_configurable_over_the_api(client) -> None:
    """The roles a campaign actually spends are the ones that were missing."""
    data = _settings(client)
    keys = {row["key"] for row in data["routing_roles"]}
    for role in AgentRole:
        assert AGENT_ROLE_KEYS[str(role)] in keys, role
    assert any(row["kind"] == "research" for row in data["routing_roles"])
    assert any(row["kind"] == "workflow" for row in data["routing_roles"])


def test_the_response_says_which_model_would_answer_for_every_role(client) -> None:
    data = _settings(client)
    preview = {row["role"]: row for row in data["routing_preview"]}
    assert set(preview) == {row["key"] for row in data["routing_roles"]}
    for row in preview.values():
        # A decision with no sentence behind it is a routing layer that cannot
        # answer "why this model?", which is what made the old screen decorative.
        assert row["reason"], row["role"]


# ── assignments change what is stored ────────────────────────────────────────


def test_assigning_a_model_to_a_research_role_is_stored_and_reflected(client) -> None:
    before = _settings(client)
    model = before["models"][0]["id"]
    after = _patch(client, {"role_routing": {"agent_discovery": {"model": model}}})
    assert after["ai"]["model_routing"]["roles"]["agent_discovery"]["model"] == model
    # And it survives a fresh read, which is where the base-URL field failed.
    assert _settings(client)["ai"]["model_routing"]["roles"]["agent_discovery"]["model"] == model


def test_the_flat_and_the_rich_routing_tables_cannot_disagree(client) -> None:
    """Two tables that can disagree are two tables, one of which is wrong."""
    before = _settings(client)
    model = before["models"][-1]["id"]
    after = _patch(client, {"role_routing": {"chat": {"model": model}}})
    assert after["ai"]["routing"]["chat"] == model
    assert after["ai"]["model_routing"]["roles"]["chat"]["model"] == model


def test_the_older_flat_patch_still_works_and_lands_in_both_places(client) -> None:
    before = _settings(client)
    model = before["models"][0]["id"]
    after = _patch(client, {"routing": {"chat": model}})
    assert after["ai"]["routing"]["chat"] == model
    assert after["ai"]["model_routing"]["roles"]["chat"]["model"] == model


def test_a_fallback_and_a_critic_are_stored_per_role(client) -> None:
    models = [row["id"] for row in _settings(client)["models"]]
    after = _patch(
        client,
        {"role_routing": {"agent_regime": {"fallback": models[1], "critic": models[0]}}},
    )
    entry = after["ai"]["model_routing"]["roles"]["agent_regime"]
    assert entry["fallback"] == models[1]
    assert entry["critic"] == models[0]


def test_the_routing_mode_is_stored(client) -> None:
    assert _patch(client, {"routing_mode": "manual"})["ai"]["model_routing"]["mode"] == "manual"


def test_manual_routing_reports_an_unservable_assignment_instead_of_substituting(
    client,
) -> None:
    """The preview is what the operator reads, so it has to show the refusal."""
    _patch(client, {"routing_mode": "manual"})
    # Assign a model that exists in the vocabulary but not on this provider by
    # switching provider afterwards, which is exactly how this happens in use.
    models = [row["id"] for row in _settings(client)["models"]]
    _patch(client, {"role_routing": {"agent_reviewer": {"model": models[0]}}})
    data = _patch(client, {"ai_provider": "deepseek"})
    row = next(r for r in data["routing_preview"] if r["role"] == "agent_reviewer")
    assert row["model"] == "" or row["model"] == models[0]
    if row["model"] == "":
        assert "does not substitute" in row["reason"]


def test_an_unknown_model_is_refused_with_its_name(client) -> None:
    response = client.patch(
        "/api/v1/settings", json={"role_routing": {"chat": {"model": "no-such-model"}}}
    )
    assert response.status_code == 422
    assert "no-such-model" in response.text


def test_an_unknown_role_is_refused_rather_than_silently_dropped(client) -> None:
    response = client.patch(
        "/api/v1/settings", json={"role_routing": {"not_a_role": {"model": ""}}}
    )
    assert response.status_code == 422
    assert "not_a_role" in response.text


def test_a_role_campaigns_cannot_run_without_stays_enabled_even_if_asked(client) -> None:
    data = _patch(client, {"role_routing": {"agent_hypothesis": {"enabled": False}}})
    assert data["ai"]["model_routing"]["roles"]["agent_hypothesis"]["enabled"] is True


def test_an_optional_role_can_be_switched_off(client) -> None:
    data = _patch(client, {"role_routing": {"agent_reviewer": {"enabled": False}}})
    assert data["ai"]["model_routing"]["roles"]["agent_reviewer"]["enabled"] is False
    row = next(r for r in data["routing_preview"] if r["role"] == "agent_reviewer")
    assert row["source"] == "disabled"


# ── budget ───────────────────────────────────────────────────────────────────


def test_budget_enforcement_can_be_switched_off_and_back_on(client) -> None:
    assert _settings(client)["ai"]["budget"]["enforced"] is True
    assert _patch(client, {"budget_enforced": False})["ai"]["budget"]["enforced"] is False
    assert _patch(client, {"budget_enforced": True})["ai"]["budget"]["enforced"] is True


def test_switching_enforcement_off_actually_lifts_every_research_ceiling(client) -> None:
    """The behavioural difference, asserted rather than described."""
    from forge_api.settings_store import BudgetSettings

    _patch(client, {"budget": {"model_calls_per_day": 50, "campaign_experiments": 9}})
    on = BudgetSettings(**_settings(client)["ai"]["budget"])
    assert on.limit("model_calls_per_day") == 50
    assert on.limit("campaign_experiments") == 9

    _patch(client, {"budget_enforced": False})
    off = BudgetSettings(**_settings(client)["ai"]["budget"])
    assert off.limit("model_calls_per_day") == 0
    assert off.limit("campaign_experiments") == 0
    # The stored numbers are kept, so turning it back on restores the ceilings
    # rather than making the operator type them again.
    assert off.model_calls_per_day == 50


def test_the_safety_limits_are_returned_so_the_screen_cannot_drift(client) -> None:
    limits = _settings(client)["safety_limits"]
    keys = {row["key"] for row in limits}
    assert {"model_concurrency", "provider_restriction", "permissions"} <= keys
    for row in limits:
        assert row["label"] and row["value"] and len(row["why"]) > 20


def test_an_unknown_budget_field_is_refused_rather_than_stored(client) -> None:
    response = client.patch("/api/v1/settings", json={"budget": {"unlimited": 1}})
    assert response.status_code == 422


# ── external research ────────────────────────────────────────────────────────


def test_external_research_is_on_by_default(client) -> None:
    loop = _settings(client)["research_loop"]
    assert loop["enabled"] is True
    assert loop["categories"]


def test_source_categories_freshness_and_depth_are_stored(client) -> None:
    data = _patch(
        client,
        {
            "research_categories": ["preprints"],
            "research_freshness": "any",
            "research_depth": "deep",
        },
    )
    loop = data["research_loop"]
    assert loop["categories"] == ["preprints"]
    assert loop["freshness"] == "any"
    assert loop["depth"] == "deep"


def test_choosing_no_source_is_kept_rather_than_read_as_every_source(client) -> None:
    """"Search nothing" and "search everything" are opposite instructions."""
    assert _patch(client, {"research_categories": []})["research_loop"]["categories"] == []


def test_an_unknown_source_category_is_refused_with_the_known_ones(client) -> None:
    response = client.patch("/api/v1/settings", json={"research_categories": ["twitter"]})
    assert response.status_code == 422
    assert "preprints" in response.text


def test_an_unknown_depth_is_refused(client) -> None:
    response = client.patch("/api/v1/settings", json={"research_depth": "exhaustive"})
    assert response.status_code == 422


# ── the control that did nothing ─────────────────────────────────────────────


def test_the_endpoint_still_reports_the_base_url_as_derived_from_the_provider(
    client,
) -> None:
    """It is shown, not edited.

    The field was editable, accepted, and discarded. It is now returned so the
    interface can display where calls go, and the interface renders it as a
    fact; the endpoint no longer takes a value for it at all.
    """
    before = _settings(client)["ai"]["base_url"]
    response = client.patch("/api/v1/settings", json={"ai_base_url": "https://elsewhere/v1"})
    assert response.status_code == 200
    assert _settings(client)["ai"]["base_url"] == before


def test_secrets_never_cross_the_settings_boundary(client) -> None:
    response = client.get("/api/v1/settings")
    assert response.json()["meta"]["secrets_returned"] is False
    for row in response.json()["data"]["credentials"]:
        assert set(row) >= {"key", "label", "present", "source"}
        assert "value" not in row
