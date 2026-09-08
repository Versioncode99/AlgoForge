"""OpenCode Go: a $10/month subscription over curated open coding models.

**Go is not Zen, and confusing the two wastes a lot of time.** They are separate
products on separate base URLs:

* Zen — ``https://opencode.ai/zen/v1`` — pay-as-you-go against an account
  balance. With no balance, everything but the free tier answers ``401
  Insufficient balance``.
* Go — ``https://opencode.ai/zen/go/v1`` — a flat subscription with usage
  limits denominated in dollars ($12 per 5 hours, $30 per week, $60 per month).

A Go key pointed at the Zen URL fails with a billing error that looks exactly
like an unfunded account, which is the trap this module exists to avoid.

**Go speaks three different request shapes, with different authentication**, and
the docs' endpoint table is the only place that says which model uses which:

===============  ==========================  ================================
Shape            Auth                        Families
===============  ==========================  ================================
``/chat/completions``  ``Authorization: Bearer``   GLM, Kimi, LongCat, DeepSeek,
                                                   MiMo, Hy, Omen
``/messages``          ``x-api-key`` +             MiniMax, Qwen
                       ``anthropic-version``
``/responses``         ``Authorization: Bearer``   Grok, GPT, Muse Spark
===============  ==========================  ================================

Sending a Bearer token to ``/messages`` returns ``401 Missing API key``, which
reads like a credential problem and is actually a protocol one.

Every request carries a specific user agent and an ``x-opencode-session``
header. The provider's terms ask for both — broad user agents get flagged as
abusive traffic, and the session header is what lets them cache prompts.

Credentials are resolved server-side and never cross the API boundary.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Literal

import httpx

from forge_api.credentials import CredentialMaterial

OPENCODE_GO_DEFAULT_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_ZEN_DEFAULT_URL = "https://opencode.ai/zen/v1"

# The terms ask that clients identify themselves specifically rather than with a
# broad user agent, and send a session id so prompts can be cached.
USER_AGENT = (
    "AlgoForge/0.1 (quant research workstation; https://github.com/Versioncode99/AlgoForge)"
)
ANTHROPIC_VERSION = "2023-06-01"

_REASONING_MARKERS = (
    "here's a thinking process",
    "here is a thinking process",
    "let me think through",
    "thinking process:",
)

Shape = Literal["chat", "messages", "responses"]


class OpenCodeError(RuntimeError):
    """A refusal from the gateway, carrying the status that caused it."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# Probed against the Go endpoint on 2026-09-06. Latency is a single cold
# measurement, useful for ranking rather than as a promise.
#
# `shape` is load-bearing: it decides both the URL and the auth header, and
# getting it wrong produces an error that blames the credential.
OPENCODE_GO_MODELS: tuple[dict[str, Any], ...] = (
    # ── GLM · Zhipu AI ───────────────────────────────────────────────────────
    {
        "id": "glm-5.3",
        "label": "Go · GLM 5.3",
        "tier": "frontier",
        "origin": "china",
        "shape": "chat",
        "note": "Verified ~1.1s. Zhipu AI flagship. ~1,080 requests/month.",
    },
    {
        "id": "glm-5.3-flash",
        "label": "Go · GLM 5.3 Flash",
        "tier": "fast",
        "origin": "china",
        "shape": "chat",
        "note": "Verified ~5.5s. Cheapest GLM; ~7,900 requests/month.",
    },
    {
        "id": "glm-5.2",
        "label": "Go · GLM 5.2",
        "tier": "frontier",
        "origin": "china",
        "shape": "chat",
        "note": "Verified ~2.5s. ~4,300 requests/month.",
    },
    {
        "id": "glm-5.1",
        "label": "Go · GLM 5.1",
        "tier": "frontier",
        "origin": "china",
        "shape": "chat",
        "note": "Verified ~1.0s. ~4,300 requests/month.",
    },
    {
        "id": "glm-5",
        "label": "Go · GLM 5",
        "tier": "frontier",
        "origin": "china",
        "shape": "chat",
        "note": "Verified ~1.5s. Served but absent from the published table.",
    },
    # ── Kimi · Moonshot AI ───────────────────────────────────────────────────
    {
        "id": "kimi-k2.7-code",
        "label": "Go · Kimi K2.7 Code",
        "tier": "frontier",
        "origin": "china",
        "shape": "chat",
        # Rejects any temperature but 1 with an invalid_request_error, which
        # surfaces as a bare upstream 400 and looks like the model is down.
        "temperature": 1.0,
        "note": "Verified ~1.3s. Code-tuned; the natural fit for strategy generation.",
    },
    {
        "id": "kimi-k3",
        "label": "Go · Kimi K3",
        "tier": "frontier",
        "origin": "china",
        "shape": "chat",
        "note": "Verified ~1.3s. Strongest Kimi, but only ~490 requests/month.",
    },
    {
        "id": "kimi-k2.6",
        "label": "Go · Kimi K2.6",
        "tier": "frontier",
        "origin": "china",
        "shape": "chat",
        "note": "Verified ~2.0s. ~5,750 requests/month.",
    },
    # ── DeepSeek ─────────────────────────────────────────────────────────────
    {
        "id": "deepseek-v4-pro",
        "label": "Go · DeepSeek V4 Pro",
        "tier": "frontier",
        "origin": "china",
        "shape": "chat",
        "note": (
            "Verified ~2.0s. Strongest reasoning here. "
            "Peak pricing 01:00-04:00 and 06:00-10:00 UTC."
        ),
    },
    {
        "id": "deepseek-v4-flash",
        "label": "Go · DeepSeek V4 Flash",
        "tier": "fast",
        "origin": "china",
        "shape": "chat",
        "note": "Verified ~1.2s. ~37,800 requests/month; the workhorse of this set.",
    },
    {
        "id": "deepseek-v4-flash-vision-exp",
        "label": "Go · DeepSeek V4 Flash Vision (exp)",
        "tier": "fast",
        "origin": "china",
        "shape": "chat",
        "note": "Verified ~11.0s. Experimental vision variant; images bill as input tokens.",
    },
    # ── MiMo · Xiaomi ────────────────────────────────────────────────────────
    {
        "id": "mimo-v2.5",
        "label": "Go · MiMo V2.5",
        "tier": "economy",
        "origin": "china",
        "shape": "chat",
        "note": "Verified ~10.4s. Cheapest model on Go; ~150,400 requests/month.",
    },
    {
        "id": "mimo-v2.5-pro",
        "label": "Go · MiMo V2.5 Pro",
        "tier": "fast",
        "origin": "china",
        "shape": "chat",
        "note": "Verified ~1.8s. ~16,300 requests/month.",
    },
    # ── Other open models ────────────────────────────────────────────────────
    {
        "id": "longcat-2.0",
        "label": "Go · LongCat 2.0",
        "tier": "fast",
        "origin": "china",
        "shape": "chat",
        "note": "Verified ~2.7s but returns a non-standard body; treat output as unreliable.",
    },
    {
        "id": "hy4-preview",
        "label": "Go · Hy4 (preview)",
        "tier": "frontier",
        "origin": "china",
        "shape": "chat",
        "note": "Verified ~3.7s. Tencent Hunyuan preview.",
    },
    {
        "id": "hy3",
        "label": "Go · Hy3",
        "tier": "fast",
        "origin": "china",
        "shape": "chat",
        "note": "Verified ~1.7s. ~21,500 requests/month.",
    },
    {
        "id": "omen-alpha",
        "label": "Go · Omen Alpha",
        "tier": "fast",
        "origin": "other",
        "shape": "chat",
        "note": "Verified ~2.0s. Undisclosed model; ~57,900 requests/month.",
    },
    # ── MiniMax · Anthropic-shaped ───────────────────────────────────────────
    {
        "id": "minimax-m3",
        "label": "Go · MiniMax M3",
        "tier": "frontier",
        "origin": "china",
        "shape": "messages",
        "note": "Verified ~1.2s. ~16,000 requests/month.",
    },
    {
        "id": "minimax-m2.7",
        "label": "Go · MiniMax M2.7",
        "tier": "frontier",
        "origin": "china",
        "shape": "messages",
        "note": "Verified ~1.5s. ~17,000 requests/month.",
    },
    {
        "id": "minimax-m2.5",
        "label": "Go · MiniMax M2.5",
        "tier": "frontier",
        "origin": "china",
        "shape": "messages",
        "note": "Verified ~1.5s.",
    },
    # ── Qwen · Alibaba · Anthropic-shaped ────────────────────────────────────
    {
        "id": "qwen3.8-max",
        "label": "Go · Qwen 3.8 Max",
        "tier": "frontier",
        "origin": "china",
        "shape": "messages",
        "note": "Verified ~2.4s. Best Qwen, but only ~810 requests/month.",
    },
    {
        "id": "qwen3.8-flash",
        "label": "Go · Qwen 3.8 Flash",
        "tier": "fast",
        "origin": "china",
        "shape": "messages",
        "note": "Verified ~2.6s. ~27,000 requests/month.",
    },
    {
        "id": "qwen3.7-max",
        "label": "Go · Qwen 3.7 Max",
        "tier": "frontier",
        "origin": "china",
        "shape": "messages",
        "note": "Verified ~4.7s. ~840 requests/month.",
    },
    {
        "id": "qwen3.7-plus",
        "label": "Go · Qwen 3.7 Plus",
        "tier": "frontier",
        "origin": "china",
        "shape": "messages",
        "note": "Verified ~4.4s. ~21,600 requests/month.",
    },
    {
        "id": "qwen3.6-plus",
        "label": "Go · Qwen 3.6 Plus",
        "tier": "frontier",
        "origin": "china",
        "shape": "messages",
        "note": "Verified ~10.3s. ~16,300 requests/month.",
    },
    {
        "id": "qwen3.5-plus",
        "label": "Go · Qwen 3.5 Plus",
        "tier": "fast",
        "origin": "china",
        "shape": "messages",
        "note": "Verified ~6.5s. Served but absent from the published table.",
    },
    # ── Western models on Go ─────────────────────────────────────────────────
    {
        "id": "gpt-5.6-luna",
        "label": "Go · GPT-5.6 Luna",
        "tier": "frontier",
        "origin": "us",
        "shape": "responses",
        "note": "Verified ~1.6s. ~10,250 requests/month.",
    },
    {
        "id": "grok-4.6",
        "label": "Go · Grok 4.6",
        "tier": "frontier",
        "origin": "us",
        "shape": "responses",
        "note": "Verified ~3.4s. Only ~845 requests/month.",
    },
)

# Advertised by /models but refused when called, so never offered. Kept as a
# record: rediscovering these by hitting a 400 in the middle of a run is worse
# than reading one line here.
OPENCODE_GO_NOT_SERVED: tuple[str, ...] = (
    "kimi-k2.5",
    "mimo-v2-pro",
    "mimo-v2-omni",
    "hy3-preview",
    # Both Muse Spark tiers return 403: they train on submitted prompts and need
    # an explicit opt-in in the OpenCode console before the gateway will route.
    "muse-spark-1.3-contributor",
    "muse-spark-1.2-contributor",
)


def resolve_opencode_credential() -> CredentialMaterial:
    """Primary key: environment first, then `.env`. Never logged or returned."""
    return _credential("OPENCODE_API_KEY")


def resolve_opencode_standby() -> CredentialMaterial:
    """Optional second key, tried once when the primary is refused."""
    return _credential("OPENCODE_API_KEY_2")


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


def temperature_for(model: str) -> float:
    """Sampling temperature this model will accept.

    Most take the house default. Kimi K2.7 Code rejects anything but 1.0, and
    does so as a plain upstream 400 that reads like the model being unavailable
    rather than the request being wrong.
    """
    for entry in OPENCODE_GO_MODELS:
        if entry["id"] == model and "temperature" in entry:
            return float(entry["temperature"])
    return 0.2


def shape_for(model: str) -> Shape:
    """Which request shape a model speaks. Defaults to chat for unknown ids."""
    for entry in OPENCODE_GO_MODELS:
        if entry["id"] == model:
            shape = str(entry["shape"])
            if shape in ("chat", "messages", "responses"):
                return shape  # type: ignore[return-value]
    return "chat"


class OpenCodeGoClient:
    def __init__(
        self,
        base_url: str = OPENCODE_GO_DEFAULT_URL,
        *,
        timeout_seconds: float = 8.0,
        transport: httpx.BaseTransport | None = None,
        session_id: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        # One session id per client, so a run's prompts cache together.
        self.session_id = session_id or f"algoforge-{uuid.uuid4().hex[:16]}"

    def _headers(self, key: str, shape: Shape) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            "user-agent": USER_AGENT,
            "x-opencode-session": self.session_id,
        }
        if not key:
            return headers
        # The Anthropic-shaped route rejects a Bearer token with "Missing API
        # key", which reads like a credential fault and is a protocol one.
        if shape == "messages":
            headers["x-api-key"] = key
            headers["anthropic-version"] = ANTHROPIC_VERSION
        else:
            headers["authorization"] = f"Bearer {key}"
        return headers

    def status(self) -> dict[str, Any]:
        credential = resolve_opencode_credential()
        started = time.perf_counter()
        base = {
            "provider": "opencode_go",
            "base_url": self.base_url,
            "credential_source": credential.source,
            "standby_present": resolve_opencode_standby().present,
        }
        if not credential.present:
            return {
                **base,
                "connected": False,
                "status_code": None,
                "latency_ms": 0,
                "model_count": 0,
                "credential_present": False,
                "error": "OPENCODE_KEY_MISSING",
            }
        try:
            with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = client.get(
                    f"{self.base_url}/models", headers=self._headers(credential.value, "chat")
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
                "error": "OPENCODE_UNAVAILABLE",
            }

    def model_catalog(self) -> list[dict[str, str]]:
        """Verified models only, cheap-and-fast first within each tier."""
        order = {"frontier": 0, "fast": 1, "economy": 2}
        return [
            {k: str(v) for k, v in entry.items()}
            for entry in sorted(
                OPENCODE_GO_MODELS,
                key=lambda e: (order.get(str(e["tier"]), 3), str(e["label"])),
            )
        ]

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
        """One completion, dispatched to whichever shape the model speaks."""
        shape = shape_for(model)
        url, body = self._request(shape, model, system, prompt, max_tokens)

        attempts = [resolve_opencode_credential()]
        standby = resolve_opencode_standby()
        if standby.present:
            attempts.append(standby)

        last: httpx.Response | None = None
        with httpx.Client(timeout=180.0, transport=self.transport) as client:
            for index, credential in enumerate(attempts):
                if not credential.present:
                    continue
                response = client.post(
                    url, headers=self._headers(credential.value, shape), json=body
                )
                last = response
                if response.status_code == 200:
                    payload = response.json()
                    used_in, used_out = self._usage(shape, payload)
                    truncated = self._truncated(shape, payload)
                    answer = self._clean(self._extract(shape, payload, truncated=truncated))
                    if truncated and not answer:
                        # The budget ran out before the model said anything. The
                        # honest report is a refusal naming the cause, not an
                        # empty string a caller will render as a blank reply.
                        raise OpenCodeError(
                            f"OPENCODE_TRUNCATED: {model} used its entire "
                            f"{max_tokens}-token budget without producing an answer. "
                            "Raise max_tokens, or choose a model that reasons less.",
                            200,
                        )
                    return {
                        "model": payload.get("model", model),
                        "answer": answer,
                        "error": None,
                        "status_code": 200,
                        "shape": shape,
                        "grounded": True,
                        "route": "opencode_go",
                        "input_tokens": used_in,
                        "output_tokens": used_out,
                        "used_standby_key": index > 0,
                        # Carried so a caller can tell a complete answer from one
                        # that stopped mid-sentence. A truncated answer is still
                        # returned — it may be useful — but never silently.
                        "truncated": truncated,
                    }
                # Only a rejected credential or an exhausted quota could be
                # fixed by a different key; anything else would repeat.
                if response.status_code not in {401, 429}:
                    break

        # Raise rather than return a partial dict. Callers share one contract
        # with the other providers and read `input_tokens` off the result; a
        # dict without it produced a `KeyError: 'input_tokens'` that reported
        # itself as the provider failing, hiding the real cause completely.
        raise OpenCodeError(self._describe(last), None if last is None else last.status_code)

    def _request(
        self, shape: Shape, model: str, system: str, prompt: str, max_tokens: int
    ) -> tuple[str, dict[str, Any]]:
        if shape == "messages":
            # Anthropic keeps the system prompt out of the message list.
            return f"{self.base_url}/messages", {
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            }
        if shape == "responses":
            return f"{self.base_url}/responses", {
                "model": model,
                "instructions": system,
                "input": prompt,
                "max_output_tokens": max_tokens,
            }
        return f"{self.base_url}/chat/completions", {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature_for(model),
        }

    @staticmethod
    def _truncated(shape: Shape, payload: Any) -> bool:
        """Did the gateway stop because it ran out of budget rather than words?

        Discarding this is how a truncated non-answer gets presented as an
        answer. It matters most on the reasoning models, where the tokens spent
        before the budget ran out were spent thinking rather than replying.
        """
        try:
            if shape == "chat":
                return str(payload["choices"][0].get("finish_reason") or "") == "length"
            if shape == "messages":
                return str(payload.get("stop_reason") or "") == "max_tokens"
            details = payload.get("incomplete_details") or {}
            reason = details.get("reason") if isinstance(details, dict) else None
            return str(payload.get("status") or "") == "incomplete" or reason is not None
        except (KeyError, IndexError, TypeError, AttributeError):
            return False

    @staticmethod
    def _extract(shape: Shape, payload: Any, *, truncated: bool = False) -> str:
        """Pull the answer out of whichever body shape came back.

        The `reasoning_content` fallback is narrower than it looks, and the
        narrowing is the point. LongCat genuinely puts its answer there, so the
        fallback has to exist. But a reasoning model cut off by the token budget
        also has an empty `content` and a full `reasoning_content` — and taking
        it then hands the operator the model's private working as though it were
        the reply, marked grounded, with no sign it was cut off. Measured on
        glm-5.3 at max_tokens=32: the "answer" to "what is 2+2" came back as
        'The user asks "What is 2+2?" and the sys'.

        So the fallback applies only when the response actually finished.
        """
        try:
            if shape == "chat":
                message = payload["choices"][0]["message"]
                content = message.get("content") or ""
                if content:
                    return str(content)
                return "" if truncated else str(message.get("reasoning_content") or "")
            if shape == "messages":
                return "".join(
                    block.get("text", "")
                    for block in payload.get("content", [])
                    if isinstance(block, dict)
                )
            # /responses returns a list of output items; the answer is the
            # message item's text, not the reasoning item that precedes it.
            parts: list[str] = []
            for item in payload.get("output", []):
                if not isinstance(item, dict) or item.get("type") != "message":
                    continue
                for block in item.get("content", []):
                    if isinstance(block, dict) and block.get("text"):
                        parts.append(block["text"])
            return "".join(parts) or str(payload.get("output_text", ""))
        except (KeyError, IndexError, TypeError, AttributeError):
            return ""

    @staticmethod
    def _usage(shape: Shape, payload: Any) -> tuple[int, int]:
        """Token counts, under whichever names this route uses.

        OpenAI-shaped bodies say prompt/completion; Anthropic-shaped ones say
        input/output. Reporting zero would silently under-count the budget, so
        both spellings are read.
        """
        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            return 0, 0
        if shape == "chat":
            return (
                int(usage.get("prompt_tokens", 0) or 0),
                int(usage.get("completion_tokens", 0) or 0),
            )
        return (
            int(usage.get("input_tokens", 0) or 0),
            int(usage.get("output_tokens", 0) or 0),
        )

    @staticmethod
    def _describe(response: httpx.Response | None) -> str:
        """Turn the gateway's failure into something an operator can act on."""
        if response is None:
            return "OPENCODE_NO_CREDENTIAL"
        if response.status_code == 401:
            return (
                "OPENCODE_UNAUTHORISED: the key was refused. If this is a Go subscription key, "
                "check the base URL is /zen/go/v1 — a Go key on the Zen endpoint reports a "
                "balance error instead."
            )
        if response.status_code == 429:
            return (
                "OPENCODE_LIMIT_REACHED: Go's usage allowance is spent ($12 per 5 hours, "
                "$30 per week, $60 per month). Enable 'Use balance' in the console to fall "
                "back to Zen credit, or wait for the window to roll."
            )
        if response.status_code == 403:
            return (
                "OPENCODE_CONSENT_REQUIRED: this model trains on submitted prompts and needs "
                "an explicit opt-in in the OpenCode console."
            )
        try:
            message = str(response.json().get("error", {}).get("message", ""))[:160]
        except ValueError:
            message = response.text[:160]
        return f"OPENCODE_HTTP_{response.status_code}: {message}".strip()
