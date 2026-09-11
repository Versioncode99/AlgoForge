"""The model providers.

Two are wired:

* **OpenCode Go** — a $10/month subscription over curated open coding models:
  DeepSeek, GLM, Kimi, MiniMax, Qwen, MiMo, LongCat and Hunyuan, plus GPT-5.6
  Luna and Grok 4.6.
* **DeepSeek** — an account key called directly, pay-as-you-go over two models.

OmniRoute and NIM were removed and have not come back. OmniRoute needed a local
gateway process that was never running, and NIM's catalogue was three usable
models behind a key that 404s for most of what it advertises.

This is deliberately not a return to `auto`. There is no chooser and no
fallback chain: the operator selects a provider in settings and every call goes
there. Automatic failover across providers is what cost the routing layer, the
fallback chain and the pseudo-model translation table last time, and the two
providers do not even agree on model ids — `deepseek-flash` exists on one side
and `deepseek-v4-flash` on the other. Silently rerouting would mean silently
substituting the model, which is the failure the ids were made explicit to
prevent.

Credentials are resolved server-side and never returned across the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from forge_api.credentials import CredentialMaterial
from forge_api.deepseek import (
    DEEPSEEK_DEFAULT_URL,
    DeepSeekClient,
    default_deepseek_model,
    resolve_deepseek_credential,
)
from forge_api.deepseek import (
    serves as deepseek_serves,
)
from forge_api.opencode import (
    OPENCODE_GO_DEFAULT_URL,
    OpenCodeGoClient,
    resolve_opencode_credential,
)

PROVIDER_OPENCODE = "opencode_go"
PROVIDER_DEEPSEEK = "deepseek"
DEFAULT_PROVIDER = PROVIDER_OPENCODE

PROVIDERS: tuple[dict[str, str], ...] = (
    {
        "id": PROVIDER_OPENCODE,
        "label": "OpenCode Go (subscription)",
        "detail": (
            "DeepSeek, GLM, Kimi, MiniMax, Qwen, MiMo, LongCat and Hunyuan on a flat "
            "$10/month allowance. 30 models verified working."
        ),
    },
    {
        "id": PROVIDER_DEEPSEEK,
        "label": "DeepSeek (direct)",
        "detail": (
            "An account key straight to DeepSeek: V4 Pro and Flash, billed per token "
            "against its own balance. Independent of the subscription's allowance."
        ),
    },
)

#: Each provider's fixed endpoint. Neither is operator-editable — the base URL
#: was only ever a knob for the local OmniRoute gateway, and pointing a key at
#: the wrong host is how the Go/Zen billing trap happens.
BASE_URLS: dict[str, str] = {
    PROVIDER_OPENCODE: OPENCODE_GO_DEFAULT_URL,
    PROVIDER_DEEPSEEK: DEEPSEEK_DEFAULT_URL,
}


def known(provider: str) -> str:
    """The provider id, or the default if it is unset or no longer exists."""
    return provider if provider in BASE_URLS else DEFAULT_PROVIDER


def base_url_for(provider: str) -> str:
    return BASE_URLS[known(provider)]


@dataclass(frozen=True)
class Resolved:
    """Which provider answered. Kept as a shape so callers did not all change."""

    provider: str
    client: Any
    status: dict[str, Any]
    fell_back: bool = False


def credential_for(provider: str = DEFAULT_PROVIDER) -> CredentialMaterial:
    if known(provider) == PROVIDER_DEEPSEEK:
        return resolve_deepseek_credential()
    return resolve_opencode_credential()


def client_for(provider: str = DEFAULT_PROVIDER, base_url: str | None = None) -> Any:
    chosen = known(provider)
    if chosen == PROVIDER_DEEPSEEK:
        return DeepSeekClient(base_url or DEEPSEEK_DEFAULT_URL)
    return OpenCodeGoClient(base_url or OPENCODE_GO_DEFAULT_URL)


def catalog_for(provider: str = DEFAULT_PROVIDER) -> list[dict[str, str]]:
    return list(client_for(provider).model_catalog())


def status_for(provider: str = DEFAULT_PROVIDER, base_url: str | None = None) -> dict[str, Any]:
    return dict(client_for(provider, base_url).status())


def resolve(provider: str = DEFAULT_PROVIDER, base_url: str | None = None) -> Resolved:
    chosen = known(provider)
    client = client_for(chosen, base_url)
    return Resolved(chosen, client, client.status())


def model_for(provider: str, requested: str) -> str:
    """The model to send, given which provider is about to be called.

    Two things are repaired here, both of which otherwise surface as an upstream
    400 that blames the model rather than the routing:

    * an unset or stale choice — `auto/*` survives in settings files written
      before the provider collapse, and sending it verbatim asks for a model
      named "auto/smart";
    * a model the *selected* provider does not serve. Routing is stored per
      role, not per provider, so switching to DeepSeek leaves eight roles
      pointing at Kimi, GLM and MiMo. Those are real models on the other
      provider, so nothing rejected them at save time.
    """
    chosen = known(provider)
    if not requested or requested in {"none", "auto"} or requested.startswith("auto/"):
        return default_model(chosen)
    if chosen == PROVIDER_DEEPSEEK and not deepseek_serves(requested):
        return default_deepseek_model()
    return requested


def default_model(provider: str = DEFAULT_PROVIDER) -> str:
    """A fast, cheap model with a large monthly allowance."""
    if known(provider) == PROVIDER_DEEPSEEK:
        return default_deepseek_model()
    return "deepseek-v4-flash"
