"""Startup imports: what the application is allowed to pay for before it runs.

These are regression tests for a latency budget, not style rules. Each heavy
dependency here was, at some point, imported at module scope by something on the
startup path, and each cost measurably more than the work it was imported for:

* ``scipy.stats`` — 1.08s, for two scalar functions no gate calls until a
  backtest has finished.
* ``pandas`` — 0.42s, because the API imports ``forge.data.live`` to call
  ``load_keys``, which reads two text files.

A subprocess is used rather than ``sys.modules`` inspection in-process, because
by the time pytest has collected the suite something else has already imported
both.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def imports_after(statement: str) -> set[str]:
    """Top-level modules loaded by running ``statement`` in a fresh interpreter."""
    program = textwrap.dedent(f"""
        import sys
        sys.path[:0] = [{str(ROOT / "packages")!r}, {str(ROOT / "apps" / "api")!r}]
        {statement}
        print("\\n".join(sorted({{name.split(".")[0] for name in sys.modules}})))
    """)
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    return set(completed.stdout.split())


@pytest.mark.parametrize(
    ("statement", "forbidden"),
    [
        ("import forge.judge", "scipy"),
        ("import forge.agents", "scipy"),
        ("from forge.data.live import load_keys; load_keys()", "pandas"),
        ("import forge_api.main", "scipy"),
        ("import forge_api.main", "pandas"),
    ],
)
def test_startup_does_not_pay_for_what_it_does_not_use(statement: str, forbidden: str) -> None:
    assert forbidden not in imports_after(statement), (
        f"`{statement}` imports {forbidden} at module scope. "
        f"It was deferred on purpose; see the accessor in the module that used to import it."
    )


def test_the_deferred_normal_is_scipys_own_to_the_last_bit() -> None:
    """Deferring the import must not become reimplementing the estimator.

    An approximation to the normal quantile function would change PSR and DSR
    silently, which is the exact failure these statistics exist to catch.
    """
    from forge.judge.statistics import _normal
    from scipy.stats import norm

    assert _normal() is norm


def test_the_judge_still_produces_numbers_once_scipy_loads() -> None:
    from forge.judge.statistics import expected_max_sharpe, probabilistic_sharpe_ratio

    series = tuple(float(value) for value in (80, -25, 95, -30, 70, -20, 110, -35, 60, 45))
    psr = probabilistic_sharpe_ratio(series)
    assert 0.0 <= psr <= 1.0
    assert expected_max_sharpe(1000, 0.25) > expected_max_sharpe(10, 0.25) > 0.0
