"""What an AI actor may do, decided here and nowhere else.

Two properties make this module worth its own file rather than a few `if`
statements at the call site.

**It is deterministic, and it is not reachable by the thing it governs.** The
policy is a pure function of four facts — who is asking, which mode, which
stance, and what the action *is* — and none of those four can be set by a model
generating a tool call. An AI actor asking to switch to the autonomous stance is
asking to widen its own permissions, so the actions that carry mode and stance
are `protected` and every mode denies them to AI unconditionally. That is the
whole of the escape route, closed at the only place it could open.

**Refusals are ordered and named.** The rules are evaluated first-match-first so
that a refusal has exactly one reason, and the reason is the rule that fired. An
audit entry saying "denied: protected control" is a fact a reader can check
against this file; "denied" on its own is not.

A note on what *approval* means. `REQUIRE_APPROVAL` is not a soft deny and not a
prompt the caller may answer on its own behalf: it routes the call to a queue a
person acts on. `forge_api.actions.Actions.call` will not run an action on an
approval ruling, and there is no argument a caller can pass to skip it — the
operator's answer arrives as a separate, human-originated call.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from forge.modes.models import Stance, WorkspaceMode


class Actor(StrEnum):
    """Who is making the call.

    Deliberately two values. "The orchestrator", "the research agent" and "a
    mission step" are all AI: a boundary with one entry per agent is a boundary
    that grows a hole every time somebody adds an agent.
    """

    HUMAN = "human"
    AI = "ai"


class Ruling(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class ActionFacts:
    """The only things about an action the policy may consider.

    Not the action itself: passing the callable in would let a future edit make
    the decision depend on what the action *does at run time*, and a permission
    that depends on run-time behaviour cannot be reported before it runs.
    """

    name: str
    mutating: bool
    #: "safe" | "confirm" | "high", matching `forge_api.actions.ActionRisk`.
    risk: str
    #: Changes a deterministic control: a risk limit, a prop rule, the gate
    #: configuration, the kill switch, the operating mode or the stance.
    protected: bool = False


@dataclass(frozen=True)
class Judgement:
    ruling: Ruling
    reason: str
    actor: Actor
    mode: WorkspaceMode
    stance: Stance | None

    @property
    def allowed(self) -> bool:
        return self.ruling is Ruling.ALLOW

    def as_dict(self) -> dict[str, object]:
        return {
            "ruling": self.ruling.value,
            "reason": self.reason,
            "actor": self.actor.value,
            "mode": self.mode.value,
            "stance": self.stance.value if self.stance else None,
        }


#: What an AI actor may run unattended in Hedge Fund mode on the autonomous
#: stance: the research-to-proposal half of the loop, plus reading anything.
#:
#: The list stops where consequence begins. Preparing orders is here; screening
#: them through the gate is here, because the gate is the thing that refuses;
#: *submitting* them is not, and neither is anything that deletes an operator's
#: work. Autonomy means the loop runs without being nudged, not that the last
#: door is unlocked.
#:
#: `tests/modes/test_permissions.py` asserts every name here is a registered
#: action, so this cannot quietly come to allowlist a verb that no longer exists.
AUTONOMOUS_WORKFLOW: frozenset[str] = frozenset(
    {
        # research and alpha
        "search_papers",
        "create_family",
        "create_template",
        "create_strategy",
        "create_strategy_from_blueprint",
        "backtest_strategy",
        "run_analysis",
        "run_agent",
        # validation
        "validate_strategy",
        # portfolio, risk and the gate
        "construct_portfolio",
        "calculate_risk",
        "prepare_orders",
        "screen_orders",
        # layout: rearranging panels is not consequential in any mode
        "add_panel",
        "remove_panel",
        "move_panel",
        "resize_panel",
        "set_panel_setting",
        "add_indicator",
        "link_panels",
        "build_workspace",
        "open_workspace",
    }
)


def evaluate(
    facts: ActionFacts,
    *,
    actor: Actor,
    mode: WorkspaceMode,
    stance: Stance | None = None,
) -> Judgement:
    """Rule on one call. First matching rule wins, and names itself."""

    def ruled(ruling: Ruling, reason: str) -> Judgement:
        return Judgement(ruling=ruling, reason=reason, actor=actor, mode=mode, stance=stance)

    # 1. A person is not governed by this policy. The separate confirmation gate
    #    on CONFIRM and HIGH actions still applies to them; that gate exists to
    #    make somebody mean it, which is a different question from permission.
    if actor is Actor.HUMAN:
        return ruled(Ruling.ALLOW, "the operator is not restricted by the AI policy")

    # 2. Protected controls. Every mode, every stance, no exception. This is the
    #    rule that makes "AI cannot raise its own limits" true rather than
    #    merely intended.
    if facts.protected:
        return ruled(
            Ruling.DENY,
            f"'{facts.name}' changes a protected control; AI may never call it in any mode",
        )

    # 3. Anything that would reach an account or real money. There is no live
    #    path in this application today, and this rule is what keeps that true
    #    for AI on the day there is one.
    if facts.risk == "high":
        return ruled(
            Ruling.DENY,
            f"'{facts.name}' is a high-risk action; AI may not perform one on inference alone",
        )

    # 4. Reading. Free everywhere: an AI that has to ask permission to look
    #    cannot report honestly on what it found.
    if not facts.mutating:
        return ruled(Ruling.ALLOW, "read-only")

    # 5. Writing, by mode.
    if mode is WorkspaceMode.NORMAL:
        return ruled(
            Ruling.REQUIRE_APPROVAL,
            "Normal mode keeps AI assistance advisory; a person applies the change",
        )

    if mode is WorkspaceMode.PROP_FIRM:
        return ruled(
            Ruling.REQUIRE_APPROVAL,
            "in Prop Firm mode the account rule engine is authoritative and AI proposes only",
        )

    if mode is WorkspaceMode.AI:
        if facts.risk == "confirm":
            return ruled(
                Ruling.REQUIRE_APPROVAL,
                f"'{facts.name}' destroys work or changes lasting configuration",
            )
        return ruled(Ruling.ALLOW, "AI mode grants the workflow actions")

    # Hedge Fund.
    if stance is Stance.AUTONOMOUS:
        if facts.risk == "confirm":
            return ruled(
                Ruling.REQUIRE_APPROVAL,
                f"'{facts.name}' destroys work or changes lasting configuration; "
                "autonomy does not extend to that",
            )
        if facts.name in AUTONOMOUS_WORKFLOW:
            return ruled(Ruling.ALLOW, "inside the configured autonomous workflow")
        return ruled(
            Ruling.REQUIRE_APPROVAL,
            f"'{facts.name}' is outside the configured autonomous workflow",
        )

    return ruled(
        Ruling.REQUIRE_APPROVAL,
        "human-in-the-loop: consequential actions are prepared by AI and applied by a person",
    )


def summarise(mode: WorkspaceMode, stance: Stance | None = None) -> dict[str, object]:
    """The policy in a form the interface can show without re-deriving it.

    §20 asks for the permission model to be *explicit*. A sentence per mode,
    rendered from the same code that enforces it, is the version that cannot go
    out of date.
    """
    lines = {
        WorkspaceMode.NORMAL: "AI assists and explains. Changes are proposed and applied by you.",
        WorkspaceMode.PROP_FIRM: (
            "AI analyses and proposes. The account rule engine is deterministic and "
            "AI cannot alter it."
        ),
        WorkspaceMode.AI: (
            "AI runs the research and strategy workflow directly. Destructive and "
            "configuration changes still need you."
        ),
        WorkspaceMode.HEDGE_FUND: (
            "AI prepares consequential actions and you approve them."
            if stance is not Stance.AUTONOMOUS
            else "AI runs the configured workflow unattended, inside deterministic controls "
            "it cannot modify."
        ),
    }
    return {
        "mode": mode.value,
        "stance": stance.value if stance else None,
        "summary": lines[mode],
        "always_denied_to_ai": [
            "protected controls: risk limits, prop rules, gate configuration, kill switch, "
            "operating mode and stance",
            "high-risk actions: anything that would reach an account or real money",
        ],
    }
