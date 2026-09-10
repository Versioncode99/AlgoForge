"""The research/execution boundary, asserted rather than assumed.

AlgoForge has never had a live-order path. That was true because nobody wrote
one, which is a different kind of true from "the repository refuses to contain
one". These tests convert the first into the second: they scan the source for
broker SDKs and order-submission verbs, and they check that the lifecycle graph
has no edge from research to a venue.

They are deliberately noisy to break. When a connector is eventually built, the
scan will fail and someone will have to change it on purpose — which is the
moment to think about it, rather than discovering afterwards that a research
agent could reach a broker.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from forge.execution import (
    EXECUTION_STAGES,
    LIVE_EXECUTION_AVAILABLE,
    LIVE_STAGES,
    RESEARCH_STAGES,
    Authorization,
    Stage,
    TransitionRefused,
    allowed_from,
    check_transition,
    requires_authorization,
)

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ROOT / "packages"
API = ROOT / "apps" / "api"


def python_sources(*roots: Path) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        files.extend(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    return files


# ── no live-order path exists ────────────────────────────────────────────────

# Client libraries that could reach a venue. Adding one is a decision, not a
# dependency bump.
BROKER_SDKS = {
    "ib_insync",
    "ibapi",
    "alpaca",
    "alpaca_trade_api",
    "ccxt",
    "binance",
    "oandapyV20",
    "tradovate",
    "projectx",
    "topstepx",
    "ninjatrader",
    "tradestation",
    "kiteconnect",
    "robin_stocks",
}


def test_no_broker_client_library_is_imported() -> None:
    offenders: list[str] = []
    for path in python_sources(PACKAGES, API):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if name in BROKER_SDKS:
                    offenders.append(f"{path.relative_to(ROOT)}: {name}")
    assert not offenders, (
        "A broker client library is now imported. AlgoForge is paper-only and this "
        "test is the boundary: if a connector is genuinely being built, change it "
        "deliberately.\n" + "\n".join(offenders)
    )


# Function names that would place, modify or cancel a real order. Matched on
# definitions, not calls, so a variable named `submit_order` is not a false
# positive but a function that could be one is caught.
ORDER_VERBS = re.compile(
    r"^(submit|place|send|cancel|modify|amend|close)_(order|position|trade)s?$"
)


#: The one audited path, enumerated by hand.
#:
#: AlgoForge now has a *simulated* execution path — Hedge Fund mode routes
#: cleared orders through an OMS into a local simulator. That is what this scan
#: was always going to catch, and the module docstring says this is the moment to
#: think about it rather than to widen the pattern.
#:
#: So the pattern is unchanged and the exceptions are named. A reviewer reads
#: this list to see the entire order-submitting surface of the repository, and
#: adding to it is a decision somebody makes on purpose. What makes the list
#: safe is asserted separately below and remains exactly as strict as before: no
#: broker SDK is importable, `forge.execution` reaches no network, every adapter
#: reports itself simulated, and `LIVE_EXECUTION_AVAILABLE` is still False.
SIMULATED_ORDER_PATH = {
    ("apps/api/forge_api/actions.py", "submit_orders"),
    ("apps/api/forge_api/actions.py", "cancel_order"),
    ("apps/api/forge_api/control.py", "submit_orders"),
    ("apps/api/forge_api/control.py", "cancel_order"),
}


def test_nothing_defines_an_order_submission_function_outside_the_audited_path() -> None:
    offenders: list[str] = []
    for path in python_sources(PACKAGES, API):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and ORDER_VERBS.match(
                node.name
            ):
                relative = path.relative_to(ROOT).as_posix()
                if (relative, node.name) in SIMULATED_ORDER_PATH:
                    continue
                offenders.append(f"{relative}:{node.lineno} {node.name}")
    assert not offenders, (
        "Something now defines an order-submission function outside the audited "
        "simulated path. If this is a real connector, that is a decision to make "
        "deliberately.\n" + "\n".join(offenders)
    )


def test_the_audited_path_is_not_a_list_of_functions_that_no_longer_exist() -> None:
    """An exception nobody removed is an exception that will excuse the next one.

    A stale entry here silently pre-authorises any future function that happens
    to take the same name in the same file, which is the failure mode of every
    allowlist that is only ever added to.
    """
    defined = {
        (path.relative_to(ROOT).as_posix(), node.name)
        for path in python_sources(PACKAGES, API)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    stale = sorted(f"{path} {name}" for path, name in SIMULATED_ORDER_PATH - defined)
    assert not stale, "SIMULATED_ORDER_PATH names functions that do not exist:\n" + "\n".join(
        stale
    )


def test_every_execution_adapter_reports_itself_simulated() -> None:
    """The label is on the record, not on the screen.

    A fill that reaches the book saying it came from a venue is the single most
    damaging thing this half of the application could produce, so the claim is
    checked here rather than trusted to whatever draws it.
    """
    from forge.execution.paper import PaperBroker

    broker = PaperBroker()
    assert broker.simulated is True
    assert broker.name == "paper"


def test_the_oms_will_not_route_an_order_without_gate_clearance() -> None:
    """The gate is not advisory, and the OMS is where that is enforced."""
    import tempfile
    from datetime import UTC, datetime
    from pathlib import Path as _Path

    from forge.execution.gate import ProposedOrder, Side
    from forge.execution.oms import ExecutionStore, OrderManagementSystem, OrderRefused
    from forge.execution.paper import PaperBroker

    order = ProposedOrder(
        order_id="o1",
        symbol="NQ",
        side=Side.BUY,
        quantity=1,
        reference_price=100.0,
        created_at=datetime.now(UTC),
    )
    with tempfile.TemporaryDirectory() as tmp:
        oms = OrderManagementSystem(
            ExecutionStore(_Path(tmp) / "exec.db"), PaperBroker()
        )
        with pytest.raises(OrderRefused, match="no pre-trade clearance"):
            oms.submit(order, None, reference_price=100.0)


def test_the_execution_package_places_nothing() -> None:
    """The module that owns the boundary must not cross it."""
    for path in python_sources(PACKAGES / "execution"):
        source = path.read_text(encoding="utf-8")
        assert "import requests" not in source
        assert "urllib.request" not in source
        assert "httpx" not in source


def test_live_execution_is_reported_as_unavailable() -> None:
    assert LIVE_EXECUTION_AVAILABLE is False


# ── research cannot reach execution ──────────────────────────────────────────

RESEARCH_PACKAGES = ("research", "judge", "strategy", "memory", "analytics", "provenance")


def test_research_modules_do_not_import_execution_or_risk() -> None:
    """Dependency direction. Execution may know about research; not the reverse."""
    offenders: list[str] = []
    for name in RESEARCH_PACKAGES:
        for path in python_sources(PACKAGES / name):
            source = path.read_text(encoding="utf-8")
            for banned in ("forge.execution", "forge.risk", "forge.connectors"):
                if banned in source:
                    offenders.append(f"{path.relative_to(ROOT)} imports {banned}")
    assert not offenders, "\n".join(offenders)


# ── the lifecycle graph ──────────────────────────────────────────────────────


def test_no_research_stage_reaches_an_execution_stage_directly() -> None:
    for stage in RESEARCH_STAGES:
        reachable = allowed_from(stage)
        assert not (reachable & EXECUTION_STAGES), (
            f"{stage} can move straight into execution: {reachable & EXECUTION_STAGES}"
        )


def test_approval_is_its_own_step() -> None:
    """VALIDATED cannot jump to PAPER; a human accepting evidence is recorded."""
    assert Stage.APPROVED in allowed_from(Stage.VALIDATED)
    assert Stage.PAPER not in allowed_from(Stage.VALIDATED)
    assert Stage.DEPLOYED not in allowed_from(Stage.VALIDATED)


def test_retired_is_terminal() -> None:
    assert allowed_from(Stage.RETIRED) == frozenset()


def test_every_stage_can_be_retired() -> None:
    for stage in Stage:
        if stage is not Stage.RETIRED:
            assert Stage.RETIRED in allowed_from(stage)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (Stage.DRAFT, Stage.PAPER),
        (Stage.DRAFT, Stage.DEPLOYED),
        (Stage.RESEARCHING, Stage.APPROVED),
        (Stage.VALIDATED, Stage.PAPER),
        (Stage.VALIDATING, Stage.DEPLOYED),
    ],
)
def test_illegal_transitions_are_refused(current: Stage, target: Stage) -> None:
    with pytest.raises(TransitionRefused):
        check_transition(current, target)


def test_a_transition_to_itself_is_refused() -> None:
    with pytest.raises(TransitionRefused, match="already in"):
        check_transition(Stage.PAPER, Stage.PAPER)


# ── authorization ────────────────────────────────────────────────────────────


def authorization(**overrides: object) -> Authorization:
    base: dict[str, object] = {
        "strategy_id": "demo",
        "to_stage": Stage.PAPER,
        "authorized_by": "videen",
        "verdict_id": "verdict_1",
        "code_hash": "a" * 64,
        "risk_profile_id": "risk_1",
        "note": "evidence reviewed",
    }
    return Authorization(**{**base, **overrides})  # type: ignore[arg-type]


def test_entering_execution_requires_an_authorization() -> None:
    with pytest.raises(TransitionRefused, match="requires an explicit authorization"):
        check_transition(Stage.APPROVED, Stage.PAPER)


def test_an_authorized_move_into_paper_is_allowed() -> None:
    check_transition(Stage.APPROVED, Stage.PAPER, authorization=authorization())


def test_an_authorization_for_a_different_stage_is_refused() -> None:
    """A signature for paper trading is not a signature for capital."""
    with pytest.raises(TransitionRefused, match="authorization is for"):
        check_transition(
            Stage.PAPER,
            Stage.DEPLOYED,
            authorization=authorization(to_stage=Stage.PAPER),
            live_available=True,
        )


def test_paper_to_deployed_is_a_second_authorization() -> None:
    assert requires_authorization(Stage.PAPER, Stage.DEPLOYED) is True


def test_deploying_is_refused_because_nothing_could_be_placed() -> None:
    """Refused for the true reason, not for a missing signature."""
    with pytest.raises(TransitionRefused, match="no broker connector"):
        check_transition(
            Stage.PAPER, Stage.DEPLOYED, authorization=authorization(to_stage=Stage.DEPLOYED)
        )


def test_deploying_still_needs_authorization_even_when_live_exists() -> None:
    with pytest.raises(TransitionRefused, match="requires an explicit authorization"):
        check_transition(Stage.PAPER, Stage.DEPLOYED, live_available=True)


@pytest.mark.parametrize(
    "missing", ["strategy_id", "authorized_by", "verdict_id", "code_hash", "risk_profile_id"]
)
def test_an_authorization_with_a_blank_field_is_not_an_authorization(missing: str) -> None:
    """No default author. An agent must not manufacture one by omission."""
    with pytest.raises(TransitionRefused, match=missing):
        authorization(**{missing: "   "})


def test_an_authorization_is_content_addressed() -> None:
    first, second = authorization(), authorization()
    # Timestamps differ, so the ids differ — an authorization is an event, not a
    # reusable token.
    assert first.authorization_id != second.authorization_id or first.authorized_at == (
        second.authorized_at
    )
    assert len(first.content_hash) == 64


def test_moving_back_into_research_needs_no_authorization() -> None:
    """Stopping is never the thing that needs a signature."""
    assert requires_authorization(Stage.PAPER, Stage.RETIRED) is False
    assert requires_authorization(Stage.VALIDATED, Stage.RESEARCHING) is False


def test_pausing_needs_no_authorization() -> None:
    assert requires_authorization(Stage.DEPLOYED, Stage.PAUSED) is False
    check_transition(Stage.DEPLOYED, Stage.PAUSED)


def test_resuming_into_live_does_need_one() -> None:
    assert requires_authorization(Stage.PAUSED, Stage.DEPLOYED) is True


def test_live_stages_are_exactly_deployed() -> None:
    assert frozenset({Stage.DEPLOYED}) == LIVE_STAGES
