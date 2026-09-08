"""The questions AlgoForge can answer about a strategy's own trades.

The brief for this asked for an agent that understands a research question,
writes executable code, runs it, and produces artifacts. This module is the
half of that which is not a sandbox.

**Why a bounded set rather than generated code.** The same reason the action
registry is bounded and the panel kinds are closed: everything an agent can do
here is something a person can do, and something that can be checked. An
analysis that generated and executed arbitrary Python would be a capability with
no upper bound on what it computes, no way to test the thing it computed, and no
way to say afterwards what was measured. What is here instead is a set of real,
parameterised computations over real trades — each of which reports its own
sample sizes, refuses to estimate from too few observations, and carries the
provenance needed to reproduce it.

**Every cell carries the trades behind it.** That is what makes an analysis a
way *into* the evidence rather than a picture of it: "expectancy collapses above
the 90th volatility percentile" is a claim, and the 183 trades that claim rests
on are attached to it, so the claim can be opened and read.

**Nothing here re-runs a backtest.** These analyses slice a ledger that already
exists, which is what makes them cheap enough to ask casually and, more
importantly, what stops research exploration from quietly consuming compute or
touching a holdout. An analysis is not evidence; it is a way of looking at
evidence that was already produced.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

import numpy as np
from pydantic import Field

from forge.contracts.hashing import content_hash, stable_id
from forge.contracts.models import FrozenModel

ANALYSIS_VERSION = "1"

#: A bucket with fewer trades than this gets its statistics reported and marked
#: as not an estimate. Stated as a constant so the threshold is one number a
#: reader can disagree with, rather than a scattering of magic numbers.
MIN_TRADES_PER_BUCKET = 20


class AnalysisError(ValueError):
    """The analysis cannot be run, and the message says why."""


class Axis(FrozenModel):
    """One dimension of a result, and what its bucket labels mean."""

    name: str
    label: str
    #: Ordered bucket labels. A surface's X and Y are these, in this order.
    categories: tuple[str, ...]
    unit: str = ""
    note: str = ""


class Cell(FrozenModel):
    """One bucket: what was measured, from how many trades, and which ones.

    `trade_ids` is the drilldown. It is the whole reason an analysis is worth
    more than a screenshot: a cell that says expectancy is -$47 can be opened,
    and the trades that produced it read one at a time.
    """

    #: Index into each axis's `categories`, in axis order.
    coords: tuple[int, ...]
    #: Human-readable position, e.g. ("14:00", "P90-P100").
    labels: tuple[str, ...]
    trade_count: int
    #: The measured quantity. None when the bucket held no trades — which is a
    #: different statement from zero, and must not be drawn as a zero.
    value: float | None
    net_pnl: float
    win_rate: float | None
    average_trade: float | None
    #: True when the bucket is too small for its rates to estimate anything.
    insufficient: bool
    trade_ids: tuple[str, ...] = ()


class AnalysisProvenance(FrozenModel):
    """Everything needed to say where a number came from.

    A result without this is a picture. With it, an artifact can be traced to
    the run, the code, the data and the parameters that produced it, and a
    second person can obtain the same numbers.
    """

    analysis: str
    analysis_version: str = ANALYSIS_VERSION
    strategy_id: str = ""
    backtest_id: str = ""
    dataset_key: str = ""
    spec_hash: str = ""
    code_hash: str = ""
    data_hash: str = ""
    evidence_tier: str = ""
    partition_name: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    regime_fingerprint: str = ""
    seed: int | None = None
    created_at: datetime
    created_by: str = "operator"

    @property
    def artifact_id(self) -> str:
        return stable_id("analysis", self.model_dump(mode="json"))


class AnalysisResult(FrozenModel):
    """One answered question."""

    analysis: str
    title: str
    question: str
    #: How the result wants to be drawn. The renderer decides, but a surface
    #: with one axis is a bar chart and saying so here keeps that decision in
    #: one place.
    shape: Literal["bars", "grid", "surface", "scatter", "series"]
    axes: tuple[Axis, ...]
    #: What `Cell.value` measures, so an axis label can be written once.
    measure: str
    measure_unit: str
    cells: tuple[Cell, ...]
    total_trades: int
    covered_trades: int
    warnings: tuple[str, ...] = ()
    findings: tuple[str, ...] = ()
    provenance: AnalysisProvenance

    @property
    def content_hash(self) -> str:
        """Identity of this result, over inputs and outputs together."""
        return content_hash(
            {
                "provenance": self.provenance.model_dump(mode="json"),
                "cells": [cell.model_dump(mode="json") for cell in self.cells],
            }
        )


# ── helpers ──────────────────────────────────────────────────────────────────


def _stats(trades: Sequence[Any]) -> tuple[float, float | None, float | None]:
    """Net, win rate and average trade for a bucket. None where undefined."""
    if not trades:
        return 0.0, None, None
    values = [float(t.net_pnl) for t in trades]
    net = float(sum(values))
    wins = sum(1 for v in values if v > 0)
    return round(net, 4), round(wins / len(values), 4), round(net / len(values), 4)


def _cell(
    coords: tuple[int, ...],
    labels: tuple[str, ...],
    trades: Sequence[Any],
    *,
    measure: str = "average_trade",
    keep_ids: int = 400,
) -> Cell:
    net, win_rate, average = _stats(trades)
    value: float | None
    if not trades:
        value = None
    elif measure == "net_pnl":
        value = net
    elif measure == "win_rate":
        value = win_rate
    elif measure == "trade_count":
        value = float(len(trades))
    else:
        value = average
    return Cell(
        coords=coords,
        labels=labels,
        trade_count=len(trades),
        value=value,
        net_pnl=net,
        win_rate=win_rate,
        average_trade=average,
        insufficient=0 < len(trades) < MIN_TRADES_PER_BUCKET,
        # Capped: a cell holding forty thousand trades does not need to carry
        # them all to be openable, and the count is exact regardless.
        trade_ids=tuple(str(t.trade_id) for t in trades[:keep_ids]),
    )


def _thin_warning(cells: Sequence[Cell]) -> str:
    thin = [c for c in cells if c.insufficient]
    if not thin:
        return ""
    return (
        f"{len(thin)} of {len(cells)} buckets hold fewer than {MIN_TRADES_PER_BUCKET} "
        "trades. Their P&L is what happened; their rates are not an estimate of "
        "anything, and they are marked rather than hidden."
    )


def _empty_warning(cells: Sequence[Cell]) -> str:
    empty = [c for c in cells if c.trade_count == 0]
    if not empty:
        return ""
    return (
        f"{len(empty)} bucket(s) held no trades at all. They are reported as absent "
        "rather than as zero: the strategy never went there, which is a different "
        "statement from going there and breaking even."
    )


# ── the analyses ─────────────────────────────────────────────────────────────

HOURS = tuple(f"{h:02d}:00" for h in range(24))
PERCENTILE_BANDS = (
    "P0-P10", "P10-P20", "P20-P30", "P30-P40", "P40-P50",
    "P50-P60", "P60-P70", "P70-P80", "P80-P90", "P90-P100",
)


def by_hour(
    trades: Sequence[Any], provenance: AnalysisProvenance, *, measure: str = "average_trade"
) -> AnalysisResult:
    """Expectancy by hour of day, UTC.

    The hour is taken from the *entry* time, which is a choice: a trade opened
    at 15:59 and closed at 04:00 belongs to neither hour cleanly, and attributing
    it to the decision is at least attributing it to something the strategy did.
    """
    buckets: dict[int, list[Any]] = {h: [] for h in range(24)}
    for trade in trades:
        buckets[trade.entry_time.hour].append(trade)

    cells = tuple(
        _cell((hour,), (HOURS[hour],), buckets[hour], measure=measure) for hour in range(24)
    )
    populated = [c for c in cells if c.trade_count >= MIN_TRADES_PER_BUCKET]
    findings: list[str] = []
    if len(populated) >= 3:
        best = max(populated, key=lambda c: c.average_trade or 0.0)
        worst = min(populated, key=lambda c: c.average_trade or 0.0)
        findings.append(
            f"Best hour {best.labels[0]} at {best.average_trade:+,.2f} per trade over "
            f"{best.trade_count} trades; worst {worst.labels[0]} at "
            f"{worst.average_trade:+,.2f} over {worst.trade_count}."
        )
        traded = sum(c.trade_count for c in cells)
        top = sorted(cells, key=lambda c: c.trade_count, reverse=True)[:3]
        share = sum(c.trade_count for c in top) / traded if traded else 0.0
        if share > 0.6:
            findings.append(
                f"{share:.0%} of trades fall in three hours "
                f"({', '.join(c.labels[0] for c in top)}). Read the other hours as "
                "sparse rather than as evidence about them."
            )

    return AnalysisResult(
        analysis="by_hour",
        title="Expectancy by hour of day",
        question="Does this strategy's edge depend on when in the day it trades?",
        shape="bars",
        axes=(
            Axis(
                name="hour",
                label="Hour of day (UTC)",
                categories=HOURS,
                note="Taken from the entry timestamp, which is UTC on every archive here.",
            ),
        ),
        measure=measure,
        measure_unit="currency per trade" if measure == "average_trade" else measure,
        cells=cells,
        total_trades=len(trades),
        covered_trades=sum(c.trade_count for c in cells),
        warnings=tuple(w for w in (_thin_warning(cells), _empty_warning(cells)) if w),
        findings=tuple(findings),
        provenance=provenance,
    )


def by_volatility_percentile(
    trades: Sequence[Any],
    volatility: Sequence[float | None],
    provenance: AnalysisProvenance,
    *,
    measure: str = "average_trade",
) -> AnalysisResult:
    """Expectancy by the volatility percentile the trade was entered in.

    **The percentile is descriptive and the result says so.** Ranking a trade's
    entry volatility against the whole run's distribution uses observations that
    came after it. That is fine for slicing results already produced — which is
    all this does — and would not be fine inside a trading rule. The warning is
    attached to every result rather than left to the reader to remember.
    """
    paired = [
        (trade, value)
        for trade, value in zip(trades, volatility, strict=True)
        if value is not None and value == value
    ]
    if not paired:
        raise AnalysisError(
            "no trade has a recorded entry volatility, so there is nothing to rank "
            "them by. This needs a run whose dataset could be classified."
        )
    values = np.array([value for _, value in paired], dtype=np.float64)
    edges = np.percentile(values, np.arange(10, 100, 10))
    # `np.digitize` with right=False puts a value equal to an edge in the upper
    # band, which is the convention the labels describe.
    bands = np.digitize(values, edges)

    buckets: dict[int, list[Any]] = {index: [] for index in range(10)}
    for (trade, _), band in zip(paired, bands, strict=True):
        buckets[int(band)].append(trade)

    cells = tuple(
        _cell((index,), (PERCENTILE_BANDS[index],), buckets[index], measure=measure)
        for index in range(10)
    )
    findings: list[str] = []
    populated = [c for c in cells if c.trade_count >= MIN_TRADES_PER_BUCKET]
    if len(populated) >= 4:
        top = populated[-1]
        rest = [c for c in populated[:-1]]
        rest_avg = (
            sum((c.average_trade or 0.0) * c.trade_count for c in rest)
            / max(1, sum(c.trade_count for c in rest))
        )
        if top.average_trade is not None and top.average_trade < 0 <= rest_avg:
            findings.append(
                f"Expectancy is {top.average_trade:+,.2f} in the highest volatility "
                f"decile ({top.trade_count} trades) against {rest_avg:+,.2f} below it. "
                "The edge does not survive the top decile."
            )
        best = max(populated, key=lambda c: c.average_trade or 0.0)
        findings.append(
            f"Best band {best.labels[0]} at {best.average_trade:+,.2f} per trade over "
            f"{best.trade_count} trades."
        )

    return AnalysisResult(
        analysis="by_volatility_percentile",
        title="Expectancy by volatility percentile",
        question="Does this strategy's edge depend on how volatile the market is?",
        shape="bars",
        axes=(
            Axis(
                name="percentile",
                label="Entry volatility percentile",
                categories=PERCENTILE_BANDS,
                unit="percentile of this run's entry volatilities",
            ),
        ),
        measure=measure,
        measure_unit="currency per trade" if measure == "average_trade" else measure,
        cells=cells,
        total_trades=len(trades),
        covered_trades=sum(c.trade_count for c in cells),
        warnings=(
            (
                "Percentiles rank each trade's entry volatility against the whole "
                "run, which includes observations later than the trade. Descriptive "
                "slicing of results already produced — not a rule a strategy could "
                "have followed."
            ),
            *(w for w in (_thin_warning(cells), _empty_warning(cells)) if w),
        ),
        findings=tuple(findings),
        provenance=provenance,
    )


def hour_by_volatility(
    trades: Sequence[Any],
    volatility: Sequence[float | None],
    provenance: AnalysisProvenance,
    *,
    measure: str = "average_trade",
    hour_bucket: int = 2,
) -> AnalysisResult:
    """Expectancy as a surface over time of day and volatility.

    Two axes because the interesting question is usually the interaction: a
    strategy can be fine in the morning and fine in high volatility and lose
    money in high-volatility mornings, and neither one-dimensional cut shows it.

    Hours are grouped in pairs by default. Twenty-four by ten is 240 buckets and
    a few hundred trades, which is a surface made almost entirely of noise.
    """
    if hour_bucket not in (1, 2, 3, 4, 6):
        raise AnalysisError("hour_bucket must divide 24: one of 1, 2, 3, 4, 6")
    paired = [
        (trade, value)
        for trade, value in zip(trades, volatility, strict=True)
        if value is not None and value == value
    ]
    if not paired:
        raise AnalysisError("no trade has a recorded entry volatility to rank")

    values = np.array([value for _, value in paired], dtype=np.float64)
    # Five bands rather than ten: a two-dimensional cut divides the sample twice
    # and ten by twelve would leave single-digit counts almost everywhere.
    edges = np.percentile(values, [20, 40, 60, 80])
    bands = np.digitize(values, edges)
    band_labels = ("P0-P20", "P20-P40", "P40-P60", "P60-P80", "P80-P100")

    slots = 24 // hour_bucket
    hour_labels = tuple(
        f"{i * hour_bucket:02d}-{(i + 1) * hour_bucket:02d}" for i in range(slots)
    )

    buckets: dict[tuple[int, int], list[Any]] = {
        (h, b): [] for h in range(slots) for b in range(5)
    }
    for (trade, _), band in zip(paired, bands, strict=True):
        buckets[(trade.entry_time.hour // hour_bucket, int(band))].append(trade)

    cells = tuple(
        _cell(
            (hour, band),
            (hour_labels[hour], band_labels[band]),
            buckets[(hour, band)],
            measure=measure,
            keep_ids=200,
        )
        for hour in range(slots)
        for band in range(5)
    )

    findings: list[str] = []
    populated = [c for c in cells if c.trade_count >= MIN_TRADES_PER_BUCKET]
    if populated:
        worst = min(populated, key=lambda c: c.average_trade or 0.0)
        best = max(populated, key=lambda c: c.average_trade or 0.0)
        findings.append(
            f"Worst region {worst.labels[0]} UTC at {worst.labels[1]} volatility: "
            f"{worst.average_trade:+,.2f} per trade over {worst.trade_count} trades."
        )
        findings.append(
            f"Best region {best.labels[0]} UTC at {best.labels[1]} volatility: "
            f"{best.average_trade:+,.2f} per trade over {best.trade_count} trades."
        )
    findings.append(
        f"{len(populated)} of {len(cells)} regions hold enough trades to estimate from."
    )

    return AnalysisResult(
        analysis="hour_by_volatility",
        title="Expectancy surface: time of day against volatility",
        question=(
            "Is there a combination of session time and volatility where this "
            "strategy's edge lives, or dies?"
        ),
        shape="surface",
        axes=(
            Axis(
                name="hour",
                label="Hour of day (UTC)",
                categories=hour_labels,
                note=f"Grouped in {hour_bucket}-hour slots.",
            ),
            Axis(
                name="volatility",
                label="Entry volatility band",
                categories=band_labels,
                unit="percentile of this run's entry volatilities",
            ),
        ),
        measure=measure,
        measure_unit="currency per trade" if measure == "average_trade" else measure,
        cells=cells,
        total_trades=len(trades),
        covered_trades=sum(c.trade_count for c in cells),
        warnings=(
            (
                "Volatility bands rank each trade against the whole run, which "
                "includes later observations. Descriptive only."
            ),
            *(w for w in (_thin_warning(cells), _empty_warning(cells)) if w),
        ),
        findings=tuple(findings),
        provenance=provenance,
    )


def edge_over_time(
    trades: Sequence[Any], provenance: AnalysisProvenance, *, measure: str = "average_trade"
) -> AnalysisResult:
    """Expectancy by calendar month.

    The question this answers is "did the edge stop working?", and the honest
    caveat is that a month is a small sample: a strategy taking four hundred
    trades a year has thirty a month, which is enough to see a collapse and not
    enough to see a decline.
    """
    buckets: dict[str, list[Any]] = {}
    for trade in trades:
        buckets.setdefault(trade.entry_time.strftime("%Y-%m"), []).append(trade)
    if not buckets:
        raise AnalysisError("this run recorded no trades, so there is no series to plot")

    months = tuple(sorted(buckets))
    cells = tuple(
        _cell((index,), (month,), buckets[month], measure=measure)
        for index, month in enumerate(months)
    )
    findings: list[str] = []
    populated = [c for c in cells if c.trade_count >= MIN_TRADES_PER_BUCKET]
    if len(populated) >= 4:
        half = len(populated) // 2
        early = populated[:half]
        late = populated[half:]

        def weighted(group: list[Cell]) -> float:
            total = sum(c.trade_count for c in group)
            return (
                sum((c.average_trade or 0.0) * c.trade_count for c in group) / total
                if total
                else 0.0
            )

        first, second = weighted(early), weighted(late)
        findings.append(
            f"First half of the sample averages {first:+,.2f} per trade; second half "
            f"{second:+,.2f}. Split at {late[0].labels[0]}."
        )
        if first > 0 > second:
            findings.append(
                "The edge is positive in the first half and negative in the second. "
                "That is the shape of a decayed edge, and it is also the shape of "
                "bad luck over a short second half — the trade counts above say "
                "which reading the sample can support."
            )
    losing = [c for c in cells if c.trade_count and (c.average_trade or 0.0) < 0]
    findings.append(f"{len(losing)} of {len(cells)} months lost money on average.")

    return AnalysisResult(
        analysis="edge_over_time",
        title="Expectancy by month",
        question="Has this edge decayed?",
        shape="series",
        axes=(Axis(name="month", label="Month", categories=months),),
        measure=measure,
        measure_unit="currency per trade" if measure == "average_trade" else measure,
        cells=cells,
        total_trades=len(trades),
        covered_trades=sum(c.trade_count for c in cells),
        warnings=(
            (
                "A month is a small sample. These points are what happened in each "
                "month, not an estimate of the edge in each month."
            ),
            *(w for w in (_thin_warning(cells),) if w),
        ),
        findings=tuple(findings),
        provenance=provenance,
    )


def excursion(
    trades: Sequence[Any], provenance: AnalysisProvenance
) -> AnalysisResult:
    """How far trades ran in each direction before they closed.

    Bucketed by MFE against MAE, both measured over the bars the position was
    actually open. The useful reading is the top-left: trades that reached a
    large favourable excursion and were closed for a loss are giving something
    back, and how much is the difference between the exit and the stop being
    the problem.
    """
    usable = [t for t in trades if t.mfe is not None and t.mae is not None]
    if not usable:
        raise AnalysisError(
            "no trade in this run recorded its excursion. Runs written before "
            "AlgoForge measured MFE and MAE do not carry it, and it is not "
            "re-derivable without the bars the run used."
        )

    mfe = np.array([float(t.mfe) for t in usable])
    mae = np.array([abs(float(t.mae)) for t in usable])
    mfe_edges = np.percentile(mfe, [25, 50, 75])
    mae_edges = np.percentile(mae, [25, 50, 75])
    quartiles = ("Q1 lowest", "Q2", "Q3", "Q4 highest")

    buckets: dict[tuple[int, int], list[Any]] = {
        (a, b): [] for a in range(4) for b in range(4)
    }
    for trade, favourable, adverse in zip(usable, mfe, mae, strict=True):
        row = int(np.digitize(favourable, mfe_edges))
        column = int(np.digitize(adverse, mae_edges))
        buckets[(row, column)].append(trade)

    cells = tuple(
        _cell((a, b), (quartiles[a], quartiles[b]), buckets[(a, b)], keep_ids=200)
        for a in range(4)
        for b in range(4)
    )

    gave_back = [
        t
        for t in usable
        if float(t.mfe) > 0 and float(t.net_pnl) < 0
    ]
    findings = [
        f"{len(gave_back)} of {len(usable)} trades were in profit at some point and "
        f"closed at a loss."
    ]
    if gave_back:
        surrendered = sum(float(t.mfe) - float(t.gross_pnl) for t in gave_back)
        findings.append(
            f"Those trades gave back {surrendered:,.0f} in total from their best "
            "points. That is the size of the question about the exit, not proof "
            "that a different exit would have kept it."
        )

    return AnalysisResult(
        analysis="excursion",
        title="Maximum favourable against maximum adverse excursion",
        question="Are trades giving back gains, or being stopped before they work?",
        shape="grid",
        axes=(
            Axis(
                name="mfe",
                label="Favourable excursion (MFE)",
                categories=quartiles,
                unit="currency, gross of costs",
            ),
            Axis(
                name="mae",
                label="Adverse excursion (MAE)",
                categories=quartiles,
                unit="currency, gross of costs",
            ),
        ),
        measure="average_trade",
        measure_unit="currency per trade",
        cells=cells,
        total_trades=len(trades),
        covered_trades=len(usable),
        warnings=(
            (
                f"{len(trades) - len(usable)} trade(s) recorded no excursion and are "
                "excluded rather than counted as zero."
            )
            if len(usable) != len(trades)
            else "",
            *(w for w in (_thin_warning(cells),) if w),
        ),
        findings=tuple(findings),
        provenance=provenance,
    )


def worst_decile(
    trades: Sequence[Any],
    volatility: Sequence[float | None],
    regimes: Sequence[str | None],
    provenance: AnalysisProvenance,
) -> AnalysisResult:
    """What characterises the worst tenth of trades.

    Answers the brief's "find the market conditions responsible for the worst
    10% of trades" — and answers it as a *comparison*, because the worst decile
    of anything has properties, and the only ones worth reporting are those it
    does not share with the rest.
    """
    if len(trades) < 30:
        raise AnalysisError(
            f"a worst-decile comparison over {len(trades)} trades would be three "
            "trades against twenty-seven. This needs at least 30."
        )
    order = sorted(range(len(trades)), key=lambda i: float(trades[i].net_pnl))
    cut = max(1, len(trades) // 10)
    worst_idx = set(order[:cut])

    def group(indices: Sequence[int]) -> list[Any]:
        return [trades[i] for i in indices]

    worst = group(sorted(worst_idx))
    rest = group([i for i in range(len(trades)) if i not in worst_idx])

    cells = (
        _cell((0,), ("Worst 10%",), worst, keep_ids=1000),
        _cell((1,), ("The other 90%",), rest, keep_ids=400),
    )

    findings: list[str] = []
    def _vol_at(index: int) -> float | None:
        value = volatility[index]
        return float(value) if value is not None else None

    worst_vol = [v for v in (_vol_at(i) for i in sorted(worst_idx)) if v is not None]
    rest_vol = [
        v
        for v in (_vol_at(i) for i in range(len(trades)) if i not in worst_idx)
        if v is not None
    ]
    if worst_vol and rest_vol:
        wm, rm = float(np.mean(worst_vol)), float(np.mean(rest_vol))
        ratio = wm / rm if rm else float("nan")
        findings.append(
            f"Mean entry volatility in the worst decile is {wm:,.2f} against "
            f"{rm:,.2f} elsewhere ({ratio:.2f}x)."
        )
        if ratio > 1.25:
            findings.append(
                "The worst trades are concentrated in higher volatility. That is a "
                "condition a strategy can be told to avoid; whether avoiding it "
                "helps is a separate question this analysis does not answer."
            )
        elif 0.8 < ratio < 1.25:
            findings.append(
                "Volatility does not separate the worst decile from the rest. "
                "Whatever is causing these losses, it is not the market being busy."
            )

    counts: dict[str, int] = {}
    for i in sorted(worst_idx):
        label = regimes[i] or "UNCLASSIFIED"
        counts[label] = counts.get(label, 0) + 1
    if counts:
        top = max(counts, key=lambda k: counts[k])
        share = counts[top] / len(worst)
        overall = sum(1 for r in regimes if r == top) / max(1, len(regimes))
        findings.append(
            f"{share:.0%} of the worst decile was entered in {top}, against "
            f"{overall:.0%} of all trades."
        )

    hours: dict[int, int] = {}
    for trade in worst:
        hours[trade.entry_time.hour] = hours.get(trade.entry_time.hour, 0) + 1
    if hours:
        peak = max(hours, key=lambda k: hours[k])
        findings.append(
            f"Most common entry hour among the worst decile: {peak:02d}:00 UTC "
            f"({hours[peak]} of {len(worst)})."
        )

    return AnalysisResult(
        analysis="worst_decile",
        title="What the worst 10% of trades have in common",
        question="Which market conditions produced the worst trades?",
        shape="bars",
        axes=(
            Axis(
                name="group",
                label="Group",
                categories=("Worst 10%", "The other 90%"),
                note="Split by net P&L, worst first.",
            ),
        ),
        measure="average_trade",
        measure_unit="currency per trade",
        cells=cells,
        total_trades=len(trades),
        covered_trades=len(trades),
        warnings=(
            (
                "The worst decile of any sample has properties. Only the ones it "
                "does not share with the rest are reported, and none of them is a "
                "cause — this is a description, not an explanation."
            ),
        ),
        findings=tuple(findings),
        provenance=provenance,
    )


#: Everything the research lab can be asked, keyed the way a caller names it.
CATALOGUE: dict[str, dict[str, str]] = {
    "by_hour": {
        "title": "Expectancy by hour of day",
        "question": "Does this strategy's edge depend on when in the day it trades?",
        "shape": "bars",
        "needs": "trades",
    },
    "by_volatility_percentile": {
        "title": "Expectancy by volatility percentile",
        "question": "Does this strategy's edge depend on how volatile the market is?",
        "shape": "bars",
        "needs": "trades + classified bars",
    },
    "hour_by_volatility": {
        "title": "Expectancy surface: time of day against volatility",
        "question": "Where does the edge live, and where does it die?",
        "shape": "surface",
        "needs": "trades + classified bars",
    },
    "edge_over_time": {
        "title": "Expectancy by month",
        "question": "Has this edge decayed?",
        "shape": "series",
        "needs": "trades",
    },
    "excursion": {
        "title": "MFE against MAE",
        "question": "Are trades giving back gains, or being stopped before they work?",
        "shape": "grid",
        "needs": "trades with recorded excursion",
    },
    "worst_decile": {
        "title": "What the worst 10% of trades have in common",
        "question": "Which market conditions produced the worst trades?",
        "shape": "bars",
        "needs": "trades + classified bars, at least 30 trades",
    },
}


def make_provenance(
    analysis: str,
    payload: dict[str, Any],
    *,
    regime_fingerprint: str = "",
    parameters: dict[str, Any] | None = None,
    created_by: str = "operator",
    seed: int | None = None,
) -> AnalysisProvenance:
    """Build provenance from a backtest artifact, without trusting its shape."""
    return AnalysisProvenance(
        analysis=analysis,
        strategy_id=str(payload.get("strategy_id") or ""),
        backtest_id=str(payload.get("backtest_id") or ""),
        dataset_key=str(payload.get("dataset_key") or ""),
        spec_hash=str(payload.get("spec_hash") or ""),
        code_hash=str(payload.get("code_hash") or ""),
        data_hash=str(payload.get("data_hash") or ""),
        evidence_tier=str(payload.get("evidence_tier") or ""),
        partition_name=str(payload.get("partition_name") or ""),
        parameters=dict(parameters or payload.get("parameters") or {}),
        regime_fingerprint=regime_fingerprint,
        seed=seed,
        created_at=datetime.now(UTC),
        created_by=created_by,
    )
