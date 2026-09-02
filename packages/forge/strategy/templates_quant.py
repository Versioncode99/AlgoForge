"""Session-aware strategy families.

Modelled on the NinjaTrader strategies in the operator's own library: session
VWAP with sigma bands gated by an ADX regime filter, opening-range breaks with a
trend bias and an entry cutoff, and liquidity sweeps that require a reclaim.

These need more than price arrays — they need to know where they are in the
session — which is why `Window` grew VWAP, opening-range, ADX and session-clock
helpers.
"""

from __future__ import annotations

from forge.strategy.models import ParameterSpec
from forge.strategy.templates import Template

VWAP_SIGMA_REVERSION = Template(
    key="vwap_sigma_reversion",
    name="VWAP Sigma Reversion",
    family="mean_reversion",
    hypothesis=(
        "Intraday price is anchored to session VWAP because that is the benchmark large "
        "participants are filled against. A displacement of k volume-weighted standard "
        "deviations is disproportionately impatient flow rather than genuine repricing, so it "
        "should revert toward VWAP — but only while the market is ranging, since in a trending "
        "regime the same displacement is the trend itself."
    ),
    falsifiable_prediction=(
        "The edge must be concentrated in the low-ADX subset. If reversion is equally strong or "
        "stronger when ADX is above the trend threshold, the ranging-regime premise is false and "
        "the hypothesis is abandoned however profitable it looks in aggregate."
    ),
    parameters=(
        ParameterSpec(
            name="entry_sigma",
            default=2.0,
            low=1.0,
            high=3.5,
            step=0.25,
            description="Displacement from session VWAP, in volume-weighted sigma.",
        ),
        ParameterSpec(
            name="adx_max",
            default=25,
            low=15,
            high=45,
            step=5,
            description="Only trade when ADX(14) is below this — the range-regime gate.",
        ),
        ParameterSpec(
            name="stop_atr",
            default=1.6,
            low=0.75,
            high=4.0,
            step=0.25,
            description="Stop distance in ATR(14) multiples.",
        ),
        ParameterSpec(
            name="max_bars",
            default=40,
            low=10,
            high=120,
            step=10,
            description="Hard time stop, in bars.",
        ),
    ),
    warmup_bars=240,
    source='''"""VWAP Sigma Reversion.

Session-anchored VWAP with volume-weighted sigma bands, gated by an ADX regime
filter. Long when price is stretched below the lower band in a ranging market;
exit on the VWAP touch, an ATR stop, or a time stop.
"""


def entry_signal(w, p):
    vwap, sigma = w.session_vwap()
    if vwap != vwap or sigma != sigma or sigma <= 0:
        return None
    # Needs enough of the session built up for VWAP to mean anything.
    if w.bars_since_session_open() < 20:
        return None
    adx = w.adx(14)
    if adx != adx or adx > float(p["adx_max"]):
        return None
    if w.closes[-1] < vwap - float(p["entry_sigma"]) * sigma:
        return 1
    return None


def exit_signal(w, p, pos):
    if w.index - pos.entry_index >= int(p["max_bars"]):
        return "max_bars"
    vwap, _ = w.session_vwap()
    if vwap == vwap and w.closes[-1] >= vwap:
        return "signal"
    atr = w.atr(14)
    if atr == atr and w.lows[-1] <= pos.entry_price - float(p["stop_atr"]) * atr:
        return "stop"
    return None
''',
)


OPENING_RANGE_BREAK = Template(
    key="opening_range_break",
    name="Opening Range Breakout",
    family="breakout",
    hypothesis=(
        "The first minutes of a session establish a reference range while overnight positioning "
        "is cleared. A break of that range, in the direction the longer-term average already "
        "leans, marks the point where one side has run out of supply, so continuation should "
        "follow within the same session."
    ),
    falsifiable_prediction=(
        "Breaks aligned with the trend bias must outperform breaks against it by a clear margin. "
        "If unaligned breaks do as well, the bias filter carries no information and the stated "
        "exhaustion mechanism is wrong."
    ),
    parameters=(
        ParameterSpec(
            name="range_bars",
            default=15,
            low=5,
            high=60,
            step=5,
            description="Bars from the session open that define the opening range.",
        ),
        ParameterSpec(
            name="bias_period",
            default=50,
            low=20,
            high=200,
            step=10,
            description="Moving average that sets the directional bias.",
        ),
        ParameterSpec(
            name="cutoff_bars",
            default=240,
            low=60,
            high=480,
            step=30,
            description="No new entries after this many bars into the session.",
        ),
        ParameterSpec(
            name="stop_atr",
            default=2.0,
            low=0.75,
            high=4.0,
            step=0.25,
            description="Stop distance in ATR(14) multiples.",
        ),
        ParameterSpec(
            name="target_atr",
            default=1.2,
            low=0.5,
            high=4.0,
            step=0.25,
            description="Target distance in ATR(14) multiples.",
        ),
    ),
    warmup_bars=260,
    source='''"""Opening Range Breakout.

Builds the opening range from the first N bars of each session, then takes a
close above it while the longer-term average agrees. Wide stop, tighter target —
the convex geometry used in the prop-account variants.
"""


def entry_signal(w, p):
    elapsed = w.bars_since_session_open()
    range_bars = int(p["range_bars"])
    # The range must be complete, and the session must not be too far gone.
    if elapsed <= range_bars or elapsed > int(p["cutoff_bars"]):
        return None
    high, low = w.session_range(range_bars)
    if high != high or low != low:
        return None
    bias = w.sma(int(p["bias_period"]))
    if bias != bias:
        return None
    if w.closes[-1] > high and w.closes[-1] > bias:
        return 1
    return None


def exit_signal(w, p, pos):
    atr = w.atr(14)
    if atr == atr:
        if w.highs[-1] >= pos.entry_price + float(p["target_atr"]) * atr:
            return "signal"
        if w.lows[-1] <= pos.entry_price - float(p["stop_atr"]) * atr:
            return "stop"
    # Never carry a session trade into the next session.
    if w.bars_since_session_open() < w.index - pos.entry_index:
        return "signal"
    return None
''',
)


LIQUIDITY_SWEEP_RECLAIM = Template(
    key="liquidity_sweep_reclaim",
    name="Liquidity Sweep Reclaim",
    family="breakout",
    hypothesis=(
        "Resting stops accumulate beneath an obvious prior low. A push through that low which is "
        "immediately reclaimed indicates the move was liquidity collection rather than genuine "
        "repricing: the sellers who triggered are done, and the level should hold from above."
    ),
    falsifiable_prediction=(
        "The reclaim must carry the information. Sweeps that are not reclaimed within the "
        "confirmation window should perform materially worse. If entering on the sweep alone "
        "does as well, the mechanism is disproved."
    ),
    parameters=(
        ParameterSpec(
            name="lookback",
            default=60,
            low=20,
            high=200,
            step=10,
            description="Bars used to locate the swing low that holds the stops.",
        ),
        ParameterSpec(
            name="reclaim_bars",
            default=5,
            low=1,
            high=20,
            step=1,
            description="Bars allowed for price to reclaim the swept level.",
        ),
        ParameterSpec(
            name="stop_atr",
            default=1.5,
            low=0.5,
            high=4.0,
            step=0.25,
            description="Stop distance in ATR(14) multiples.",
        ),
        ParameterSpec(
            name="max_bars",
            default=60,
            low=10,
            high=180,
            step=10,
            description="Hard time stop, in bars.",
        ),
    ),
    warmup_bars=280,
    source='''"""Liquidity Sweep Reclaim.

Finds a swing low, waits for price to trade through it and then close back above
it within a short window. The sweep alone is not the signal; the reclaim is.
"""

import numpy as np


def entry_signal(w, p):
    lookback = int(p["lookback"])
    reclaim = int(p["reclaim_bars"])
    if w.closes.size < lookback + reclaim + 2:
        return None

    # The level is the low of the window that ended before the reclaim window,
    # so the sweep itself cannot define the level it swept.
    level = float(np.min(w.lows[-(lookback + reclaim) : -reclaim]))
    swept = bool(np.any(w.lows[-reclaim:] < level))
    if swept and w.closes[-1] > level:
        return 1
    return None


def exit_signal(w, p, pos):
    if w.index - pos.entry_index >= int(p["max_bars"]):
        return "max_bars"
    atr = w.atr(14)
    if atr == atr:
        if w.lows[-1] <= pos.entry_price - float(p["stop_atr"]) * atr:
            return "stop"
        if w.highs[-1] >= pos.entry_price + 2.0 * float(p["stop_atr"]) * atr:
            return "signal"
    return None
''',
)


VOLATILITY_COMPRESSION_BREAK = Template(
    key="volatility_compression_break",
    name="Volatility Compression Break",
    family="volatility",
    hypothesis=(
        "When short-horizon realised volatility contracts far below its own baseline, resting "
        "liquidity accumulates close to price. A range break after that compression should carry "
        "more information than an ordinary breakout because the market is transitioning from a "
        "low-information state into active price discovery."
    ),
    falsifiable_prediction=(
        "Breakouts preceded by genuine short-versus-long volatility compression must outperform "
        "otherwise identical breaks from normal volatility. If the uncompressed control is equal "
        "or better, the state-transition mechanism is rejected."
    ),
    parameters=(
        ParameterSpec(name="short_vol", default=12, low=6, high=30, step=3),
        ParameterSpec(name="long_vol", default=90, low=40, high=240, step=10),
        ParameterSpec(name="compression", default=0.55, low=0.25, high=0.9, step=0.05),
        ParameterSpec(name="breakout", default=20, low=8, high=80, step=4),
        ParameterSpec(name="max_bars", default=30, low=8, high=120, step=4),
        ParameterSpec(name="stop_atr", default=1.8, low=0.75, high=4.0, step=0.25),
    ),
    warmup_bars=280,
    source='''"""Volatility Compression Break.

Requires a measurable contraction in realised volatility before a prior-range
break. The compression is the claim; the breakout is only the entry vehicle.
"""

import numpy as np


def _compressed(w, p):
    short = int(p["short_vol"])
    long = int(p["long_vol"])
    if short >= long or w.closes.size < long + 2:
        return False
    returns = np.diff(np.log(w.closes[-long - 1:]))
    baseline = float(np.std(returns, ddof=1))
    recent = float(np.std(returns[-short:], ddof=1))
    return baseline > 0 and recent <= float(p["compression"]) * baseline


def entry_signal(w, p):
    lookback = int(p["breakout"])
    if not _compressed(w, p) or w.highs.size < lookback + 1:
        return None
    if w.closes[-1] > float(np.max(w.highs[-lookback - 1:-1])):
        return 1
    return None


def exit_signal(w, p, pos):
    if w.index - pos.entry_index >= int(p["max_bars"]):
        return "max_bars"
    atr = w.atr(14)
    if atr == atr and w.lows[-1] <= pos.entry_price - float(p["stop_atr"]) * atr:
        return "stop"
    return None
''',
)


VOLUME_SURGE_CONTINUATION = Template(
    key="volume_surge_continuation",
    name="Volume Surge Continuation",
    family="momentum",
    hypothesis=(
        "A wide directional close accompanied by statistically unusual volume represents "
        "aggressive participation rather than a thin-book price jump. When the higher-horizon "
        "trend agrees, some inventory should remain to execute and continuation should persist."
    ),
    falsifiable_prediction=(
        "Signals above the preregistered volume z-score must outperform direction-matched bars "
        "with ordinary volume. If volume does not separate the samples, participation is not the "
        "mechanism and the family is retired."
    ),
    parameters=(
        ParameterSpec(name="volume_lookback", default=60, low=20, high=240, step=10),
        ParameterSpec(name="volume_z", default=2.0, low=1.0, high=4.0, step=0.25),
        ParameterSpec(name="trend_period", default=80, low=20, high=240, step=10),
        ParameterSpec(name="body_atr", default=0.8, low=0.25, high=2.5, step=0.25),
        ParameterSpec(name="max_bars", default=20, low=4, high=80, step=4),
    ),
    warmup_bars=280,
    source='''"""Volume Surge Continuation."""

import numpy as np


def entry_signal(w, p):
    n = int(p["volume_lookback"])
    if w.volumes.size < n + 1:
        return None
    history = w.volumes[-n - 1:-1]
    std = float(np.std(history, ddof=1))
    if std <= 0:
        return None
    zscore = (float(w.volumes[-1]) - float(np.mean(history))) / std
    atr = w.atr(14)
    trend = w.sma(int(p["trend_period"]))
    body = w.closes[-1] - w.opens[-1]
    if (
        zscore >= float(p["volume_z"])
        and atr == atr
        and body >= float(p["body_atr"]) * atr
        and trend == trend
        and w.closes[-1] > trend
    ):
        return 1
    return None


def exit_signal(w, p, pos):
    if w.index - pos.entry_index >= int(p["max_bars"]):
        return "max_bars"
    if w.closes[-1] < w.sma(int(p["trend_period"])):
        return "signal"
    return None
''',
)


TREND_PULLBACK_RESUME = Template(
    key="trend_pullback_resume",
    name="Trend Pullback Resume",
    family="momentum",
    hypothesis=(
        "In an established multi-horizon uptrend, a shallow retracement into the fast mean can "
        "clear weak late buyers without changing the slower directional state. A close back above "
        "the fast mean should mark renewed participation with less adverse excursion than chasing."
    ),
    falsifiable_prediction=(
        "Pullback-and-reclaim entries must outperform trend-aligned entries taken without a "
        "pullback. If the reclaim condition adds no improvement in drawdown-adjusted expectancy, "
        "the inventory-reset explanation is false."
    ),
    parameters=(
        ParameterSpec(name="fast", default=24, low=8, high=60, step=4),
        ParameterSpec(name="slow", default=100, low=50, high=240, step=10),
        ParameterSpec(name="touch_atr", default=0.35, low=0.1, high=1.5, step=0.1),
        ParameterSpec(name="stop_atr", default=1.5, low=0.5, high=4.0, step=0.25),
        ParameterSpec(name="max_bars", default=45, low=10, high=160, step=5),
    ),
    warmup_bars=280,
    source='''"""Trend Pullback Resume."""


def entry_signal(w, p):
    fast_n, slow_n = int(p["fast"]), int(p["slow"])
    if fast_n >= slow_n:
        return None
    fast, slow, atr = w.sma(fast_n), w.sma(slow_n), w.atr(14)
    if fast != fast or slow != slow or atr != atr:
        return None
    touched = w.lows[-1] <= fast + float(p["touch_atr"]) * atr
    reclaimed = w.closes[-1] > fast and w.closes[-1] > w.opens[-1]
    if fast > slow and touched and reclaimed:
        return 1
    return None


def exit_signal(w, p, pos):
    if w.index - pos.entry_index >= int(p["max_bars"]):
        return "max_bars"
    atr = w.atr(14)
    if atr == atr and w.lows[-1] <= pos.entry_price - float(p["stop_atr"]) * atr:
        return "stop"
    if w.sma(int(p["fast"])) < w.sma(int(p["slow"])):
        return "signal"
    return None
''',
)


QUANT_TEMPLATES: dict[str, Template] = {
    t.key: t
    for t in (
        VWAP_SIGMA_REVERSION,
        OPENING_RANGE_BREAK,
        LIQUIDITY_SWEEP_RECLAIM,
        VOLATILITY_COMPRESSION_BREAK,
        VOLUME_SURGE_CONTINUATION,
        TREND_PULLBACK_RESUME,
    )
}
