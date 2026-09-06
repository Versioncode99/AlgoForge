from __future__ import annotations

import httpx
import pytest
from forge_api.model_gateway import OMNIROUTE_DEFAULT_URL, OmniRouteClient
from forge_api.nvidia_nim import NVIDIA_NIM_MODELS, NvidiaNimClient
from forge_api.providers import (
    PROVIDER_AUTO,
    PROVIDER_NVIDIA,
    PROVIDER_OMNIROUTE,
    PROVIDER_OPENCODE,
    PROVIDERS,
    catalog_for,
    client_for,
    model_for,
    resolve,
)


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _models_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})


def _dead(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


def test_every_advertised_provider_is_selectable():
    ids = {p["id"] for p in PROVIDERS}
    assert ids == {PROVIDER_AUTO, PROVIDER_OMNIROUTE, PROVIDER_NVIDIA, PROVIDER_OPENCODE}


def test_nim_client_ignores_the_omniroute_loopback_url():
    """base_url configures the local gateway only; NIM has its own endpoint."""
    client = client_for(PROVIDER_NVIDIA, OMNIROUTE_DEFAULT_URL)
    assert "127.0.0.1" not in client.base_url
    assert client.base_url.startswith("https://")


def test_auto_falls_back_to_nim_when_omniroute_is_down(monkeypatch):
    monkeypatch.setattr(
        "forge_api.providers.OmniRouteClient",
        lambda *a, **k: OmniRouteClient(OMNIROUTE_DEFAULT_URL, transport=_transport(_dead)),
    )
    monkeypatch.setattr(
        "forge_api.providers.NvidiaNimClient",
        lambda *a, **k: NvidiaNimClient(transport=_transport(_models_ok)),
    )
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "test-key")

    selection = resolve(PROVIDER_AUTO)
    assert selection.provider == PROVIDER_NVIDIA
    assert selection.fell_back is True


def test_auto_prefers_omniroute_when_it_is_up(monkeypatch):
    monkeypatch.setattr(
        "forge_api.providers.OmniRouteClient",
        lambda *a, **k: OmniRouteClient(OMNIROUTE_DEFAULT_URL, transport=_transport(_models_ok)),
    )
    selection = resolve(PROVIDER_AUTO)
    assert selection.provider == PROVIDER_OMNIROUTE
    assert selection.fell_back is False


def test_pinned_provider_is_never_swapped(monkeypatch):
    """An explicit choice must be honoured even when that provider is down."""
    monkeypatch.setattr(
        "forge_api.providers.OmniRouteClient",
        lambda *a, **k: OmniRouteClient(OMNIROUTE_DEFAULT_URL, transport=_transport(_dead)),
    )
    selection = resolve(PROVIDER_OMNIROUTE)
    assert selection.provider == PROVIDER_OMNIROUTE
    assert selection.fell_back is False
    assert selection.status["connected"] is False


@pytest.mark.parametrize("pseudo", ["auto", "auto/smart", "auto/coding", "auto/fast", "auto/cheap"])
def test_omniroute_pseudo_models_map_onto_real_nim_models(pseudo: str):
    """`auto/*` means nothing to NIM; sending it verbatim would 404."""
    mapped = model_for(PROVIDER_NVIDIA, pseudo)
    assert mapped in {m["id"] for m in NVIDIA_NIM_MODELS}
    assert not mapped.startswith("auto")


def test_pseudo_models_pass_through_unchanged_for_omniroute():
    assert model_for(PROVIDER_OMNIROUTE, "auto/smart") == "auto/smart"


def test_auto_catalogue_covers_both_providers():
    ids = {m["id"] for m in catalog_for(PROVIDER_AUTO)}
    assert "auto" in ids
    assert {m["id"] for m in NVIDIA_NIM_MODELS} <= ids


def test_nim_reports_missing_credential_without_calling_out(monkeypatch):
    monkeypatch.delenv("NVIDIA_NIM_API_KEY", raising=False)
    monkeypatch.setattr("forge_api.nvidia_nim.resolve_nvidia_credential", lambda: _NoKey())
    status = NvidiaNimClient().status()
    assert status["connected"] is False
    assert status["error"] == "NVIDIA_NIM_KEY_MISSING"
    assert status["latency_ms"] == 0


class _NoKey:
    value = ""
    source = "none"

    @property
    def present(self) -> bool:
        return False


def test_reasoning_preamble_is_stripped():
    assert NvidiaNimClient._clean("<think>scratch</think>\n\nThe answer.") == "The answer."
    assert NvidiaNimClient._clean("plain answer") == "plain answer"


def test_status_never_leaks_the_key(monkeypatch):
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "nvapi-supersecret")
    status = NvidiaNimClient(transport=_transport(_models_ok)).status()
    assert "nvapi-supersecret" not in str(status)
    assert status["credential_present"] is True
