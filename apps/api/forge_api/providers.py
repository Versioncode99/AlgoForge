"""Model providers behind one OpenAI-compatible interface.

Three are wired:

- **OmniRoute** — a local gateway that does its own quota, health and cost-aware
  routing. Preferred when it is running, because nothing leaves the machine.
- **NVIDIA NIM** — a hosted OpenAI-compatible endpoint. Works without any local
  process, so it is the natural backup when OmniRoute is not listening.
- **OpenCode Zen** — a hosted gateway over many families at once, including the
  Chinese frontier models (DeepSeek, GLM, Kimi, MiniMax, Qwen). Most of its
  catalogue is gated on account balance rather than capability.

`auto` tries OmniRoute first and falls back to NIM. Any of the three can be
pinned explicitly from Settings, so the choice is the operator's rather than
implied. Zen is not in the automatic chain on purpose: with an unfunded account
only its free tier answers, and silently routing a research role onto a
free-tier model would be a quiet downgrade rather than a decision.

Credentials are resolved server-side and never returned across the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forge_api.model_gateway import (
    OMNIROUTE_DEFAULT_URL,
    OMNIROUTE_FALLBACK_MODELS,
    CredentialMaterial,
    OmniRouteClient,
    resolve_omniroute_credential,
)
from forge_api.nvidia_nim import (
    NVIDIA_NIM_DEFAULT_URL,
    NVIDIA_NIM_MODELS,
    NvidiaNimClient,
    resolve_nvidia_credential,
)
from forge_api.opencode_zen import (
    OPENCODE_ZEN_DEFAULT_URL,
    OpenCodeZenClient,
    resolve_opencode_credential,
)

PROVIDER_OMNIROUTE = "omniroute"
PROVIDER_NVIDIA = "nvidia_nim"
PROVIDER_OPENCODE = "opencode_zen"
PROVIDER_AUTO = "auto"

PROVIDERS: tuple[dict[str, str], ...] = (
    {
        "id": PROVIDER_AUTO,
        "label": "Auto · OmniRoute, then NVIDIA NIM",
        "detail": "Prefer the local gateway; fall back to the hosted endpoint if it is down.",
    },
    {
        "id": PROVIDER_OMNIROUTE,
        "label": "OmniRoute (local gateway)",
        "detail": "Nothing leaves this machine. Requires OmniRoute to be running.",
    },
    {
        "id": PROVIDER_NVIDIA,
        "label": "NVIDIA NIM (hosted)",
        "detail": "Hosted OpenAI-compatible endpoint. Needs NVIDIA_NIM_API_KEY.",
    },
    {
        "id": PROVIDER_OPENCODE,
        "label": "OpenCode Zen (hosted)",
        "detail": (
            "DeepSeek, GLM, Kimi, MiniMax, Qwen, Claude, Gemini and Grok behind one key. "
            "Most models need account credit; the free tier answers without it."
        ),
    },
)


@dataclass(frozen=True)
class Resolved:
    """Which provider actually answered, and why."""

    provider: str
    client: Any
    status: dict[str, Any]
    fell_back: bool


def credential_for(provider: str) -> CredentialMaterial:
    if provider == PROVIDER_NVIDIA:
        return resolve_nvidia_credential()
    if provider == PROVIDER_OPENCODE:
        return resolve_opencode_credential()
    return resolve_omniroute_credential()


def client_for(provider: str, base_url: str | None = None) -> Any:
    # `base_url` configures the OmniRoute gateway only. NIM has a fixed hosted
    # endpoint; passing the loopback URL to it would probe the wrong host.
    if provider == PROVIDER_NVIDIA:
        return NvidiaNimClient(NVIDIA_NIM_DEFAULT_URL)
    if provider == PROVIDER_OPENCODE:
        return OpenCodeZenClient(OPENCODE_ZEN_DEFAULT_URL)
    return OmniRouteClient(base_url or OMNIROUTE_DEFAULT_URL)


def catalog_for(provider: str) -> list[dict[str, str]]:
    """Models offered for a provider. Includes both sets under `auto`."""
    if provider == PROVIDER_NVIDIA:
        return [dict(m) for m in NVIDIA_NIM_MODELS]
    if provider == PROVIDER_OPENCODE:
        # Ask the client rather than the constant: it filters out the models
        # this code path cannot drive (the /responses family and the
        # upstream-dead ones) and puts the verified ones first.
        return OpenCodeZenClient().model_catalog()
    if provider == PROVIDER_OMNIROUTE:
        return [dict(m) for m in OMNIROUTE_FALLBACK_MODELS]
    merged = {m["id"]: dict(m) for m in OMNIROUTE_FALLBACK_MODELS}
    for model in NVIDIA_NIM_MODELS:
        merged.setdefault(model["id"], dict(model))
    # Zen is deliberately absent from `auto`: it is not in the fallback chain,
    # so offering its models here would let one be selected and then routed
    # somewhere else entirely.
    return list(merged.values())


def status_for(provider: str, base_url: str | None = None) -> dict[str, Any]:
    """Probe one provider, or both when `auto`."""
    if provider in {PROVIDER_OMNIROUTE, PROVIDER_NVIDIA, PROVIDER_OPENCODE}:
        return dict(client_for(provider, base_url).status())

    omni = OmniRouteClient(base_url or OMNIROUTE_DEFAULT_URL).status()
    if omni["connected"]:
        return {**omni, "selected": PROVIDER_OMNIROUTE, "fell_back": False}
    nim = NvidiaNimClient().status()
    return {
        **nim,
        "selected": PROVIDER_NVIDIA if nim["connected"] else PROVIDER_OMNIROUTE,
        "fell_back": nim["connected"],
        "note": (
            "OmniRoute is not listening; using NVIDIA NIM."
            if nim["connected"]
            else "Neither provider is reachable. Answers fall back to the local ledger."
        ),
    }


def resolve(provider: str, base_url: str | None = None) -> Resolved:
    """Pick a usable client, honouring an explicit pin and falling back under `auto`."""
    if provider == PROVIDER_OMNIROUTE:
        omni_only = OmniRouteClient(base_url or OMNIROUTE_DEFAULT_URL)
        return Resolved(PROVIDER_OMNIROUTE, omni_only, omni_only.status(), False)
    if provider == PROVIDER_NVIDIA:
        nim_only = NvidiaNimClient()
        return Resolved(PROVIDER_NVIDIA, nim_only, nim_only.status(), False)
    if provider == PROVIDER_OPENCODE:
        zen_only = OpenCodeZenClient()
        return Resolved(PROVIDER_OPENCODE, zen_only, zen_only.status(), False)

    omni = OmniRouteClient(base_url or OMNIROUTE_DEFAULT_URL)
    omni_status = omni.status()
    if omni_status["connected"]:
        return Resolved(PROVIDER_OMNIROUTE, omni, omni_status, False)

    nim = NvidiaNimClient()
    return Resolved(PROVIDER_NVIDIA, nim, nim.status(), True)


def model_for(provider: str, requested: str) -> str:
    """Translate a routing choice to something the chosen provider understands.

    OmniRoute's `auto/*` pseudo-models mean nothing to NIM, so when the request
    falls through to the hosted endpoint they are mapped onto a concrete model of
    matching intent rather than sent verbatim and rejected.
    """
    if provider != PROVIDER_NVIDIA or not requested.startswith("auto"):
        return requested
    # Only verified-working NIM models appear here; the obvious literal
    # translations (codestral for coding, nano for offline) all 404 on this key.
    return {
        "auto": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "auto/smart": "nvidia/nemotron-3-super-120b-a12b",
        "auto/coding": "openai/gpt-oss-120b",
        "auto/fast": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "auto/cheap": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "auto/offline": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "auto/lkgp": "openai/gpt-oss-120b",
    }.get(requested, "nvidia/nemotron-3.5-lightning-30b-a3b")
