from __future__ import annotations

import ast

# Strategy modules are executed. Whether written by a human, a template, or later
# by an agent, they are treated as untrusted input. The cost of this check is a
# few milliseconds; the cost of skipping it is unbounded.

ALLOWED_IMPORTS = frozenset({"numpy", "math", "statistics", "forge.strategy.runtime"})

BANNED_CALLS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "input",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "breakpoint",
        "memoryview",
    }
)

BANNED_ATTRIBUTES = frozenset(
    {
        "__globals__",
        "__code__",
        "__class__",
        "__bases__",
        "__subclasses__",
        "__builtins__",
        "__dict__",
        "__mro__",
    }
)

REQUIRED_FUNCTIONS = frozenset({"entry_signal", "exit_signal"})

# A strategy's conformance suite is executed too, so it passes the same static
# check. It needs three things a strategy module does not: `datetime` to build
# fixture bars, `strategy` to import the module under test, and no obligation to
# define `entry_signal` itself.
TEST_ALLOWED_IMPORTS = ALLOWED_IMPORTS | frozenset({"datetime", "strategy"})


class GuardViolation(Exception):
    """Static analysis rejected the module. It is never executed."""


def check_source(
    source: str,
    *,
    allowed_imports: frozenset[str] = ALLOWED_IMPORTS,
    required_functions: frozenset[str] = REQUIRED_FUNCTIONS,
) -> list[str]:
    """Return a list of violations. An empty list means the module may be executed."""
    problems: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error at line {exc.lineno}: {exc.msg}"]

    defined: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in allowed_imports:
                    problems.append(f"line {node.lineno}: import of '{alias.name}' is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if node.module not in allowed_imports or node.level:
                problems.append(f"line {node.lineno}: import from '{node.module}' is not allowed")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in BANNED_CALLS:
                problems.append(f"line {node.lineno}: call to '{node.func.id}' is not allowed")
        elif isinstance(node, ast.Attribute) and (
            node.attr in BANNED_ATTRIBUTES or node.attr.startswith("_")
        ):
            problems.append(f"line {node.lineno}: attribute '{node.attr}' is not allowed")
        elif isinstance(node, ast.FunctionDef):
            defined.add(node.name)

    missing = required_functions - defined
    if missing:
        problems.append(f"missing required function(s): {', '.join(sorted(missing))}")

    return problems


def check_test_source(source: str) -> list[str]:
    """The same guard, for a conformance suite rather than a strategy.

    A suite that would be refused is never run, and a suite that is never run is
    absent evidence — which G2 reports as INCONCLUSIVE, not as a pass.
    """
    return check_source(
        source, allowed_imports=TEST_ALLOWED_IMPORTS, required_functions=frozenset()
    )


def assert_safe(source: str) -> None:
    problems = check_source(source)
    if problems:
        raise GuardViolation("; ".join(problems))
