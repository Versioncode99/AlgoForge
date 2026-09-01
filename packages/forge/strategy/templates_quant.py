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


QUANT_TEMPLATES: dict[str, Template] = {
    t.key: t for t in (VWAP_SIGMA_REVERSION, OPENING_RANGE_BREAK, LIQUIDITY_SWEEP_RECLAIM)
}
