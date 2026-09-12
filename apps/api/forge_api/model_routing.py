"""Which model answers, for which job, and why that one.

Routing existed before this module, as a flat mapping from nine workflow role
names to model ids. Two things were wrong with it and both were about honesty
rather than capability.

**It did not cover the roles that do the research.** The research engine runs
ten agent roles of its own — discovery, hypothesis, falsification, regime,
robustness, validation, literature, feature, reviewer, specialist — and none of
them appeared in the routing table. Choosing "a model per role" in settings
therefore chose the model for none of the work the campaigns actually do.

**It could not say what it had done.** `model_for` quietly substituted a
default when the selected provider could not serve the configured model. That
substitution is often correct and it was invisible, which makes it the same
class of problem as a silent fallback anywhere else: the operator reads the
settings screen, believes a frontier model is answering, and is looking at the
output of a cheap one.

So every selection here returns a :class:`Decision` carrying the model, where
the choice came from, and a sentence saying why. Callers record it. Nothing in
this module reaches a provider, holds a credential, or decides what a role is
allowed to do — permissions are `forge.modes.permissions` and are not a routing
concern.

Three modes, and the difference between them is what happens when the first
choice is unavailable:

``manual``
    The assigned model, and nothing else. An unavailable model is reported as
    unavailable. This is the mode for measuring one model against another,
    where a substitution would silently contaminate the comparison.

``hybrid``
    The assigned model, then the role's declared fallback, then the operator's
    default. Each step is named in the decision.

``smart``
    AlgoForge picks from the models the operator has allowed, by what the task
    needs. It never reaches for a model outside that list, which is what stops
    "smart" meaning "expensive".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class RoutingMode(StrEnum):
    MANUAL = "manual"
    HYBRID = "hybrid"
    SMART = "smart"


#: What a job needs from a model, which is what `smart` routes on.
#:
#: Deliberately three coarse bands rather than a score. A finer ranking would
#: imply a measurement nobody made: the provider's catalogue states a tier and a
#: rough latency, and inventing a number on top of that would be a claim about
#: model quality this application has not tested.
class Demand(StrEnum):
    #: Long-horizon reasoning, where a wrong answer costs a research cycle.
    REASONING = "reasoning"
    #: Structured output under time pressure. Most of the workflow.
    BALANCED = "balanced"
    #: High volume, low stakes: tagging, summarising, triage.
    BULK = "bulk"


#: Provider tiers each demand will accept, best first. A demand that cannot be
#: met from the allowed list falls through to whatever is allowed rather than
#: reaching outside it.
_TIER_PREFERENCE: dict[Demand, tuple[str, ...]] = {
    Demand.REASONING: ("frontier", "fast", "economy"),
    Demand.BALANCED: ("fast", "frontier", "economy"),
    Demand.BULK: ("economy", "fast", "frontier"),
}


@dataclass(frozen=True)
class Role:
    """One job a model can be assigned to.

    ``kind`` separates the two halves of the system, because they are configured
    for different reasons. A *workflow* role is something the operator triggers;
    a *research* role is an agent a campaign runs on its own, often many times an
    hour, and its model choice is a cost decision as much as a quality one.
    """

    key: str
    label: str
    detail: str
    kind: str
    demand: Demand
    #: False for a role the system cannot run without. The interface must not
    #: offer to disable one, because disabling it would stop campaigns silently.
    optional: bool = True


#: Every role a model can be assigned to.
#:
#: The research half mirrors `forge.research.agents.AgentRole` exactly, and a
#: test asserts that it does: a role the engine can run and the settings cannot
#: reach is a model choice the operator does not have, and a role in settings the
#: engine never runs is a control that does nothing.
ROLES: tuple[Role, ...] = (
    # ── workflow ─────────────────────────────────────────────────────────────
    Role(
        "orchestrator",
        "Orchestrator",
        "Plans a mission and dispatches the other specialists through it",
        "workflow",
        Demand.BALANCED,
        optional=False,
    ),
    Role(
        "research",
        "Research scout",
        "Scholarly search, retrieval and provenance",
        "workflow",
        Demand.BALANCED,
    ),
    Role(
        "validation",
        "Validation analyst",
        "Walk-forward and evidence review",
        "workflow",
        Demand.REASONING,
    ),
    Role(
        "hypothesis",
        "Hypothesis analyst",
        "Writes the mechanism and the falsifiable prediction",
        "workflow",
        Demand.REASONING,
    ),
    Role(
        "strategy_code",
        "Strategy engineer",
        "Proposes source-linked variants within tested strategy templates",
        "workflow",
        Demand.BALANCED,
    ),
    Role(
        "post_mortem",
        "Post-mortem analyst",
        "Turns failures into durable constraints",
        "workflow",
        Demand.REASONING,
    ),
    Role(
        "risk",
        "Risk officer",
        "Sizing, correlation and prop-rule fit",
        "workflow",
        Demand.REASONING,
    ),
    Role(
        "chat",
        "Console chat",
        "The assistant you talk to in the app",
        "workflow",
        Demand.BALANCED,
        optional=False,
    ),
    Role(
        "bulk",
        "Bulk worker",
        "Summarising, tagging, log triage",
        "workflow",
        Demand.BULK,
    ),
    # ── research agents ──────────────────────────────────────────────────────
    Role(
        "agent_discovery",
        "Discovery agent",
        "Proposes mechanisms nothing on record already claims",
        "research",
        Demand.REASONING,
        optional=False,
    ),
    Role(
        "agent_literature",
        "Literature agent",
        "Retrieves published evidence and keeps its provenance",
        "research",
        Demand.BALANCED,
    ),
    Role(
        "agent_synthesis",
        "Synthesis agent",
        "Turns retrieved claims into mechanisms and research questions",
        "research",
        Demand.REASONING,
    ),
    Role(
        "agent_feature",
        "Construction agent",
        "Investigates how a signal is constructed, not how it is tuned",
        "research",
        Demand.BALANCED,
    ),
    Role(
        "agent_hypothesis",
        "Hypothesis agent",
        "Turns a mechanism into a claim that can be shown false",
        "research",
        Demand.REASONING,
        optional=False,
    ),
    Role(
        "agent_falsification",
        "Falsification agent",
        "Attacks the candidates that look best",
        "research",
        Demand.REASONING,
    ),
    Role(
        "agent_regime",
        "Regime agent",
        "Asks whether an effect only exists under a condition",
        "research",
        Demand.BALANCED,
    ),
    Role(
        "agent_robustness",
        "Robustness agent",
        "Stress-tests what survived, on purpose, until it breaks",
        "research",
        Demand.BALANCED,
    ),
    Role(
        "agent_validation",
        "Validation agent",
        "Runs formal validation on candidates that earned it",
        "research",
        Demand.REASONING,
        optional=False,
    ),
    Role(
        "agent_reviewer",
        "Review agent",
        "Critiques lineage and evidence quality rather than results",
        "research",
        Demand.REASONING,
    ),
    Role(
        "agent_specialist",
        "Specialist agent",
        "A role you defined, described by its own objective",
        "research",
        Demand.BALANCED,
    ),
)

ROLE_KEYS: tuple[str, ...] = tuple(role.key for role in ROLES)
ROLES_BY_KEY: dict[str, Role] = {role.key: role for role in ROLES}

#: How a research `AgentRole` maps onto a routing role. Kept here rather than on
#: the enum so `forge.research` stays free of anything provider-shaped.
#:
#: There is one role per engine role and no more. Roles that would read well on a
#: diagram and do nothing distinct in this architecture are deliberately absent:
#: a "campaign allocator" would duplicate `forge.research.allocation`, which is
#: deterministic and must stay that way, and a "supervisor" would duplicate the
#: director, which is also deterministic and cannot be allowed to be talked out
#: of a gate. A role that cannot be pointed at work only it does is a line in a
#: settings table.
AGENT_ROLE_KEYS: dict[str, str] = {
    "DISCOVERY": "agent_discovery",
    "LITERATURE": "agent_literature",
    "SYNTHESIS": "agent_synthesis",
    "FEATURE": "agent_feature",
    "HYPOTHESIS": "agent_hypothesis",
    "FALSIFICATION": "agent_falsification",
    "REGIME": "agent_regime",
    "ROBUSTNESS": "agent_robustness",
    "VALIDATION": "agent_validation",
    "REVIEWER": "agent_reviewer",
    "SPECIALIST": "agent_specialist",
}


@dataclass
class RoleRouting:
    """What one role is configured to use.

    Empty strings mean "not set", which is different from a model named and
    unavailable: the first falls through to the default without comment, the
    second is a substitution the decision has to report.
    """

    model: str = ""
    fallback: str = ""
    #: An optional second opinion. Where a role supports it, the critic model
    #: reviews the primary's output; where it does not, the field is inert and
    #: the interface says so rather than offering a control that does nothing.
    critic: str = ""
    enabled: bool = True


@dataclass
class RoutingSettings:
    mode: str = RoutingMode.HYBRID.value
    #: Used by every role that names no model of its own.
    default_model: str = ""
    #: Used when the assigned model is unavailable, for roles that name no
    #: fallback of their own.
    fallback_model: str = ""
    #: What `smart` may choose from. Empty means "every model the provider
    #: serves", which is the honest default for a single-provider install.
    allowed: list[str] = field(default_factory=list)
    roles: dict[str, RoleRouting] = field(default_factory=dict)

    def for_role(self, key: str) -> RoleRouting:
        return self.roles.get(key, RoleRouting())


@dataclass(frozen=True)
class Decision:
    """Which model is about to be called, and the sentence explaining it.

    Recorded by callers and shown in the interface. A routing layer that cannot
    answer "why this model?" is one whose settings screen is decorative.
    """

    role: str
    provider: str
    model: str
    #: Where the choice came from: assigned, fallback, default, smart, or none.
    source: str
    reason: str
    substituted: bool = False
    #: Models tried and passed over, in order, with why.
    considered: tuple[str, ...] = ()
    critic: str = ""

    @property
    def available(self) -> bool:
        return bool(self.model)

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "provider": self.provider,
            "model": self.model,
            "source": self.source,
            "reason": self.reason,
            "substituted": self.substituted,
            "considered": list(self.considered),
            "critic": self.critic,
        }


def _tier_of(model: str, catalogue: Sequence[Mapping[str, Any]]) -> str:
    for entry in catalogue:
        if entry.get("id") == model:
            return str(entry.get("tier") or "")
    return ""


def _servable(model: str, catalogue: Sequence[Mapping[str, Any]]) -> bool:
    return any(entry.get("id") == model for entry in catalogue)


def _smart_pick(
    demand: Demand, catalogue: Sequence[Mapping[str, Any]], allowed: Iterable[str]
) -> str:
    """The best allowed model for this demand, by the provider's own tiers.

    "Best" is the provider's word, not a measurement this application made. The
    preference order is stated in `_TIER_PREFERENCE` and nothing here looks
    outside the allowed list — which is what keeps `smart` from meaning
    "whatever is most expensive".
    """
    permitted = {str(item) for item in allowed}
    pool = [
        entry
        for entry in catalogue
        if not permitted or str(entry.get("id")) in permitted
    ]
    for tier in _TIER_PREFERENCE[demand]:
        for entry in pool:
            if str(entry.get("tier")) == tier:
                return str(entry.get("id"))
    return str(pool[0].get("id")) if pool else ""


def resolve(
    role_key: str,
    settings: RoutingSettings,
    *,
    provider: str,
    catalogue: Sequence[Mapping[str, Any]],
    unavailable: Iterable[str] = (),
) -> Decision:
    """Pick the model for ``role_key``, and say where the choice came from.

    ``unavailable`` is for models a caller already knows will not answer — a
    model that just returned a billing error, say. Passing it in is how a retry
    moves on rather than asking the same model twice.
    """
    role = ROLES_BY_KEY.get(role_key)
    if role is None:
        raise KeyError(f"unknown routing role '{role_key}'. Known: {', '.join(ROLE_KEYS)}")
    mode = settings.mode if settings.mode in set(RoutingMode) else RoutingMode.HYBRID.value
    routing = settings.for_role(role_key)
    blocked = {str(item) for item in unavailable}
    considered: list[str] = []

    if not routing.enabled:
        return Decision(
            role=role_key,
            provider=provider,
            model="",
            source="disabled",
            reason=f"{role.label} is switched off in settings, so nothing was called.",
        )

    def usable(model: str) -> bool:
        return bool(model) and model not in blocked and _servable(model, catalogue)

    if mode == RoutingMode.SMART.value:
        chosen = _smart_pick(role.demand, catalogue, settings.allowed)
        if chosen in blocked:
            chosen = next(
                (
                    str(entry["id"])
                    for entry in catalogue
                    if str(entry["id"]) not in blocked
                    and (not settings.allowed or str(entry["id"]) in set(settings.allowed))
                ),
                "",
            )
        tier = _tier_of(chosen, catalogue) or "untiered"
        return Decision(
            role=role_key,
            provider=provider,
            model=chosen,
            source="smart" if chosen else "none",
            reason=(
                f"Smart routing: {role.label} is a {role.demand.value} job, and "
                f"'{chosen}' is the {tier} model at the top of the allowed list."
                if chosen
                else "Smart routing had no allowed model left to choose from."
            ),
            critic=routing.critic,
        )

    # manual and hybrid both start from what the operator assigned.
    if routing.model:
        considered.append(routing.model)
        if usable(routing.model):
            return Decision(
                role=role_key,
                provider=provider,
                model=routing.model,
                source="assigned",
                reason=f"{role.label} is assigned '{routing.model}' in settings.",
                critic=routing.critic,
            )
        why = (
            "was refused on the last call"
            if routing.model in blocked
            else f"is not served by the {provider} provider"
        )
        if mode == RoutingMode.MANUAL.value:
            return Decision(
                role=role_key,
                provider=provider,
                model="",
                source="none",
                reason=(
                    f"{role.label} is assigned '{routing.model}', which {why}. Manual "
                    "routing does not substitute, so this call did not run."
                ),
                considered=tuple(considered),
                critic=routing.critic,
            )
        substitute_reason = f"'{routing.model}' {why}"
    else:
        substitute_reason = f"{role.label} names no model"

    for candidate, source, label in (
        (routing.fallback, "fallback", "the role's fallback"),
        (settings.fallback_model, "fallback", "the global fallback"),
        (settings.default_model, "default", "the default model"),
    ):
        if not candidate:
            continue
        considered.append(candidate)
        if usable(candidate):
            return Decision(
                role=role_key,
                provider=provider,
                model=candidate,
                source=source,
                reason=f"{substitute_reason}, so {label} '{candidate}' answered instead.",
                substituted=bool(routing.model),
                considered=tuple(considered),
                critic=routing.critic,
            )

    chosen = _smart_pick(role.demand, catalogue, settings.allowed)
    if chosen and chosen not in blocked:
        return Decision(
            role=role_key,
            provider=provider,
            model=chosen,
            source="smart",
            reason=(
                f"{substitute_reason} and no fallback was configured, so the "
                f"{_tier_of(chosen, catalogue) or 'first available'} model '{chosen}' "
                "was chosen for this job."
            ),
            substituted=bool(routing.model),
            considered=tuple(considered),
            critic=routing.critic,
        )
    return Decision(
        role=role_key,
        provider=provider,
        model="",
        source="none",
        reason=f"{substitute_reason} and nothing the {provider} provider serves is available.",
        considered=tuple(considered),
        critic=routing.critic,
    )


def normalise(raw: Any, *, known_models: Iterable[str]) -> RoutingSettings:
    """Read routing out of stored settings, dropping anything that no longer exists.

    A model id that survived a provider change is not an error and must not fail
    the load: it is treated as unset, which falls through to the default and is
    reported as such the first time the role is used.
    """
    catalogue = {str(item) for item in known_models}
    if not isinstance(raw, dict):
        raw = {}
    mode = str(raw.get("mode") or RoutingMode.HYBRID.value)
    if mode not in set(RoutingMode):
        mode = RoutingMode.HYBRID.value

    def keep(value: Any) -> str:
        text = str(value or "")
        return text if text in catalogue else ""

    roles: dict[str, RoleRouting] = {}
    stored = raw.get("roles")
    stored = stored if isinstance(stored, dict) else {}
    for key in ROLE_KEYS:
        entry = stored.get(key)
        entry = entry if isinstance(entry, dict) else {}
        role = ROLES_BY_KEY[key]
        roles[key] = RoleRouting(
            model=keep(entry.get("model")),
            fallback=keep(entry.get("fallback")),
            critic=keep(entry.get("critic")),
            # A role the system cannot run without is always on, whatever a
            # stored file says. A settings file that could switch off hypothesis
            # generation would stop every campaign with no message anywhere.
            enabled=True if not role.optional else bool(entry.get("enabled", True)),
        )
    allowed = raw.get("allowed")
    allowed = [str(item) for item in allowed if str(item) in catalogue] if isinstance(
        allowed, list
    ) else []
    return RoutingSettings(
        mode=mode,
        default_model=keep(raw.get("default_model")),
        fallback_model=keep(raw.get("fallback_model")),
        allowed=allowed,
        roles=roles,
    )


def to_dict(settings: RoutingSettings) -> dict[str, Any]:
    return {
        "mode": settings.mode,
        "default_model": settings.default_model,
        "fallback_model": settings.fallback_model,
        "allowed": list(settings.allowed),
        "roles": {key: asdict(value) for key, value in settings.roles.items()},
    }


def role_rows() -> list[dict[str, Any]]:
    """The role table the interface renders. Options travel with the values."""
    return [
        {
            "key": role.key,
            "label": role.label,
            "detail": role.detail,
            "kind": role.kind,
            "demand": role.demand.value,
            "optional": role.optional,
        }
        for role in ROLES
    ]


def mode_rows() -> list[dict[str, str]]:
    return [
        {
            "key": RoutingMode.MANUAL.value,
            "label": "Manual",
            "detail": (
                "Only the model you assign. An unavailable one is reported, never "
                "substituted — which is what you want when comparing two models."
            ),
        },
        {
            "key": RoutingMode.HYBRID.value,
            "label": "Hybrid",
            "detail": (
                "Your assignment first, then the fallback you chose. Every "
                "substitution is named in the activity log."
            ),
        },
        {
            "key": RoutingMode.SMART.value,
            "label": "Smart",
            "detail": (
                "AlgoForge chooses from the models you allow, by what the job needs. "
                "It never reaches outside that list."
            ),
        },
    ]
