"""Momentum Breakout.

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
