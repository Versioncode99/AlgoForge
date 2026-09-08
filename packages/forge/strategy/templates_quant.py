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
filter. Enters against a stretch away from VWAP in a ranging market, from either
side; exits on the VWAP touch, an ATR stop, or a time stop.
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
    band = float(p["entry_sigma"]) * sigma
    if w.closes[-1] < vwap - band:
        return 1
    if w.closes[-1] > vwap + band:
        return -1
    return None


def exit_signal(w, p, pos):
    if w.index - pos.entry_index >= int(p["max_bars"]):
        return "max_bars"
    vwap, _ = w.session_vwap()
    if vwap == vwap:
        if pos.direction == 1 and w.closes[-1] >= vwap:
            return "signal"
        if pos.direction == -1 and w.closes[-1] <= vwap:
            return "signal"
    atr = w.atr(14)
    if atr == atr:
        stop = pos.entry_price - float(p["stop_atr"]) * atr * pos.direction
        if pos.direction == 1 and w.lows[-1] <= stop:
            return "stop"
        if pos.direction == -1 and w.highs[-1] >= stop:
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
close outside it while the longer-term average agrees with the direction. Wide
stop, tighter target - the convex geometry used in the prop-account variants.

Both sides: a session that opens and sells off is the same structure as one that
opens and rallies, and a long-only version of this measures the index drift.
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
    close = w.closes[-1]
    if close > high and close > bias:
        return 1
    if close < low and close < bias:
        return -1
    return None


def exit_signal(w, p, pos):
    atr = w.atr(14)
    if atr == atr:
        target = pos.entry_price + float(p["target_atr"]) * atr * pos.direction
        stop = pos.entry_price - float(p["stop_atr"]) * atr * pos.direction
        # Stop checked first: a bar that reached both is reported as the stop,
        # because the order inside the bar is unknown.
        if pos.direction == 1:
            if w.lows[-1] <= stop:
                return "stop"
            if w.highs[-1] >= target:
                return "signal"
        else:
            if w.highs[-1] >= stop:
                return "stop"
            if w.lows[-1] <= target:
                return "signal"
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

Finds a swing extreme, waits for price to trade through it and then close back
inside within a short window. The sweep alone is not the signal; the reclaim is.

Mirrored: a sweep of a swing high that fails and closes back below it is the
same event as a swept low that closes back above, and both are the mechanism.
"""

import numpy as np


def entry_signal(w, p):
    lookback = int(p["lookback"])
    reclaim = int(p["reclaim_bars"])
    if w.closes.size < lookback + reclaim + 2:
        return None

    # The levels are taken from the window that ended before the reclaim window,
    # so a sweep cannot define the level it swept.
    low_level = float(np.min(w.lows[-(lookback + reclaim) : -reclaim]))
    high_level = float(np.max(w.highs[-(lookback + reclaim) : -reclaim]))
    close = w.closes[-1]

    swept_low = bool(np.any(w.lows[-reclaim:] < low_level))
    if swept_low and close > low_level:
        return 1
    swept_high = bool(np.any(w.highs[-reclaim:] > high_level))
    if swept_high and close < high_level:
        return -1
    return None


def exit_signal(w, p, pos):
    if w.index - pos.entry_index >= int(p["max_bars"]):
        return "max_bars"
    atr = w.atr(14)
    if atr == atr:
        stop = pos.entry_price - float(p["stop_atr"]) * atr * pos.direction
        target = pos.entry_price + 2.0 * float(p["stop_atr"]) * atr * pos.direction
        if pos.direction == 1:
            if w.lows[-1] <= stop:
                return "stop"
            if w.highs[-1] >= target:
                return "signal"
        else:
            if w.highs[-1] >= stop:
                return "stop"
            if w.lows[-1] <= target:
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
break. The compression is the claim; the breakout is only the entry vehicle,
and it is taken in whichever direction the range breaks.
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
    close = w.closes[-1]
    if close > float(np.max(w.highs[-lookback - 1:-1])):
        return 1
    if close < float(np.min(w.lows[-lookback - 1:-1])):
        return -1
    return None


def exit_signal(w, p, pos):
    if w.index - pos.entry_index >= int(p["max_bars"]):
        return "max_bars"
    atr = w.atr(14)
    if atr == atr:
        stop = pos.entry_price - float(p["stop_atr"]) * atr * pos.direction
        if pos.direction == 1 and w.lows[-1] <= stop:
            return "stop"
        if pos.direction == -1 and w.highs[-1] >= stop:
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
    source='''"""Volume Surge Continuation.

A volume surge with a wide directional body, in the direction the longer trend
already points. Symmetric: a surge of selling into a downtrend is the same
claim about participation as a surge of buying into an uptrend.
"""

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
    if zscore < float(p["volume_z"]):
        return None
    atr = w.atr(14)
    trend = w.sma(int(p["trend_period"]))
    if atr != atr or trend != trend:
        return None
    body = w.closes[-1] - w.opens[-1]
    threshold = float(p["body_atr"]) * atr
    if body >= threshold and w.closes[-1] > trend:
        return 1
    if body <= -threshold and w.closes[-1] < trend:
        return -1
    return None


def exit_signal(w, p, pos):
    if w.index - pos.entry_index >= int(p["max_bars"]):
        return "max_bars"
    trend = w.sma(int(p["trend_period"]))
    if trend != trend:
        return None
    # Out when price loses the trend that justified the entry.
    if pos.direction == 1 and w.closes[-1] < trend:
        return "signal"
    if pos.direction == -1 and w.closes[-1] > trend:
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
    source='''"""Trend Pullback Resume.

A pullback to the fast average inside an established trend, entered on the
resumption. Taken in whichever direction the trend runs: a rally back to the
fast average in a downtrend is the same structure as a dip to it in an uptrend.
"""


def entry_signal(w, p):
    fast_n, slow_n = int(p["fast"]), int(p["slow"])
    if fast_n >= slow_n:
        return None
    fast, slow, atr = w.sma(fast_n), w.sma(slow_n), w.atr(14)
    if fast != fast or slow != slow or atr != atr:
        return None
    touch = float(p["touch_atr"]) * atr
    if fast > slow:
        touched = w.lows[-1] <= fast + touch
        reclaimed = w.closes[-1] > fast and w.closes[-1] > w.opens[-1]
        if touched and reclaimed:
            return 1
    elif fast < slow:
        touched = w.highs[-1] >= fast - touch
        rejected = w.closes[-1] < fast and w.closes[-1] < w.opens[-1]
        if touched and rejected:
            return -1
    return None


def exit_signal(w, p, pos):
    if w.index - pos.entry_index >= int(p["max_bars"]):
        return "max_bars"
    atr = w.atr(14)
    if atr == atr:
        stop = pos.entry_price - float(p["stop_atr"]) * atr * pos.direction
        if pos.direction == 1 and w.lows[-1] <= stop:
            return "stop"
        if pos.direction == -1 and w.highs[-1] >= stop:
            return "stop"
    fast, slow = w.sma(int(p["fast"])), w.sma(int(p["slow"]))
    if fast != fast or slow != slow:
        return None
    # Out when the trend that justified the entry has turned.
    if pos.direction == 1 and fast < slow:
        return "signal"
    if pos.direction == -1 and fast > slow:
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
