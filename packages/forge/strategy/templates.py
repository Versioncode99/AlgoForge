from __future__ import annotations

from dataclasses import dataclass

from forge.strategy.models import ParameterSpec

# Each template is real, readable, runnable Python. It is written to disk under
# strategies/<id>/strategy.py, is editable by hand, and is what the backtester
# actually imports and executes. Nothing here is a placeholder.


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    family: str
    hypothesis: str
    falsifiable_prediction: str
    parameters: tuple[ParameterSpec, ...]
    warmup_bars: int
    source: str
    data_requirement: str = "BARS"
    minimum_timeframe: str = "1m"
    research_status: str = "RUNNABLE"


MOMENTUM_BREAKOUT = Template(
    key="momentum_breakout",
    name="Momentum Breakout",
    family="breakout",
    hypothesis=(
        "When price closes above the highest high of the prior N bars, a portion of that move "
        "is driven by liquidity-taking flow that must be absorbed over subsequent bars, so "
        "short-horizon continuation should follow the break rather than immediate reversion."
    ),
    falsifiable_prediction=(
        "Continuation must be stronger when the breakout occurs on above-median volume. If the "
        "low-volume subset performs equally or better, the stated absorption mechanism is wrong "
        "and this hypothesis is abandoned regardless of aggregate profitability."
    ),
    parameters=(
        ParameterSpec(
            name="lookback",
            default=20,
            low=5,
            high=80,
            step=5,
            description="Bars used for the breakout high.",
        ),
        ParameterSpec(
            name="max_bars",
            default=12,
            low=3,
            high=60,
            step=3,
            description="Hard time stop, in bars.",
        ),
        ParameterSpec(
            name="stop_atr",
            default=2.0,
            low=0.5,
            high=5.0,
            step=0.5,
            description="Stop distance as a multiple of ATR(14).",
        ),
    ),
    warmup_bars=90,
    source='''"""Momentum Breakout.

Enter long when the last closed bar breaks the prior `lookback` high.
Exit on an ATR stop or a hard time stop.

The Window handed in ends at the last CLOSED bar. Fills happen on the next bar's
open, so this module cannot see the price it will trade at.
"""


def entry_signal(w, p):
    lookback = int(p["lookback"])
    prior_high = w.highs[-lookback - 1 : -1].max()
    if w.closes[-1] > prior_high:
        return 1
    return None


def exit_signal(w, p, pos):
    held = w.index - pos.entry_index
    if held >= int(p["max_bars"]):
        return "max_bars"
    atr = w.atr(14)
    if atr == atr:  # not NaN
        stop = pos.entry_price - float(p["stop_atr"]) * atr * pos.direction
        if pos.direction == 1 and w.lows[-1] <= stop:
            return "stop"
        if pos.direction == -1 and w.highs[-1] >= stop:
            return "stop"
    return None
''',
)


MEAN_REVERSION_BAND = Template(
    key="mean_reversion_band",
    name="Mean Reversion Band",
    family="mean_reversion",
    hypothesis=(
        "After price closes more than k ATRs below its own moving average, the displacement is "
        "disproportionately the result of impatient selling into a thin book, and price should "
        "revert toward the average as liquidity replenishes over the following bars."
    ),
    falsifiable_prediction=(
        "Reversion must be measurably stronger in the low-volatility half of the sample than in "
        "the high-volatility half, because absorption is easier in a calm book. Equal or inverted "
        "behaviour disproves the mechanism."
    ),
    parameters=(
        ParameterSpec(
            name="sma_period",
            default=30,
            low=10,
            high=120,
            step=10,
            description="Moving average period.",
        ),
        ParameterSpec(
            name="entry_atr",
            default=1.5,
            low=0.5,
            high=4.0,
            step=0.25,
            description="Entry displacement in ATR(14) multiples.",
        ),
        ParameterSpec(
            name="max_bars",
            default=25,
            low=5,
            high=90,
            step=5,
            description="Hard time stop, in bars.",
        ),
    ),
    warmup_bars=140,
    source='''"""Mean Reversion Band.

Enter long when the close sits more than `entry_atr` ATRs below the moving
average. Exit when price touches the average again, or on a time stop.
"""


def entry_signal(w, p):
    sma = w.sma(int(p["sma_period"]))
    atr = w.atr(14)
    if sma != sma or atr != atr or atr <= 0:
        return None
    if w.closes[-1] < sma - float(p["entry_atr"]) * atr:
        return 1
    return None


def exit_signal(w, p, pos):
    if w.index - pos.entry_index >= int(p["max_bars"]):
        return "max_bars"
    sma = w.sma(int(p["sma_period"]))
    if sma == sma and w.closes[-1] >= sma:
        return "signal"
    return None
''',
)


VOLATILITY_REGIME = Template(
    key="volatility_regime",
    name="Volatility Regime Momentum",
    family="volatility",
    hypothesis=(
        "Trend continuation is conditional on the volatility regime: in calm regimes order flow "
        "is more persistent, so a simple moving-average cross should carry information, while in "
        "turbulent regimes the same signal is dominated by noise and should be suppressed."
    ),
    falsifiable_prediction=(
        "The calm-regime subset must outperform the turbulent-regime subset by a clear margin. "
        "If the edge is equal or stronger in turbulence, the regime-conditioning premise is false."
    ),
    parameters=(
        ParameterSpec(
            name="fast", default=10, low=3, high=40, step=1, description="Fast moving average."
        ),
        ParameterSpec(
            name="slow", default=40, low=20, high=150, step=10, description="Slow moving average."
        ),
        ParameterSpec(
            name="vol_lookback",
            default=60,
            low=20,
            high=200,
            step=20,
            description="Bars used to measure the volatility regime.",
        ),
        ParameterSpec(
            name="vol_percentile",
            default=0.6,
            low=0.2,
            high=0.95,
            step=0.05,
            description="Trade only when realised vol is below this quantile.",
        ),
    ),
    warmup_bars=220,
    source='''"""Volatility Regime Momentum.

A fast/slow moving-average cross, but only permitted to trade while realised
volatility sits below its own recent quantile. The regime filter is the claim;
the cross is the vehicle.
"""

import numpy as np


def _regime_calm(w, p):
    n = int(p["vol_lookback"])
    if w.closes.size < n + 2:
        return False
    r = np.diff(np.log(w.closes[-n - 1 :]))
    window = np.lib.stride_tricks.sliding_window_view(r, 10)
    if window.shape[0] < 5:
        return False
    rolling = window.std(axis=1, ddof=1)
    return bool(rolling[-1] <= np.quantile(rolling, float(p["vol_percentile"])))


def entry_signal(w, p):
    fast, slow = int(p["fast"]), int(p["slow"])
    if fast >= slow or not _regime_calm(w, p):
        return None
    if w.sma(fast) > w.sma(slow):
        return 1
    return None


def exit_signal(w, p, pos):
    fast, slow = int(p["fast"]), int(p["slow"])
    if w.sma(fast) < w.sma(slow):
        return "signal"
    if not _regime_calm(w, p):
        return "signal"
    return None
''',
)


TEMPLATES: dict[str, Template] = {
    t.key: t for t in (MOMENTUM_BREAKOUT, MEAN_REVERSION_BAND, VOLATILITY_REGIME)
}


TEST_TEMPLATE = '''"""Auto-generated conformance tests for {name}.

Every strategy ships a lookahead trap. A suite without one is rejected by the
harness, because the cheapest way to fake an edge is to read the future.
"""

import numpy as np

from forge.strategy.runtime import Window


def _window(n=300, seed=7):
    rng = np.random.default_rng(seed)
    close = 20000 + np.cumsum(rng.normal(0, 4, n))
    high, low = close + 3, close - 3
    return Window(close, high, low, close, np.full(n, 1000.0), [None] * n, n - 1)


def test_entry_signal_returns_valid_direction():
    w = _window()
    result = entry_signal(w, PARAMS)
    assert result in (1, -1, None)


def test_window_cannot_see_the_future():
    """The window must expose exactly index+1 bars and nothing beyond."""
    w = _window(n=300)
    assert w.closes.size == 300
    w2 = Window(w.opens, w.highs, w.lows, w.closes, w.volumes, [None] * 300, 99)
    assert w2.closes.size == 100, "window leaked bars past its end index"


def test_signal_is_deterministic():
    a, b = _window(), _window()
    assert entry_signal(a, PARAMS) == entry_signal(b, PARAMS)
'''
