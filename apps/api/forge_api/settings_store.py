"""Operator settings: model routing, budgets, and credential status.

Keys are never returned by this module. It reports whether a credential is
present and where it came from, so the interface can show a green light without
ever putting a secret on the wire.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Cheap-to-expensive. The router lets each role sit wherever the operator wants.
KNOWN_MODELS: list[dict[str, Any]] = [
    {
        "id": "claude-opus-5",
        "label": "Claude Opus 5",
        "tier": "frontier",
        "note": "Hardest reasoning. Use for hypothesis and post-mortem only.",
    },
    {
        "id": "claude-sonnet-5",
        "label": "Claude Sonnet 5",
        "tier": "balanced",
        "note": "Good default for code generation and routine analysis.",
    },
    {
        "id": "claude-haiku-4-5-20251001",
        "label": "Claude Haiku 4.5",
        "tier": "fast",
        "note": "Cheapest hosted option. Extraction, tagging, summarising.",
    },
    {
        "id": "ollama/qwen2.5-coder:14b",
        "label": "Qwen 2.5 Coder 14B (local)",
        "tier": "local",
        "note": "Runs on this machine. No API cost, no data leaves the box.",
    },
    {
        "id": "none",
        "label": "Disabled",
        "tier": "off",
        "note": "This role does no model work at all.",
    },
]

# Each role is a distinct job with a distinct cost profile, which is the whole
# reason routing exists: hypothesis work is rare and hard, tagging is constant
# and easy.
ROLES: list[dict[str, str]] = [
    {
        "key": "hypothesis",
        "label": "Hypothesis analyst",
        "detail": "Writes the mechanism and the falsifiable prediction",
    },
    {
        "key": "strategy_code",
        "label": "Strategy engineer",
        "detail": "Writes strategy code and its tests",
    },
    {
        "key": "post_mortem",
        "label": "Post-mortem analyst",
        "detail": "Turns failures into durable constraints",
    },
    {"key": "risk", "label": "Risk officer", "detail": "Sizing, correlation and prop-rule fit"},
    {"key": "chat", "label": "Console chat", "detail": "The assistant you talk to in the app"},
    {"key": "bulk", "label": "Bulk worker", "detail": "Summarising, tagging, log triage"},
]


@dataclass
class BudgetSettings:
    daily_usd_hard: float = 12.0
    daily_usd_soft: float = 9.0
    monthly_usd_hard: float = 300.0
    per_session_usd: float = 1.50
    halt_on_breach: bool = True


@dataclass
class AISettings:
    enabled: bool = False
    routing: dict[str, str] = field(
        default_factory=lambda: {
            "hypothesis": "claude-opus-5",
            "strategy_code": "claude-sonnet-5",
            "post_mortem": "claude-opus-5",
            "risk": "claude-sonnet-5",
            "chat": "claude-sonnet-5",
            "bulk": "ollama/qwen2.5-coder:14b",
        }
    )
    budget: BudgetSettings = field(default_factory=BudgetSettings)


@dataclass
class Settings:
    ai: AISettings = field(default_factory=AISettings)
    default_dataset: str = "mnq_1m_3mo"
    engine_cycle_seconds: float = 6.0
    engine_max_strategies: int = 60
    databento_max_cost_usd: float = 2.50


CREDENTIALS = [
    ("ANTHROPIC_API_KEY", "Anthropic", "Required before any hosted model can run."),
    ("DATABENTO_API_KEY", "Databento", "CME futures data. Charged per request."),
    ("FRED_API_KEY", "FRED", "Macro series. Free."),
    ("BINANCE_TESTNET_KEY", "Binance testnet", "Optional. Crypto data needs no key."),
]


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return Settings()
        ai_raw = raw.get("ai", {})
        budget = BudgetSettings(**{**asdict(BudgetSettings()), **ai_raw.get("budget", {})})
        ai = AISettings(
            enabled=bool(ai_raw.get("enabled", False)),
            routing={**AISettings().routing, **ai_raw.get("routing", {})},
            budget=budget,
        )
        return Settings(
            ai=ai,
            default_dataset=raw.get("default_dataset", "mnq_1m_3mo"),
            engine_cycle_seconds=float(raw.get("engine_cycle_seconds", 6.0)),
            engine_max_strategies=int(raw.get("engine_max_strategies", 60)),
            databento_max_cost_usd=float(raw.get("databento_max_cost_usd", 2.50)),
        )

    def save(self, settings: Settings) -> Settings:
        self.path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
        return settings

    @staticmethod
    def credential_status() -> list[dict[str, Any]]:
        """Presence only. The value is never read into the response."""
        from forge.data.live import load_keys

        load_keys()
        rows = []
        for name, label, detail in CREDENTIALS:
            value = os.environ.get(name, "")
            rows.append(
                {
                    "key": name,
                    "label": label,
                    "detail": detail,
                    "present": bool(value),
                    # Length only. Even a prefix is more than the UI needs.
                    "hint": f"set · {len(value)} chars" if value else "not set",
                }
            )
        return rows
