"""What each model actually did, and what that suggests -- never what it changes.

D1 §24 asks for routing that can be learned from and cannot quietly learn:

    "Record: task, role, model, provider, result, latency, quality, downstream
    outcome. Then use those observations to recommend routing changes. Do not
    silently change production routing based on a handful of observations."

Three properties carry that, and each one is a refusal:

**Nothing here changes routing.** There is no code path from an observation to a
setting. A recommendation is a row on a screen with a button an operator
presses; the alternative is a system that rewrites its own configuration from
its own scorekeeping, which is exactly the opaque self-modification the
directive rules out.

**A handful of observations recommends nothing.** Below `MINIMUM_OBSERVATIONS`
per model the comparison is withheld rather than made weakly -- and the row says
how many more are needed, because "not enough evidence" is only useful if it
says how much would be enough.

**Quality and downstream outcome are recorded as unknown when they are.** They
arrive later than the call, and often not at all. Defaulting them to a neutral
score would put a number nobody measured into the arithmetic that produces the
recommendation.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

Result = Literal["ok", "refused", "failed"]

#: How many calls one model must have made *for one role* before it may be
#: compared with another. Deliberately not small: the directive's own words are
#: "do not silently change production routing based on a handful of
#: observations", and a handful is roughly this many.
MINIMUM_OBSERVATIONS = 20

#: How much better a challenger's success rate must be before it is worth
#: recommending a change. A change that buys two percentage points is churn:
#: the operator re-reads their settings, re-forms a mental model, and gets
#: noise. Ten points is a difference somebody would act on.
MATERIAL_MARGIN = 0.10

#: What is stored. Recorded here so a reader of the schema sees the directive's
#: list rather than having to reconstruct it from columns.
FIELDS = (
    "task",
    "role",
    "model",
    "provider",
    "result",
    "latency_ms",
    "quality",
    "downstream",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class RoutingObservations:
    """One row per model call, and the recommendations they support."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with closing(self._connect()) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    observation_id TEXT PRIMARY KEY,
                    task TEXT NOT NULL,
                    role TEXT NOT NULL,
                    model TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    result TEXT NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    quality TEXT NOT NULL DEFAULT '',
                    downstream TEXT NOT NULL DEFAULT '',
                    at TEXT NOT NULL
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS obs_role ON observations (role, model)")
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        return db

    def record(
        self,
        *,
        task: str,
        role: str,
        model: str,
        provider: str,
        result: Result,
        latency_ms: int,
        quality: str = "",
        downstream: str = "",
    ) -> str:
        """Write one call down. Empty `quality`/`downstream` mean *not known*."""
        observation_id = f"obs_{uuid.uuid4().hex[:16]}"
        with self._lock, closing(self._connect()) as db:
            db.execute(
                """
                INSERT INTO observations
                    (observation_id, task, role, model, provider, result, latency_ms,
                     quality, downstream, at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    observation_id,
                    task[:200],
                    role[:80],
                    model[:120],
                    provider[:80],
                    result,
                    max(0, int(latency_ms)),
                    quality[:80],
                    downstream[:200],
                    _now(),
                ),
            )
            db.commit()
        return observation_id

    def attach_outcome(self, observation_id: str, *, downstream: str, quality: str = "") -> bool:
        """Fill in what only became known later. Never overwrites a known value."""
        with self._lock, closing(self._connect()) as db:
            cursor = db.execute(
                """
                UPDATE observations
                   SET downstream = CASE WHEN downstream = '' THEN ? ELSE downstream END,
                       quality = CASE WHEN quality = '' AND ? != '' THEN ? ELSE quality END
                 WHERE observation_id = ?
                """,
                (downstream[:200], quality[:80], quality[:80], observation_id),
            )
            db.commit()
            return cursor.rowcount > 0

    def rows(self, *, role: str = "", limit: int = 500) -> list[dict[str, Any]]:
        query = "SELECT * FROM observations"
        args: tuple[Any, ...] = ()
        if role:
            query += " WHERE role = ?"
            args = (role,)
        query += " ORDER BY at DESC, rowid DESC LIMIT ?"
        with closing(self._connect()) as db:
            return [dict(row) for row in db.execute(query, (*args, max(1, int(limit)))).fetchall()]

    def summary(self) -> list[dict[str, Any]]:
        """Per role and model: how many calls, how they went, how slow."""
        with closing(self._connect()) as db:
            rows = db.execute(
                """
                SELECT role, model, provider,
                       COUNT(*) AS calls,
                       SUM(result = 'ok') AS ok,
                       SUM(result = 'refused') AS refused,
                       SUM(result = 'failed') AS failed,
                       AVG(latency_ms) AS latency_ms,
                       SUM(downstream != '') AS with_outcome
                  FROM observations
              GROUP BY role, model, provider
              ORDER BY role, calls DESC
                """
            ).fetchall()
        return [
            {
                "role": row["role"],
                "model": row["model"],
                "provider": row["provider"],
                "calls": int(row["calls"]),
                "ok": int(row["ok"] or 0),
                "refused": int(row["refused"] or 0),
                "failed": int(row["failed"] or 0),
                "success_rate": round((row["ok"] or 0) / row["calls"], 4),
                "latency_ms": round(float(row["latency_ms"] or 0), 1),
                "with_outcome": int(row["with_outcome"] or 0),
            }
            for row in rows
        ]


@dataclass(frozen=True)
class Recommendation:
    """A suggestion, and everything needed to disagree with it."""

    role: str
    assigned: str
    suggested: str
    #: Why, in a sentence an operator can check against the rows.
    reason: str
    #: True when there is enough evidence to say anything at all.
    actionable: bool
    assigned_calls: int = 0
    suggested_calls: int = 0
    assigned_rate: float = 0.0
    suggested_rate: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "assigned": self.assigned,
            "suggested": self.suggested,
            "reason": self.reason,
            "actionable": self.actionable,
            "assigned_calls": self.assigned_calls,
            "suggested_calls": self.suggested_calls,
            "assigned_rate": round(self.assigned_rate, 4),
            "suggested_rate": round(self.suggested_rate, 4),
        }


def recommend(
    summary: Sequence[dict[str, Any]],
    assigned: dict[str, str],
    *,
    minimum: int = MINIMUM_OBSERVATIONS,
    margin: float = MATERIAL_MARGIN,
) -> list[Recommendation]:
    """What the observations suggest, per role. Applies nothing.

    A role appears in the output only when it has an assigned model. Where the
    evidence is too thin the row still appears, marked not actionable and saying
    how many more calls would settle it -- an absent row reads as "nothing to
    see", which is a different claim from "not enough to say yet".
    """
    by_role: dict[str, list[dict[str, Any]]] = {}
    for row in summary:
        by_role.setdefault(str(row["role"]), []).append(row)

    out: list[Recommendation] = []
    for role, model in sorted(assigned.items()):
        if not model:
            continue
        rows = by_role.get(role, [])
        current = next((row for row in rows if row["model"] == model), None)
        eligible = [row for row in rows if int(row["calls"]) >= minimum]

        if current is None or int(current["calls"]) < minimum:
            seen = 0 if current is None else int(current["calls"])
            out.append(
                Recommendation(
                    role=role,
                    assigned=model,
                    suggested=model,
                    reason=(
                        f"{seen} of {minimum} observations for {model} on this role. "
                        f"{minimum - seen} more before a comparison would mean anything."
                    ),
                    actionable=False,
                    assigned_calls=seen,
                    assigned_rate=float(current["success_rate"]) if current else 0.0,
                )
            )
            continue

        others = [row for row in eligible if row["model"] != model]
        if not others:
            out.append(
                Recommendation(
                    role=role,
                    assigned=model,
                    suggested=model,
                    reason=(
                        f"No other model has {minimum} observations on this role, so there is "
                        "nothing to compare against. Keeping the assigned one."
                    ),
                    actionable=False,
                    assigned_calls=int(current["calls"]),
                    assigned_rate=float(current["success_rate"]),
                )
            )
            continue

        best = max(others, key=lambda row: (float(row["success_rate"]), -float(row["latency_ms"])))
        lift = float(best["success_rate"]) - float(current["success_rate"])
        if lift >= margin:
            out.append(
                Recommendation(
                    role=role,
                    assigned=model,
                    suggested=str(best["model"]),
                    reason=(
                        f"{best['model']} succeeded on {best['success_rate']:.0%} of "
                        f"{best['calls']} calls against {current['success_rate']:.0%} of "
                        f"{current['calls']} for {model}. Change it if you agree; nothing "
                        "here changes it for you."
                    ),
                    actionable=True,
                    assigned_calls=int(current["calls"]),
                    suggested_calls=int(best["calls"]),
                    assigned_rate=float(current["success_rate"]),
                    suggested_rate=float(best["success_rate"]),
                )
            )
            continue

        out.append(
            Recommendation(
                role=role,
                assigned=model,
                suggested=model,
                reason=(
                    f"The best alternative is {best['model']} at {best['success_rate']:.0%} "
                    f"against {current['success_rate']:.0%}, inside the {margin:.0%} margin "
                    "worth changing a setting for."
                ),
                actionable=False,
                assigned_calls=int(current["calls"]),
                suggested_calls=int(best["calls"]),
                assigned_rate=float(current["success_rate"]),
                suggested_rate=float(best["success_rate"]),
            )
        )
    return out
