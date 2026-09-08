"""The strategy's trades, ready to draw on a chart.

The route this serves answers one question the application could not previously
answer at all: *show me what this strategy actually did, on the actual candles.*

Three things make that harder than it sounds, and all three are handled here
rather than in the browser.

**Bar indices are not chart timestamps.** A trade records `entry_index` into the
1-minute archive the backtest ran on. A chart showing 1-hour candles has no such
index, and a chart showing 1-minute candles has one only if it is showing the
same window. So every marker is emitted with the *timestamp* it happened at, and
the chart places it on whatever bars it is drawing. The alternative — passing
indices to the browser — breaks silently the moment someone changes timeframe,
and breaks by drawing markers in the wrong place rather than by failing.

**A ledger is not a chart's worth of data.** A sixteen-year backtest can hold
tens of thousands of trades and no chart can usefully show them at once, so the
window is served with a count of what is outside it rather than a truncated list
that looks complete.

**Regime attribution needs the bars the backtest ran on**, not the bars the
chart is drawing. Classifying on the 1-hour aggregate would produce a different
answer from the one the trade was taken under. So the classification is built
from the archive at the backtest's own resolution and cached by dataset, and the
chart's timeframe never enters into it.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

from forge.analytics.regime import (
    SHORT_LABEL,
    Basis,
    Regime,
    RegimeSeries,
    RegimeSettings,
    attribute,
    classify,
    summarise,
)

#: How many trades a single response will carry. A chart cannot usefully draw
#: more, and a caller that wants the whole ledger should page.
MAX_TRADES = 4000


class LedgerError(Exception):
    """The ledger cannot be built, and the message says why."""


class RegimeCache:
    """One regime classification per (dataset, settings), built once.

    Classifying 4.7 million bars is seconds of work. Doing it per request would
    make the trade inspector unusable, and doing it per *trade* would make it
    impossible. The cache is keyed by the settings as well as the dataset, so
    changing the classifier's parameters produces a different classification
    rather than a stale one.
    """

    def __init__(self, market: Any) -> None:
        self.market = market
        self._lock = threading.Lock()
        self._series: dict[str, tuple[RegimeSeries, list[datetime]]] = {}

    def key(self, dataset: str, settings: RegimeSettings) -> str:
        return f"{dataset}@{settings.model_dump_json()}"

    def get(
        self, dataset: str, settings: RegimeSettings, bar_limit: int | None = None
    ) -> tuple[RegimeSeries, list[datetime]]:
        cache_key = self.key(dataset, settings)
        with self._lock:
            cached = self._series.get(cache_key)
        if cached is not None:
            return cached

        bars, _ = self.market.load(dataset, limit=bar_limit)
        if not bars:
            raise LedgerError(f"no bars available for dataset '{dataset}'")
        series = classify(
            [b.high for b in bars], [b.low for b in bars], [b.close for b in bars], settings
        )
        times = [b.event_time for b in bars]
        with self._lock:
            self._series[cache_key] = (series, times)
        return series, times


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return str(value)


def marker_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The chart-ready form of a backtest's trades.

    Timestamps rather than indices, and the levels the trade ran under rather
    than levels recomputed now. A trade written before those fields existed
    reports them as null, which the chart renders as "not recorded" — never as
    zero, and never as a line drawn at the entry price.
    """
    rows: list[dict[str, Any]] = []
    for position, trade in enumerate(payload.get("trades") or []):
        if not isinstance(trade, dict):
            continue
        rows.append(
            {
                "sequence": position + 1,
                "trade_id": trade.get("trade_id"),
                "direction": trade.get("direction"),
                "side": "long" if trade.get("direction") == 1 else "short",
                "entry_time": _iso(trade.get("entry_time")),
                "exit_time": _iso(trade.get("exit_time")),
                "entry_price": trade.get("entry_price"),
                "exit_price": trade.get("exit_price"),
                "net_pnl": trade.get("net_pnl"),
                "gross_pnl": trade.get("gross_pnl"),
                "costs": trade.get("costs"),
                "bars_held": trade.get("bars_held"),
                "exit_reason": trade.get("exit_reason"),
                "mfe": trade.get("mfe"),
                "mae": trade.get("mae"),
                "stop_price": trade.get("stop_price"),
                "target_price": trade.get("target_price"),
                "trailing_stop_price": trade.get("trailing_stop_price"),
                "entry_context": trade.get("entry_context") or {},
                "entry_index": trade.get("entry_index"),
                "exit_index": trade.get("exit_index"),
                "entry_decision_index": trade.get("entry_decision_index"),
            }
        )
    return rows


def _seconds(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


class TradeLedgerService:
    """Builds the ledger view. Holds the regime cache, and nothing else."""

    def __init__(self, market: Any, store: Any, library: Any) -> None:
        self.market = market
        self.store = store
        self.library = library
        self.regimes = RegimeCache(market)

    # -- resolution -----------------------------------------------------------
    def resolve(self, strategy_id: str, backtest_id: str | None) -> dict[str, Any]:
        """The artifact to read trades from, named rather than guessed.

        Defaulting to "the latest run" is right for a first click and wrong to
        do silently, so the response always states which run it read.
        """
        if backtest_id:
            payload = self.store.load(backtest_id)
            if payload is None:
                raise LedgerError(f"no backtest '{backtest_id}'")
            if str(payload.get("strategy_id")) != strategy_id:
                raise LedgerError(
                    f"backtest '{backtest_id}' belongs to strategy "
                    f"'{payload.get('strategy_id')}', not '{strategy_id}'"
                )
            return dict(payload)
        latest = self.store.latest(strategy_id)
        if latest is None:
            raise LedgerError(
                f"strategy '{strategy_id}' has no backtest yet. Run one and its "
                "trades will appear on the chart."
            )
        return dict(latest if isinstance(latest, dict) else latest.model_dump(mode="json"))

    # -- the ledger -----------------------------------------------------------
    def ledger(
        self,
        strategy_id: str,
        *,
        backtest_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
        side: str = "all",
        outcome: str = "all",
        regime: str = "all",
        exit_reason: str = "all",
        limit: int = MAX_TRADES,
        with_regimes: bool = True,
    ) -> dict[str, Any]:
        payload = self.resolve(strategy_id, backtest_id)
        rows = marker_rows(payload)
        total = len(rows)

        marks: dict[str, Any] = {}
        regime_warning = ""
        dataset = str(payload.get("dataset_key") or "")
        if with_regimes and dataset and rows:
            try:
                series, _ = self.regimes.get(dataset, RegimeSettings())
                marks = {
                    item.trade_id: item
                    for item in attribute(_TradeShim.many(payload), series)
                }
            except Exception as exc:
                # A missing archive must cost the regime column, not the ledger.
                regime_warning = (
                    f"Regimes unavailable for dataset '{dataset}': {exc}. Trades are "
                    "shown without regime attribution rather than with a guessed one."
                )

        for row in rows:
            mark = marks.get(str(row["trade_id"]))
            row["regime"] = str(mark.entry) if mark else None
            row["regime_label"] = SHORT_LABEL.get(mark.entry, "—") if mark else None
            row["regime_exit"] = str(mark.exit) if mark else None
            row["regime_dominant"] = str(mark.dominant) if mark else None
            row["entry_volatility"] = mark.entry_volatility if mark else None
            row["entry_trend_strength"] = mark.entry_trend_strength if mark else None

        selected = [
            row
            for row in rows
            if _matches(row, start, end, side, outcome, regime, exit_reason)
        ]
        window = selected[:limit]

        return {
            "strategy_id": strategy_id,
            "backtest_id": payload.get("backtest_id"),
            "dataset_key": payload.get("dataset_key"),
            "evidence_tier": payload.get("evidence_tier"),
            "partition_name": payload.get("partition_name"),
            "calculation_version": payload.get("calculation_version"),
            "point_value": payload.get("point_value"),
            "tick_size": payload.get("tick_size"),
            "spec_hash": payload.get("spec_hash"),
            "code_hash": payload.get("code_hash"),
            "data_hash": payload.get("data_hash"),
            "parameters": payload.get("parameters") or {},
            "finished_at": payload.get("finished_at"),
            "total_trades": total,
            "matched_trades": len(selected),
            "returned_trades": len(window),
            "truncated": len(selected) > len(window),
            "trades": window,
            "filters": {
                "start": start,
                "end": end,
                "side": side,
                "outcome": outcome,
                "regime": regime,
                "exit_reason": exit_reason,
            },
            "warnings": [regime_warning] if regime_warning else [],
        }

    # -- one trade ------------------------------------------------------------
    def inspect(
        self, strategy_id: str, trade_id: str, *, backtest_id: str | None = None
    ) -> dict[str, Any]:
        """Everything known about one trade, with the provenance to check it.

        Nothing here is recomputed. Excursion, levels and the entry context all
        come out of the artifact, so what the inspector shows is what the run
        recorded and not what a later re-derivation would produce.
        """
        payload = self.resolve(strategy_id, backtest_id)
        rows = {str(row["trade_id"]): row for row in marker_rows(payload)}
        row = rows.get(trade_id)
        if row is None:
            raise LedgerError(f"no trade '{trade_id}' in backtest {payload.get('backtest_id')}")

        spec = self.library.load_spec(strategy_id) if hasattr(self.library, "load_spec") else None
        dataset = str(payload.get("dataset_key") or "")
        regime_detail: dict[str, Any] = {}
        if dataset:
            try:
                series, _ = self.regimes.get(dataset, RegimeSettings())
                marks = attribute(_TradeShim.many(payload), series, with_percentile=True)
                found = next((m for m in marks if m.trade_id == trade_id), None)
                if found is not None:
                    regime_detail = {
                        "entry": str(found.entry),
                        "entry_label": SHORT_LABEL.get(found.entry, "—"),
                        "exit": str(found.exit),
                        "dominant": str(found.dominant),
                        "dominant_share": found.dominant_share,
                        "entry_volatility": found.entry_volatility,
                        "entry_trend_strength": found.entry_trend_strength,
                        "entry_vol_percentile": found.entry_vol_percentile,
                        "settings": series.settings.model_dump(mode="json"),
                        "series_fingerprint": series.fingerprint,
                        "percentile_basis": (
                            "Descriptive: the percentile ranks this trade's entry "
                            "volatility against the whole series, which includes bars "
                            "after it. The regime label itself does not."
                        ),
                    }
            except Exception as exc:
                regime_detail = {"unavailable": str(exc)}

        holding = _seconds(str(row["exit_time"])) - _seconds(str(row["entry_time"]))
        return {
            "trade": row,
            "holding_seconds": holding,
            "regime": regime_detail,
            "strategy": {
                "strategy_id": strategy_id,
                "name": getattr(spec, "name", strategy_id),
                "family": getattr(spec, "family", None),
                "symbol": getattr(spec, "symbol", None),
                "hypothesis": getattr(spec, "hypothesis", None),
            },
            # The chain back to the evidence. Every one of these is read off the
            # artifact, so a trade can be traced to the exact run, code and data
            # that produced it.
            "provenance": {
                "backtest_id": payload.get("backtest_id"),
                "spec_hash": payload.get("spec_hash"),
                "code_hash": payload.get("code_hash"),
                "data_hash": payload.get("data_hash"),
                "dataset_key": payload.get("dataset_key"),
                "partition_name": payload.get("partition_name"),
                "evidence_tier": payload.get("evidence_tier"),
                "preregistration_hash": payload.get("preregistration_hash"),
                "parameters": payload.get("parameters") or {},
                "calculation_version": payload.get("calculation_version"),
                "finished_at": payload.get("finished_at"),
            },
        }

    # -- regime report --------------------------------------------------------
    def regime_report(
        self,
        strategy_id: str,
        *,
        backtest_id: str | None = None,
        attribution: str = "entry",
        descriptive: bool = False,
    ) -> dict[str, Any]:
        payload = self.resolve(strategy_id, backtest_id)
        dataset = str(payload.get("dataset_key") or "")
        if not dataset:
            raise LedgerError(
                "this backtest does not record which dataset it ran on, so its bars "
                "cannot be classified. Re-run it to attribute regimes."
            )
        settings = RegimeSettings(basis=Basis.FULL_SAMPLE) if descriptive else RegimeSettings()
        series, _ = self.regimes.get(dataset, settings)
        trades = _TradeShim.many(payload)
        if not trades:
            raise LedgerError("this backtest recorded no trades, so there is nothing to attribute")
        marks = attribute(trades, series)
        report = summarise(trades, marks, series, attribution=attribution)  # type: ignore[arg-type]
        return {
            **report.model_dump(mode="json"),
            "strategy_id": strategy_id,
            "backtest_id": payload.get("backtest_id"),
            "dataset_key": dataset,
        }


def _matches(
    row: dict[str, Any],
    start: str | None,
    end: str | None,
    side: str,
    outcome: str,
    regime: str,
    exit_reason: str,
) -> bool:
    if side != "all" and row["side"] != side:
        return False
    if outcome == "win" and float(row["net_pnl"] or 0) <= 0:
        return False
    if outcome == "loss" and float(row["net_pnl"] or 0) > 0:
        return False
    if regime != "all" and str(row.get("regime")) != regime:
        return False
    if exit_reason != "all" and row["exit_reason"] != exit_reason:
        return False
    if start and str(row["exit_time"]) < start:
        return False
    return not (end and str(row["entry_time"]) > end)


class _TradeShim:
    """A trade dict wearing the attribute access the analytics expect.

    The analytics take `Trade` objects; the store returns parsed JSON. Rather
    than reconstructing pydantic models for tens of thousands of trades — which
    is the expensive part of reading an artifact — this exposes exactly the four
    attributes the attribution reads.
    """

    __slots__ = ("entry_decision_index", "entry_index", "exit_index", "gross_pnl",
                 "net_pnl", "trade_id")

    def __init__(self, raw: dict[str, Any]) -> None:
        self.trade_id = str(raw.get("trade_id") or "")
        self.entry_decision_index = int(raw.get("entry_decision_index") or 0)
        self.entry_index = int(raw.get("entry_index") or 0)
        self.exit_index = int(raw.get("exit_index") or 0)
        self.net_pnl = float(raw.get("net_pnl") or 0.0)
        self.gross_pnl = float(raw.get("gross_pnl") or 0.0)

    @classmethod
    def many(cls, payload: dict[str, Any]) -> list[Any]:
        return [
            cls(trade) for trade in (payload.get("trades") or []) if isinstance(trade, dict)
        ]


__all__ = [
    "MAX_TRADES",
    "LedgerError",
    "Regime",
    "RegimeCache",
    "TradeLedgerService",
    "marker_rows",
]
