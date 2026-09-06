"""OpenCode Zen: one hosted gateway in front of many model families.

Same OpenAI-compatible request shape as NVIDIA NIM, so the assistant and agent
roles do not care which provider answered.

Two things about this provider are unusual and both are load-bearing:

**Balance, not capability, is what gates most of it.** The catalogue advertises
70 models. On the key configured here, 64 of them return
``401 Insufficient balance`` — including every Chinese frontier model (DeepSeek,
GLM, Kimi, MiniMax, Qwen). They are not broken and they are not missing; the
account has no credit. So unlike the NIM catalogue, which drops anything that
does not answer, this one keeps them and marks them ``needs_credit``. A model
that will work the moment you top up is a different fact from a model that 404s
forever, and flattening the two would be the dishonest simplification.

**The second key is not extra capacity.** Both keys resolve to the same account
and share one quota pool — a model rate-limited on the first key is rate-limited
on the second. The standby key is worth having for a revoked or rotated primary,
not for doubling throughput, and the docstring says so because the opposite is
the natural assumption.

Credentials are resolved server-side and never cross the API boundary.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Literal

import httpx

from forge_api.model_gateway import CredentialMaterial

OPENCODE_ZEN_DEFAULT_URL = "https://opencode.ai/zen/v1"

# Nemotron leaks its scratchpad ahead of the answer on this endpoint, verbatim
# "Here's a thinking process:". Same treatment as NIM.
_REASONING_MARKERS = (
    "here's a thinking process",
    "here is a thinking process",
    "let me think through",
    "thinking process:",
)

ZenStatus = Literal["verified", "needs_credit", "unavailable"]

# Probed against /chat/completions on 2026-09-06 with the configured key.
#
# `status` means:
#   verified     — answered, with the measured latency in `note`
#   needs_credit — 401 Insufficient balance; will work once the account is funded
#   unavailable  — the upstream itself failed (400/500), independent of billing
#
# `endpoint` records which route the provider documents. The GPT-5.x family is
# documented on /responses rather than /chat/completions, so this client cannot
# drive it even with credit; that is recorded rather than discovered painfully
# later.
OPENCODE_ZEN_MODELS: tuple[dict[str, str], ...] = (
    # ── verified on the free tier ────────────────────────────────────────────
    {
        "id": "ling-3.0-flash-fin-free",
        "label": "Zen · Ling 3.0 Flash (free)",
        "tier": "fast",
        "origin": "china",
        "status": "verified",
        "endpoint": "chat",
        "note": "Verified ~1.1s. Fastest thing on this key. InclusionAI/Ant Group.",
    },
    {
        "id": "big-pickle",
        "label": "Zen · Big Pickle (free)",
        "tier": "fast",
        "origin": "other",
        "status": "verified",
        "endpoint": "chat",
        "note": "Verified ~3-9s. Undisclosed model; returned an empty completion on probe.",
    },
    {
        "id": "mimo-v2.5-free",
        "label": "Zen · MiMo v2.5 (free)",
        "tier": "fast",
        "origin": "china",
        "status": "verified",
        "endpoint": "chat",
        "note": "Verified ~4.9s but rate-limits quickly (429). Xiaomi MiMo.",
    },
    {
        "id": "nemotron-3.5-lightning-free",
        "label": "Zen · Nemotron 3.5 Lightning (free)",
        "tier": "economy",
        "origin": "us",
        "status": "verified",
        "endpoint": "chat",
        "note": "Verified ~27s. Leaks its reasoning preamble; stripped on the way out.",
    },
    {
        "id": "nemotron-3-ultra-free",
        "label": "Zen · Nemotron 3 Ultra (free)",
        "tier": "economy",
        "origin": "us",
        "status": "verified",
        "endpoint": "chat",
        "note": "Verified ~87s. Far too slow for chat; background work only.",
    },
    # ── Chinese frontier models, all blocked on billing ──────────────────────
    {
        "id": "deepseek-v4-pro",
        "label": "Zen · DeepSeek V4 Pro",
        "tier": "frontier",
        "origin": "china",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "DeepSeek. Strongest reasoning of the Chinese set.",
    },
    {
        "id": "deepseek-v4-flash",
        "label": "Zen · DeepSeek V4 Flash",
        "tier": "fast",
        "origin": "china",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "DeepSeek. Cheap and quick once funded.",
    },
    {
        "id": "deepseek-v4-flash-vision-exp",
        "label": "Zen · DeepSeek V4 Flash Vision (exp)",
        "tier": "fast",
        "origin": "china",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "DeepSeek, experimental vision variant.",
    },
    {
        "id": "deepseek-v4-flash-free",
        "label": "Zen · DeepSeek V4 Flash (free)",
        "tier": "fast",
        "origin": "china",
        "status": "unavailable",
        "endpoint": "chat",
        "note": "Free tier exists but the upstream returned 400 'Model is unavailable'.",
    },
    {
        "id": "glm-5.3",
        "label": "Zen · GLM 5.3",
        "tier": "frontier",
        "origin": "china",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "Zhipu AI. Newest GLM on the gateway.",
    },
    {
        "id": "glm-5.3-flash",
        "label": "Zen · GLM 5.3 Flash",
        "tier": "fast",
        "origin": "china",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "Zhipu AI, latency-tuned.",
    },
    {
        "id": "glm-5.2",
        "label": "Zen · GLM 5.2",
        "tier": "frontier",
        "origin": "china",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "Zhipu AI, previous generation.",
    },
    {
        "id": "glm-5.1",
        "label": "Zen · GLM 5.1",
        "tier": "frontier",
        "origin": "china",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "Zhipu AI.",
    },
    {
        "id": "glm-5",
        "label": "Zen · GLM 5",
        "tier": "frontier",
        "origin": "china",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "Zhipu AI.",
    },
    {
        "id": "kimi-k3",
        "label": "Zen · Kimi K3",
        "tier": "frontier",
        "origin": "china",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "Moonshot AI. Long-context specialist.",
    },
    {
        "id": "kimi-k2.7-code",
        "label": "Zen · Kimi K2.7 Code",
        "tier": "frontier",
        "origin": "china",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "Moonshot AI, code-tuned. Natural fit for strategy generation.",
    },
    {
        "id": "kimi-k2.6",
        "label": "Zen · Kimi K2.6",
        "tier": "frontier",
        "origin": "china",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "Moonshot AI.",
    },
    {
        "id": "kimi-k2.5",
        "label": "Zen · Kimi K2.5",
        "tier": "frontier",
        "origin": "china",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "Moonshot AI.",
    },
    {
        "id": "minimax-m3",
        "label": "Zen · MiniMax M3",
        "tier": "frontier",
        "origin": "china",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "MiniMax.",
    },
    {
        "id": "minimax-m2.7",
        "label": "Zen · MiniMax M2.7",
        "tier": "frontier",
        "origin": "china",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "MiniMax.",
    },
    {
        "id": "minimax-m2.5",
        "label": "Zen · MiniMax M2.5",
        "tier": "frontier",
        "origin": "china",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "MiniMax.",
    },
    {
        "id": "qwen3.6-plus",
        "label": "Zen · Qwen 3.6 Plus",
        "tier": "frontier",
        "origin": "china",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "Alibaba Qwen.",
    },
    {
        "id": "qwen3.5-plus",
        "label": "Zen · Qwen 3.5 Plus",
        "tier": "frontier",
        "origin": "china",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "Alibaba Qwen.",
    },
    # ── Western frontier models, also blocked on billing ─────────────────────
    {
        "id": "claude-opus-5",
        "label": "Zen · Claude Opus 5",
        "tier": "frontier",
        "origin": "us",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "Anthropic.",
    },
    {
        "id": "claude-sonnet-5",
        "label": "Zen · Claude Sonnet 5",
        "tier": "frontier",
        "origin": "us",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "Anthropic. Best balance of the Anthropic set.",
    },
    {
        "id": "claude-haiku-4-5",
        "label": "Zen · Claude Haiku 4.5",
        "tier": "fast",
        "origin": "us",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "Anthropic, latency-tuned.",
    },
    {
        "id": "gemini-3.8-flash",
        "label": "Zen · Gemini 3.8 Flash",
        "tier": "fast",
        "origin": "us",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "Google.",
    },
    {
        "id": "gemini-3.1-pro",
        "label": "Zen · Gemini 3.1 Pro",
        "tier": "frontier",
        "origin": "us",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "Google.",
    },
    {
        "id": "grok-4.6",
        "label": "Zen · Grok 4.6",
        "tier": "frontier",
        "origin": "us",
        "status": "needs_credit",
        "endpoint": "chat",
        "note": "xAI.",
    },
    {
        "id": "gpt-6-astra",
        "label": "Zen · GPT-6 Astra",
        "tier": "frontier",
        "origin": "us",
        "status": "needs_credit",
        "endpoint": "responses",
        "note": "OpenAI. Documented on /responses, which this client does not speak.",
    },
    {
        "id": "gpt-5.6-terra",
        "label": "Zen · GPT-5.6 Terra",
        "tier": "frontier",
        "origin": "us",
        "status": "needs_credit",
        "endpoint": "responses",
        "note": "OpenAI. Documented on /responses, which this client does not speak.",
    },
)

# Models this client can actually drive: chat-completions and not upstream-dead.
SELECTABLE_STATUSES: frozenset[str] = frozenset({"verified", "needs_credit"})


def resolve_opencode_credential() -> CredentialMaterial:
    """Primary key: environment first, then `.env`. Never logged or returned."""
    return _credential("OPENCODE_ZEN_API_KEY")


def resolve_opencode_standby() -> CredentialMaterial:
    """Optional second key, tried once when the primary is rejected.

    Same account, same quota pool — this buys resilience against a rotated or
    revoked key, not additional throughput.
    """
    return _credential("OPENCODE_ZEN_API_KEY_2")


def _credential(name: str) -> CredentialMaterial:
    direct = os.environ.get(name, "").strip()
    if direct:
        return CredentialMaterial(direct, "environment")

    env_file = Path(__file__).resolve().parents[3] / ".env"
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                if line.strip().startswith(f"{name}="):
                    value = line.partition("=")[2].strip().strip("\"'")
                    if value:
                        return CredentialMaterial(value, "env_file")
        except (OSError, UnicodeError):
            pass
    return CredentialMaterial("", "none")


class OpenCodeZenClient:
    def __init__(
        self,
        base_url: str = OPENCODE_ZEN_DEFAULT_URL,
        *,
        timeout_seconds: float = 8.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def _headers(self, key: str) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if key:
            headers["authorization"] = f"Bearer {key}"
        return headers

    def status(self) -> dict[str, Any]:
        credential = resolve_opencode_credential()
        started = time.perf_counter()
        if not credential.present:
            return {
                "provider": "opencode_zen",
                "connected": False,
                "status_code": None,
                "latency_ms": 0,
                "model_count": 0,
                "credential_present": False,
                "credential_source": credential.source,
                "standby_present": resolve_opencode_standby().present,
                "base_url": self.base_url,
                "error": "OPENCODE_ZEN_KEY_MISSING",
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
                "provider": "opencode_zen",
                "connected": True,
                "status_code": response.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "model_count": len(models) if isinstance(models, list) else 0,
                "credential_present": True,
                "credential_source": credential.source,
                "standby_present": resolve_opencode_standby().present,
                "base_url": self.base_url,
                "error": None,
            }
        except (httpx.HTTPError, ValueError):
            return {
                "provider": "opencode_zen",
                "connected": False,
                "status_code": None,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "model_count": 0,
                "credential_present": True,
                "credential_source": credential.source,
                "standby_present": resolve_opencode_standby().present,
                "base_url": self.base_url,
                "error": "OPENCODE_ZEN_UNAVAILABLE",
            }

    def model_catalog(self) -> list[dict[str, str]]:
        """Everything this client can drive, verified models first.

        Billing-blocked models stay in the list with their status, so Settings
        can grey them out and say why instead of pretending they do not exist.
        """
        selectable = [
            dict(item)
            for item in OPENCODE_ZEN_MODELS
            if item["status"] in SELECTABLE_STATUSES and item["endpoint"] == "chat"
        ]
        order = {"verified": 0, "needs_credit": 1}
        return sorted(selectable, key=lambda item: (order.get(item["status"], 2), item["label"]))

    @staticmethod
    def _clean(text: str) -> str:
        """Drop a leaked reasoning preamble and any <think> block."""
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
        lowered = cleaned.lower()
        if any(lowered.startswith(marker) for marker in _REASONING_MARKERS):
            _, separator, tail = cleaned.rpartition("\n\n")
            if separator and tail.strip():
                return tail.strip()
        return cleaned

    def chat(
        self, *, model: str, system: str, prompt: str, max_tokens: int = 900
    ) -> dict[str, Any]:
        """One completion, retrying once on the standby key if the primary is refused.

        Retrying on 401 and 429 only: those are the two failures a different key
        could plausibly fix. Retrying a 400 or a 500 on the second key would just
        spend the same quota to get the same answer.
        """
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        attempts = [resolve_opencode_credential()]
        standby = resolve_opencode_standby()
        if standby.present:
            attempts.append(standby)

        last: httpx.Response | None = None
        # Some verified models take well over a minute; the catalogue labels the
        # slow ones so the operator can avoid them for interactive work.
        with httpx.Client(timeout=180.0, transport=self.transport) as client:
            for index, credential in enumerate(attempts):
                if not credential.present:
                    continue
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(credential.value),
                    json=body,
                )
                last = response
                if response.status_code == 200:
                    return self._read(response, key_index=index)
                if response.status_code not in {401, 429}:
                    break

        return {
            "model": model,
            "answer": "",
            "error": self._describe(last),
            "status_code": None if last is None else last.status_code,
        }

    def _read(self, response: httpx.Response, *, key_index: int) -> dict[str, Any]:
        payload = response.json()
        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            return {
                "model": payload.get("model", ""),
                "answer": "",
                "error": "OPENCODE_ZEN_UNPARSEABLE",
                "status_code": response.status_code,
            }
        return {
            "model": payload.get("model", ""),
            "answer": self._clean(text),
            "error": None,
            "status_code": response.status_code,
            "used_standby_key": key_index > 0,
        }

    @staticmethod
    def _describe(response: httpx.Response | None) -> str:
        """Turn the gateway's failure into something an operator can act on."""
        if response is None:
            return "OPENCODE_ZEN_NO_CREDENTIAL"
        if response.status_code == 401:
            return (
                "OPENCODE_ZEN_INSUFFICIENT_BALANCE: this account has no credit, so only the "
                "free-tier models answer. Top up at https://opencode.ai to unlock the rest."
            )
        if response.status_code == 429:
            return "OPENCODE_ZEN_RATE_LIMITED: free-tier quota exhausted; both keys share it."
        try:
            message = str(response.json().get("error", {}).get("message", ""))[:160]
        except ValueError:
            message = response.text[:160]
        return f"OPENCODE_ZEN_HTTP_{response.status_code}: {message}".strip()
