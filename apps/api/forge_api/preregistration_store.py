"""Preregistration on the operator's path, as the engine already has it.

`AutonomousEngine` freezes a claim before it reserves an experiment and
re-derives it at judge time, so G1 can genuinely fail there. The judge route a
person drives from the interface passed the literal `True`, so the *same gate*
was a measurement in one path and a rubber stamp in the other — which is worse
than either, because the verdict does not say which path produced it.

This is the missing half. The claim is frozen when a real-data backtest starts,
because the backtest is the run the claim has to precede, and re-derived when
the judge asks. They diverge if the hypothesis, the falsifiable prediction or
the parameters moved in between — the "find a good result, then rewrite the
claim" move that preregistration exists to prevent.

Records are append-only and never replaced. Overwriting one would let a second
freeze launder a moved claim into a held one, which is the failure this is
guarding against; WSB-Alpha-System raises rather than overwrite for the same
reason. A claim that has never been frozen reads as **absent**, so G1 reports
INCONCLUSIVE rather than failing a strategy for a step nobody had the chance to
take.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge.contracts.models import Preregistration

SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")

# Not the wall clock, for the same reason as the engine's version: a claim
# re-derived from the same spec and the same parameters must hash identically
# at judge time, or every candidate would fail G1 for the crime of time having
# passed. Identity is the content of the claim; *when* it was frozen is
# recorded on the row beside it.
FROZEN_AT = datetime(2026, 1, 1, tzinfo=UTC)


def freeze_claim(spec: Any, params: dict[str, float]) -> Preregistration:
    """The claim, fixed before any number exists.

    Parameters are folded into the falsification text deliberately. "This
    strategy has an edge" and "this strategy has an edge at these settings" are
    different claims, and only the second is falsifiable by the run that
    follows.
    """
    settings = ", ".join(f"{name}={params[name]}" for name in sorted(params))
    return Preregistration.freeze(
        hypothesis=spec.hypothesis,
        mechanism=f"{spec.family} family, {spec.template} template",
        falsification=f"{spec.falsifiable_prediction} Evaluated at {settings}.",
        frozen_at=FROZEN_AT,
    )


def _path(root: Path, strategy_id: str) -> Path | None:
    if not SAFE_ID.fullmatch(strategy_id):
        return None
    return root / "data" / "preregistrations" / f"{strategy_id}.json"


def _load(root: Path, strategy_id: str) -> list[dict[str, Any]]:
    path = _path(root, strategy_id)
    if path is None or not path.exists():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Unreadable evidence must read as absent, never as favourable.
        return []
    return rows if isinstance(rows, list) else []


def record_claim(root: Path, strategy_id: str, claim: Preregistration) -> bool:
    """Freeze a claim. Returns False when this exact claim was already frozen.

    Append-only: an existing hash is left exactly as it was recorded, so the
    first freeze is the one that counts.
    """
    path = _path(root, strategy_id)
    if path is None:
        return False
    rows = _load(root, strategy_id)
    if any(row.get("content_hash") == claim.content_hash for row in rows):
        return False
    rows.append(
        {
            "preregistration_id": claim.preregistration_id,
            "content_hash": claim.content_hash,
            "hypothesis": claim.hypothesis,
            "mechanism": claim.mechanism,
            "falsification": claim.falsification,
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return True


def claim_holds(root: Path, strategy_id: str, spec: Any, params: dict[str, float]) -> bool | None:
    """What G1 should read: ``True``, ``False``, or ``None`` for never frozen.

    ``False`` means a claim *was* frozen for this strategy and the current one
    does not match it — the goalposts moved. ``None`` means nothing was ever
    frozen, which is a different statement and must not be reported as a
    failure of the strategy.
    """
    rows = _load(root, strategy_id)
    if not rows:
        return None
    return any(row.get("content_hash") == freeze_claim(spec, params).content_hash for row in rows)


def claims_for(root: Path, strategy_id: str) -> list[dict[str, Any]]:
    """Every claim frozen for this strategy, for the dossier."""
    return _load(root, strategy_id)
