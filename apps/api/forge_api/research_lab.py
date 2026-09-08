"""Running the analyses, keeping what they produced, and getting back to trades.

Three responsibilities, deliberately separate from the analyses themselves:

* **Assemble the inputs.** An analysis takes trades and the volatility each was
  entered at. Both come out of a backtest artifact and its classified bars, and
  putting that in one place is what stops each analysis from re-deriving the
  window and getting it subtly different.

* **Keep the result.** An artifact is a durable record of a question that was
  asked and the answer it got, with the provenance to reproduce it. Kept in
  SQLite next to the other application state rather than as loose JSON, because
  a research session produces a lot of them and listing them must not cost a
  directory walk.

* **Get back to the trades.** Every cell carries the ids behind it, so clicking
  a region of a surface can open the ledger filtered to exactly those trades.
  That is the connection the brief asked for, and it is a lookup rather than a
  re-derivation: the ids in the artifact are the ids the analysis actually used,
  so a drilldown cannot drift from the picture it came from.

**An artifact is not evidence.** These analyses slice a ledger that already
exists; they do not run a strategy, consume a holdout, or produce a verdict.
That separation is why exploring is cheap and safe, and it is stated on every
stored record so a dossier can never mistake one for the other.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge.research.analyses import (
    CATALOGUE,
    AnalysisError,
    AnalysisResult,
    by_hour,
    by_volatility_percentile,
    edge_over_time,
    excursion,
    hour_by_volatility,
    make_provenance,
    worst_decile,
)

SCHEMA_VERSION = 2


class LabError(Exception):
    """The analysis could not be run. The message says why."""


class ArtifactStore:
    """Durable analysis artifacts, one row each.

    The projection columns exist so a listing is a query rather than a parse:
    the full result is a JSON blob and is only read when someone opens one.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._prepare()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=15)

    def _prepare(self) -> None:
        with closing(self._connect()) as db, db:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            if version and version != SCHEMA_VERSION:
                # These are derived records: every one can be recomputed from the
                # backtest it cites. Discarding on a shape change costs a re-run
                # of an analysis and loses no evidence, which is why this is a
                # drop rather than a migration.
                db.execute("DROP TABLE IF EXISTS artifacts")
            db.execute(
                "CREATE TABLE IF NOT EXISTS artifacts ("
                "artifact_id TEXT PRIMARY KEY, analysis TEXT NOT NULL, "
                "strategy_id TEXT NOT NULL, backtest_id TEXT NOT NULL, "
                "title TEXT NOT NULL, shape TEXT NOT NULL, "
                "content_hash TEXT NOT NULL, created_at TEXT NOT NULL, "
                "created_by TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', "
                "payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS artifacts_by_strategy "
                "ON artifacts (strategy_id, created_at DESC)"
            )
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def save(self, result: AnalysisResult, *, note: str = "") -> str:
        artifact_id = result.provenance.artifact_id
        # Identity is stored with the record rather than left to be recomputed
        # on read. A reader holding an artifact must be able to say what it is
        # without re-deriving a hash from it and hoping the derivation matches.
        body = result.model_dump(mode="json")
        body["artifact_id"] = artifact_id
        body["content_hash"] = result.content_hash
        body["is_evidence"] = False
        body["evidence_note"] = (
            "An analysis slices a ledger that already exists. It runs no strategy, "
            "consumes no holdout and produces no verdict."
        )
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR REPLACE INTO artifacts (artifact_id, analysis, strategy_id, "
                "backtest_id, title, shape, content_hash, created_at, created_by, note, "
                "payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact_id,
                    result.analysis,
                    result.provenance.strategy_id,
                    result.provenance.backtest_id,
                    result.title,
                    result.shape,
                    result.content_hash,
                    result.provenance.created_at.astimezone(UTC).isoformat(),
                    result.provenance.created_by,
                    note,
                    json.dumps(body),
                ),
            )
        return artifact_id

    def list(self, strategy_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        query = (
            "SELECT artifact_id, analysis, strategy_id, backtest_id, title, shape, "
            "content_hash, created_at, created_by, note FROM artifacts"
        )
        params: tuple[Any, ...] = ()
        if strategy_id:
            query += " WHERE strategy_id = ?"
            params = (strategy_id,)
        query += " ORDER BY created_at DESC LIMIT ?"
        params = (*params, max(1, min(int(limit), 500)))
        with closing(self._connect()) as db:
            rows = db.execute(query, params).fetchall()
        keys = (
            "artifact_id", "analysis", "strategy_id", "backtest_id", "title",
            "shape", "content_hash", "created_at", "created_by", "note",
        )
        return [dict(zip(keys, row, strict=True)) for row in rows]

    def get(self, artifact_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT payload, note FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            payload: dict[str, Any] = json.loads(row[0])
        except ValueError:
            return None
        payload["note"] = row[1]
        return payload

    def delete(self, artifact_id: str) -> bool:
        with closing(self._connect()) as db, db:
            cursor = db.execute("DELETE FROM artifacts WHERE artifact_id = ?", (artifact_id,))
        return cursor.rowcount > 0

    def count(self) -> int:
        with closing(self._connect()) as db:
            return int(db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0])


class ResearchLab:
    """Answers questions about a strategy's own trades, and keeps the answers."""

    def __init__(self, ledger: Any, store: ArtifactStore) -> None:
        self.ledger = ledger
        self.store = store

    # ── inputs ───────────────────────────────────────────────────────────────
    def _inputs(
        self, strategy_id: str, backtest_id: str | None
    ) -> tuple[dict[str, Any], list[Any], list[float | None], list[str | None], str]:
        """The trades, and the market conditions each was entered in.

        Volatility and regime come from the classified bars, aligned by
        timestamp. A run whose dataset cannot be classified still gets its
        trades — the analyses that need conditions refuse by name rather than
        running against a column of nulls.
        """
        payload = self.ledger.resolve(strategy_id, backtest_id)
        trades = _Trades.many(payload)
        if not trades:
            raise LabError(
                "this run recorded no trades, so there is nothing to analyse. A "
                "strategy that never traded is a result, but not one with a shape."
            )

        volatility: list[float | None] = [None] * len(trades)
        regimes: list[str | None] = [None] * len(trades)
        fingerprint = ""
        try:
            from forge.analytics.regime import attribute_at_times

            series, times, _ = self.ledger.classified(payload)
            marks = {
                item.trade_id: item
                for item in attribute_at_times(trades, series, times)
            }
            fingerprint = series.fingerprint
            for index, trade in enumerate(trades):
                mark = marks.get(trade.trade_id)
                if mark is not None:
                    volatility[index] = mark.entry_volatility
                    regimes[index] = str(mark.entry)
        except Exception:
            # A missing archive costs the conditions column, not the trades. The
            # analyses that require conditions refuse by name further down.
            pass
        return payload, trades, volatility, regimes, fingerprint

    # ── running ──────────────────────────────────────────────────────────────
    def run(
        self,
        analysis: str,
        strategy_id: str,
        *,
        backtest_id: str | None = None,
        measure: str = "average_trade",
        hour_bucket: int = 2,
        created_by: str = "operator",
        save: bool = True,
        note: str = "",
    ) -> dict[str, Any]:
        if analysis not in CATALOGUE:
            raise LabError(
                f"'{analysis}' is not an analysis. Available: "
                f"{', '.join(sorted(CATALOGUE))}."
            )
        if measure not in ("average_trade", "net_pnl", "win_rate", "trade_count"):
            raise LabError(
                "measure must be one of average_trade, net_pnl, win_rate, trade_count"
            )

        payload, trades, volatility, regimes, fingerprint = self._inputs(
            strategy_id, backtest_id
        )
        provenance = make_provenance(
            analysis,
            payload,
            regime_fingerprint=fingerprint,
            parameters={"measure": measure, "hour_bucket": hour_bucket},
            created_by=created_by,
        )

        needs_conditions = analysis in (
            "by_volatility_percentile", "hour_by_volatility", "worst_decile"
        )
        if needs_conditions and all(value is None for value in volatility):
            raise LabError(
                f"'{analysis}' needs the market conditions each trade was entered in, "
                "and this run's bars could not be classified. Its dataset may be "
                "missing, or the run may predate AlgoForge recording its bar range."
            )

        try:
            if analysis == "by_hour":
                result = by_hour(trades, provenance, measure=measure)
            elif analysis == "by_volatility_percentile":
                result = by_volatility_percentile(
                    trades, volatility, provenance, measure=measure
                )
            elif analysis == "hour_by_volatility":
                result = hour_by_volatility(
                    trades, volatility, provenance, measure=measure, hour_bucket=hour_bucket
                )
            elif analysis == "edge_over_time":
                result = edge_over_time(trades, provenance, measure=measure)
            elif analysis == "excursion":
                result = excursion(trades, provenance)
            else:
                result = worst_decile(trades, volatility, regimes, provenance)
        except AnalysisError as exc:
            raise LabError(str(exc)) from exc

        body = result.model_dump(mode="json")
        body["content_hash"] = result.content_hash
        body["artifact_id"] = result.provenance.artifact_id
        body["is_evidence"] = False
        body["evidence_note"] = (
            "An analysis slices a ledger that already exists. It runs no strategy, "
            "consumes no holdout and produces no verdict."
        )
        if save:
            self.store.save(result, note=note)
            body["saved"] = True
        else:
            body["saved"] = False
        return body

    # ── drilldown ────────────────────────────────────────────────────────────
    def drilldown(
        self, artifact_id: str, coords: Iterable[int], *, limit: int = 500
    ) -> dict[str, Any]:
        """The trades behind one cell of a stored artifact.

        Read from the artifact's own recorded ids rather than recomputed from
        the cell's definition. Recomputing would let the drilldown drift from
        the picture it came from — a different regime classification, a
        re-percentiled bucket — and show a set of trades that is not the set the
        number was calculated over.
        """
        artifact = self.store.get(artifact_id)
        if artifact is None:
            raise LabError(f"no analysis artifact '{artifact_id}'")
        wanted = tuple(int(value) for value in coords)
        cell = next(
            (c for c in artifact.get("cells", []) if tuple(c.get("coords", ())) == wanted),
            None,
        )
        if cell is None:
            raise LabError(
                f"artifact '{artifact_id}' has no cell at {wanted}. Its axes are "
                f"{[axis['name'] for axis in artifact.get('axes', [])]}."
            )

        provenance = artifact.get("provenance", {})
        strategy_id = str(provenance.get("strategy_id") or "")
        backtest_id = str(provenance.get("backtest_id") or "")
        ids = set(cell.get("trade_ids") or ())
        ledger = self.ledger.ledger(
            strategy_id, backtest_id=backtest_id, limit=20_000, with_regimes=True
        )
        rows = [row for row in ledger["trades"] if row["trade_id"] in ids]

        return {
            "artifact_id": artifact_id,
            "analysis": artifact.get("analysis"),
            "title": artifact.get("title"),
            "cell": {
                "coords": list(wanted),
                "labels": cell.get("labels"),
                "trade_count": cell.get("trade_count"),
                "value": cell.get("value"),
                "net_pnl": cell.get("net_pnl"),
                "win_rate": cell.get("win_rate"),
                "insufficient": cell.get("insufficient"),
            },
            "strategy_id": strategy_id,
            "backtest_id": backtest_id,
            "returned_trades": len(rows[:limit]),
            "trades": rows[:limit],
            # A cell caps how many ids it stores, so a very large bucket can hold
            # more trades than it can hand back. Said plainly rather than left to
            # be inferred from a count that does not match.
            "capped": len(ids) < int(cell.get("trade_count") or 0),
            "note": (
                f"These are the {len(ids)} trade ids the analysis recorded for this "
                "cell, not a recomputation of which trades belong in it."
            ),
        }

    def catalogue(self) -> list[dict[str, Any]]:
        return [{"key": key, **value} for key, value in sorted(CATALOGUE.items())]


class _Trades:
    """A backtest's trades with the attributes the analyses read.

    Parsed once, rather than reconstructing pydantic models for tens of
    thousands of rows — which is the expensive part of reading an artifact.
    """

    __slots__ = (
        "entry_time", "exit_time", "gross_pnl", "mae", "mfe", "net_pnl", "trade_id",
    )

    def __init__(self, raw: dict[str, Any]) -> None:
        self.trade_id = str(raw.get("trade_id") or "")
        self.net_pnl = float(raw.get("net_pnl") or 0.0)
        self.gross_pnl = float(raw.get("gross_pnl") or 0.0)
        self.mfe = raw.get("mfe")
        self.mae = raw.get("mae")
        self.entry_time = _parse(raw.get("entry_time"))
        self.exit_time = _parse(raw.get("exit_time"))

    @classmethod
    def many(cls, payload: dict[str, Any]) -> list[Any]:
        out: list[Any] = []
        for raw in payload.get("trades") or []:
            if not isinstance(raw, dict):
                continue
            item = cls(raw)
            # A trade without a usable timestamp cannot be placed in time, and
            # every analysis here is a slice through time or conditions. Dropped
            # rather than bucketed at the epoch.
            if item.entry_time is not None and item.exit_time is not None:
                out.append(item)
        return out


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


__all__ = ["ArtifactStore", "LabError", "ResearchLab"]
