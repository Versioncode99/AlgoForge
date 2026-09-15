"""The three operating environments, and what each one is for.

AlgoForge is one platform. A *mode* is not a separate product and not a
licence tier: it is a declaration of what the operator came here to do, and
everything downstream — which sections appear, which layout is seeded, what an
AI actor is permitted to do on their behalf — is derived from it.

**Why the manifest lives here rather than in the interface.** The navigation
used to exist only as a React constant, which meant an agent asked "what can I
see in Prop Firm mode" had to be told by a person. One declaration, read by the
HTTP API, the action registry and the shell alike, is what stops the answer
from depending on which surface is asking.

**What a mode is not.** It is not a permission boundary on the *human*. Nothing
here removes a capability from the operator: every mode can reach validation,
every mode can reach the judge, and switching modes never deletes work. The
restriction a mode carries applies to the *AI actor* — see
`forge.modes.permissions` — because that is the actor whose reach has to be
bounded rather than merely tidy.

**Why there is no Hedge Fund mode.** There was one, and it was a product
category rather than a capability: AlgoForge is not sold as hedge-fund
infrastructure, and a top-level mode named after an institution told quants,
traders and prop traders that the deepest part of the application was for
somebody else. Nothing built for it was deleted. The deterministic loop it
carried — portfolio construction, the risk engine, the pre-trade gate,
execution, operations — moved into Normal, which is the environment for
somebody trading their own book; its oversight surfaces — the orchestrator
log, the approval queue, the audit trail — moved into AI, which is where the
actor those surfaces exist to watch actually lives.

**Where the autonomous stance went.** It moved with the oversight surfaces, to
AI, and it grants exactly what it granted before. Autonomy was never a property
of being a fund; it is a property of running unattended, which is what AI mode
is for. The reachability is unchanged and deliberately so: the same two-step
opt-in (choose the mode, then choose the stance) reaches the same single
permission, and the same deterministic controls stand behind it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from forge.product.navigation import DESTINATIONS


class WorkspaceMode(StrEnum):
    """The three environments the product opens into."""

    NORMAL = "normal"
    PROP_FIRM = "prop_firm"
    AI = "ai"


class Stance(StrEnum):
    """How much an AI actor may do without a person in the way.

    Only AI mode offers the choice, because it is the only mode whose purpose is
    unattended work — the distinction has nothing to say in an environment where
    a person is driving. Both stances are bounded by the same deterministic
    controls: *autonomous* moves the approval gate, it does not remove the risk
    engine, the pre-trade gate or the kill switch. See
    `forge.modes.permissions`.
    """

    HUMAN_IN_THE_LOOP = "human_in_the_loop"
    AUTONOMOUS = "autonomous"


@dataclass(frozen=True)
class Section:
    """One destination inside a mode.

    `route` is the identifier the shell navigates to and the action registry
    names. `panel_kinds` says which workspace panels belong to this section, so
    "add the thing I am looking at to my layout" has an answer that does not
    require the interface to hold a second mapping.
    """

    route: str
    label: str
    detail: str
    group: str
    panel_kinds: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "label": self.label,
            "detail": self.detail,
            "group": self.group,
            "panel_kinds": list(self.panel_kinds),
        }


@dataclass(frozen=True)
class ModeDescriptor:
    """Everything a surface needs to open a mode without knowing about modes."""

    mode: WorkspaceMode
    name: str
    tagline: str
    purpose: str
    #: The workspace template seeded the first time this mode is entered. §22:
    #: the operator should not have to assemble their own tools to begin.
    workspace_template: str
    sections: tuple[Section, ...]
    #: Empty for every mode but AI. An empty tuple means "this mode has no
    #: stance", which is different from "it has one and it is the default".
    stances: tuple[Stance, ...] = ()
    #: Stated limitations, shown in the interface rather than discovered. A mode
    #: that cannot do something says so where the operator is standing.
    limitations: tuple[str, ...] = ()

    @property
    def default_stance(self) -> Stance | None:
        return self.stances[0] if self.stances else None

    def section(self, route: str) -> Section | None:
        return next((s for s in self.sections if s.route == route), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "name": self.name,
            "tagline": self.tagline,
            "purpose": self.purpose,
            "workspace_template": self.workspace_template,
            "sections": [s.as_dict() for s in self.sections],
            "stances": [s.value for s in self.stances],
            "default_stance": self.default_stance.value if self.default_stance else None,
            "limitations": list(self.limitations),
        }


# ── the manifests ────────────────────────────────────────────────────────────
#: The product's destinations, shared by all three descriptors.
#:
#: They used to be three hand-written tuples of twenty-odd sections each, and
#: the duplication was not the worst of it: a mode is not a permission boundary
#: on the *human* — every mode could always reach validation, the judge and the
#: strategy library — so three navigations for one product meant the rail
#: changed while what it could reach did not. There is one now, in
#: `forge.product.navigation`, and this derives from it so a descriptor cannot
#: offer a destination the shell does not have.
PRODUCT_SECTIONS: tuple[Section, ...] = tuple(
    Section(
        route=destination.route,
        label=destination.label,
        detail=destination.detail,
        group=destination.group,
        panel_kinds=destination.panels(),
    )
    for destination in DESTINATIONS
)


_NORMAL = ModeDescriptor(
    mode=WorkspaceMode.NORMAL,
    name="Normal",
    tagline="Your trading environment.",
    purpose=(
        "Trade, analyse and monitor markets without a firm's constraints or an "
        "institutional workflow in the way. The least opinionated of the four: it "
        "supplies the tools and leaves the arrangement to you."
    ),
    workspace_template="normal_desk",
    sections=PRODUCT_SECTIONS,
    limitations=(
        "Paper only. No live-order path exists anywhere in this application.",
        "Fills are modelled from bar data, not calibrated against a broker.",
        "Execution is simulated locally. No broker, OMS vendor or venue is connected.",
        "No commercial factor model is installed. Factor exposure is reported only "
        "against loadings you supply.",
    ),
)

_PROP_FIRM = ModeDescriptor(
    mode=WorkspaceMode.PROP_FIRM,
    name="Prop Firm",
    tagline="Trade within account constraints.",
    purpose=(
        "Operate a funded or evaluation account against its own rule set. The "
        "workspace is arranged around one question — how close am I to breaching "
        "— and the rule engine that answers it is deterministic and configurable, "
        "not a copy of any one firm's contract."
    ),
    workspace_template="prop_desk",
    sections=PRODUCT_SECTIONS,
    limitations=(
        "Rule sets are supplied by you. AlgoForge makes no claim about what any "
        "named firm's live contract says.",
        "An assessment describes the configured rules, not a firm's discretion.",
        "The Prop Desk has no live broker connector. Rithmic, Tradovate and "
        "ProjectX are declared with their real interfaces and refuse every "
        "command; only the local simulator executes, and it labels every fill "
        "simulated.",
        "Firm permissions start UNKNOWN and you record them. An unrecorded rule "
        "is never treated as permission.",
        "Adaptive and AI-managed risk move one number: the share of an account's "
        "buffer that an allocation may cost. Everything from there to an order is "
        "deterministic, and your boundaries are the ceiling.",
        "Autonomous deployment cannot reach a venue in this build: the lifecycle "
        "refuses the deployed stage while no broker connector exists, and reports "
        "that refusal as a mandatory control that did not pass.",
    ),
)

_AI = ModeDescriptor(
    mode=WorkspaceMode.AI,
    name="AI",
    tagline="Build, analyse and automate with AI.",
    purpose=(
        "Use AI across research, strategy work and automation, reaching the same "
        "bounded action registry the interface uses. Every call is schema-checked, "
        "logged and refusable; there is no verb here the interface does not also "
        "have."
    ),
    workspace_template="ai_desk",
    sections=PRODUCT_SECTIONS,
    stances=(Stance.HUMAN_IN_THE_LOOP, Stance.AUTONOMOUS),
    limitations=(
        "AI reaches only the registered actions. There is no arbitrary-code verb.",
        "A model that is not configured is reported as unavailable, never simulated.",
        "The autonomous stance moves the approval gate and nothing else. The risk "
        "engine, the pre-trade gate and the kill switch stand in front of every "
        "order either way, and AI cannot modify any of them.",
        "Execution is simulated locally. No broker, OMS vendor or venue is connected, "
        "so an autonomously submitted order reaches a simulator and says so.",
    ),
)

MODES: dict[WorkspaceMode, ModeDescriptor] = {
    descriptor.mode: descriptor
    for descriptor in (_NORMAL, _PROP_FIRM, _AI)
}

#: Display order. Deliberately not `WorkspaceMode` iteration order by accident —
#: the home screen reads this, and the sequence is a product decision.
MODE_ORDER: tuple[WorkspaceMode, ...] = (
    WorkspaceMode.NORMAL,
    WorkspaceMode.PROP_FIRM,
    WorkspaceMode.AI,
)


def descriptor(mode: WorkspaceMode | str) -> ModeDescriptor:
    """The manifest for one mode.

    Accepts the string form so an HTTP path parameter does not have to be
    converted by every caller, and raises with the valid set rather than a bare
    `KeyError`, because this refusal is shown to people.
    """
    try:
        key = WorkspaceMode(mode)
    except ValueError:
        valid = ", ".join(m.value for m in MODE_ORDER)
        raise KeyError(f"no mode '{mode}'. Modes: {valid}") from None
    return MODES[key]


def catalogue() -> list[dict[str, Any]]:
    """Every mode, in display order, as the home screen renders them."""
    return [MODES[mode].as_dict() for mode in MODE_ORDER]


def parse_stance(mode: WorkspaceMode, stance: str | None) -> Stance | None:
    """Validate a stance *against the mode that would hold it*.

    A stance on a mode that has none is a caller error rather than something to
    ignore: silently dropping it is how a request to run autonomously ends up
    looking like it was honoured.
    """
    available = MODES[mode].stances
    if stance is None:
        return available[0] if available else None
    if not available:
        raise ValueError(f"{mode.value} mode has no operating stances")
    try:
        parsed = Stance(stance)
    except ValueError:
        raise ValueError(
            f"no stance '{stance}'. Available: {', '.join(s.value for s in available)}"
        ) from None
    if parsed not in available:
        raise ValueError(f"{mode.value} mode does not offer the {parsed.value} stance")
    return parsed
