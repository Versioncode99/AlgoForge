"""The closed catalogue of things a strategy may compute.

The IR has always had a closed feature set, and the reason is in
:mod:`forge.strategy.ir`: an agent may compose a strategy, not invent an
indicator whose semantics nobody can check. That decision is not in question
here. What *was* in question is how small the closed set had been left.

Twenty-one feature kinds, all of them computed directly from the bar arrays,
gave the research engine one move: pick a different indicator. There was no way
to say "the same observation, but measured against its own recent distribution",
which is most of what quantitative research actually does. A campaign therefore
ran out of constructions long before it ran out of ideas.

So the catalogue is in two halves, and the second half is the new one.

**Observations** are computed from the bars: a price, a range, a session
extreme, a realised volatility. They are the raw material.

**Transformations** are computed from *another declared feature's own history*.
``zscore`` of an ATR is not an indicator anybody has to name; it is the ATR
observed against the last N of itself. Because a transformation names its source
rather than embedding it, the two halves multiply: N observations and M
transformations reach N x M constructions without N x M implementations, and
every one of them is still a closed, tested primitive rather than generated
arithmetic.

Three properties hold for every entry here, and each is load-bearing.

**Causal.** A primitive's value at bar *i* is a function of bars at or before
*i* and nothing else. This is what lets the evaluator cache a value once and
reuse it: a value that could change when later bars arrive would be a value that
had seen them. :func:`window_length` states exactly how much history each one
reads, so the warmup a definition needs is derived rather than hoped for.

**Deterministic.** No clock, no randomness, no I/O. The same inputs give the
same float, on any machine, twice.

**Undefined rather than wrong.** Insufficient history, a zero denominator, a
degenerate range: all return NaN, and the IR's comparison rules already treat
NaN as "no signal". A primitive that guessed a value when it had none would
produce a strategy that trades on the guess.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

NAN = float("nan")

#: How a primitive gets its input.
#:
#: ``bars`` reads the OHLCV window directly. ``series`` reads the history of
#: another declared feature, named by the declaring feature's ``source`` field.
SourceKind = Literal["bars", "series"]


@dataclass(frozen=True)
class Primitive:
    """One computable quantity, with everything a caller needs to reason about it.

    The metadata is not decoration. ``category`` is what the research grammar
    groups by when it decides which observations a mechanism can be built from;
    ``data_requirement`` is what the frontier records as blocking when the data
    is not there; ``description`` is what the interface shows so that a person
    reading a generated strategy can tell what it reads without reading the
    implementation.
    """

    kind: str
    arity: int
    source: SourceKind
    category: str
    label: str
    description: str
    #: What the result is measured in. Two features with different units must
    #: not be compared directly, and the grammar uses this to refuse a
    #: comparison that would silently never fire.
    unit: str = "price"
    #: The data class this needs. Everything currently available is BARS; the
    #: field exists so a primitive needing more says so rather than failing at
    #: run time.
    data_requirement: str = "BARS"
    #: Bars of history needed beyond the length argument, for primitives whose
    #: definition reads further back than their argument implies.
    warmup_multiplier: int = 1
    warmup_extra: int = 2


# ── observations: computed from the bar window ───────────────────────────────

_OBSERVATIONS: tuple[Primitive, ...] = (
    Primitive("close", 0, "bars", "price", "Close", "The bar's closing price."),
    Primitive("open", 0, "bars", "price", "Open", "The bar's opening price."),
    Primitive("high", 0, "bars", "price", "High", "The bar's high."),
    Primitive("low", 0, "bars", "price", "Low", "The bar's low."),
    Primitive("volume", 0, "bars", "volume", "Volume", "Contracts traded in the bar.", "volume"),
    Primitive(
        "typical_price",
        0,
        "bars",
        "price",
        "Typical price",
        "(high + low + close) / 3. The price a volume weight is usually applied to.",
    ),
    Primitive(
        "bar_range",
        0,
        "bars",
        "range",
        "Bar range",
        "High minus low. The bar's realised travel, before any smoothing.",
        "price_distance",
    ),
    Primitive(
        "true_range",
        0,
        "bars",
        "range",
        "True range",
        "The bar's range extended to include a gap from the prior close.",
        "price_distance",
        warmup_extra=3,
    ),
    Primitive(
        "gap",
        0,
        "bars",
        "range",
        "Gap",
        "This bar's open minus the previous close. Positive is a gap up.",
        "price_distance",
        warmup_extra=3,
    ),
    Primitive(
        "clv",
        0,
        "bars",
        "microstructure",
        "Close location value",
        "Where the close sat inside the bar's range, from -1 at the low to +1 at "
        "the high. Undefined on a bar with no range.",
        "ratio",
    ),
    Primitive(
        "signed_volume",
        0,
        "bars",
        "microstructure",
        "Signed volume",
        "Volume signed by where the bar closed in its own range. A bounded proxy "
        "for buying against selling pressure when no order flow is available.",
        "volume",
    ),
    Primitive("sma", 1, "bars", "trend", "Simple moving average", "Mean close over N bars."),
    Primitive(
        "volume_sma",
        1,
        "bars",
        "volume",
        "Average volume",
        "Mean volume over N bars. What 'unusual volume' is unusual against.",
        "volume",
    ),
    Primitive(
        "ema",
        1,
        "bars",
        "trend",
        "Exponential moving average",
        "Exponentially weighted mean close with span N.",
        warmup_multiplier=5,
    ),
    Primitive(
        "atr",
        1,
        "bars",
        "volatility",
        "Average true range",
        "Mean true range over N bars. The risk unit stops are sized in.",
        "price_distance",
        warmup_multiplier=2,
    ),
    Primitive(
        "adx",
        1,
        "bars",
        "trend",
        "ADX",
        "Directional movement strength over N bars, from 0 to 100.",
        "index",
        warmup_multiplier=3,
    ),
    Primitive(
        "rsi",
        1,
        "bars",
        "oscillator",
        "RSI",
        "Relative strength over N bars, from 0 to 100.",
        "index",
        warmup_multiplier=4,
    ),
    Primitive(
        "highest",
        1,
        "bars",
        "range",
        "Rolling high",
        "Highest high of the last N bars.",
    ),
    Primitive("lowest", 1, "bars", "range", "Rolling low", "Lowest low of the last N bars."),
    Primitive(
        "realised_vol",
        1,
        "bars",
        "volatility",
        "Realised volatility",
        "Standard deviation of the last N log returns.",
        "return",
        warmup_multiplier=2,
    ),
    Primitive(
        "upside_vol",
        1,
        "bars",
        "volatility",
        "Upside volatility",
        "Standard deviation of the positive log returns among the last N. Paired "
        "with downside_vol it states volatility asymmetry, which a single "
        "realised volatility cannot.",
        "return",
        warmup_multiplier=2,
    ),
    Primitive(
        "downside_vol",
        1,
        "bars",
        "volatility",
        "Downside volatility",
        "Standard deviation of the negative log returns among the last N.",
        "return",
        warmup_multiplier=2,
    ),
    Primitive(
        "roc",
        1,
        "bars",
        "momentum",
        "Rate of change",
        "Fractional change in close over N bars.",
        "return",
    ),
    Primitive(
        "efficiency_ratio",
        1,
        "bars",
        "trend",
        "Efficiency ratio",
        "Net movement over N bars divided by the total path travelled. 1 is a "
        "straight line, 0 is pure churn. Trend strength without a directional sign.",
        "ratio",
    ),
    Primitive(
        "variance_ratio",
        1,
        "bars",
        "momentum",
        "Variance ratio",
        "Variance of N-bar returns against N times the variance of one-bar "
        "returns. Above 1 is persistence, below 1 is reversion, 1 is a random "
        "walk. The Lo-MacKinlay statistic, computed over a rolling window.",
        "ratio",
        warmup_multiplier=5,
    ),
    Primitive(
        "return_autocorr",
        1,
        "bars",
        "momentum",
        "Return autocorrelation",
        "Lag-one autocorrelation of the last N log returns. Positive is "
        "continuation at the bar scale, negative is bar-to-bar reversal.",
        "ratio",
        warmup_multiplier=2,
    ),
    Primitive(
        "session_vwap",
        0,
        "bars",
        "session",
        "Session VWAP",
        "Volume-weighted average price since the session open.",
    ),
    Primitive(
        "session_vwap_sd",
        0,
        "bars",
        "session",
        "Session VWAP deviation",
        "Volume-weighted standard deviation around the session VWAP.",
        "price_distance",
    ),
    Primitive(
        "session_high",
        0,
        "bars",
        "session",
        "Session high",
        "Highest high since the session open.",
    ),
    Primitive(
        "session_low",
        0,
        "bars",
        "session",
        "Session low",
        "Lowest low since the session open.",
    ),
    Primitive(
        "session_range_position",
        0,
        "bars",
        "session",
        "Position in session range",
        "Where the close sits between the session low and high, from 0 to 1. "
        "Undefined before the session has produced a range.",
        "ratio",
    ),
    Primitive(
        "opening_range_high",
        1,
        "bars",
        "session",
        "Opening range high",
        "Highest high of the session's first N bars.",
    ),
    Primitive(
        "opening_range_low",
        1,
        "bars",
        "session",
        "Opening range low",
        "Lowest low of the session's first N bars.",
    ),
    Primitive(
        "bars_since_session_open",
        0,
        "bars",
        "session",
        "Bars since the open",
        "How far into the session this bar is.",
        "bars",
    ),
    Primitive(
        "minute_of_day",
        0,
        "bars",
        "session",
        "Minute of day",
        "Minutes since midnight UTC.",
        "minutes",
    ),
)


# ── transformations: computed from another feature's own history ─────────────

_TRANSFORMS: tuple[Primitive, ...] = (
    Primitive(
        "mean",
        1,
        "series",
        "transform",
        "Rolling mean",
        "The source's average over its own last N values.",
        "same_as_source",
    ),
    Primitive(
        "stdev",
        1,
        "series",
        "transform",
        "Rolling deviation",
        "Sample standard deviation of the source's last N values.",
        "same_as_source",
    ),
    Primitive(
        "zscore",
        1,
        "series",
        "transform",
        "Z-score",
        "How far the source sits from its own recent mean, in its own standard "
        "deviations. This is what makes a threshold comparable across "
        "instruments and regimes instead of being a number that fit one chart.",
        "zscore",
    ),
    Primitive(
        "percentile_rank",
        1,
        "series",
        "transform",
        "Percentile rank",
        "The share of the source's last N values at or below the current one, "
        "from 0 to 1. Distribution-free, so it survives a source whose scale "
        "drifts.",
        "percentile",
    ),
    Primitive(
        "slope",
        1,
        "series",
        "transform",
        "Slope",
        "Least-squares slope of the source over its last N values, per bar.",
        "same_as_source",
    ),
    Primitive(
        "accel",
        1,
        "series",
        "transform",
        "Acceleration",
        "The change in the source over N bars, minus the change over the N "
        "before that. Whether the move is speeding up, not merely continuing.",
        "same_as_source",
        warmup_multiplier=2,
    ),
    Primitive(
        "change",
        1,
        "series",
        "transform",
        "Change",
        "The source now minus the source N bars ago.",
        "same_as_source",
    ),
    Primitive(
        "pct_change",
        1,
        "series",
        "transform",
        "Proportional change",
        "The source's change over N bars as a fraction of its earlier value.",
        "ratio",
    ),
    Primitive(
        "ewm",
        1,
        "series",
        "transform",
        "Exponentially weighted mean",
        "The source smoothed with span N, weighting recent values more.",
        "same_as_source",
        warmup_multiplier=5,
    ),
    Primitive(
        "max_of",
        1,
        "series",
        "transform",
        "Rolling maximum",
        "The largest of the source's last N values.",
        "same_as_source",
    ),
    Primitive(
        "min_of",
        1,
        "series",
        "transform",
        "Rolling minimum",
        "The smallest of the source's last N values.",
        "same_as_source",
    ),
    Primitive(
        "persistence",
        1,
        "series",
        "transform",
        "Persistence",
        "The share of the source's last N values that were above zero, from 0 "
        "to 1. States how consistently a condition held rather than whether it "
        "holds now, which is the difference between a regime and a bar.",
        "percentile",
    ),
)


CATALOGUE: dict[str, Primitive] = {p.kind: p for p in (*_OBSERVATIONS, *_TRANSFORMS)}

#: Arity by kind. Kept as its own mapping because the IR validates against it on
#: every definition and a dict lookup is the whole check.
ARITY: dict[str, int] = {p.kind: p.arity for p in CATALOGUE.values()}

#: Kinds that read another feature's history and therefore require ``source``.
SERIES_KINDS: frozenset[str] = frozenset(
    p.kind for p in CATALOGUE.values() if p.source == "series"
)

#: Kinds computed straight from the bars.
BAR_KINDS: frozenset[str] = frozenset(p.kind for p in CATALOGUE.values() if p.source == "bars")

#: Categories, for grouping in the interface and in the research grammar.
CATEGORIES: tuple[str, ...] = tuple(
    sorted({p.category for p in CATALOGUE.values()})
)


def primitive(kind: str) -> Primitive:
    try:
        return CATALOGUE[kind]
    except KeyError:
        raise KeyError(
            f"unknown feature kind '{kind}'. Known: {', '.join(sorted(CATALOGUE))}"
        ) from None


def window_length(kind: str, length: int) -> int:
    """How many source values a series primitive reads for a given length.

    Stated rather than assumed, because a transformation that quietly reads
    further back than its argument suggests is a transformation whose warmup is
    wrong, and a wrong warmup does not fail — it produces NaN comparisons that
    read as "no signal" for the first stretch of every backtest.
    """
    spec = primitive(kind)
    n = max(1, int(length))
    if kind in ("change", "pct_change"):
        return n + 1
    if kind == "accel":
        return 2 * n + 1
    if kind == "ewm":
        return 5 * n
    return n * spec.warmup_multiplier


def catalogue_rows() -> list[dict[str, Any]]:
    """The vocabulary as data, for the API and the interface.

    One row per primitive so a person can see exactly what the research engine
    is allowed to build from — which is the honest answer to "how large is the
    construction vocabulary?".
    """
    return [
        {
            "kind": p.kind,
            "label": p.label,
            "arity": p.arity,
            "source": p.source,
            "category": p.category,
            "unit": p.unit,
            "description": p.description,
            "data_requirement": p.data_requirement,
        }
        for p in sorted(
            CATALOGUE.values(), key=lambda item: (item.source, item.category, item.kind)
        )
    ]


# ── series transformations ───────────────────────────────────────────────────
# Each takes the source's last `window_length(kind, n)` values, oldest first,
# and returns one float. A short array means "not enough history yet" and the
# answer is NaN, never a value computed from what happened to be there.


def _guard(values: np.ndarray, need: int) -> bool:
    return values.size >= need and bool(np.isfinite(values[-need:]).all())


def _t_mean(values: np.ndarray, n: int) -> float:
    return float(values[-n:].mean()) if _guard(values, n) else NAN


def _t_stdev(values: np.ndarray, n: int) -> float:
    if n < 2 or not _guard(values, n):
        return NAN
    return float(values[-n:].std(ddof=1))


def _t_zscore(values: np.ndarray, n: int) -> float:
    if n < 2 or not _guard(values, n):
        return NAN
    tail = values[-n:]
    spread = float(tail.std(ddof=1))
    if spread <= 0.0:
        return NAN
    return float((tail[-1] - tail.mean()) / spread)


def _t_percentile_rank(values: np.ndarray, n: int) -> float:
    if not _guard(values, n):
        return NAN
    tail = values[-n:]
    return float((tail <= tail[-1]).sum()) / float(tail.size)


def _t_slope(values: np.ndarray, n: int) -> float:
    if n < 2 or not _guard(values, n):
        return NAN
    tail = values[-n:]
    x = np.arange(tail.size, dtype=np.float64)
    x_centred = x - x.mean()
    denominator = float((x_centred * x_centred).sum())
    if denominator <= 0.0:
        return NAN
    return float((x_centred * (tail - tail.mean())).sum() / denominator)


def _t_accel(values: np.ndarray, n: int) -> float:
    need = 2 * n + 1
    if not _guard(values, need):
        return NAN
    tail = values[-need:]
    return float((tail[-1] - tail[-1 - n]) - (tail[-1 - n] - tail[0]))


def _t_change(values: np.ndarray, n: int) -> float:
    if not _guard(values, n + 1):
        return NAN
    tail = values[-(n + 1) :]
    return float(tail[-1] - tail[0])


def _t_pct_change(values: np.ndarray, n: int) -> float:
    if not _guard(values, n + 1):
        return NAN
    tail = values[-(n + 1) :]
    base = abs(float(tail[0]))
    if base <= 0.0:
        return NAN
    return float((tail[-1] - tail[0]) / base)


def _t_ewm(values: np.ndarray, n: int) -> float:
    depth = 5 * n
    if not _guard(values, depth):
        return NAN
    tail = values[-depth:]
    alpha = 2.0 / (max(2, n) + 1.0)
    weights = (1.0 - alpha) ** np.arange(tail.size - 1, -1, -1)
    return float((tail * weights).sum() / weights.sum())


def _t_max(values: np.ndarray, n: int) -> float:
    return float(values[-n:].max()) if _guard(values, n) else NAN


def _t_min(values: np.ndarray, n: int) -> float:
    return float(values[-n:].min()) if _guard(values, n) else NAN


def _t_persistence(values: np.ndarray, n: int) -> float:
    if not _guard(values, n):
        return NAN
    tail = values[-n:]
    return float((tail > 0.0).sum()) / float(tail.size)


TRANSFORMS: dict[str, Callable[[np.ndarray, int], float]] = {
    "mean": _t_mean,
    "stdev": _t_stdev,
    "zscore": _t_zscore,
    "percentile_rank": _t_percentile_rank,
    "slope": _t_slope,
    "accel": _t_accel,
    "change": _t_change,
    "pct_change": _t_pct_change,
    "ewm": _t_ewm,
    "max_of": _t_max,
    "min_of": _t_min,
    "persistence": _t_persistence,
}


def apply_transform(kind: str, values: np.ndarray, length: int) -> float:
    """Run one transformation over a source's history.

    ``values`` is the source's own series, oldest first, ending at the bar being
    evaluated. Nothing in here indexes past the end of it, which is what makes
    the transformation causal for the same reason the observations are.
    """
    try:
        function = TRANSFORMS[kind]
    except KeyError:
        raise KeyError(f"'{kind}' is not a series transformation") from None
    return function(np.asarray(values, dtype=np.float64), max(1, int(length)))
