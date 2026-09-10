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


#: Actions whose effect is a proposal, an experiment or a measurement. They
#: create research, they rearrange a screen, they produce a portfolio nobody has
#: traded — none of them moves money, deletes an operator's work, or changes a
#: control. An AI actor may run these unattended in every mode: an assistant that
#: has to ask permission to run a backtest is not assisting.
#:
#: `tests/modes/test_permissions.py` asserts every name in all three sets is a
#: registered action, so a list here cannot quietly come to allowlist a verb
#: that no longer exists — or, worse, keep permitting one that was renamed into
#: something more dangerous.
PREPARATORY: frozenset[str] = frozenset(
    {
        # research and alpha
        "search_papers",
        "create_family",
        "create_template",
        "create_strategy",
        "create_strategy_from_blueprint",
        "backtest_strategy",
        "run_analysis",
        "export_strategy",
        # validation
        "validate_strategy",
        # portfolio, risk and the gate. Screening is here because the gate is
        # the thing that *refuses*: running it can only ever narrow what is
        # permitted, so needing approval to run it would be backwards.
        "construct_portfolio",
        "calculate_risk",
        "prepare_orders",
        "screen_orders",
        "assess_prop_account",
        # layout. Rearranging panels is not consequential in any mode.
        "add_panel",
        "remove_panel",
        "move_panel",
        "resize_panel",
        "set_panel_setting",
        "add_indicator",
        "link_panels",
        "build_workspace",
        "create_workspace",
        "open_workspace",
        "rename_workspace",
        "clone_workspace",
    }
)

#: Workflow automation: starting the autonomous engine, dispatching a specialist.
#: These commit the machine to doing work on its own for a while, which is what
#: the AI and Hedge Fund modes are *for* and is not what somebody opened Normal
#: mode to get. Held for a person in Normal and Prop Firm.
AUTOMATION: frozenset[str] = frozenset(
    {
        "start_engine",
        "stop_engine",
        "run_agent",
    }
)

#: Consequential: it reaches the book. Permitted to an AI actor on exactly one
#: configuration — Hedge Fund mode on the autonomous stance — and even there it
#: passes the pre-trade gate first, because the gate is deterministic and this
#: policy is not what keeps a bad order out.
CONSEQUENTIAL: frozenset[str] = frozenset(
    {
        "submit_orders",
        "cancel_order",
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

    # 5. Destroys work, or changes configuration that outlives the session.
    #    Held in every mode, autonomous included: autonomy is about running the
    #    loop without being nudged, not about deleting things unattended.
    if facts.risk == "confirm":
        return ruled(
            Ruling.REQUIRE_APPROVAL,
            f"'{facts.name}' destroys work or changes lasting configuration, which needs "
            "a person in every mode",
        )

    # 6. Preparation: a proposal, an experiment, a measurement, a layout.
    if facts.name in PREPARATORY:
        return ruled(Ruling.ALLOW, "preparatory: it proposes or measures, it does not commit")

    # 7. Automation, in the two modes that exist to run it.
    if facts.name in AUTOMATION:
        if mode in {WorkspaceMode.AI, WorkspaceMode.HEDGE_FUND}:
            return ruled(Ruling.ALLOW, f"{mode.value} mode grants workflow automation")
        return ruled(
            Ruling.REQUIRE_APPROVAL,
            f"'{facts.name}' starts unattended work, which {mode.value} mode holds for a person",
        )

    # 8. The book.
    if facts.name in CONSEQUENTIAL:
        if mode is WorkspaceMode.HEDGE_FUND and stance is Stance.AUTONOMOUS:
            return ruled(
                Ruling.ALLOW,
                "the autonomous stance permits execution inside the deterministic "
                "controls, which this policy does not relax",
            )
        return ruled(
            Ruling.REQUIRE_APPROVAL,
            f"'{facts.name}' reaches the book; only Hedge Fund mode on the autonomous "
            "stance runs it without a person",
        )

    # 9. Anything nobody has classified. Held, deliberately: a new mutating
    #    action becomes available to AI when somebody puts it in a list above,
    #    not by default the moment it is written.
    return ruled(
        Ruling.REQUIRE_APPROVAL,
        f"'{facts.name}' is not in any permitted set for an AI actor; a person applies it",
    )


def summarise(mode: WorkspaceMode, stance: Stance | None = None) -> dict[str, object]:
    """The policy in a form the interface can show without re-deriving it.

    §20 asks for the permission model to be *explicit*. A sentence per mode,
    rendered from the same code that enforces it, is the version that cannot go
    out of date.
    """
    lines = {
        WorkspaceMode.NORMAL: (
            "AI researches, backtests and explains. It does not start unattended work "
            "and it does not reach the book."
        ),
        WorkspaceMode.PROP_FIRM: (
            "AI analyses and proposes. The account rule engine is deterministic, and AI "
            "cannot alter a rule, a limit or a recorded balance."
        ),
        WorkspaceMode.AI: (
            "AI runs the research and automation workflow directly. Reaching the book, "
            "and anything destructive, still needs you."
        ),
        WorkspaceMode.HEDGE_FUND: (
            "AI researches, constructs portfolios and prepares orders on its own. "
            "Submitting them needs your approval."
            if stance is not Stance.AUTONOMOUS
            else "AI runs the whole loop unattended, including submission - inside the "
            "risk engine, the pre-trade gate and the kill switch, none of which it can "
            "modify."
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
