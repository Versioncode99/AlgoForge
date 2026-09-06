"""OpenCode Zen.

The behaviour worth pinning down is that a model blocked on *billing* is a
different thing from a model that is broken, and that the second key buys
resilience rather than throughput. Both are easy to lose in a refactor and both
would mislead the operator if lost.
"""

from __future__ import annotations

import httpx
import pytest
from forge_api.opencode_zen import (
    OPENCODE_ZEN_MODELS,
    OpenCodeZenClient,
    resolve_opencode_credential,
    resolve_opencode_standby,
)
from forge_api.providers import (
    PROVIDER_AUTO,
    PROVIDER_NVIDIA,
    PROVIDER_OMNIROUTE,
    PROVIDER_OPENCODE,
    PROVIDERS,
    catalog_for,
    client_for,
)

KEYS = ("OPENCODE_ZEN_API_KEY", "OPENCODE_ZEN_API_KEY_2")


def _ok(payload):
    return httpx.MockTransport(lambda request: httpx.Response(200, json=payload))


def test_zen_is_advertised_and_selectable():
    ids = {p["id"] for p in PROVIDERS}
    assert ids == {PROVIDER_AUTO, PROVIDER_OMNIROUTE, PROVIDER_NVIDIA, PROVIDER_OPENCODE}
    assert client_for(PROVIDER_OPENCODE).base_url.startswith("https://opencode.ai")


def test_zen_stays_out_of_the_auto_catalogue():
    """`auto` never routes to Zen, so offering its models there would mislead."""
    auto_ids = {model["id"] for model in catalog_for(PROVIDER_AUTO)}
    zen_ids = {model["id"] for model in catalog_for(PROVIDER_OPENCODE)}
    assert not (auto_ids & zen_ids)


def test_catalogue_keeps_billing_blocked_models_and_labels_them():
    """A model that needs credit is not the same as a model that does not exist."""
    catalogue = catalog_for(PROVIDER_OPENCODE)
    statuses = {model["status"] for model in catalogue}
    assert statuses == {"verified", "needs_credit"}
    assert any(model["status"] == "needs_credit" for model in catalogue)


def test_verified_models_are_listed_before_the_blocked_ones():
    statuses = [model["status"] for model in catalog_for(PROVIDER_OPENCODE)]
    assert statuses.index("verified") < statuses.index("needs_credit")


def test_the_chinese_frontier_families_are_all_present():
    """The reason to add this provider at all; a silent drop would be invisible."""
    ids = " ".join(model["id"] for model in catalog_for(PROVIDER_OPENCODE))
    for family in ("deepseek", "glm", "kimi", "minimax", "qwen"):
        assert family in ids, f"{family} missing from the Zen catalogue"


def test_responses_only_models_are_not_offered_for_chat():
    """This client speaks /chat/completions; offering a /responses model would fail."""
    offered = {model["id"] for model in catalog_for(PROVIDER_OPENCODE)}
    responses_only = {m["id"] for m in OPENCODE_ZEN_MODELS if m["endpoint"] == "responses"}
    assert responses_only
    assert not (offered & responses_only)


def test_upstream_dead_models_are_not_offered():
    offered = {model["id"] for model in catalog_for(PROVIDER_OPENCODE)}
    assert "deepseek-v4-flash-free" not in offered


def test_insufficient_balance_says_what_to_do_about_it(monkeypatch):
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "sk-test")
    monkeypatch.delenv("OPENCODE_ZEN_API_KEY_2", raising=False)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, json={"error": {"message": "Insufficient balance."}})
    )
    result = OpenCodeZenClient(transport=transport).chat(model="glm-5.3", system="s", prompt="p")
    assert result["answer"] == ""
    assert "INSUFFICIENT_BALANCE" in result["error"]
    assert "opencode.ai" in result["error"]


def test_a_rejected_primary_key_retries_once_on_the_standby(monkeypatch):
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "sk-primary")
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY_2", "sk-standby")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["authorization"].removeprefix("Bearer ")
        seen.append(token)
        if token == "sk-primary":
            return httpx.Response(401, json={"error": {"message": "Insufficient balance."}})
        return httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": "ok"}}]})

    result = OpenCodeZenClient(transport=httpx.MockTransport(handler)).chat(
        model="m", system="s", prompt="p"
    )
    assert seen == ["sk-primary", "sk-standby"]
    assert result["answer"] == "ok"
    assert result["used_standby_key"] is True


@pytest.mark.parametrize("status", [400, 404, 500])
def test_only_auth_and_quota_failures_are_retried(monkeypatch, status: int):
    """Retrying a 500 on the second key spends quota to get the same answer."""
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "sk-primary")
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY_2", "sk-standby")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["authorization"])
        return httpx.Response(status, json={"error": {"message": "nope"}})

    OpenCodeZenClient(transport=httpx.MockTransport(handler)).chat(
        model="m", system="s", prompt="p"
    )
    assert len(calls) == 1


def test_rate_limit_is_reported_as_a_shared_quota(monkeypatch):
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "sk-primary")
    monkeypatch.delenv("OPENCODE_ZEN_API_KEY_2", raising=False)
    transport = httpx.MockTransport(lambda request: httpx.Response(429, json={}))
    result = OpenCodeZenClient(transport=transport).chat(model="m", system="s", prompt="p")
    assert "RATE_LIMITED" in result["error"]
    assert "share" in result["error"]


def test_reasoning_preamble_is_stripped():
    """Nemotron leaks its scratchpad on this endpoint exactly as it does on NIM."""
    leaked = "Here's a thinking process:\n1. Consider it.\n\nTokyo"
    assert OpenCodeZenClient._clean(leaked) == "Tokyo"
    assert OpenCodeZenClient._clean("<think>noise</think>Tokyo") == "Tokyo"


def test_status_reports_a_missing_key_without_calling_out(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("forge_api.opencode_zen.resolve_opencode_credential", lambda: _absent())
    monkeypatch.setattr("forge_api.opencode_zen.resolve_opencode_standby", lambda: _absent())

    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError("status must not call out without a credential")

    status = OpenCodeZenClient(transport=httpx.MockTransport(explode)).status()
    assert status["connected"] is False
    assert status["error"] == "OPENCODE_ZEN_KEY_MISSING"


def test_status_never_leaks_the_key(monkeypatch):
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "sk-secret-value")
    status = OpenCodeZenClient(transport=_ok({"data": [{"id": "a"}]})).status()
    assert "sk-secret-value" not in repr(status)
    assert status["credential_present"] is True
    assert status["credential_source"] == "environment"


def test_environment_beats_the_env_file(monkeypatch):
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "sk-from-env")
    assert resolve_opencode_credential().value == "sk-from-env"
    assert resolve_opencode_credential().source == "environment"


def test_standby_is_resolved_separately(monkeypatch):
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "sk-one")
    monkeypatch.setenv("OPENCODE_ZEN_API_KEY_2", "sk-two")
    assert resolve_opencode_credential().value == "sk-one"
    assert resolve_opencode_standby().value == "sk-two"


def _absent():
    from forge_api.model_gateway import CredentialMaterial

    return CredentialMaterial("", "none")
