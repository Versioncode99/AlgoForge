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

from forge_api.opencode import resolve_opencode_credential, resolve_opencode_standby
from forge_api.providers import catalog_for

KNOWN_MODELS: list[dict[str, Any]] = catalog_for()
MODEL_MIGRATIONS = {
    "claude-opus-5": "auto/smart",
    "claude-sonnet-5": "auto",
    "claude-haiku-4-5-20251001": "auto/fast",
    "ollama/qwen2.5-coder:14b": "auto/offline",
}

# Each role is a distinct job with a distinct cost profile, which is the whole
# reason routing exists: hypothesis work is rare and hard, tagging is constant
# and easy.
ROLES: list[dict[str, str]] = [
    {
        "key": "orchestrator",
        "label": "Orchestrator",
        "detail": "Plans a mission and dispatches the other specialists through it",
    },
    {"key": "research", "label": "Research scout", "detail": "Scholarly search and provenance"},
    {
        "key": "validation",
        "label": "Validation analyst",
        "detail": "Walk-forward and evidence review",
    },
    {
        "key": "hypothesis",
        "label": "Hypothesis analyst",
        "detail": "Writes the mechanism and the falsifiable prediction",
    },
    {
        "key": "strategy_code",
        "label": "Strategy engineer",
        "detail": "Proposes source-linked variants within tested strategy templates",
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


# Roles mapped onto real models by cost profile and monthly allowance, which is
# the whole point of routing. The reasoning jobs are rare enough to afford a
# frontier model; the constant ones go to the cheapest thing with headroom.
DEFAULT_ROUTING: dict[str, str] = {
    # Planning needs instruction-following, not reasoning depth. Measured on the
    # real planner prompt: deepseek-v4-pro, glm-5.3, glm-5.3-flash and qwen3.8-max
    # all narrate their thinking and spend the entire response budget before
    # emitting the object, which reads to the caller as "no model reachable" and
    # silently drops every mission onto the offline playbook. minimax-m3 returns
    # a clean plan in about a quarter of the tokens.
    "orchestrator": "minimax-m3",
    "research": "deepseek-v4-flash",
    "validation": "deepseek-v4-pro",
    "hypothesis": "deepseek-v4-pro",  # strongest reasoning; ~5,200/month
    "strategy_code": "kimi-k2.7-code",  # code-tuned; ~6,750/month
    "post_mortem": "glm-5.3",  # ~1,080/month, and post-mortems are rare
    "risk": "deepseek-v4-pro",
    "chat": "deepseek-v4-flash",  # fast and ~37,800/month
    "bulk": "mimo-v2.5",  # cheapest on the plan; ~150,400/month
}


@dataclass
class AISettings:
    enabled: bool = True
    provider: str = "opencode_go"
    base_url: str = "https://opencode.ai/zen/go/v1"
    routing: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ROUTING))
    budget: BudgetSettings = field(default_factory=BudgetSettings)


@dataclass
class ResearchLoopSettings:
    """Cadence for evidence intake while the desktop API is running."""

    enabled: bool = True
    interval_minutes: int = 60
    topics: list[str] = field(
        default_factory=lambda: [
            "intraday futures momentum transaction costs",
            "futures mean reversion market microstructure",
            "walk forward backtest overfitting futures",
            "futures volatility regime forecasting",
            "order flow liquidity futures price impact",
        ]
    )


#: The themes the interface ships with. Stated here rather than only in CSS so
#: the server can refuse a value the stylesheet has no palette for — a theme
#: name that persisted but did not exist would leave the application unstyled
#: with nothing to say why.
THEMES: list[dict[str, str]] = [
    {
        "key": "graphite",
        "label": "Premium Graphite",
        "detail": "Deep graphite surfaces, silver type, restrained accent. The default.",
    },
    {
        "key": "silver",
        "label": "Light / Silver",
        "detail": "The same structure on a light ground, for bright rooms and printing.",
    },
    {
        "key": "contrast",
        "label": "Dark / High Contrast",
        "detail": "Maximum separation between surfaces and type. Fewer tonal steps.",
    },
]

#: Accents are deliberately few and all low-chroma. The accent marks "a human
#: must act"; a saturated one competes with the P&L colours, which carry meaning
#: the accent does not.
ACCENTS: list[dict[str, str]] = [
    {"key": "silver", "label": "Silver", "detail": "Neutral. Nothing competes with the data."},
    {"key": "amber", "label": "Amber", "detail": "Warm. The existing AlgoForge accent."},
    {"key": "ice", "label": "Ice", "detail": "Cool blue-grey."},
]

DENSITIES: list[dict[str, str]] = [
    {"key": "compact", "label": "Compact", "detail": "Workstation default. More on screen."},
    {"key": "comfortable", "label": "Comfortable", "detail": "Looser rows and padding."},
]

MOTIONS: list[dict[str, str]] = [
    {"key": "standard", "label": "Standard", "detail": "Short transitions on state changes."},
    {
        "key": "reduced",
        "label": "Reduced",
        "detail": "No transitions. Also applied automatically when the OS asks for it.",
    },
]


@dataclass
class AppearanceSettings:
    """How the workstation looks and sounds.

    Server-side rather than in the browser, for the same reason model routing
    is: these are operator settings, and an operator opening the application on
    a second machine should find the workstation they configured. It also keeps
    them out of a scattering of localStorage keys written by whichever component
    happened to need one.
    """

    theme: str = "graphite"
    accent: str = "silver"
    density: str = "compact"
    motion: str = "standard"
    sound_enabled: bool = False
    #: Quiet by default. A sound the operator did not ask for is worse than no
    #: sound at all, and this is a room where people concentrate.
    sound_volume: float = 0.35


@dataclass
class Settings:
    ai: AISettings = field(default_factory=AISettings)
    appearance: AppearanceSettings = field(default_factory=AppearanceSettings)
    research_loop: ResearchLoopSettings = field(default_factory=ResearchLoopSettings)
    default_dataset: str = "nq_1m_16y"
    engine_cycle_seconds: float = 6.0
    engine_max_strategies: int = 60
    databento_max_cost_usd: float = 2.50


CREDENTIALS = [
    ("DATABENTO_API_KEY", "Databento", "CME futures data. Charged per request."),
    ("FRED_API_KEY", "FRED", "Macro series. Free."),
    ("BINANCE_TESTNET_KEY", "Binance testnet", "Optional. Crypto data needs no key."),
]


def _one_of(value: Any, options: list[dict[str, str]], fallback: str) -> str:
    """A stored value that no longer names anything falls back rather than sticking.

    A theme key that survives a rename would leave the interface with no palette
    and no explanation, so an unknown value is treated as absent.
    """
    keys = {item["key"] for item in options}
    text = str(value or "")
    return text if text in keys else fallback


def _appearance(raw: Any) -> AppearanceSettings:
    if not isinstance(raw, dict):
        return AppearanceSettings()
    defaults = AppearanceSettings()
    try:
        volume = float(raw.get("sound_volume", defaults.sound_volume))
    except (TypeError, ValueError):
        volume = defaults.sound_volume
    return AppearanceSettings(
        theme=_one_of(raw.get("theme"), THEMES, defaults.theme),
        accent=_one_of(raw.get("accent"), ACCENTS, defaults.accent),
        density=_one_of(raw.get("density"), DENSITIES, defaults.density),
        motion=_one_of(raw.get("motion"), MOTIONS, defaults.motion),
        sound_enabled=bool(raw.get("sound_enabled", defaults.sound_enabled)),
        sound_volume=min(1.0, max(0.0, volume)),
    )


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
        routing = {**AISettings().routing, **ai_raw.get("routing", {})}
        routing = {role: MODEL_MIGRATIONS.get(model, model) for role, model in routing.items()}
        # Settings written before the provider collapse still carry OmniRoute's
        # `auto/*` pseudo-models and a provider id that no longer exists. Left
        # alone they make every save fail validation, so they are migrated on
        # read rather than requiring the file to be deleted by hand.
        routing = {
            role: DEFAULT_ROUTING.get(role, "deepseek-v4-flash")
            if model in {"auto", "none"} or model.startswith("auto/")
            else model
            for role, model in routing.items()
        }
        provider = str(ai_raw.get("provider", "opencode_go"))
        if provider not in {"opencode_go"}:
            provider = "opencode_go"
        ai = AISettings(
            enabled=bool(ai_raw.get("enabled", True)),
            provider=provider,
            base_url="https://opencode.ai/zen/go/v1",
            routing=routing,
            budget=budget,
        )
        loop_raw = raw.get("research_loop", {})
        defaults = ResearchLoopSettings()
        topics = loop_raw.get("topics", defaults.topics)
        if not isinstance(topics, list):
            topics = defaults.topics
        return Settings(
            ai=ai,
            appearance=_appearance(raw.get("appearance", {})),
            research_loop=ResearchLoopSettings(
                enabled=bool(loop_raw.get("enabled", defaults.enabled)),
                interval_minutes=max(
                    5,
                    min(1440, int(loop_raw.get("interval_minutes", defaults.interval_minutes))),
                ),
                topics=[str(topic).strip()[:240] for topic in topics if str(topic).strip()][:12]
                or defaults.topics,
            ),
            default_dataset=raw.get("default_dataset", "nq_1m_16y"),
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
        primary = resolve_opencode_credential()
        standby = resolve_opencode_standby()
        rows = [
            {
                "key": "OPENCODE_API_KEY",
                "label": "OpenCode Go",
                "detail": "Subscription key. Every model in the app runs on this.",
                "present": primary.present,
                "hint": "configured" if primary.present else "not set",
                "source": primary.source,
            },
            {
                "key": "OPENCODE_API_KEY_2",
                "label": "OpenCode Go standby",
                "detail": "Optional. Retried once if the primary key is refused.",
                "present": standby.present,
                "hint": "configured" if standby.present else "not set",
                "source": standby.source,
            },
        ]
        for name, label, detail in CREDENTIALS:
            value = os.environ.get(name, "")
            rows.append(
                {
                    "key": name,
                    "label": label,
                    "detail": detail,
                    "present": bool(value),
                    "hint": "configured" if value else "not set",
                    "source": "environment" if value else "none",
                }
            )
        return rows
