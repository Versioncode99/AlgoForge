"""DeepSeek called directly, alongside the subscription gateway.

Adding a second provider re-opened a class of bug the single-provider collapse
had closed, and the tests here are aimed at it rather than at the HTTP plumbing:

1. Model ids do not agree across providers. `deepseek-flash` exists on the
   account and `deepseek-v4-flash` exists on the gateway, and neither serves the
   other's spelling.
2. Routing is stored per role, not per provider, so switching provider leaves
   every role pointing at models the new provider has never heard of.
3. A reasoning model that is cut off mid-thought must not have its private
   working returned as the answer.
"""

from __future__ import annotations

import httpx
import pytest
from forge_api.deepseek import (
    DEEPSEEK_DEFAULT_URL,
    DEEPSEEK_MODELS,
    DeepSeekClient,
    DeepSeekError,
    resolve_deepseek_credential,
)
from forge_api.providers import (
    PROVIDER_DEEPSEEK,
    PROVIDER_OPENCODE,
    PROVIDERS,
    base_url_for,
    catalog_for,
    client_for,
    credential_for,
    default_model,
    model_for,
)


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    monkeypatch.setenv("OPENCODE_API_KEY", "sk-test-opencode")


def _capture(status: int = 200, body: dict | None = None):
    """Record every outgoing request so auth and routing can be asserted."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body if body is not None else {})

    return httpx.MockTransport(handler), seen


def _answer(content: str, *, finish: str = "stop", reasoning: str = "") -> dict:
    message: dict = {"content": content}
    if reasoning:
        message["reasoning_content"] = reasoning
    return {"choices": [{"message": message, "finish_reason": finish}], "model": "deepseek-flash"}


# ── the provider is selected, never inferred ─────────────────────────────────


def test_deepseek_is_offered_as_its_own_provider():
    assert PROVIDER_DEEPSEEK in {p["id"] for p in PROVIDERS}
    assert base_url_for(PROVIDER_DEEPSEEK) == DEEPSEEK_DEFAULT_URL
    assert client_for(PROVIDER_DEEPSEEK).base_url == DEEPSEEK_DEFAULT_URL


def test_each_provider_resolves_its_own_key():
    """The two keys are unrelated; reading the wrong one reports a false green."""
    assert credential_for(PROVIDER_DEEPSEEK).value == "sk-test-deepseek"
    assert credential_for(PROVIDER_OPENCODE).value == "sk-test-opencode"


def test_a_missing_deepseek_key_is_reported_as_absent_not_as_the_other_one(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        "forge_api.credentials.Path.is_file", lambda self: False
    )  # ignore the developer's own .env
    assert not resolve_deepseek_credential().present
    assert DeepSeekClient().status()["error"] == "DEEPSEEK_KEY_MISSING"


def test_an_unknown_provider_id_falls_back_rather_than_raising():
    """A settings file naming a removed provider must still load."""
    assert client_for("omniroute").base_url != DEEPSEEK_DEFAULT_URL
    assert base_url_for("omniroute") == base_url_for(PROVIDER_OPENCODE)


# ── the ids do not agree across providers ────────────────────────────────────


def test_the_gateway_serves_deepseek_ids_the_account_does_not():
    """The trap this module exists to document.

    The catalogues overlap without matching: `deepseek-flash` and
    `deepseek-v4-pro` are served by both, while `deepseek-v4-flash` — the id the
    gateway's own default routing uses — does not exist on the account at all.
    So an id being valid says nothing about the *selected* provider serving it,
    and sending it anyway returns a 400 that blames the model, not the routing.
    """
    account = {m["id"] for m in catalog_for(PROVIDER_DEEPSEEK)}
    gateway = {m["id"] for m in catalog_for(PROVIDER_OPENCODE)}
    assert "deepseek-v4-flash" in gateway and "deepseek-v4-flash" not in account
    assert {"deepseek-flash", "deepseek-v4-pro"} <= (account & gateway)


def test_a_model_the_selected_provider_cannot_serve_is_repaired():
    """Routing is per role, not per provider. Switching to DeepSeek leaves roles
    pointing at Kimi, GLM and MiMo — all real, none of them servable here."""
    for stale in ("kimi-k2.7-code", "glm-5.3", "mimo-v2.5", "deepseek-v4-flash"):
        assert model_for(PROVIDER_DEEPSEEK, stale) == "deepseek-flash"


def test_a_model_this_provider_does_serve_is_passed_through_untouched():
    assert model_for(PROVIDER_DEEPSEEK, "deepseek-v4-pro") == "deepseek-v4-pro"
    assert model_for(PROVIDER_DEEPSEEK, "deepseek-flash") == "deepseek-flash"


def test_repair_does_not_reach_across_to_the_other_provider():
    """The gateway serves everything it is routed; only DeepSeek narrows."""
    assert model_for(PROVIDER_OPENCODE, "kimi-k2.7-code") == "kimi-k2.7-code"


def test_a_stale_auto_choice_resolves_per_provider():
    assert model_for(PROVIDER_DEEPSEEK, "auto/smart") == default_model(PROVIDER_DEEPSEEK)
    assert default_model(PROVIDER_DEEPSEEK) != default_model(PROVIDER_OPENCODE)


def test_the_default_model_is_one_that_actually_exists():
    assert default_model(PROVIDER_DEEPSEEK) in {m["id"] for m in catalog_for(PROVIDER_DEEPSEEK)}


def test_every_catalogued_model_was_verified_working():
    """Nothing aspirational: both answered a live completion on probe."""
    catalogue = catalog_for(PROVIDER_DEEPSEEK)
    assert len(catalogue) == len(DEEPSEEK_MODELS) == 2
    assert all("Verified" in model["note"] for model in catalogue)


# ── the wire ─────────────────────────────────────────────────────────────────


def test_the_account_api_is_openai_shaped_with_a_bearer_token():
    """One endpoint and one auth header — the gateway's three-shape table has no
    equivalent here, and inventing one would be the bug."""
    transport, seen = _capture(200, _answer("ok"))
    DeepSeekClient(transport=transport).chat(model="deepseek-flash", system="s", prompt="p")
    request = seen[0]
    assert request.url.path.endswith("/chat/completions")
    assert request.headers["authorization"] == "Bearer sk-test-deepseek"
    assert "x-api-key" not in request.headers


def test_a_completion_reports_tokens_and_its_route():
    body = _answer("ok") | {"usage": {"prompt_tokens": 11, "completion_tokens": 3}}
    transport, _ = _capture(200, body)
    result = DeepSeekClient(transport=transport).chat(
        model="deepseek-flash", system="s", prompt="p"
    )
    assert result["answer"] == "ok"
    assert (result["input_tokens"], result["output_tokens"]) == (11, 3)
    assert result["route"] == "deepseek"
    assert result["grounded"] is True
    assert result["truncated"] is False
    # Read by every caller that shares the gateway's contract.
    assert result["used_standby_key"] is False


def test_a_think_block_is_not_part_of_the_answer():
    transport, _ = _capture(200, _answer("<think>weighing it up</think>the answer"))
    result = DeepSeekClient(transport=transport).chat(
        model="deepseek-v4-pro", system="s", prompt="p"
    )
    assert result["answer"] == "the answer"


def test_a_finished_reply_may_fall_back_to_reasoning_content():
    """Some replies genuinely arrive only in reasoning_content."""
    transport, _ = _capture(200, _answer("", reasoning="the answer"))
    result = DeepSeekClient(transport=transport).chat(
        model="deepseek-v4-pro", system="s", prompt="p"
    )
    assert result["answer"] == "the answer"


def test_a_truncated_reasoning_reply_is_refused_not_presented_as_an_answer():
    """V4 Pro reasons before it answers. Cut off mid-thought it has an empty
    content and a full reasoning_content, and returning that hands the operator
    the model's private working marked grounded."""
    transport, _ = _capture(200, _answer("", finish="length", reasoning="The user asks abo"))
    with pytest.raises(DeepSeekError) as caught:
        DeepSeekClient(transport=transport).chat(
            model="deepseek-v4-pro", system="s", prompt="p", max_tokens=32
        )
    assert "DEEPSEEK_TRUNCATED" in str(caught.value)


def test_a_partial_answer_is_returned_but_flagged():
    """Truncated with something to show is still useful — but never silently."""
    transport, _ = _capture(200, _answer("half a sent", finish="length"))
    result = DeepSeekClient(transport=transport).chat(
        model="deepseek-v4-pro", system="s", prompt="p"
    )
    assert result["truncated"] is True
    assert result["answer"] == "half a sent"


def test_an_empty_balance_says_so_rather_than_blaming_the_key():
    """402 is the pay-as-you-go failure a subscription key never produces."""
    transport, _ = _capture(402, {"error": {"message": "Insufficient Balance"}})
    with pytest.raises(DeepSeekError) as caught:
        DeepSeekClient(transport=transport).chat(model="deepseek-flash", system="s", prompt="p")
    assert "DEEPSEEK_NO_BALANCE" in str(caught.value)
    assert caught.value.status_code == 402


def test_a_refused_key_is_named_as_such():
    transport, _ = _capture(401, {"error": {"message": "Authentication Fails"}})
    with pytest.raises(DeepSeekError) as caught:
        DeepSeekClient(transport=transport).chat(model="deepseek-flash", system="s", prompt="p")
    assert "DEEPSEEK_UNAUTHORISED" in str(caught.value)


def test_a_failure_raises_rather_than_returning_a_partial_result():
    """Callers read input_tokens off the result; a dict without it reports
    itself as the provider failing and hides the real cause."""
    transport, _ = _capture(500, {"error": {"message": "boom"}})
    with pytest.raises(DeepSeekError):
        DeepSeekClient(transport=transport).chat(model="deepseek-flash", system="s", prompt="p")
