"""NVIDIA NIM: hosted, OpenAI-compatible, usable when OmniRoute is not running.

Same request shape as the local gateway, so the assistant and agent roles do not
care which one answered. The key is read server-side from the environment or
`.env` and never crosses the API boundary.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from forge_api.model_gateway import CredentialMaterial

# Several NIM reasoning models emit their scratchpad before the answer. It is not
# useful to the operator and makes short answers unreadable, so it is stripped.
_REASONING_MARKERS = (
    "here's a thinking process",
    "here is a thinking process",
    "let me think through",
    "thinking process:",
)

NVIDIA_NIM_DEFAULT_URL = "https://integrate.api.nvidia.com/v1"

# A deliberately short list. The endpoint exposes 80+ models; these are the ones
# worth routing a role to, tiered the same way as the OmniRoute pseudo-models so
# the two catalogues read consistently in Settings.
# Verified by probing /chat/completions on 2026-09-02, not taken from /models:
# the endpoint advertises 80+ models but most return 404 for a given key. Only
# models that actually answered are listed, with their measured latency, because
# a dropdown full of models that 404 is worse than a short one that works.
NVIDIA_NIM_MODELS: tuple[dict[str, str], ...] = (
    {
        "id": "nvidia/nemotron-3-super-120b-a12b",
        "label": "NIM · Nemotron 3 Super 120B",
        "tier": "frontier",
        "note": "Verified ~3s. Strongest reasoning here; hypotheses and post-mortems.",
    },
    {
        "id": "openai/gpt-oss-120b",
        "label": "NIM · GPT-OSS 120B",
        "tier": "frontier",
        "note": "Verified ~3s. Open-weight frontier-class general model.",
    },
    {
        "id": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "label": "NIM · Nemotron 3.5 Lightning 30B",
        "tier": "fast",
        "note": "Verified ~1s. Fastest option; chat, triage and bulk work.",
    },
    {
        "id": "openai/gpt-oss-20b",
        "label": "NIM · GPT-OSS 20B",
        "tier": "economy",
        "note": "Verified but slow (~30s). Only for background work.",
    },
    {
        "id": "deepseek-ai/deepseek-v4-pro-0813",
        "label": "NIM · DeepSeek V4 Pro",
        "tier": "frontier",
        "note": "Verified but very slow (~150s). Not suitable for interactive chat.",
    },
)


def resolve_nvidia_credential() -> CredentialMaterial:
    """Environment first, then `.env`. The value is never logged or returned."""
    direct = os.environ.get("NVIDIA_NIM_API_KEY", "").strip()
    if direct:
        return CredentialMaterial(direct, "environment")

    env_file = Path(__file__).resolve().parents[3] / ".env"
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                if line.strip().startswith("NVIDIA_NIM_API_KEY="):
                    value = line.partition("=")[2].strip().strip("\"'")
                    if value:
                        return CredentialMaterial(value, "env_file")
        except (OSError, UnicodeError):
            pass
    return CredentialMaterial("", "none")


class NvidiaNimClient:
    def __init__(
        self,
        base_url: str = NVIDIA_NIM_DEFAULT_URL,
        *,
        timeout_seconds: float = 8.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        credential = resolve_nvidia_credential()
        headers = {"content-type": "application/json"}
        if credential.present:
            headers["authorization"] = f"Bearer {credential.value}"
        return headers

    def status(self) -> dict[str, Any]:
        credential = resolve_nvidia_credential()
        started = time.perf_counter()
        if not credential.present:
            return {
                "provider": "nvidia_nim",
                "connected": False,
                "status_code": None,
                "latency_ms": 0,
                "model_count": 0,
                "credential_present": False,
                "credential_source": credential.source,
                "base_url": self.base_url,
                "error": "NVIDIA_NIM_KEY_MISSING",
            }
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = client.get(f"{self.base_url}/models", headers=self._headers())
                response.raise_for_status()
                payload = response.json()
            models = payload.get("data", []) if isinstance(payload, dict) else []
            return {
                "provider": "nvidia_nim",
                "connected": True,
                "status_code": response.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "model_count": len(models) if isinstance(models, list) else 0,
                "credential_present": True,
                "credential_source": credential.source,
                "base_url": self.base_url,
                "error": None,
            }
        except (httpx.HTTPError, ValueError):
            return {
                "provider": "nvidia_nim",
                "connected": False,
                "status_code": None,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "model_count": 0,
                "credential_present": True,
                "credential_source": credential.source,
                "base_url": self.base_url,
                "error": "NVIDIA_NIM_UNAVAILABLE",
            }

    def model_catalog(self) -> list[dict[str, str]]:
        return [dict(item) for item in NVIDIA_NIM_MODELS]

    @staticmethod
    def _clean(text: str) -> str:
        """Drop a leaked reasoning preamble and any <think> block."""
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
        lowered = cleaned.lower()
        if any(lowered.startswith(marker) for marker in _REASONING_MARKERS):
            # Keep everything after the last blank line; the answer follows the
            # scratchpad. If there is no clear break, return the text unchanged
            # rather than silently truncating something that may be the answer.
            _, separator, tail = cleaned.rpartition("\n\n")
            if separator and tail.strip():
                return tail.strip()
        return cleaned

    def chat(
        self, *, model: str, system: str, prompt: str, max_tokens: int = 900
    ) -> dict[str, Any]:
        # Some verified models take well over a minute; the slow ones are
        # labelled in the catalogue so the operator can avoid them for chat.
        with httpx.Client(timeout=180.0, transport=self.transport) as client:
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.2,
                },
            )
            response.raise_for_status()
            payload = response.json()
        choices = payload.get("choices", [])
        if not choices:
            raise ValueError("NVIDIA_NIM_EMPTY_RESPONSE")
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("NVIDIA_NIM_EMPTY_RESPONSE")
        usage = payload.get("usage", {})
        return {
            "answer": self._clean(content),
            "model": payload.get("model", model),
            "grounded": True,
            "input_tokens": int(usage.get("prompt_tokens", 0)),
            "output_tokens": int(usage.get("completion_tokens", 0)),
            "route": "nvidia_nim",
        }
