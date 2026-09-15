"""AI Chat as the conversational interface to AlgoForge, not a chat window.

Part 3 of the directive asks for this specifically: not whether conversations
persist -- `test_chat_api.py` covers that -- but whether a quantitative workflow
can actually be *performed* through it, using the real engines, with the
deterministic boundary intact.

The distinction these tests enforce is that there is no second implementation
behind the conversation. Every verb below is the same one the interface calls
and the same one the permission policy judges; a chat-only backtest path would
be a second engine that eventually disagrees with the first about a number
somebody trades on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from forge.modes.models import Stance, WorkspaceMode
from forge.modes.permissions import ActionFacts, Actor, Ruling, evaluate
from forge_api.actions import ActionError, ApprovalRequired
from forge_api.main import create_app


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ALGOFORGE_VAULT", str(tmp_path / "workspace"))
    return create_app(tmp_path / "chat.db")


@pytest.fixture
def actions(app: Any) -> Any:
    return app.state.actions


def registry(actions: Any) -> dict[str, Any]:
    return actions._registry


# ── the workflow is reachable, verb by verb ──────────────────────────────────


@pytest.mark.parametrize(
    ("capability", "verb"),
    [
        ("create a strategy", "create_strategy"),
        ("create one from a blueprint", "create_strategy_from_blueprint"),
        ("read its definition back", "strategy_definition"),
        ("backtest it", "backtest_strategy"),
        ("validate it", "validate_strategy"),
        ("read its trades", "strategy_trades"),
        ("classify its regimes", "strategy_regimes"),
        ("run an analysis", "run_analysis"),
        ("assemble its evidence", "strategy_dossier"),
        ("simulate a prop account", "assess_prop_account"),
        ("port it to another platform", "export_strategy"),
        ("build a workspace", "build_workspace"),
        ("dock a panel", "stack_panel"),
        ("map a parameter surface", "parameter_surface"),
    ],
)
def test_the_conversation_can_reach_every_step_of_the_workflow(
    actions: Any, capability: str, verb: str
) -> None:
    """Named one per capability so a removal says which workflow broke."""
    assert verb in registry(actions), f"an assistant cannot {capability}"


def test_the_chat_adds_no_verbs_of_its_own(actions: Any) -> None:
    """The whole point: one registry, one permission surface.

    A verb that exists only for the conversation is a second way to reach the
    engine, judged by whatever that path remembered to check.
    """
    conversational = {name for name in registry(actions) if name.startswith("chat_")}
    assert conversational == set()


# ── a real workflow, end to end, through the registry ────────────────────────


def test_a_strategy_can_be_created_through_the_registry(actions: Any) -> None:
    """The first link of the chain, on the real engine.

    Deliberately stops before the backtest: that needs market data this
    container does not have, and a test that skips when a key is absent proves
    nothing on the day it matters.
    """
    created = actions.call(
        "create_strategy", {"name": "Conversation smoke", "template": "momentum_breakout"}
    )
    assert str(created["strategy_id"])


def test_asking_for_the_definition_of_hand_written_python_refuses_to_guess(
    actions: Any,
) -> None:
    """The honesty principle, reached through the conversation.

    A template produces real Python with no canonical IR behind it. Asked to
    describe one, the engine refuses rather than reconstructing something
    plausible from the source -- which is the same rule as the provenance work
    one layer down: a guess presented as the record is worse than no record.

    Worth pinning here because a conversational interface is exactly where
    somebody would be tempted to synthesise an answer rather than return a
    refusal to a person who asked politely.
    """
    created = actions.call(
        "create_strategy", {"name": "Hand written", "template": "trend_pullback_resume"}
    )
    with pytest.raises(ActionError, match="no definition"):
        actions.call("strategy_definition", {"strategy_id": created["strategy_id"]})


def test_an_unknown_strategy_refuses_rather_than_inventing_one(actions: Any) -> None:
    with pytest.raises(ActionError):
        actions.call("strategy_definition", {"strategy_id": "no_such_strategy"})


def test_a_workspace_can_be_composed_and_docked_in_one_conversation(
    actions: Any,
) -> None:
    """'Build me an NQ desk and put risk under the chart' as the registry sees it."""
    workspace = actions.call("create_workspace", {"name": "NQ desk"})
    workspace_id = str(workspace["workspace_id"])
    actions.call(
        "add_panel", {"workspace_id": workspace_id, "kind": "chart", "width": 12}
    )
    described = actions.call("describe_workspace", {"workspace_id": workspace_id})
    chart = str(described["panels"][0]["panel_id"])

    actions.call(
        "split_panel",
        {"workspace_id": workspace_id, "panel_id": chart, "kind": "risk", "along": "column"},
    )
    after = actions.call("describe_workspace", {"workspace_id": workspace_id})
    assert len(after["panels"]) == 2
    # Under, not beside: the request said "under".
    risk = next(p for p in after["panels"] if p["panel_id"] != chart)
    assert risk["y"] > 0


# ── the boundary, which is the reason this is worth testing at all ───────────


def test_an_assistant_cannot_reach_a_protected_action_in_any_mode() -> None:
    """Protected means protected wherever the conversation is open from."""
    protected = ActionFacts(
        name="set_fund_config", mutating=True, risk="high", protected=True
    )
    for mode in WorkspaceMode:
        for stance in Stance:
            judged = evaluate(protected, actor=Actor.AI, mode=mode, stance=stance)
            assert judged.ruling is Ruling.DENY, f"{mode}/{stance} let an AI through"


def test_the_conversation_does_not_widen_what_an_assistant_may_do(actions: Any) -> None:
    """Every mutating verb is still judged by the same pure function.

    If the chat had its own execution path this would be the test that missed
    it, so it asserts the shape rather than a list: nothing in the registry is
    marked as exempt from judgement.
    """
    for name, action in registry(actions).items():
        if not getattr(action, "mutating", False):
            continue
        assert not getattr(action, "skip_permissions", False), f"{name} claims exemption"


def test_every_protected_action_is_denied_to_an_assistant(actions: Any) -> None:
    """The invariant, asserted over the real registry rather than a sample.

    `protected` and `risk` are orthogonal and must not be conflated: `protected`
    says an AI may never call this, `risk` says a person has to mean it.
    `enter_mode` is protected and rated safe, and both are correct -- opening a
    mode is harmless for a human and is a deterministic control an assistant
    must not touch. So the assertion is about the ruling, not the rating.
    """
    protected = [
        name
        for name, action in registry(actions).items()
        if getattr(action, "protected", False)
    ]
    assert protected, "nothing is protected, which would mean the flag stopped being set"
    for name in protected:
        action = registry(actions)[name]
        facts = ActionFacts(
            name=name, mutating=action.mutating, risk=str(action.risk), protected=True
        )
        for mode in WorkspaceMode:
            judged = evaluate(facts, actor=Actor.AI, mode=mode, stance=Stance.AUTONOMOUS)
            assert judged.ruling is Ruling.DENY, f"{name} reachable in {mode}"


def test_approval_required_is_raised_not_swallowed(actions: Any) -> None:
    """The refusal has to reach the caller as a refusal.

    Swallowing it and returning a plausible result is the exact failure the
    provenance work exists to prevent, one layer down.

    The second assertion here used to read `not issubclass(ApprovalRequired,
    ActionError) or True`, which is false and could never fail. It *is* an
    `ActionError`, deliberately and as its own docstring says: that is what
    makes every existing handler show the reason instead of returning a 500.
    What the test means to hold is that it carries the request, so a surface
    that understands approvals can offer the decision rather than only
    reporting the refusal.
    """
    assert issubclass(ApprovalRequired, Exception)
    assert issubclass(ApprovalRequired, ActionError), (
        "an approval that is not an ActionError would surface as a 500 rather "
        "than as the reason the call was held"
    )
    # And it is a carrier, not a bare message: raising one loses nothing.
    assert "request" in ApprovalRequired.__init__.__code__.co_varnames

# ── the parameter surface, reachable through the conversation ────────────────


def test_an_assistant_can_run_a_parameter_surface(actions: Any) -> None:
    """The last capability the chat could not reach.

    It returns a job rather than a surface: 36 backtests is not something to
    wait for inside a turn, and the note says where the result lands.
    """
    created = actions.call(
        "create_strategy", {"name": "Surface smoke", "template": "momentum_breakout"}
    )
    sid = str(created["strategy_id"])
    spec = actions.library.get_spec(sid)
    names = [p.name for p in spec.parameters]
    if len(names) < 2:
        pytest.skip("this template has fewer than two parameters to sweep")

    started = actions.call(
        "parameter_surface",
        {"strategy_id": sid, "x_parameter": names[0], "y_parameter": names[1], "steps": 2},
    )
    assert started["job_id"]
    assert started["promotable"] is False, "a sweep is in-sample and can never promote"


def test_sweeping_a_parameter_against_itself_is_refused(actions: Any) -> None:
    created = actions.call(
        "create_strategy", {"name": "Self sweep", "template": "momentum_breakout"}
    )
    sid = str(created["strategy_id"])
    name = actions.library.get_spec(sid).parameters[0].name
    with pytest.raises(ActionError, match="two different parameters"):
        actions.call(
            "parameter_surface",
            {"strategy_id": sid, "x_parameter": name, "y_parameter": name},
        )


def test_an_unknown_parameter_names_the_ones_that_exist(actions: Any) -> None:
    created = actions.call(
        "create_strategy", {"name": "Bad axis", "template": "momentum_breakout"}
    )
    sid = str(created["strategy_id"])
    real = actions.library.get_spec(sid).parameters[0].name
    with pytest.raises(ActionError, match="No parameter"):
        actions.call(
            "parameter_surface",
            {"strategy_id": sid, "x_parameter": real, "y_parameter": "not_a_parameter"},
        )
