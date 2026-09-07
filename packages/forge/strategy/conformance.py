"""Run the conformance suite that ships with every strategy.

`StrategyLibrary.create_from_template` writes a `test_strategy.py` next to every
strategy it creates, from `TEST_TEMPLATE`. That template's docstring states:

    Every strategy ships a lookahead trap. A suite without one is rejected by
    the harness, because the cheapest way to fake an edge is to read the future.

There was no harness. `get_tests()` had one caller, which served the text to the
interface for display. So 399 strategies each carried an unrun suite while gate
G2 reported "tests pass" for all of them — the same defect G1 had, in a gate
whose evidence was already sitting on disk.

This module is that harness. It produces a `ConformanceReport`, and G2 reads it.
Three properties matter:

* **A suite that cannot be run is absent evidence, not a pass.** A guard
  violation, an import error or a missing file yields ``passed is None``, which
  the judge reports as INCONCLUSIVE.
* **A suite without a lookahead trap is not conformance evidence.** The template
  promised the harness rejects one; it now does. Otherwise a strategy could
  satisfy G2 by shipping a suite that asserts nothing about the future.
* **Test code is executed, so it passes the same static guard as strategy code.**
  A hand-edited suite is untrusted input by the same argument the strategy
  itself is.

Not sandboxed beyond the static guard, and not time-limited. A suite that loops
forever hangs the caller — but so does a strategy whose ``entry_signal`` loops
forever, and that already executes on every backtest. This adds no new class of
risk; it is recorded here because "no timeout" should be a stated property
rather than a discovered one.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from forge.contracts.hashing import content_hash
from forge.strategy.guard import check_test_source

# A case name containing any of these is treated as the lookahead trap. The
# template ships `test_window_cannot_see_the_future`; the substrings are broad
# enough that a hand-written trap under a reasonable name still counts.
LOOKAHEAD_TRAP_MARKERS = ("future", "lookahead", "look_ahead", "peek")

# `sys.modules` is process-global, and the suite imports the module under test as
# plain `strategy`. Two strategies conformance-checked at once would otherwise
# see each other's code.
_IMPORT_LOCK = threading.Lock()


@dataclass(frozen=True)
class ConformanceCase:
    """One `test_*` function and what happened when it ran."""

    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class ConformanceReport:
    """The result of running a strategy's own suite against its own code.

    ``passed`` is deliberately three-valued. ``None`` means the suite could not
    be run or does not qualify as evidence, and the judge must not read that as
    a pass.
    """

    strategy_id: str
    code_hash: str
    test_hash: str
    cases: tuple[ConformanceCase, ...]
    problems: tuple[str, ...]
    error: str | None = None

    @property
    def ran(self) -> bool:
        return self.error is None and not self.problems and bool(self.cases)

    @property
    def has_lookahead_trap(self) -> bool:
        return any(
            marker in case.name.lower() for case in self.cases for marker in LOOKAHEAD_TRAP_MARKERS
        )

    @property
    def failures(self) -> tuple[ConformanceCase, ...]:
        return tuple(case for case in self.cases if not case.passed)

    @property
    def passed(self) -> bool | None:
        """``True``/``False`` when the suite is evidence, ``None`` when it is not."""
        if not self.ran:
            return None
        if not self.has_lookahead_trap:
            # The suite ran and may even be green, but it never asserts anything
            # about reading the future. Treating that as conformance would make
            # G2 satisfiable by deleting the one test that matters.
            return None
        return not self.failures

    @property
    def reason(self) -> str:
        """Why the suite is not evidence, for the gate's observed value."""
        if self.error is not None:
            return f"SUITE_ERROR: {self.error}"
        if self.problems:
            return f"GUARD_REFUSED: {'; '.join(self.problems[:3])}"
        if not self.cases:
            return "NO_TEST_CASES"
        if not self.has_lookahead_trap:
            return "NO_LOOKAHEAD_TRAP"
        if self.failures:
            return f"{len(self.failures)}_OF_{len(self.cases)}_FAILED"
        return f"{len(self.cases)}_PASSED"

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "code_hash": self.code_hash,
            "test_hash": self.test_hash,
            "passed": self.passed,
            "reason": self.reason,
            "case_count": len(self.cases),
            "failure_count": len(self.failures),
            "has_lookahead_trap": self.has_lookahead_trap,
            "cases": [case.as_dict() for case in self.cases],
            "problems": list(self.problems),
            "error": self.error,
        }


def _empty(strategy_id: str, code_hash: str, *, error: str) -> ConformanceReport:
    return ConformanceReport(
        strategy_id=strategy_id,
        code_hash=code_hash,
        test_hash="",
        cases=(),
        problems=(),
        error=error,
    )


def run_conformance(
    strategy_id: str,
    *,
    code_hash: str,
    test_source: str,
    module: ModuleType,
    test_path: Path | None = None,
) -> ConformanceReport:
    """Execute ``test_source`` against an already-loaded strategy ``module``.

    The module is passed in rather than loaded here so that the suite runs
    against exactly the code the caller is judging, not a fresh import that
    might differ.
    """
    if not test_source.strip():
        return _empty(strategy_id, code_hash, error="no conformance suite on disk")

    test_hash = content_hash({"tests": test_source})
    problems = check_test_source(test_source)
    if problems:
        return ConformanceReport(
            strategy_id=strategy_id,
            code_hash=code_hash,
            test_hash=test_hash,
            cases=(),
            problems=tuple(problems),
        )

    name = f"algoforge_conformance_{strategy_id}"
    location = test_path or Path(f"<conformance:{strategy_id}>")
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(location))
    if spec is None:
        return _empty(strategy_id, code_hash, error="cannot construct a module for the suite")
    suite = importlib.util.module_from_spec(spec)
    suite.__file__ = str(location)

    with _IMPORT_LOCK:
        previous = sys.modules.get("strategy")
        sys.modules["strategy"] = module
        sys.modules[name] = suite
        try:
            # Executing the suite is the point. `check_test_source` above is the
            # gate on what may reach this line, and it is the same static guard
            # that decides whether the strategy itself may be executed.
            exec(compile(test_source, str(location), "exec"), suite.__dict__)
        except Exception as exc:
            return ConformanceReport(
                strategy_id=strategy_id,
                code_hash=code_hash,
                test_hash=test_hash,
                cases=(),
                problems=(),
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            sys.modules.pop(name, None)
            if previous is None:
                sys.modules.pop("strategy", None)
            else:
                sys.modules["strategy"] = previous

        cases = _run_cases(suite)

    return ConformanceReport(
        strategy_id=strategy_id,
        code_hash=code_hash,
        test_hash=test_hash,
        cases=cases,
        problems=(),
    )


def _run_cases(suite: ModuleType) -> tuple[ConformanceCase, ...]:
    """Call every zero-argument ``test_*`` in definition order.

    Definition order, not alphabetical: a suite reads top to bottom and a report
    that reorders it is harder to match against the file.
    """
    names = [
        name
        for name in vars(suite)
        if name.startswith("test_") and callable(getattr(suite, name, None))
    ]
    cases: list[ConformanceCase] = []
    for name in names:
        function = getattr(suite, name)
        try:
            function()
        except AssertionError as exc:
            cases.append(ConformanceCase(name, False, str(exc) or "assertion failed"))
        except Exception as exc:
            detail = traceback.format_exception_only(type(exc), exc)[-1].strip()
            cases.append(ConformanceCase(name, False, detail))
        else:
            cases.append(ConformanceCase(name, True, "ok"))
    return tuple(cases)
