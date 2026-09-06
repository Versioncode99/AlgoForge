"""The model provider.

One is wired: **OpenCode Go**, a $10/month subscription over curated open
coding models — DeepSeek, GLM, Kimi, MiniMax, Qwen, MiMo, LongCat and Hunyuan,
plus GPT-5.6 Luna and Grok 4.6.

OmniRoute and NVIDIA NIM were removed. OmniRoute needed a local gateway process
that was never running, and NIM's catalogue was three usable models behind a
key that 404s for most of what it advertises. Keeping either meant maintaining
a routing layer, a fallback chain and a pseudo-model translation table to reach
worse models than the subscription already provides.

There is consequently no `auto` any more. With one provider, a chooser that
picks between providers is a menu with a single item, and the fallback logic it
existed to drive has nothing to fall back to.

Credentials are resolved server-side and never returned across the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forge_api.credentials import CredentialMaterial
from forge_api.opencode import (
    OPENCODE_GO_DEFAULT_URL,
    OpenCodeGoClient,
    resolve_opencode_credential,
)

PROVIDER_OPENCODE = "opencode_go"
DEFAULT_PROVIDER = PROVIDER_OPENCODE

PROVIDERS: tuple[dict[str, str], ...] = (
    {
        "id": PROVIDER_OPENCODE,
        "label": "OpenCode Go (subscription)",
        "detail": (
            "DeepSeek, GLM, Kimi, MiniMax, Qwen, MiMo, LongCat and Hunyuan on a flat "
            "$10/month allowance. 28 models verified working."
        ),
    },
)


@dataclass(frozen=True)
class Resolved:
    """Which provider answered. Kept as a shape so callers did not all change."""

    provider: str
    client: Any
    status: dict[str, Any]
    fell_back: bool = False


def credential_for(provider: str = PROVIDER_OPENCODE) -> CredentialMaterial:
    return resolve_opencode_credential()


def client_for(provider: str = PROVIDER_OPENCODE, base_url: str | None = None) -> Any:
    return OpenCodeGoClient(base_url or OPENCODE_GO_DEFAULT_URL)


def catalog_for(provider: str = PROVIDER_OPENCODE) -> list[dict[str, str]]:
    return OpenCodeGoClient().model_catalog()


def status_for(provider: str = PROVIDER_OPENCODE, base_url: str | None = None) -> dict[str, Any]:
    return dict(client_for(provider, base_url).status())


def resolve(provider: str = PROVIDER_OPENCODE, base_url: str | None = None) -> Resolved:
    client = client_for(provider, base_url)
    return Resolved(PROVIDER_OPENCODE, client, client.status())


def model_for(provider: str, requested: str) -> str:
    """Model ids are passed through.

    This used to translate OmniRoute's `auto/*` pseudo-models into concrete NIM
    ids. With one provider there is nothing to translate, but an unset or stale
    routing choice must not be sent verbatim as a model name.
    """
    if not requested or requested in {"none", "auto"} or requested.startswith("auto/"):
        return default_model()
    return requested


def default_model() -> str:
    """A fast, cheap model with a large monthly allowance."""
    return "deepseek-v4-flash"
