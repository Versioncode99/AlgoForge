from __future__ import annotations

import math

import numpy as np


def max_drawdown(pnl: np.ndarray) -> float:
    equity = np.concatenate(([0.0], np.cumsum(pnl)))
    peaks = np.maximum.accumulate(equity)
    return float(np.max(peaks - equity))


def safe_sharpe(pnl: np.ndarray) -> float:
    if pnl.size < 2:
        return 0.0
    deviation = float(np.std(pnl, ddof=1))
    return 0.0 if deviation == 0 else float(np.mean(pnl) / deviation * math.sqrt(pnl.size))


def profit_factor(pnl: np.ndarray) -> float:
    gains = float(pnl[pnl > 0].sum())
    losses = float(-pnl[pnl < 0].sum())
    return gains / losses if losses else (999.0 if gains else 0.0)


def adjusted_sharpe(sharpe: float, trial_count: int) -> float:
    penalty = math.sqrt(max(math.log(max(trial_count, 1)), 0.0)) * 0.35
    return sharpe - penalty
