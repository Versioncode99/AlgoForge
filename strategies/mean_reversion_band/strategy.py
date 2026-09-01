"""Mean Reversion Band.

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
