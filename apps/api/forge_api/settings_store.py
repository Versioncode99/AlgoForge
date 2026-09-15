"""Operator settings: model routing, budgets, and credential status.

Keys are never returned by this module. It reports whether a credential is
present and where it came from, so the interface can show a green light without
ever putting a secret on the wire.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from forge_api.deepseek import resolve_deepseek_credential
from forge_api.model_routing import (
    ROLE_KEYS,
    ROLES_BY_KEY,
    RoleRouting,
    RoutingSettings,
)
from forge_api.model_routing import (
    normalise as normalise_routing,
)
from forge_api.opencode import resolve_opencode_credential, resolve_opencode_standby
from forge_api.providers import PROVIDERS, base_url_for, catalog_for, known

#: Every model the app can name, across providers. This is a *vocabulary*, not a
#: permission: a saved routing choice is checked for existing somewhere before
#: it is written, and `providers.model_for` decides at call time whether the
#: selected provider can actually serve it. Restricting this to the active
#: provider would make it impossible to set up routing for a provider before
#: switching to it.
KNOWN_MODELS: list[dict[str, Any]] = [
    model for provider in PROVIDERS for model in catalog_for(provider["id"])
]
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


class BudgetMode(StrEnum):
    """§12's three modes, named as the directive names them.

    A boolean could carry two of these and the third is the one an operator
    actually wants: spend freely while it is cheap, and stop when it is not.
    """

    #: The ceilings apply, always.
    ENFORCED = "ENFORCED"
    #: No research ceiling. `SAFETY_LIMITS` still hold -- they are about what
    #: this process can survive, not about what the research is worth.
    UNLIMITED_WITH_SAFETY_LIMITS = "UNLIMITED_WITH_SAFETY_LIMITS"
    #: The ceilings apply once the day's spend crosses the soft threshold.
    ADAPTIVE = "ADAPTIVE"


#: What each mode does, for the surface that renders the choice. Data rather
#: than prose in a component, so the screen cannot describe a mode the code no
#: longer implements.
BUDGET_MODES: list[dict[str, str]] = [
    {
        "key": str(BudgetMode.ENFORCED),
        "label": "Enforced",
        "detail": "The research ceilings below apply from the first call.",
    },
    {
        "key": str(BudgetMode.UNLIMITED_WITH_SAFETY_LIMITS),
        "label": "Unlimited, with safety limits",
        "detail": (
            "No research ceiling. The safety limits stay in force: they are about what "
            "this process can survive, not about what the research is worth."
        ),
    },
    {
        "key": str(BudgetMode.ADAPTIVE),
        "label": "Adaptive",
        "detail": (
            "No ceiling while today's spend is under the soft threshold, and the full "
            "ceilings once it crosses. If the spend cannot be read, the ceilings apply."
        ),
    },
]


@dataclass
class BudgetSettings:
    """What a campaign may spend, and whether that ceiling is enforced at all.

    The switch is the point. Budget ceilings are a useful safety mechanism and
    deleting them would be a regression, but an operator running an overnight
    campaign on a flat-rate subscription is not protected by a dollar figure
    that means nothing on their plan — they are interrupted by it.

    So enforcement is explicit. With ``enforced`` off, no research ceiling stops
    a campaign and the interface says so plainly; the accounting keeps running,
    because knowing what was spent is useful whether or not anything stops.

    **System safety limits are not budget** and do not live here. Concurrency
    caps, per-response token limits, request timeouts, the provider restriction
    and the agent ceiling are in force regardless of this switch, because they
    are about what this process can survive rather than what the research is
    worth. `SAFETY_LIMITS` states them so the distinction is visible rather
    than asserted.
    """

    #: Which of §12's three modes is in force.
    #:
    #: `ENFORCED` applies the research ceilings below. `UNLIMITED_WITH_SAFETY_LIMITS`
    #: applies none of them and leaves `SAFETY_LIMITS` in force. `ADAPTIVE` is the
    #: middle one and is the reason the switch could not stay a boolean: it applies
    #: nothing while the day's spend is below the soft threshold and everything once
    #: it crosses, so an operator is not interrupted during ordinary work and is
    #: stopped when the spending starts to matter.
    mode: BudgetMode = BudgetMode.ENFORCED
    daily_usd_hard: float = 12.0
    daily_usd_soft: float = 9.0
    monthly_usd_hard: float = 300.0
    per_session_usd: float = 1.50
    halt_on_breach: bool = True
    #: Research ceilings, applied only while `enforced` is true. Zero means "no
    #: ceiling on this dimension" even when enforcement is on, so an operator
    #: can cap model calls without capping experiments.
    campaign_experiments: int = 0
    model_calls_per_day: int = 0
    wall_clock_minutes: int = 0
    backtests_per_campaign: int = 0
    external_research_per_day: int = 0

    def __post_init__(self) -> None:
        """Coerce a stored string back into the enum.

        Settings round-trip through JSON, so `mode` comes back as a plain
        string. `StrEnum` members compare *equal* to their string and are not
        *identical* to it, and every decision below is an `is` check -- so
        without this the mode loaded from disk silently behaved as `ENFORCED`
        whatever it said, which is the failure that looks like the setting not
        saving.
        """
        if not isinstance(self.mode, BudgetMode):
            try:
                object.__setattr__(self, "mode", BudgetMode(str(self.mode)))
            except ValueError:
                # An unreadable mode enforces. A settings file with a mode this
                # build does not know must not be the reason no ceiling applies.
                object.__setattr__(self, "mode", BudgetMode.ENFORCED)

    @property
    def enforced(self) -> bool:
        """Whether any research ceiling can bite at all.

        Kept because callers and the settings payload have always asked this
        question, and because the honest answer for `ADAPTIVE` is yes: its
        ceilings are configured and can stop a campaign. *When* they bite is
        `limit`'s business, and a caller that needed to know that was already
        calling `limit`.
        """
        return self.mode is not BudgetMode.UNLIMITED_WITH_SAFETY_LIMITS

    def limit(self, name: str, spent_usd: float | None = None) -> int:
        """The ceiling on one dimension right now, or 0 for none.

        `spent_usd` is today's spend and only `ADAPTIVE` reads it. **Not knowing
        it enforces.** A caller that cannot say what has been spent has not
        established that spending is low, and treating unknown as "under the
        threshold" would make the safest-sounding mode the one that stops
        applying the moment the accounting is unavailable -- which is exactly
        when it should apply.
        """
        if self.mode is BudgetMode.UNLIMITED_WITH_SAFETY_LIMITS:
            return 0
        under_soft = spent_usd is not None and spent_usd < self.daily_usd_soft
        if self.mode is BudgetMode.ADAPTIVE and under_soft:
            return 0
        return max(0, int(getattr(self, name, 0) or 0))


#: Limits that hold whatever the budget switch says, and why each one exists.
#:
#: Stated as data so the settings screen can show the operator exactly what
#: turning enforcement off does *not* turn off. A list of promises in prose
#: would drift; this is read by the endpoint that renders it.
SAFETY_LIMITS: list[dict[str, Any]] = [
    {
        "key": "model_concurrency",
        "label": "Concurrent model requests",
        "value": "2",
        "why": "More in flight than the provider accepts returns errors, not answers.",
    },
    {
        "key": "response_tokens",
        "label": "Response token ceiling",
        "value": "1,600",
        "why": "A model that never stops talking holds a worker until it times out.",
    },
    {
        "key": "request_timeout",
        "label": "Request timeout",
        "value": "180s",
        "why": "An unbounded wait is a worker that never comes back.",
    },
    {
        "key": "retrieval_timeout",
        "label": "External retrieval timeout and size cap",
        "value": "20s / 2 MB",
        "why": "A slow or enormous response is a hung research worker.",
    },
    {
        "key": "agent_ceiling",
        "label": "Maximum research agents",
        "value": "64",
        "why": "Past this the scheduling costs more than the research.",
    },
    {
        "key": "provider_restriction",
        "label": "Provider restriction",
        "value": "the one you selected",
        "why": "No call is ever routed to a provider you did not choose.",
    },
    {
        "key": "permissions",
        "label": "Action permissions",
        "value": "unchanged",
        "why": (
            "What an assistant may do is a pure function of who is asking, the mode, "
            "the stance and the action. No budget setting is an input to it."
        ),
    },
]


# Roles mapped onto real models by cost profile and monthly allowance, which is
# the whole point of routing. The reasoning jobs are rare enough to afford a
# frontier model; the constant ones go to the cheapest thing with headroom.
#
# The research agent roles get their defaults from `DEFAULT_AGENT_ROUTING`
# below, by the demand each one carries, rather than being listed twice.
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
    #: The flat role-to-model mapping, kept because it is what the existing
    #: callers read. `model_routing` is the richer form on top of it, and
    #: `_sync_routing` keeps the two from disagreeing.
    routing: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ROUTING))
    #: Mode, defaults, per-role fallbacks, and the research agent roles the
    #: flat mapping never covered.
    model_routing: RoutingSettings = field(default_factory=lambda: default_routing())
    budget: BudgetSettings = field(default_factory=BudgetSettings)


#: Source categories external research may draw on, and what each one is.
#: A closed list: the retrieval layer reaches fixed, credential-free indexes,
#: and an operator choosing a category is choosing among those rather than
#: naming a site for something to go and fetch.
SOURCE_CATEGORIES: list[dict[str, str]] = [
    {
        "key": "preprints",
        "label": "Preprints (arXiv q-fin)",
        "detail": "Quantitative finance preprints. Where most of this work appears first.",
    },
    {
        "key": "journals",
        "label": "Journals and DOI metadata (Crossref)",
        "detail": "Published articles across publishers, reached through their DOIs.",
    },
]

FRESHNESS: list[dict[str, str]] = [
    {"key": "any", "label": "Any", "detail": "Age is not weighed. Foundational work ranks too."},
    {
        "key": "recent",
        "label": "Prefer recent",
        "detail": "Newer work ranks higher, without excluding older work.",
    },
    {
        "key": "current",
        "label": "Current only",
        "detail": "Published in the last five years. Narrow, and it will find less.",
    },
]

DEPTHS: list[dict[str, str]] = [
    {"key": "shallow", "label": "Shallow", "detail": "Three results per topic. Fastest."},
    {"key": "standard", "label": "Standard", "detail": "Six results per topic."},
    {"key": "deep", "label": "Deep", "detail": "Ten results per topic. Slowest, and the cap."},
]

#: Results kept per topic at each depth.
DEPTH_RESULTS: dict[str, int] = {"shallow": 3, "standard": 6, "deep": 10}


@dataclass
class ResearchLoopSettings:
    """Cadence and reach for evidence intake while the desktop API is running.

    External research is **on by default**. The retrieval layer has no credential
    and no arbitrary-URL fetch: it searches two fixed public indexes and stores
    what they return, with the provenance a citation needs. A failed retrieval is
    recorded as a failure — there is no path in this application that invents a
    paper — so defaulting it on costs an operator a network call and buys the
    research engine an input it otherwise has to do without.
    """

    enabled: bool = True
    interval_minutes: int = 60
    #: Which indexes may be searched. Empty is treated as every category.
    categories: list[str] = field(default_factory=lambda: ["preprints", "journals"])
    freshness: str = "recent"
    depth: str = "standard"
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


#: The model each demand band gets when nothing is assigned. Three ids, and a
#: research role picks the one its own demand names — so adding a role does not
#: mean adding a line to a table somebody has to remember to update.
DEFAULT_BY_DEMAND: dict[str, str] = {
    "reasoning": "deepseek-v4-pro",
    "balanced": "deepseek-v4-flash",
    "bulk": "mimo-v2.5",
}


def default_routing() -> RoutingSettings:
    """What AlgoForge ships, as recommendations rather than as assignments.

    Built rather than written out, so a role added to `model_routing.ROLES`
    arrives with a sensible model instead of falling through to a generic one.

    **`recommended`, not `model`, and the distinction is load-bearing.** These
    used to land in `model`, which is where an *operator's* choice goes. Every
    role was therefore "assigned" on a fresh installation, the role assignment
    beats everything below it, and setting the Research feature to a frontier
    model changed nothing whatsoever — the simple half of the settings screen
    was decorative. Held apart, a fresh install still gets the measured per-role
    choice and an operator who sets one model gets it everywhere they have not
    said otherwise.

    `default_model` is empty for the same reason: it is the operator's global
    default, and shipping a value in it would mean the recommendations below
    were never consulted.
    """
    roles: dict[str, RoleRouting] = {}
    for key in ROLE_KEYS:
        role = ROLES_BY_KEY[key]
        chosen = DEFAULT_ROUTING.get(key) or DEFAULT_BY_DEMAND.get(role.demand.value, "")
        roles[key] = RoleRouting(
            recommended=chosen, fallback=DEFAULT_BY_DEMAND["balanced"]
        )
    return RoutingSettings(
        mode="hybrid",
        default_model="",
        fallback_model=DEFAULT_BY_DEMAND["balanced"],
        # Nothing to migrate: this *is* the shipped table.
        flat_migrated=True,
        roles=roles,
    )


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


def _categories(raw: Any, fallback: list[str]) -> list[str]:
    """Source categories, dropping any the retrieval layer does not have.

    An unknown category is not an error and must not fail the load, but it must
    not survive either: a category nothing searches would show as enabled on the
    settings screen while contributing nothing.

    An *empty* stored list is kept rather than replaced by the defaults. "Search
    nothing" and "search everything" are opposite instructions, and reading the
    first as the second turns an operator's deliberate choice into its inverse
    on the next restart. Absent — not a list at all — is the only case that
    falls back, because that is a file written before the field existed.
    """
    known = {item["key"] for item in SOURCE_CATEGORIES}
    if not isinstance(raw, list):
        return list(fallback)
    return [str(item) for item in raw if str(item) in known]


def _merge_routing(routing: RoutingSettings, flat: dict[str, str]) -> RoutingSettings:
    """Fill the rich routing from the flat mapping, and from the defaults.

    The flat `routing` mapping is what the existing callers read and what older
    settings files carry. Treating it as the source for the roles it covers means
    an operator's existing choices survive this change; everything it never
    covered — every research agent role — arrives at its default rather than
    unset, which on the settings screen would look like a deliberate blank.
    """
    base = default_routing()
    merged: dict[str, RoleRouting] = {}
    for key in ROLE_KEYS:
        stored = routing.roles.get(key, RoleRouting())
        shipped = base.roles[key]
        # A flat entry equal to what this build ships is not an operator's
        # choice; it is the default, written into the copy at save time. Reading
        # it back as an assignment would pin every role on every existing
        # installation and make the feature overrides do nothing — the same
        # failure `default_routing` above describes, arriving by migration
        # instead of by construction. A flat entry that *differs* is a real
        # choice and is carried across.
        carried = flat.get(key, "")
        migrated = "" if carried == shipped.recommended else carried
        merged[key] = RoleRouting(
            model=stored.model or migrated,
            recommended=stored.recommended or shipped.recommended,
            fallback=stored.fallback or shipped.fallback,
            enabled=stored.enabled,
        )
    return RoutingSettings(
        mode=routing.mode,
        default_model=routing.default_model,
        fallback_model=routing.fallback_model or base.fallback_model,
        allowed=list(routing.allowed),
        features=dict(routing.features),
        flat_migrated=True,
        roles=merged,
    )


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
        stored_budget = ai_raw.get("budget", {})
        stored_budget = stored_budget if isinstance(stored_budget, dict) else {}
        known_budget = set(asdict(BudgetSettings()))
        budget = BudgetSettings(
            **{
                **asdict(BudgetSettings()),
                # A settings file written before a field existed carries fewer
                # keys; one written after a field was removed carries more, and
                # passing an unknown key raises. Neither should stop the load.
                **{k: v for k, v in stored_budget.items() if k in known_budget},
            }
        )
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
        # A provider id that no longer exists falls back to the default rather
        # than failing the load; the endpoint is derived from the id, never read
        # from the file, so a stale base_url cannot survive a rename.
        provider = known(str(ai_raw.get("provider", "opencode_go")))
        model_routing = normalise_routing(
            ai_raw.get("model_routing"), known_models={m["id"] for m in KNOWN_MODELS}
        )
        # One-way, and only for a file written before the consolidation. The
        # flat map is derived from this table now, so folding it back in on
        # every load would let the deprecated copy keep governing behaviour.
        if not model_routing.flat_migrated:
            model_routing = _merge_routing(model_routing, routing)
        ai = AISettings(
            enabled=bool(ai_raw.get("enabled", True)),
            provider=provider,
            base_url=base_url_for(provider),
            routing=routing,
            model_routing=model_routing,
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
                categories=_categories(loop_raw.get("categories"), defaults.categories),
                freshness=_one_of(loop_raw.get("freshness"), FRESHNESS, defaults.freshness),
                depth=_one_of(loop_raw.get("depth"), DEPTHS, defaults.depth),
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
        deepseek = resolve_deepseek_credential()
        rows = [
            {
                "key": "OPENCODE_API_KEY",
                "label": "OpenCode Go",
                "detail": "Subscription key. Every model on that provider runs on this.",
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
            {
                "key": "DEEPSEEK_API_KEY",
                "label": "DeepSeek (direct)",
                "detail": (
                    "Account key, billed per token. Used when the DeepSeek provider "
                    "is selected; independent of the subscription allowance."
                ),
                "present": deepseek.present,
                "hint": "configured" if deepseek.present else "not set",
                "source": deepseek.source,
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
