"""The research layer's authority has edges. These assert where they are.

Every claim in `docs/RISK_AUTOMATION_ARCHITECTURE.md` about what the research
fabric cannot reach is checked here rather than remembered, because the failure
mode is silent: an import added in good faith is how a scheduler that allocates
compute becomes one that can also relax a limit.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RESEARCH = Path("packages/forge/research")
DIRECTOR = Path("apps/api/forge_api/director.py")
ORCHESTRATION = RESEARCH / "orchestration.py"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_the_scheduler_reaches_nothing_that_decides() -> None:
    """A scheduler that could weigh evidence is a way to give a favoured
    campaign an easier gate."""
    forbidden = ("forge.propdesk", "forge.risk", "forge.judge", "forge.execution")
    for module in _imports(ORCHESTRATION):
        assert not module.startswith(forbidden), (
            f"orchestration.py imports {module}. Allocating compute and deciding what a "
            "result means must stay separate concerns."
        )


def test_no_research_module_can_build_a_verdict() -> None:
    """`forge.judge.statistics` is shared arithmetic and is allowed.

    `Judge` and `JudgeInput` are not: the research layer records what the judge
    said and has no business being able to construct one.
    """
    offenders: list[str] = []
    for path in [*RESEARCH.glob("*.py"), DIRECTOR]:
        source = path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if node.module == "forge.judge.statistics":
                continue  # pure arithmetic, no gate and no threshold in it
            if node.module.startswith("forge.judge"):
                names = ", ".join(a.name for a in node.names)
                offenders.append(f"{path}: from {node.module} import {names}")
    assert not offenders, "\n".join(offenders)


def test_the_research_layer_cannot_reach_execution_or_risk() -> None:
    offenders: list[str] = []
    for path in RESEARCH.glob("*.py"):
        for module in _imports(path):
            if module.startswith(("forge.propdesk", "forge.risk", "forge.execution")):
                offenders.append(f"{path}: {module}")
    assert not offenders, "\n".join(offenders)


def test_the_sidebar_cannot_reach_permissions() -> None:
    """A navigation list that could widen a capability would be a permission
    system with an "add to sidebar" button."""
    sidebar = Path("packages/forge/workstation/sidebar.py")
    for module in _imports(sidebar):
        assert not module.startswith("forge.modes.permissions"), (
            "sidebar.py imports the permission model. Putting a destination in a rail "
            "must not be able to grant access to what is behind it."
        )


@pytest.mark.parametrize(
    "path",
    [RESEARCH / "agents.py", RESEARCH / "orchestration.py", RESEARCH / "skips.py"],
)
def test_the_new_modules_stay_inside_the_research_package(path: Path) -> None:
    """Each imports only the domain contracts and its own package.

    Stated as a test because these three are the ones a future change is most
    likely to reach out of: a scheduler wants to know about accounts, an agent
    registry wants to know about orders, and neither may.
    """
    allowed = ("forge.research", "forge.contracts", "forge.memory", "forge.strategy")
    for module in _imports(path):
        if not module.startswith("forge."):
            continue
        assert module.startswith(allowed), f"{path.name} imports {module}"


def test_the_gate_ladder_is_untouched_by_this_work() -> None:
    """G0-G13 must be byte-identical to what shipped before the research fabric.

    Asserted structurally rather than by diffing a commit, so it keeps holding:
    the judge declares its gates, and this pins both the set and the count. A
    gate quietly removed, renamed or added is the one change to this system that
    must never pass unnoticed.
    """
    from forge.judge import Judge

    verdict = Judge().evaluate(_minimal_input())
    gates = [gate.gate for gate in verdict.gates]
    assert gates == [f"G{index}" for index in range(14)], gates
    # And every one carries a status the interface can tell apart. INCONCLUSIVE
    # is not FAIL: "never measured" and "measured and failed" are different
    # facts, and the whole design rests on the difference.
    assert {gate.status for gate in verdict.gates} <= {"PASS", "FAIL", "INCONCLUSIVE"}


def _minimal_input():
    from forge.judge import JudgeInput

    return JudgeInput(
        run_id="boundary-check",
        tier="SWEEP_SYNTHETIC",
        pnl=tuple(float(n % 7) - 3.0 for n in range(120)),
        trial_count=1,
        data_gate_passed=False,
        preregistered=False,
    )
