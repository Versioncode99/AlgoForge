"""Currency-space metrics.

Ratio statistics that carry a distributional claim live in
:mod:`forge.judge.statistics`. The two here are pure arithmetic over a P&L
series and need no such care.

``safe_sharpe`` and ``adjusted_sharpe`` used to live here. Both were removed
rather than deprecated: the first returned ``mean / sd * sqrt(n)``, which is a
t-statistic that rewards trading more often, and the second subtracted an
invented ``0.35 * sqrt(log(trials))`` penalty with no distribution behind it.
Use :func:`forge.judge.statistics.per_period_sharpe` and
:func:`forge.judge.statistics.deflated_sharpe_ratio` instead.
"""

from __future__ import annotations

import numpy as np


def max_drawdown(pnl: np.ndarray) -> float:
    """Largest peak-to-trough fall of the cumulative P&L curve."""
    equity = np.concatenate(([0.0], np.cumsum(pnl)))
    peaks = np.maximum.accumulate(equity)
    return float(np.max(peaks - equity))


def profit_factor(pnl: np.ndarray) -> float:
    """Gross gains over gross losses. 999.0 stands in for "no losing trades"."""
    gains = float(pnl[pnl > 0].sum())
    losses = float(-pnl[pnl < 0].sum())
    return gains / losses if losses else (999.0 if gains else 0.0)
