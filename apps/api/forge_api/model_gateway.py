"""Server-only model routing through OmniRoute's OpenAI-compatible API."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

OMNIROUTE_DEFAULT_URL = "http://127.0.0.1:20128/v1"
OMNIROUTE_FALLBACK_MODELS: tuple[dict[str, str], ...] = (
    {
        "id": "auto",
        "label": "OmniRoute Auto",
        "tier": "balanced",
        "note": "Live quota, health, cost and task-aware routing.",
    },
    {
        "id": "auto/coding",
        "label": "OmniRoute Coding",
        "tier": "coding",
        "note": "Coding-specialist auto route.",
    },
    {
        "id": "auto/smart",
        "label": "OmniRoute Smart",
        "tier": "frontier",
        "note": "Higher-reasoning route for hypotheses and post-mortems.",
    },
    {
        "id": "auto/fast",
        "label": "OmniRoute Fast",
        "tier": "fast",
        "note": "Latency-first route for interactive work.",
    },
    {
        "id": "auto/cheap",
        "label": "OmniRoute Cheap",
        "tier": "economy",
        "note": "Cost-first route for bulk classification and summaries.",
    },
    {
        "id": "auto/offline",
        "label": "OmniRoute Offline",
        "tier": "local",
        "note": "Prefer local providers when configured in OmniRoute.",
    },
    {
        "id": "auto/lkgp",
        "label": "OmniRoute Last Known Good",
        "tier": "resilient",
        "note": "Pin the last healthy provider, then fall back.",
    },
    {
        "id": "none",
        "label": "Disabled",
        "tier": "off",
        "note": "This role performs no model work.",
    },
)

_KEY_LABEL = re.compile(
    r"^(?:omniroute[ _-]*)?(?:endpoint[ _-]*)?(?:api[ _-]*)?(?:key|token)\s*[:=]\s*(.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CredentialMaterial:
    value: str
    source: str

    @property
    def present(self) -> bool:
        return bool(self.value)


def _key_from_file(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return ""
    for line in lines:
        stripped = line.strip()
        if not stripped or "password" in stripped.lower():
            continue
        match = _KEY_LABEL.match(stripped)
        if match:
            return match.group(1).strip().strip("\"'")
    return ""


def resolve_omniroute_credential() -> CredentialMaterial:
    direct = os.environ.get("OMNIROUTE_API_KEY", "").strip()
    if direct:
        return CredentialMaterial(direct, "environment")

    configured = os.environ.get("OMNIROUTE_KEY_FILE", "").strip()
    candidates = [Path(configured)] if configured else []
    candidates.append(Path.home() / "Desktop" / "Main" / "omni route key.txt")
    for path in candidates:
        value = _key_from_file(path)
        if value:
            return CredentialMaterial(value, "external_file")
    return CredentialMaterial("", "none")


class OmniRouteClient:
    def __init__(
        self,
        base_url: str = OMNIROUTE_DEFAULT_URL,
        *,
        timeout_seconds: float = 3.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        credential = resolve_omniroute_credential()
        headers = {"content-type": "application/json"}
        if credential.present:
            headers["authorization"] = f"Bearer {credential.value}"
        return headers

    def status(self) -> dict[str, Any]:
        credential = resolve_omniroute_credential()
        started = time.perf_counter()
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.get(f"{self.base_url}/models", headers=self._headers())
                response.raise_for_status()
                payload = response.json()
            models = payload.get("data", []) if isinstance(payload, dict) else []
            return {
                "provider": "omniroute",
                "connected": True,
                "status_code": response.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "model_count": len(models) if isinstance(models, list) else 0,
                "credential_present": credential.present,
                "credential_source": credential.source,
                "base_url": self.base_url,
                "error": None,
            }
        except (httpx.HTTPError, ValueError):
            return {
                "provider": "omniroute",
                "connected": False,
                "status_code": None,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "model_count": 0,
                "credential_present": credential.present,
                "credential_source": credential.source,
                "base_url": self.base_url,
                "error": "OMNIROUTE_UNAVAILABLE",
            }

    def model_catalog(self) -> list[dict[str, str]]:
        by_id = {item["id"]: dict(item) for item in OMNIROUTE_FALLBACK_MODELS}
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.get(f"{self.base_url}/models", headers=self._headers())
                response.raise_for_status()
                payload = response.json()
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                        continue
                    model_id = row["id"]
                    by_id.setdefault(
                        model_id,
                        {
                            "id": model_id,
                            "label": model_id,
                            "tier": "live",
                            "note": "Discovered from the local OmniRoute catalog.",
                        },
                    )
        except (httpx.HTTPError, ValueError):
            pass
        return list(by_id.values())

    def chat(
        self, *, model: str, system: str, prompt: str, max_tokens: int = 900
    ) -> dict[str, Any]:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
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
            raise ValueError("OMNIROUTE_EMPTY_RESPONSE")
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("OMNIROUTE_EMPTY_RESPONSE")
        usage = payload.get("usage", {})
        return {
            "answer": content,
            "model": payload.get("model", model),
            "grounded": True,
            "input_tokens": int(usage.get("prompt_tokens", 0)),
            "output_tokens": int(usage.get("completion_tokens", 0)),
            "route": response.headers.get("x-omniroute-decision"),
        }
