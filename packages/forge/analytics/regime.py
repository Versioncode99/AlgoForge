"""What the market was actually doing while a trade was open.

This module replaces a genuinely bad piece of work. `build_normal_analysis` used
to split the P&L array into four equal chronological chunks and label them
"TREND", "HIGH_VOL", "RANGE" and "LOW_VOL". Nothing measured trend and nothing
measured volatility: the first quarter of the trades was called TREND because it
came first. Anyone reading "TREND: net P&L $2,400" would have believed a claim
about the market that had never been made about the market.

A regime here is measured from the bars. Two axes, because they are the two that
change what a strategy is doing rather than merely when it is doing it:

* **Trend** — where price sits relative to its own trailing average.
* **Volatility** — where the current true range sits in the distribution of
  true ranges that came *before* it.

**The lookahead point, which is the whole reason this file is careful.**
Classifying a 2019 bar as "high volatility" using 2024's volatility distribution
is a use of the future. It is easy to do by accident — `np.percentile` over the
whole series is one line — and it matters enormously, because a strategy tuned
on regime labels built that way has been tuned on information it could not have
had. So :func:`classify` computes its threshold from bars strictly before the
bar it is labelling, and the alternative is available only under a name that
says what it is, `Basis.FULL_SAMPLE`, marked descriptive, and refused wherever a
label could reach a trading decision.

Bars before the classifier has enough history are `UNCLASSIFIED`. They are not
quietly folded into "low volatility": an unmeasured bar and a calm bar are
different things, and a summary that conflates them overstates how much of the
sample it understood.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

import numpy as np
from pydantic import Field

from forge.contracts.hashing import content_hash
from forge.contracts.models import FrozenModel


class Regime(StrEnum):
    """The 2x2, plus the honest fifth state."""

    BULL_LOW = "BULL_LOW"
    BULL_HIGH = "BULL_HIGH"
    BEAR_LOW = "BEAR_LOW"
    BEAR_HIGH = "BEAR_HIGH"
    #: Not enough prior history to place this bar. Never treated as a regime.
    UNCLASSIFIED = "UNCLASSIFIED"


#: The four measured regimes, in a fixed order so a matrix's axes are stable.
MEASURED: tuple[Regime, ...] = (
    Regime.BULL_LOW,
    Regime.BULL_HIGH,
    Regime.BEAR_LOW,
    Regime.BEAR_HIGH,
)

SHORT_LABEL: dict[Regime, str] = {
    Regime.BULL_LOW: "Bull·Lo",
    Regime.BULL_HIGH: "Bull·Hi",
    Regime.BEAR_LOW: "Bear·Lo",
    Regime.BEAR_HIGH: "Bear·Hi",
    Regime.UNCLASSIFIED: "—",
}


class Basis(StrEnum):
    """How the volatility threshold was derived."""

    #: Threshold from bars strictly before the one being labelled. Safe to use
    #: anywhere, including in a claim about what a strategy would have done.
    TRAILING = "TRAILING"
    #: Threshold from the whole series. Uses the future. Descriptive slicing of
    #: results already produced, and nothing else.
    FULL_SAMPLE = "FULL_SAMPLE"


class RegimeSettings(FrozenModel):
    """The classifier's parameters, recorded so a label can be reproduced."""

    trend_length: int = Field(default=200, ge=10, le=20_000)
    vol_length: int = Field(default=14, ge=2, le=1000)
    #: Volatility at or above this percentile of prior volatility is "high".
    vol_percentile: float = Field(default=50.0, gt=0.0, lt=100.0)
    #: Prior bars required before any label is issued.
    min_history: int = Field(default=500, ge=20)
    basis: Basis = Basis.TRAILING
    #: How often the trailing threshold is recomputed. Between recomputes it is
    #: held constant, which keeps a multi-million-bar classification affordable
    #: without ever letting a threshold see past the bar it applies to.
    refresh_bars: int = Field(default=0, ge=0)

    def resolved_refresh(self, bar_count: int) -> int:
        """The recompute interval actually used.

        Zero means "choose one": enough recomputes that the threshold tracks the
        series, few enough that classification stays linear-ish. Two hundred is
        the compromise, floored so short series recompute every bar.
        """
        if self.refresh_bars:
            return self.refresh_bars
        return max(1, bar_count // 200)


class RegimeSeries(FrozenModel):
    """Per-bar labels, and everything needed to reproduce them."""

    settings: RegimeSettings
    bar_count: int
    #: One label per bar, aligned to the bars it was built from.
    labels: tuple[Regime, ...]
    #: The volatility measure per bar, in price units. NaN before warmup.
    volatility: tuple[float, ...]
    #: Trend strength: (close - trailing mean) / volatility. NaN before warmup.
    trend_strength: tuple[float, ...]
    #: The threshold in force at each bar, so a label can be checked by hand.
    vol_threshold: tuple[float, ...]
    classified_bars: int

    @property
    def coverage(self) -> float:
        """Share of bars the classifier could actually place."""
        return self.classified_bars / self.bar_count if self.bar_count else 0.0

    @property
    def fingerprint(self) -> str:
        """Identity of this classification, for provenance on anything derived."""
        return content_hash(
            {
                "settings": self.settings.model_dump(mode="json"),
                "bar_count": self.bar_count,
                # The labels themselves, not a summary: two classifications that
                # agree on counts but disagree per bar are different evidence.
                "labels": [str(label) for label in self.labels],
            }
        )

    def at(self, index: int) -> Regime:
        if 0 <= index < len(self.labels):
            return self.labels[index]
        return Regime.UNCLASSIFIED

    def window(self, start: int, stop: int) -> RegimeSeries:
        """The same classification, restricted to bars [start, stop).

        Used when a classifier is given history *before* the span being
        reported on. That extra history is free and strictly good — it lets
        early bars be classified at all, and it cannot contain the future — but
        it must not end up in the report's denominators. Exposure over "the
        bars this run touched" and exposure over "those plus a month of warmup"
        are different numbers, and only the first answers the question.

        The labels are not recomputed. Slicing after classification is the
        point: each label keeps the prior history it was derived from.
        """
        start = max(0, start)
        stop = min(len(self.labels), stop)
        if stop <= start:
            raise ValueError(f"empty window [{start}, {stop})")
        labels = self.labels[start:stop]
        return RegimeSeries(
            settings=self.settings,
            bar_count=stop - start,
            labels=labels,
            volatility=self.volatility[start:stop],
            trend_strength=self.trend_strength[start:stop],
            vol_threshold=self.vol_threshold[start:stop],
            classified_bars=sum(1 for label in labels if label is not Regime.UNCLASSIFIED),
        )


def _trailing_mean(values: np.ndarray, length: int) -> np.ndarray:
    """Mean of the `length` values ending at each position, NaN before warmup.

    Trailing by construction: position i sees i-length+1..i and nothing after.
    """
    out = np.full(values.size, np.nan, dtype=np.float64)
    if values.size < length:
        return out
    cumulative = np.concatenate(([0.0], np.cumsum(values, dtype=np.float64)))
    out[length - 1 :] = (cumulative[length:] - cumulative[:-length]) / length
    return out


def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    previous = np.concatenate(([close[0]], close[:-1]))
    span: np.ndarray = np.maximum(
        high - low, np.maximum(np.abs(high - previous), np.abs(low - previous))
    )
    return span


def _trailing_thresholds(
    values: np.ndarray, percentile: float, min_history: int, refresh: int
) -> np.ndarray:
    """The volatility threshold in force at each bar, using only earlier bars.

    Recomputed every `refresh` bars from every finite value before the recompute
    point, and held constant in between. Holding it constant is what makes this
    affordable on millions of bars; computing it from a *prefix* is what makes
    it honest. A threshold recomputed at bar 10,000 and applied through bar
    10,199 has still never seen bar 10,200.
    """
    out = np.full(values.size, np.nan, dtype=np.float64)
    current = np.nan
    for start in range(min_history, values.size, refresh):
        history = values[:start]
        finite = history[np.isfinite(history)]
        if finite.size >= min_history // 2:
            current = float(np.percentile(finite, percentile))
        out[start : start + refresh] = current
    return out


def classify(
    high: Any, low: Any, close: Any, settings: RegimeSettings | None = None
) -> RegimeSeries:
    """Label every bar by trend and volatility.

    Accepts anything array-like — the arrays the runtime already built, a
    dataframe's columns, a list — because the callers are the backtest path and
    the research path and they hold their bars differently.
    """
    config = settings or RegimeSettings()
    h = np.asarray(high, dtype=np.float64)
    low_arr = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    if not (h.size == low_arr.size == c.size):
        raise ValueError("high, low and close must be the same length")
    count = c.size

    volatility = _trailing_mean(_true_range(h, low_arr, c), config.vol_length)
    trend_mean = _trailing_mean(c, config.trend_length)
    with np.errstate(invalid="ignore", divide="ignore"):
        strength = (c - trend_mean) / volatility

    if config.basis is Basis.FULL_SAMPLE:
        finite = volatility[np.isfinite(volatility)]
        level = float(np.percentile(finite, config.vol_percentile)) if finite.size else np.nan
        threshold = np.full(count, level, dtype=np.float64)
        # Bars before warmup still have no volatility measure of their own, so
        # they remain unclassified whatever the threshold says.
    else:
        threshold = _trailing_thresholds(
            volatility,
            config.vol_percentile,
            config.min_history,
            config.resolved_refresh(count),
        )

    usable = (
        np.isfinite(volatility)
        & np.isfinite(trend_mean)
        & np.isfinite(threshold)
        & (np.arange(count) >= config.min_history)
    )
    bull = c > trend_mean
    high_vol = volatility >= threshold

    labels = np.full(count, Regime.UNCLASSIFIED.value, dtype=object)
    labels[usable & bull & ~high_vol] = Regime.BULL_LOW.value
    labels[usable & bull & high_vol] = Regime.BULL_HIGH.value
    labels[usable & ~bull & ~high_vol] = Regime.BEAR_LOW.value
    labels[usable & ~bull & high_vol] = Regime.BEAR_HIGH.value

    return RegimeSeries(
        settings=config,
        bar_count=count,
        labels=tuple(Regime(str(value)) for value in labels),
        volatility=tuple(
            round(float(v), 6) if np.isfinite(v) else float("nan") for v in volatility
        ),
        trend_strength=tuple(
            round(float(v), 6) if np.isfinite(v) else float("nan") for v in strength
        ),
        vol_threshold=tuple(
            round(float(v), 6) if np.isfinite(v) else float("nan") for v in threshold
        ),
        classified_bars=int(usable.sum()),
    )


# ── attributing trades ───────────────────────────────────────────────────────

Attribution = Literal["entry", "dominant", "exit"]


class TradeRegime(FrozenModel):
    """One trade's regime on each of the three bases.

    Three rather than one because they disagree, and which one is right depends
    on the question. A trade opened in calm and closed in a panic belongs to
    both; saying so is more useful than picking.
    """

    trade_id: str
    entry: Regime
    exit: Regime
    dominant: Regime
    #: Share of the holding period spent in the dominant regime.
    dominant_share: float
    #: Volatility and trend strength at the decision bar, for slicing.
    entry_volatility: float | None = None
    entry_trend_strength: float | None = None
    #: Percentile of this trade's entry volatility within the classified series.
    #: Descriptive, and therefore only populated on request.
    entry_vol_percentile: float | None = None


def attribute_at_times(
    trades: list[Any],
    series: RegimeSeries,
    times: Sequence[datetime],
    *,
    with_percentile: bool = False,
) -> list[TradeRegime]:
    """Map trades onto regimes by *timestamp* rather than by bar index.

    This exists because of a defect that produced entirely plausible nonsense.
    A trade's `entry_index` is an index into the bars that backtest ran on — the
    last 120,000 of an archive, say. A regime series built from the whole
    4.7-million-bar archive has an index 119 too, and it is a bar from sixteen
    years earlier. Reading `series.at(trade.entry_index)` against a differently
    windowed series therefore returns a real label for a real bar that has
    nothing to do with the trade, and the resulting report looks correct in
    every respect: sensible coverage, sensible cells, sensible warnings.

    Indices are only meaningful within one window. Timestamps are meaningful
    everywhere, which is the same reason chart markers carry them.

    A trade whose bars are not in the classified series is `UNCLASSIFIED`. Not
    approximated to the nearest bar: a nearby bar is a different bar, and the
    whole point of this function is to stop a label being attached to one.
    """
    if len(times) != len(series.labels):
        raise ValueError(
            f"the classification covers {len(series.labels)} bars but {len(times)} "
            "timestamps were supplied; they must describe the same bars"
        )
    position = {stamp: index for index, stamp in enumerate(times)}
    vol = np.asarray(series.volatility, dtype=np.float64)
    strength = np.asarray(series.trend_strength, dtype=np.float64)
    reference = vol[np.isfinite(vol)] if with_percentile else np.empty(0)
    ordered = np.sort(reference) if reference.size else reference

    out: list[TradeRegime] = []
    for trade in trades:
        entry_at = position.get(trade.entry_time)
        exit_at = position.get(trade.exit_time)
        if entry_at is None or exit_at is None:
            out.append(
                TradeRegime(
                    trade_id=str(trade.trade_id),
                    entry=Regime.UNCLASSIFIED,
                    exit=Regime.UNCLASSIFIED,
                    dominant=Regime.UNCLASSIFIED,
                    dominant_share=0.0,
                )
            )
            continue

        # The decision sits exactly one bar before the fill, by construction of
        # the runtime, and the decision is what the regime is meant to explain.
        decision = entry_at - 1
        entry_regime = series.at(decision) if decision >= 0 else Regime.UNCLASSIFIED
        span = series.labels[entry_at : exit_at + 1]
        measured = [label for label in span if label is not Regime.UNCLASSIFIED]
        dominant: Regime = Regime.UNCLASSIFIED
        share = 0.0
        if measured:
            dominant = max(set(measured), key=measured.count)
            share = measured.count(dominant) / len(span)

        entry_vol = float(vol[decision]) if 0 <= decision < vol.size else float("nan")
        entry_strength = (
            float(strength[decision]) if 0 <= decision < strength.size else float("nan")
        )
        percentile: float | None = None
        if with_percentile and ordered.size and np.isfinite(entry_vol):
            percentile = round(
                100.0 * float(np.searchsorted(ordered, entry_vol, side="right")) / ordered.size, 3
            )

        out.append(
            TradeRegime(
                trade_id=str(trade.trade_id),
                entry=entry_regime,
                exit=series.at(exit_at),
                dominant=dominant,
                dominant_share=round(share, 4),
                entry_volatility=round(entry_vol, 6) if np.isfinite(entry_vol) else None,
                entry_trend_strength=(
                    round(entry_strength, 6) if np.isfinite(entry_strength) else None
                ),
                entry_vol_percentile=percentile,
            )
        )
    return out


def attribute(
    trades: list[Any], series: RegimeSeries, *, with_percentile: bool = False
) -> list[TradeRegime]:
    """Map each trade onto the regime(s) it ran in, by bar index.

    **Only correct when `series` was classified from exactly the bars the trades
    were produced on.** An index is meaningful within one window and nowhere
    else; against a differently windowed series this returns real labels for the
    wrong bars, and the result looks entirely reasonable. Anything reading
    trades back from an artifact must use :func:`attribute_at_times` instead.

    The entry regime is read at the *decision* bar, not the fill bar: the
    decision is what the regime is supposed to explain, and it is one bar
    earlier by construction.

    `with_percentile` computes each entry's volatility percentile against the
    whole classified series. That looks at the future and is descriptive only —
    a way of slicing results already produced. It is off by default and named so
    the caller has to mean it.
    """
    vol = np.asarray(series.volatility, dtype=np.float64)
    strength = np.asarray(series.trend_strength, dtype=np.float64)
    reference = vol[np.isfinite(vol)] if with_percentile else np.empty(0)
    ordered = np.sort(reference) if reference.size else reference

    out: list[TradeRegime] = []
    for trade in trades:
        decision = int(trade.entry_decision_index)
        entry_regime = series.at(decision)
        exit_regime = series.at(int(trade.exit_index))

        span = series.labels[int(trade.entry_index) : int(trade.exit_index) + 1]
        measured = [label for label in span if label is not Regime.UNCLASSIFIED]
        dominant: Regime = Regime.UNCLASSIFIED
        share = 0.0
        if measured:
            dominant = max(set(measured), key=measured.count)
            share = measured.count(dominant) / len(span)

        entry_vol = float(vol[decision]) if 0 <= decision < vol.size else float("nan")
        entry_strength = (
            float(strength[decision]) if 0 <= decision < strength.size else float("nan")
        )
        percentile: float | None = None
        if with_percentile and ordered.size and np.isfinite(entry_vol):
            percentile = round(
                100.0 * float(np.searchsorted(ordered, entry_vol, side="right")) / ordered.size, 3
            )

        out.append(
            TradeRegime(
                trade_id=str(trade.trade_id),
                entry=entry_regime,
                exit=exit_regime,
                dominant=dominant,
                dominant_share=round(share, 4),
                entry_volatility=round(entry_vol, 6) if np.isfinite(entry_vol) else None,
                entry_trend_strength=(
                    round(entry_strength, 6) if np.isfinite(entry_strength) else None
                ),
                entry_vol_percentile=percentile,
            )
        )
    return out


# ── summarising ──────────────────────────────────────────────────────────────


class RegimeCell(FrozenModel):
    """What a strategy did inside one regime."""

    regime: Regime
    label: str
    trade_count: int
    net_pnl: float
    gross_pnl: float
    win_rate: float | None
    average_trade: float | None
    #: Share of all classified bars spent in this regime. Exposure, not trade
    #: share: a regime holding 5% of the trades but 40% of the time is a
    #: different fact from one holding 40% of the trades.
    bar_exposure: float
    trade_share: float
    #: Set when the cell has too few trades to say anything. The number is still
    #: shown — suppressing it would hide that the strategy went there at all —
    #: but nothing derived from it should be read as an estimate.
    insufficient: bool
    note: str = ""


#: Below this, a cell's win rate and average trade are noise. Stated as a
#: constant so the threshold is one number a reader can disagree with.
MIN_TRADES_FOR_ESTIMATE = 20


class RegimeReport(FrozenModel):
    attribution: Attribution
    settings: RegimeSettings
    series_fingerprint: str
    total_trades: int
    classified_trades: int
    unclassified_trades: int
    coverage: float
    cells: tuple[RegimeCell, ...]
    #: from -> to -> count, over the classified bars. Rows sum to their own
    #: observation count, which is carried so a 33% built on three bars cannot
    #: be mistaken for a 33% built on three thousand.
    transitions: tuple[tuple[int, ...], ...]
    transition_labels: tuple[str, ...]
    concentration: float | None = None
    concentration_regime: Regime | None = None
    warnings: tuple[str, ...] = ()


def summarise(
    trades: list[Any],
    attributions: list[TradeRegime],
    series: RegimeSeries,
    *,
    attribution: Attribution = "entry",
) -> RegimeReport:
    """Per-regime performance, exposure, and how the regimes follow each other."""
    by_id = {item.trade_id: item for item in attributions}
    buckets: dict[Regime, list[Any]] = {regime: [] for regime in MEASURED}
    unclassified = 0
    for trade in trades:
        item = by_id.get(str(trade.trade_id))
        regime = getattr(item, attribution) if item else Regime.UNCLASSIFIED
        if regime is Regime.UNCLASSIFIED:
            unclassified += 1
        else:
            buckets[regime].append(trade)

    classified_bars = max(1, series.classified_bars)
    label_counts = {regime: 0 for regime in MEASURED}
    for label in series.labels:
        if label is not Regime.UNCLASSIFIED:
            label_counts[label] += 1

    total = len(trades)
    cells: list[RegimeCell] = []
    for regime in MEASURED:
        members = buckets[regime]
        net = sum(float(t.net_pnl) for t in members)
        gross = sum(float(t.gross_pnl) for t in members)
        wins = sum(1 for t in members if float(t.net_pnl) > 0)
        thin = len(members) < MIN_TRADES_FOR_ESTIMATE
        cells.append(
            RegimeCell(
                regime=regime,
                label=SHORT_LABEL[regime],
                trade_count=len(members),
                net_pnl=round(net, 4),
                gross_pnl=round(gross, 4),
                win_rate=round(wins / len(members), 4) if members else None,
                average_trade=round(net / len(members), 4) if members else None,
                bar_exposure=round(label_counts[regime] / classified_bars, 4),
                trade_share=round(len(members) / total, 4) if total else 0.0,
                insufficient=thin,
                note=(
                    f"{len(members)} trade(s) — below the {MIN_TRADES_FOR_ESTIMATE} "
                    "needed to estimate a win rate. The P&L is what happened; the "
                    "rates are not an estimate of anything."
                    if thin
                    else ""
                ),
            )
        )

    # Where the gross profit came from. Computed on gross so a regime that made
    # money and paid it away in costs still shows as the source of the edge.
    winners = [cell for cell in cells if cell.gross_pnl > 0]
    total_gross = sum(cell.gross_pnl for cell in winners)
    concentration: float | None = None
    top: Regime | None = None
    if total_gross > 0:
        best = max(winners, key=lambda cell: cell.gross_pnl)
        concentration = round(best.gross_pnl / total_gross, 4)
        top = best.regime

    warnings: list[str] = []
    if unclassified:
        warnings.append(
            f"{unclassified} of {total} trades ran in bars the classifier could not "
            f"place ({series.settings.min_history} bars of prior history are needed). "
            "They are counted nowhere rather than folded into a regime."
        )
    if series.coverage < 0.9:
        warnings.append(
            f"Only {series.coverage:.1%} of bars were classified. Regime shares are "
            "over the classified bars, not over the whole series."
        )
    if series.settings.basis is Basis.FULL_SAMPLE:
        warnings.append(
            "Volatility thresholds were taken from the whole series, which uses "
            "information later than the bars they label. Descriptive only — this "
            "report is not evidence about what a strategy would have done."
        )
    if concentration is not None and concentration > 0.6 and top is not None:
        warnings.append(
            f"{concentration:.1%} of gross profit came from {SHORT_LABEL[top]}. A "
            "profit that appears in one regime is a bet that the regime persists."
        )

    return RegimeReport(
        attribution=attribution,
        settings=series.settings,
        series_fingerprint=series.fingerprint,
        total_trades=total,
        classified_trades=total - unclassified,
        unclassified_trades=unclassified,
        coverage=round(series.coverage, 4),
        cells=tuple(cells),
        transitions=transition_matrix(series),
        transition_labels=tuple(SHORT_LABEL[regime] for regime in MEASURED),
        concentration=concentration,
        concentration_regime=top,
        warnings=tuple(warnings),
    )


def transition_matrix(series: RegimeSeries) -> tuple[tuple[int, ...], ...]:
    """How often each regime is followed by each other, in observation counts.

    Counts rather than probabilities. A 33% built on three observations and a
    33% built on three thousand are different claims, and only the count can
    tell them apart — which is why the renderer is given both.

    Transitions across an unclassified stretch are not counted: the pair either
    side of a gap did not follow each other, and pretending it did would invent
    a transition that never happened.
    """
    index = {regime: position for position, regime in enumerate(MEASURED)}
    matrix = [[0 for _ in MEASURED] for _ in MEASURED]
    labels = series.labels
    for position in range(1, len(labels)):
        before, after = labels[position - 1], labels[position]
        if before is Regime.UNCLASSIFIED or after is Regime.UNCLASSIFIED:
            continue
        matrix[index[before]][index[after]] += 1
    return tuple(tuple(row) for row in matrix)
