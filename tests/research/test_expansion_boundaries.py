"""What the expansion did not reach.

This phase added a feature vocabulary, a composition grammar, a mechanism
catalogue, a claim-to-question pipeline over retrieved text, and a routing layer
that covers the research agents. Each of those is a new surface, and the
question a security regression asks is not "is the new thing safe?" but "did
adding it move any boundary that was already there?".

The boundaries in question, and why each one matters here:

* **The research layer cannot decide.** `forge.research` must not import the
  judge, the risk engine, the execution layer or the prop desk. A grammar that
  could reach the gate ladder is a grammar that could compose its way past it.
* **Retrieved text is data.** Nothing in the retrieval or synthesis path may
  execute, evaluate, import or fetch on the strength of what came back.
* **External research raises questions, not standards.** A lead may admit a
  frontier item; it may not create a family, register a template, promote a
  candidate, or appear as evidence.
* **Generated code is not trusted code.** Everything the grammar composes is
  rendered through the same exporter and passes the same static guard as
  anything else this application executes.
* **No setting is an input to permission.** Budget enforcement, routing mode
  and research depth change what is spent and what is asked; none of them
  changes what an assistant may do.
"""

from __future__ import annotations

import ast
import random
from pathlib import Path

import pytest
from forge.research.grammar import DrawConstraints, draw
from forge.research.synthesis import compose_construction
from forge.strategy.export import to_python
from forge.strategy.guard import BANNED_ATTRIBUTES, BANNED_CALLS, check_source

RESEARCH = Path("packages/forge/research")
NEW_MODULES = (
    RESEARCH / "grammar.py",
    RESEARCH / "mechanisms.py",
    RESEARCH / "leads.py",
)
PRIMITIVES = Path("packages/forge/strategy/primitives.py")


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _bare_calls(path: Path) -> set[str]:
    """Calls by bare name, which is what a dangerous builtin looks like.

    Deliberately not attribute calls: `re.compile` is a regular expression and
    `compile` is the builtin that turns a string into code, and a check that
    cannot tell them apart fails on every module that uses a regex.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            found.add(node.func.id)
    return found


def _attribute_calls(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            found.add(node.func.attr)
    return found


# ── the research layer still cannot decide ───────────────────────────────────


@pytest.mark.parametrize("path", [*NEW_MODULES, PRIMITIVES], ids=lambda p: p.name)
def test_no_new_module_reaches_a_layer_that_decides(path: Path) -> None:
    forbidden = (
        "forge.judge",
        "forge.risk",
        "forge.execution",
        "forge.propdesk",
        "forge.prop",
        "forge.portfolio",
        "forge.ledger",
        "forge.modes",
    )
    for module in _imports(path):
        assert not module.startswith(forbidden), (
            f"{path.name} imports {module}. Composing research and deciding what a "
            "result means must stay separate concerns."
        )


@pytest.mark.parametrize("path", [*NEW_MODULES, PRIMITIVES], ids=lambda p: p.name)
def test_no_new_module_reaches_the_network_the_filesystem_or_a_subprocess(
    path: Path,
) -> None:
    """A vocabulary module has no business fetching anything.

    Retrieval is `forge.research.literature`, which reaches two fixed hosts and
    follows no redirect. Nothing that *composes* should be able to reach out at
    all, and an import added in good faith is how that changes silently.
    """
    forbidden = ("httpx", "requests", "urllib", "socket", "subprocess", "os", "shutil")
    for module in _imports(path):
        assert module.split(".")[0] not in forbidden, f"{path.name} imports {module}"


@pytest.mark.parametrize("path", [*NEW_MODULES, PRIMITIVES], ids=lambda p: p.name)
def test_no_new_module_executes_anything(path: Path) -> None:
    """The whole point of a closed vocabulary is that nothing is evaluated."""
    dangerous = {"eval", "exec", "compile", "__import__", "open", "system", "popen"}
    assert not (_bare_calls(path) & dangerous), f"{path.name} calls one of {dangerous}"


def test_the_claim_pipeline_never_treats_retrieved_text_as_code() -> None:
    """An abstract is a string in a database, whatever it says.

    Checked structurally rather than by searching for substrings: the module
    uses regular expressions, and `re.compile` and the `compile` builtin are
    different things that a substring search cannot separate.
    """
    path = RESEARCH / "leads.py"
    assert not (_bare_calls(path) & {"eval", "exec", "compile", "__import__", "open"})
    assert not (_attribute_calls(path) & {"system", "popen", "run", "check_output"})
    assert not (_imports(path) & {"subprocess", "os", "importlib", "pickle"})


# ── external research raises questions, not standards ────────────────────────


def test_the_lead_pipeline_cannot_create_a_family_or_a_template() -> None:
    """A citation may raise a question. It may not build the thing that answers it."""
    imports = _imports(RESEARCH / "leads.py")
    assert not any(module.startswith("forge.strategy.families") for module in imports)
    assert not any(module.startswith("forge.strategy.authoring") for module in imports)
    assert not any(module.startswith("forge.research.promotion") for module in imports)


def test_the_lead_pipeline_cannot_reach_the_promotion_queue() -> None:
    calls = _bare_calls(RESEARCH / "leads.py") | _attribute_calls(RESEARCH / "leads.py")
    for verb in ("promote", "validate", "enqueue", "approve"):
        assert verb not in calls, f"leads.py calls {verb}"


def test_a_lead_is_labelled_an_input_on_every_row_it_produces() -> None:
    from forge.research.leads import leads_from_source
    from forge.research.literature import Claim, Source

    text = "We document significant time series momentum in intraday futures returns."
    source = Source(
        source_id="s", title="t", authors="a", source="arxiv", url="u",
        published="2024-01-01", retrieved_at="2026-09-12T00:00:00Z", abstract=text,
        claims=(Claim(text=text, start=0, end=len(text)),), relevance=0.5,
        query="q", content_level="abstract",
    )
    for lead in leads_from_source(source):
        assert lead.role == "HYPOTHESIS_INPUT"
        assert "not validation evidence" in lead.as_dict()["note"]


# ── generated is not trusted ─────────────────────────────────────────────────


def test_every_assembled_construction_passes_the_same_static_guard() -> None:
    """The guard is not relaxed for something the engine composed itself."""
    rng = random.Random(101)
    for index in range(30):
        spec = draw(rng, DrawConstraints())
        assert spec is not None
        definition = compose_construction(spec, seed=index, symbol="MNQ").definition
        check_source(to_python(definition).code)


def test_generated_code_contains_none_of_the_banned_calls_or_attributes() -> None:
    """The guard's own rules, applied to the tree rather than to the text.

    `w.opens` contains the letters of `open`, so a substring search reports a
    banned builtin in every generated module that reads an opening price.
    """
    rng = random.Random(103)
    for index in range(20):
        spec = draw(rng, DrawConstraints())
        assert spec is not None
        code = to_python(compose_construction(spec, seed=index, symbol="MNQ").definition).code
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in BANNED_CALLS, node.func.id
            if isinstance(node, ast.Attribute):
                assert node.attr not in BANNED_ATTRIBUTES, node.attr


def test_the_generated_module_imports_nothing_outside_the_allowed_set() -> None:
    from forge.strategy.guard import ALLOWED_IMPORTS

    rng = random.Random(107)
    spec = draw(rng, DrawConstraints())
    assert spec is not None
    code = to_python(compose_construction(spec, seed=1, symbol="MNQ").definition).code
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in {
                    name.split(".")[0] for name in ALLOWED_IMPORTS
                }
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] in {
                name.split(".")[0] for name in ALLOWED_IMPORTS
            }


# ── no setting is an input to permission ─────────────────────────────────────


def test_the_permission_function_reads_nothing_from_settings() -> None:
    """What an assistant may do is who, which mode, which stance, and what.

    A budget switch, a routing mode or a research depth that could widen it
    would make the settings screen a privilege escalation surface.
    """
    permissions = Path("packages/forge/modes/permissions.py")
    for module in _imports(permissions):
        assert "settings" not in module
        assert not module.startswith("forge_api")


def test_the_routing_layer_cannot_reach_permissions_or_the_action_registry() -> None:
    routing = Path("apps/api/forge_api/model_routing.py")
    for module in _imports(routing):
        assert not module.startswith(("forge.modes", "forge_api.actions", "forge.execution"))


def test_the_routing_layer_holds_no_credential() -> None:
    """It decides which model; resolving the credential is somewhere else.

    Structural, not textual: the module's prose says it holds no credential, and
    a substring search for the word would fail on the sentence saying so.
    """
    path = Path("apps/api/forge_api/model_routing.py")
    assert not (_imports(path) & {"os", "forge_api.credentials", "forge.data.live"})
    assert not (_bare_calls(path) & {"getenv", "environ"})
    assert not any(module.startswith("forge_api.opencode") for module in _imports(path))
    assert not any(module.startswith("forge_api.deepseek") for module in _imports(path))


def test_turning_budget_enforcement_off_changes_no_permission() -> None:
    """The switch lifts research ceilings. It is not an input to anything else."""
    import inspect

    from forge.modes.permissions import evaluate
    from forge_api.settings_store import BudgetSettings

    # The function's signature is the argument: it takes who, which mode, which
    # stance and which action, and there is nowhere for a budget to enter.
    parameters = set(inspect.signature(evaluate).parameters)
    assert not parameters & {"budget", "settings", "enforced", "limits"}
    off = BudgetSettings(enforced=False, model_calls_per_day=100)
    assert off.limit("model_calls_per_day") == 0
    assert off.model_calls_per_day == 100


def test_the_safety_limits_are_stated_beside_the_switch_they_do_not_depend_on() -> None:
    """An operator turning budget off has to be able to see what stays on."""
    from forge_api.settings_store import SAFETY_LIMITS, BudgetSettings

    keys = {row["key"] for row in SAFETY_LIMITS}
    assert {"model_concurrency", "provider_restriction", "permissions"} <= keys
    for row in SAFETY_LIMITS:
        assert row["label"] and row["value"] and len(row["why"]) > 20, row["key"]
    # None of them is a field on the budget, which is the structural form of
    # "these are not budget".
    assert not (keys & set(vars(BudgetSettings())))
