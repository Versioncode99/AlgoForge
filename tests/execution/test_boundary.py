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


def test_nothing_defines_an_order_submission_function() -> None:
    offenders: list[str] = []
    for path in python_sources(PACKAGES, API):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and ORDER_VERBS.match(
                node.name
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.name}")
    assert not offenders, (
        "Something now defines an order-submission function.\n" + "\n".join(offenders)
    )


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
