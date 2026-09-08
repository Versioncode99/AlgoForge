"""OpenCode Go.

Three things here cost real time to discover and would be silently lost in a
refactor, so each has a test:

1. Go and Zen are different products on different base URLs.
2. Go's models split across three request shapes with *different auth* — a
   Bearer token on the Anthropic-shaped route returns "Missing API key", which
   blames the credential for a protocol mistake.
3. One model rejects every temperature but 1.0, as a bare upstream 400.
"""

from __future__ import annotations

import httpx
import pytest
from forge_api.opencode import (
    OPENCODE_GO_DEFAULT_URL,
    OPENCODE_GO_MODELS,
    OPENCODE_GO_NOT_SERVED,
    OPENCODE_ZEN_DEFAULT_URL,
    OpenCodeError,
    OpenCodeGoClient,
    resolve_opencode_credential,
    resolve_opencode_standby,
    shape_for,
    temperature_for,
)
from forge_api.providers import (
    PROVIDER_OPENCODE,
    PROVIDERS,
    catalog_for,
    client_for,
    default_model,
    model_for,
)

KEYS = ("OPENCODE_API_KEY", "OPENCODE_API_KEY_2")


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "sk-test-primary")
    monkeypatch.delenv("OPENCODE_API_KEY_2", raising=False)


def _capture(status: int = 200, body: dict | None = None):
    """Record every outgoing request so auth and routing can be asserted."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body if body is not None else {})

    return httpx.MockTransport(handler), seen


def test_go_is_a_different_endpoint_from_zen():
    """A Go key on the Zen URL fails with a balance error that means nothing."""
    assert OPENCODE_GO_DEFAULT_URL == "https://opencode.ai/zen/go/v1"
    assert OPENCODE_ZEN_DEFAULT_URL == "https://opencode.ai/zen/v1"
    assert client_for(PROVIDER_OPENCODE).base_url == OPENCODE_GO_DEFAULT_URL


def test_go_is_the_only_provider():
    """OmniRoute needed a local process that never ran; NIM offered three usable
    models behind a key that 404s for most of its catalogue. Both were removed."""
    assert [p["id"] for p in PROVIDERS] == [PROVIDER_OPENCODE]


def test_a_stale_auto_routing_choice_does_not_become_a_model_name():
    """Settings saved before the collapse still hold `auto/*` pseudo-models."""
    assert model_for(PROVIDER_OPENCODE, "auto") == default_model()
    assert model_for(PROVIDER_OPENCODE, "auto/smart") == default_model()
    assert model_for(PROVIDER_OPENCODE, "none") == default_model()
    assert model_for(PROVIDER_OPENCODE, "") == default_model()
    assert model_for(PROVIDER_OPENCODE, "glm-5.3") == "glm-5.3"


def test_the_default_model_is_one_that_actually_exists():
    assert default_model() in {m["id"] for m in catalog_for()}


def test_every_catalogued_model_was_verified_working():
    """Unlike Zen, nothing here is aspirational; all 28 answered on probe."""
    catalogue = catalog_for(PROVIDER_OPENCODE)
    assert len(catalogue) == 28
    assert all("Verified" in model["note"] for model in catalogue)


def test_models_that_are_advertised_but_refused_are_never_offered():
    offered = {model["id"] for model in catalog_for(PROVIDER_OPENCODE)}
    assert OPENCODE_GO_NOT_SERVED
    assert not (offered & set(OPENCODE_GO_NOT_SERVED))


def test_the_chinese_families_are_all_present():
    """The whole point of the Go subscription."""
    ids = " ".join(model["id"] for model in catalog_for(PROVIDER_OPENCODE))
    for family in ("deepseek", "glm", "kimi", "minimax", "qwen", "mimo", "longcat", "hy"):
        assert family in ids, f"{family} missing from the Go catalogue"


# ── the three request shapes ─────────────────────────────────────────────────


def test_chat_models_use_a_bearer_token_on_chat_completions():
    transport, seen = _capture(200, {"choices": [{"message": {"content": "ok"}}]})
    OpenCodeGoClient(transport=transport).chat(model="glm-5.3", system="s", prompt="p")
    request = seen[0]
    assert request.url.path.endswith("/chat/completions")
    assert request.headers["authorization"] == "Bearer sk-test-primary"
    assert "x-api-key" not in request.headers


def test_anthropic_shaped_models_use_x_api_key_not_bearer():
    """A Bearer token here returns 'Missing API key' — a protocol fault that
    reads as a credential fault. This is the assertion that stops it recurring."""
    transport, seen = _capture(200, {"content": [{"type": "text", "text": "ok"}]})
    OpenCodeGoClient(transport=transport).chat(model="qwen3.8-max", system="s", prompt="p")
    request = seen[0]
    assert request.url.path.endswith("/messages")
    assert request.headers["x-api-key"] == "sk-test-primary"
    assert request.headers["anthropic-version"]
    assert "authorization" not in request.headers


def test_responses_models_use_the_responses_route():
    transport, seen = _capture(200, {"output": [{"type": "message", "content": [{"text": "ok"}]}]})
    result = OpenCodeGoClient(transport=transport).chat(model="grok-4.6", system="s", prompt="p")
    assert seen[0].url.path.endswith("/responses")
    assert result["answer"] == "ok"


@pytest.mark.parametrize(
    ("model", "shape"),
    [("glm-5.3", "chat"), ("minimax-m3", "messages"), ("gpt-5.6-luna", "responses")],
)
def test_shape_lookup_matches_the_catalogue(model: str, shape: str):
    assert shape_for(model) == shape


def test_unknown_models_fall_back_to_chat():
    assert shape_for("something-new") == "chat"


# ── per-model quirks ─────────────────────────────────────────────────────────


def test_kimi_code_gets_the_only_temperature_it_accepts():
    """It rejects anything else as a bare upstream 400 that looks like an outage."""
    assert temperature_for("kimi-k2.7-code") == 1.0
    assert temperature_for("glm-5.3") == 0.2

    transport, seen = _capture(200, {"choices": [{"message": {"content": "ok"}}]})
    OpenCodeGoClient(transport=transport).chat(model="kimi-k2.7-code", system="s", prompt="p")
    import json

    assert json.loads(seen[0].content)["temperature"] == 1.0


def test_longcat_answer_is_read_from_reasoning_content():
    """LongCat returns its text under a non-standard key."""
    transport, _ = _capture(200, {"choices": [{"message": {"reasoning_content": "Tokyo"}}]})
    result = OpenCodeGoClient(transport=transport).chat(model="longcat-2.0", system="s", prompt="p")
    assert result["answer"] == "Tokyo"


def test_responses_reasoning_items_are_not_mistaken_for_the_answer():
    payload = {
        "output": [
            {"type": "reasoning", "content": [{"text": "thinking out loud"}]},
            {"type": "message", "content": [{"text": "Tokyo"}]},
        ]
    }
    transport, _ = _capture(200, payload)
    result = OpenCodeGoClient(transport=transport).chat(
        model="gpt-5.6-luna", system="s", prompt="p"
    )
    assert result["answer"] == "Tokyo"


# ── required headers and error reporting ─────────────────────────────────────


def test_every_request_identifies_itself_and_carries_a_session():
    """The terms ask for both; broad user agents get flagged as abusive."""
    transport, seen = _capture(200, {"choices": [{"message": {"content": "ok"}}]})
    client = OpenCodeGoClient(transport=transport)
    client.chat(model="glm-5.3", system="s", prompt="p")
    headers = seen[0].headers
    assert "AlgoForge" in headers["user-agent"]
    assert headers["x-opencode-session"] == client.session_id


def test_a_session_id_is_stable_across_calls():
    transport, seen = _capture(200, {"choices": [{"message": {"content": "ok"}}]})
    client = OpenCodeGoClient(transport=transport)
    client.chat(model="glm-5.3", system="s", prompt="p")
    client.chat(model="glm-5.2", system="s", prompt="p")
    assert seen[0].headers["x-opencode-session"] == seen[1].headers["x-opencode-session"]


def test_a_401_points_at_the_endpoint_not_just_the_key():
    transport, _ = _capture(401, {"error": {"message": "nope"}})
    with pytest.raises(OpenCodeError) as caught:
        OpenCodeGoClient(transport=transport).chat(model="glm-5.3", system="s", prompt="p")
    assert "UNAUTHORISED" in str(caught.value)
    assert "/zen/go/v1" in str(caught.value)


def test_a_429_explains_the_subscription_allowance():
    transport, _ = _capture(429, {})
    with pytest.raises(OpenCodeError) as caught:
        OpenCodeGoClient(transport=transport).chat(model="glm-5.3", system="s", prompt="p")
    assert "LIMIT_REACHED" in str(caught.value)
    assert "Use balance" in str(caught.value)


def test_a_403_names_the_consent_requirement():
    transport, _ = _capture(403, {})
    with pytest.raises(OpenCodeError) as caught:
        OpenCodeGoClient(transport=transport).chat(model="glm-5.3", system="s", prompt="p")
    assert "CONSENT_REQUIRED" in str(caught.value)


def test_only_auth_and_quota_failures_retry_on_the_standby(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY_2", "sk-standby")
    transport, seen = _capture(500, {"error": {"message": "boom"}})
    with pytest.raises(OpenCodeError):
        OpenCodeGoClient(transport=transport).chat(model="glm-5.3", system="s", prompt="p")
    assert len(seen) == 1


def test_a_rejected_primary_retries_once_on_the_standby(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY_2", "sk-standby")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers["authorization"].removeprefix("Bearer ")
        seen.append(token)
        if token == "sk-test-primary":
            return httpx.Response(401, json={})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    result = OpenCodeGoClient(transport=httpx.MockTransport(handler)).chat(
        model="glm-5.3", system="s", prompt="p"
    )
    assert seen == ["sk-test-primary", "sk-standby"]
    assert result["used_standby_key"] is True


def test_reasoning_preamble_is_stripped():
    assert OpenCodeGoClient._clean("Here's a thinking process:\n1. Hm.\n\nTokyo") == "Tokyo"
    assert OpenCodeGoClient._clean("<think>noise</think>Tokyo") == "Tokyo"


def test_status_never_leaks_the_key():
    transport, _ = _capture(200, {"data": [{"id": "glm-5.3"}]})
    status = OpenCodeGoClient(transport=transport).status()
    assert "sk-test-primary" not in repr(status)
    assert status["credential_present"] is True


def test_status_reports_a_missing_key_without_calling_out(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("forge_api.opencode.resolve_opencode_credential", _absent)
    monkeypatch.setattr("forge_api.opencode.resolve_opencode_standby", _absent)

    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not call out without a credential")

    status = OpenCodeGoClient(transport=httpx.MockTransport(explode)).status()
    assert status["connected"] is False
    assert status["error"] == "OPENCODE_KEY_MISSING"


def test_credentials_resolve_from_the_environment(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "sk-one")
    monkeypatch.setenv("OPENCODE_API_KEY_2", "sk-two")
    assert resolve_opencode_credential().value == "sk-one"
    assert resolve_opencode_standby().value == "sk-two"


def test_catalogue_entries_are_internally_consistent():
    for entry in OPENCODE_GO_MODELS:
        assert entry["shape"] in {"chat", "messages", "responses"}
        assert entry["tier"] in {"frontier", "fast", "economy"}
        assert entry["origin"] in {"china", "us", "other"}
        assert entry["label"].startswith("Go · ")


def _absent():
    from forge_api.credentials import CredentialMaterial

    return CredentialMaterial("", "none")


def test_success_reports_token_usage_like_every_other_provider():
    """The assistant reads input_tokens off the result; omitting it produced a
    KeyError that reported itself as the provider failing."""
    transport, _ = _capture(
        200,
        {
            "model": "glm-5.3",
            "choices": [{"message": {"content": "Tokyo"}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 3},
        },
    )
    result = OpenCodeGoClient(transport=transport).chat(model="glm-5.3", system="s", prompt="p")
    assert result["input_tokens"] == 11
    assert result["output_tokens"] == 3
    assert result["grounded"] is True
    assert result["route"] == "opencode_go"


def test_anthropic_usage_uses_the_other_spelling():
    transport, _ = _capture(
        200,
        {
            "content": [{"type": "text", "text": "Tokyo"}],
            "usage": {"input_tokens": 7, "output_tokens": 2},
        },
    )
    result = OpenCodeGoClient(transport=transport).chat(model="qwen3.8-max", system="s", prompt="p")
    assert (result["input_tokens"], result["output_tokens"]) == (7, 2)


# ── truncation ───────────────────────────────────────────────────────────────
# A reasoning model cut off by the token budget has an empty `content` and a
# full `reasoning_content`. Falling back to the second one there hands the
# operator the model's private working as though it were the reply. Measured
# against the live gateway on glm-5.3 at max_tokens=32: the "answer" to "what
# is 2+2" came back as 'The user asks "What is 2+2?" and the sys'.


def test_a_truncated_reasoning_model_does_not_pass_its_thinking_off_as_an_answer():
    transport, _ = _capture(
        200,
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": "The user asks what 2+2 is. I should",
                    },
                    "finish_reason": "length",
                }
            ]
        },
    )
    with pytest.raises(OpenCodeError, match="OPENCODE_TRUNCATED"):
        OpenCodeGoClient(transport=transport).chat(
            model="glm-5.3", system="s", prompt="p", max_tokens=32
        )


def test_longcat_still_gets_its_answer_from_reasoning_content_when_it_finished():
    """The fallback has to survive; it is only the truncated case that is wrong."""
    transport, _ = _capture(
        200,
        {"choices": [{"message": {"reasoning_content": "Tokyo"}, "finish_reason": "stop"}]},
    )
    result = OpenCodeGoClient(transport=transport).chat(
        model="longcat-2.0", system="s", prompt="p"
    )
    assert result["answer"] == "Tokyo"
    assert result["truncated"] is False


def test_a_truncated_answer_is_returned_but_flagged():
    """It may still be useful. It must never be silent."""
    transport, _ = _capture(
        200,
        {
            "choices": [
                {"message": {"content": "The first half of a sen"}, "finish_reason": "length"}
            ]
        },
    )
    result = OpenCodeGoClient(transport=transport).chat(model="glm-5.3", system="s", prompt="p")
    assert result["answer"] == "The first half of a sen"
    assert result["truncated"] is True


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        ("qwen3.8-max", {"content": [{"type": "text", "text": "ok"}], "stop_reason": "max_tokens"}),
        (
            "grok-4.6",
            {
                "output": [{"type": "message", "content": [{"text": "ok"}]}],
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
            },
        ),
    ],
)
def test_every_shape_reports_its_own_truncation(model: str, payload: dict):
    """Each route spells it differently, and reading only one spelling would
    make the other two silently look complete."""
    transport, _ = _capture(200, payload)
    result = OpenCodeGoClient(transport=transport).chat(model=model, system="s", prompt="p")
    assert result["truncated"] is True
