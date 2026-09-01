"""Volatility Regime Momentum.

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
