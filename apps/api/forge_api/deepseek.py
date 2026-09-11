"""DeepSeek, called directly rather than through a reseller.

The subscription gateway already carries DeepSeek models, so the reason to hold
an account key as well is not access — it is independence. When the Go
allowance is spent the gateway answers ``429`` for every model at once, and
until the window rolls there is nothing behind it. A direct key is metered
per-token against its own balance, so it is still there in that hour.

**The model ids are not the gateway's ids, and the overlap is the trap.** The
account serves ``deepseek-flash`` where Go spells the same family
``deepseek-v4-flash``, and serves ``deepseek-v4-pro`` under a name Go also
uses. So one id means the same model by two routes and the other does not exist
on this side at all — which is why routing is validated against the selected
provider's catalogue rather than a merged one, and why switching provider
repairs a model this provider cannot serve instead of sending it and reading
back a 400 that blames the model.

The API is OpenAI-shaped: one endpoint, one auth header, no per-model request
shape to get wrong. That is the whole of the difference from ``opencode``.

Credentials are resolved server-side and never cross the API boundary.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from forge_api.credentials import CredentialMaterial, resolve_credential
from forge_api.model_text import clean_answer

DEEPSEEK_DEFAULT_URL = "https://api.deepseek.com"

USER_AGENT = (
    "AlgoForge/0.1 (quant research workstation; https://github.com/Versioncode99/AlgoForge)"
)


class DeepSeekError(RuntimeError):
    """A refusal from the API, carrying the status that caused it."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# Probed against the account key on 2026-09-11: `GET /models` advertises exactly
# these two, and both answered a live completion. Latency is a single cold
# measurement, useful for ranking rather than as a promise.
#
# No `shape` key here, unlike the gateway's table — everything DeepSeek serves
# directly speaks /chat/completions, so there is nothing to dispatch on.
DEEPSEEK_MODELS: tuple[dict[str, Any], ...] = (
    {
        "id": "deepseek-v4-pro",
        "label": "DeepSeek · V4 Pro (direct)",
        "tier": "frontier",
        "origin": "china",
        "note": (
            "Verified ~2.7s. Reasoning model: it spends tokens thinking before it "
            "answers, so give it a wider max_tokens than its reply looks like it needs."
        ),
    },
    {
        "id": "deepseek-flash",
        "label": "DeepSeek · Flash (direct)",
        "tier": "fast",
        "origin": "china",
        "note": (
            "Verified ~0.8s. The account's cheap workhorse. Note the id: the gateway "
            "spells this family deepseek-v4-flash, which this provider will not serve."
        ),
    },
)


def resolve_deepseek_credential() -> CredentialMaterial:
    """Account key: environment first, then `.env`. Never logged or returned."""
    return resolve_credential("DEEPSEEK_API_KEY")


def default_deepseek_model() -> str:
    """Fast and cheap, for when a routed model is unset or belongs to the gateway."""
    return "deepseek-flash"


def serves(model: str) -> bool:
    return any(entry["id"] == model for entry in DEEPSEEK_MODELS)


class DeepSeekClient:
    def __init__(
        self,
        base_url: str = DEEPSEEK_DEFAULT_URL,
        *,
        timeout_seconds: float = 8.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def _headers(self, key: str) -> dict[str, str]:
        headers = {"content-type": "application/json", "user-agent": USER_AGENT}
        if key:
            headers["authorization"] = f"Bearer {key}"
        return headers

    def status(self) -> dict[str, Any]:
        credential = resolve_deepseek_credential()
        started = time.perf_counter()
        base = {
            "provider": "deepseek",
            "base_url": self.base_url,
            "credential_source": credential.source,
            # No second key for this provider. Reported so the interface can read
            # one shape for every provider rather than special-casing the field.
            "standby_present": False,
        }
        if not credential.present:
            return {
                **base,
                "connected": False,
                "status_code": None,
                "latency_ms": 0,
                "model_count": 0,
                "credential_present": False,
                "error": "DEEPSEEK_KEY_MISSING",
            }
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = client.get(
                    f"{self.base_url}/models", headers=self._headers(credential.value)
                )
                response.raise_for_status()
                payload = response.json()
            models = payload.get("data", []) if isinstance(payload, dict) else []
            return {
                **base,
                "connected": True,
                "status_code": response.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "model_count": len(models) if isinstance(models, list) else 0,
                "credential_present": True,
                "error": None,
            }
        except (httpx.HTTPError, ValueError):
            return {
                **base,
                "connected": False,
                "status_code": None,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "model_count": 0,
                "credential_present": True,
                "error": "DEEPSEEK_UNAVAILABLE",
            }

    def model_catalog(self) -> list[dict[str, str]]:
        """Verified models only, frontier first, matching the gateway's ordering."""
        order = {"frontier": 0, "fast": 1, "economy": 2}
        return [
            {k: str(v) for k, v in entry.items()}
            for entry in sorted(
                DEEPSEEK_MODELS,
                key=lambda e: (order.get(str(e["tier"]), 3), str(e["label"])),
            )
        ]

    def chat(
        self, *, model: str, system: str, prompt: str, max_tokens: int = 900
    ) -> dict[str, Any]:
        """One completion. Returns the shape every caller already reads."""
        credential = resolve_deepseek_credential()
        if not credential.present:
            raise DeepSeekError("DEEPSEEK_NO_CREDENTIAL", None)

        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        with httpx.Client(timeout=180.0, transport=self.transport) as client:
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(credential.value),
                json=body,
            )
        if response.status_code != 200:
            raise DeepSeekError(self._describe(response), response.status_code)

        payload = response.json()
        truncated = self._truncated(payload)
        answer = clean_answer(self._extract(payload, truncated=truncated))
        if truncated and not answer:
            # The budget ran out before the model said anything. V4 Pro reasons
            # first, so this is the ordinary failure when max_tokens is set from
            # the length of the expected reply rather than the thinking ahead of
            # it. An empty string here would render as a blank reply.
            raise DeepSeekError(
                f"DEEPSEEK_TRUNCATED: {model} used its entire {max_tokens}-token "
                "budget without producing an answer. Raise max_tokens, or choose "
                "a model that reasons less.",
                200,
            )
        usage = payload.get("usage") or {}
        usage = usage if isinstance(usage, dict) else {}
        return {
            "model": payload.get("model", model),
            "answer": answer,
            "error": None,
            "status_code": 200,
            "shape": "chat",
            "grounded": True,
            "route": "deepseek",
            "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "output_tokens": int(usage.get("completion_tokens", 0) or 0),
            # Contract parity with the gateway, which retries on a standby key.
            "used_standby_key": False,
            "truncated": truncated,
        }

    @staticmethod
    def _truncated(payload: Any) -> bool:
        try:
            return str(payload["choices"][0].get("finish_reason") or "") == "length"
        except (KeyError, IndexError, TypeError, AttributeError):
            return False

    @staticmethod
    def _extract(payload: Any, *, truncated: bool = False) -> str:
        """The reply, falling back to reasoning only when the model finished.

        Same narrowing as the gateway, for the same reason: a reasoning model cut
        off by the token budget has an empty `content` and a full
        `reasoning_content`, and taking it then presents the model's private
        working as the answer, marked grounded, with no sign it was cut off.
        """
        try:
            message = payload["choices"][0]["message"]
            content = message.get("content") or ""
            if content:
                return str(content)
            return "" if truncated else str(message.get("reasoning_content") or "")
        except (KeyError, IndexError, TypeError, AttributeError):
            return ""

    @staticmethod
    def _describe(response: httpx.Response) -> str:
        """Turn the API's failure into something an operator can act on."""
        if response.status_code == 401:
            return "DEEPSEEK_UNAUTHORISED: the key was refused. Check DEEPSEEK_API_KEY."
        if response.status_code == 402:
            return (
                "DEEPSEEK_NO_BALANCE: the account is out of credit. This provider is "
                "pay-as-you-go — top up the balance in the DeepSeek console."
            )
        if response.status_code == 429:
            return "DEEPSEEK_RATE_LIMITED: too many requests. Retry after a pause."
        try:
            message = str(response.json().get("error", {}).get("message", ""))[:160]
        except ValueError:
            message = response.text[:160]
        return f"DEEPSEEK_HTTP_{response.status_code}: {message}".strip()
