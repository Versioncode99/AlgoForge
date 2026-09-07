"""Persisted conformance evidence, keyed to the code it was produced against.

G2 asks whether a strategy's own implementation tests pass. Running them is
cheap, but not free, and the judge runs on a request path — so the report is
stored and re-read rather than recomputed on every verdict.

The store is deliberately strict about staleness. A report is keyed by the
strategy's `code_hash`, and a report whose hash does not match the code being
judged is treated as **absent**, not as evidence. The alternative — trusting a
report produced against different source — is precisely how a gate stops
measuring anything.

This mirrors `judge_evidence` in `forge_api.strategies`, which applies the same
rule to validation evidence, for the same reason.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from forge.contracts.hashing import content_hash
from forge.strategy import ConformanceReport, StrategyLibrary, run_conformance

SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")


def _path(root: Path, strategy_id: str) -> Path | None:
    if not SAFE_ID.fullmatch(strategy_id):
        return None
    return root / "data" / "conformance" / f"{strategy_id}.json"


def store_conformance(root: Path, report: ConformanceReport) -> Path | None:
    path = _path(root, report.strategy_id)
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **report.as_dict(),
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_conformance(root: Path, strategy_id: str) -> dict[str, Any] | None:
    """The stored report, or None when it was never run or will not decode."""
    path = _path(root, strategy_id)
    if path is None or not path.exists():
        return None
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Unreadable evidence must read as absent, never as favourable.
        return None
    return payload


def suite_hash(test_source: str) -> str:
    """Identity of a conformance suite, matching what the report records.

    Not named ``test_hash_of``: pytest collects anything starting with ``test_``
    from any module it imports, so that name made the helper itself get
    collected and fail as a test with a missing fixture.
    """
    return content_hash({"tests": test_source})


def conformance_verdict(
    root: Path, strategy_id: str, *, code_hash: str, test_hash: str = ""
) -> bool | None:
    """What G2 should read: ``True``, ``False``, or ``None`` for absent.

    ``None`` covers every way the evidence can fail to exist — never run, run
    against different source, run against a *different suite*, refused by the
    guard, or a suite with no lookahead trap. The judge turns all of them into
    INCONCLUSIVE, which is the honest answer to "did the implementation tests
    pass?" when nobody ran them.

    Both hashes are checked, and the second one is not redundant. Keying on
    ``code_hash`` alone meant the suite could be gutted — the lookahead trap
    deleted outright — without changing the strategy's code hash, so the stored
    PASS stayed valid for a suite that no longer existed. The evidence is about
    a *pair*: this code, checked by these tests.
    """
    payload = load_conformance(root, strategy_id)
    if payload is None:
        return None
    if code_hash and payload.get("code_hash") != code_hash:
        return None
    if test_hash and payload.get("test_hash") != test_hash:
        return None
    passed = payload.get("passed")
    return passed if isinstance(passed, bool) else None


def refresh_conformance(
    root: Path,
    library: StrategyLibrary,
    strategy_id: str,
    *,
    module: ModuleType,
    code_hash: str,
) -> ConformanceReport:
    """Run the suite against ``module`` and persist the result."""
    report = run_conformance(
        strategy_id,
        code_hash=code_hash,
        test_source=library.get_tests(strategy_id),
        module=module,
        test_path=library.test_path(strategy_id),
    )
    store_conformance(root, report)
    return report


def ensure_conformance(
    root: Path,
    library: StrategyLibrary,
    strategy_id: str,
    *,
    module: ModuleType,
    code_hash: str,
) -> bool | None:
    """Read the stored verdict, running the suite once if it is missing or stale.

    Called from the judge routes. Running it here rather than demanding a
    separate operator step is what stops G2 quietly reporting INCONCLUSIVE for
    every strategy that was created before the harness existed.

    Staleness in *either* the code or the suite re-runs it, so editing the test
    file re-measures rather than reusing a verdict about a different suite.
    """
    existing = conformance_verdict(
        root,
        strategy_id,
        code_hash=code_hash,
        test_hash=suite_hash(library.get_tests(strategy_id)),
    )
    if existing is not None:
        return existing
    return refresh_conformance(
        root, library, strategy_id, module=module, code_hash=code_hash
    ).passed
