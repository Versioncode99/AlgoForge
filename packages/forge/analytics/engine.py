from __future__ import annotations

import math

import numpy as np

from forge.contracts.hashing import stable_id
from forge.contracts.models import FrozenModel
from forge.judge import Verdict


class RegimeSummary(FrozenModel):
    """A slice of the P&L series, named for what it is.

    This used to carry `name` values of "TREND", "HIGH_VOL", "RANGE" and
    "LOW_VOL" over four equal chronological chunks of the trade list. Nothing
    measured trend and nothing measured volatility — the first quarter of the
    trades was called TREND because it came first — so a reader was shown a
    claim about the market that had never been made about the market.

    Real regime attribution needs the *bars*, which a P&L series does not carry.
    It lives in `forge.analytics.regime`, which measures trend and volatility
    from price and refuses to label a bar it has too little history for. What
    survives here is what a P&L series can honestly support: performance across
    equal parts of the run, in order, which answers "did this decay?" and
    nothing else.
    """

    name: str
    trade_count: int
    net_pnl: float
    confidence: str
    #: What the slice actually is, so no caller can read a market claim into it.
    basis: str = "chronological_quarter"


class RiskAnalysis(FrozenModel):
    simulation_id: str
    seed: int
    method: str
    path_count: int
    equity_paths: tuple[tuple[float, ...], ...]
    median_path: tuple[float, ...]
    p05_path: tuple[float, ...]
    p95_path: tuple[float, ...]
    terminal_median: float
    loss_probability: float
    var_95: float
    cvar_95: float
    longest_losing_streak: int
    warnings: tuple[str, ...]


class NormalAnalysis(FrozenModel):
    run_id: str
    verdict: Verdict
    regimes: tuple[RegimeSummary, ...]
    risk: RiskAnalysis
    labels: tuple[str, ...] = ("SAMPLE_DATA", "UNCALIBRATED")


def _stationary_blocks(pnl: np.ndarray, paths: int, seed: int, block: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    result = np.empty((paths, pnl.size), dtype=float)
    for path in range(paths):
        cursor = 0
        while cursor < pnl.size:
            start = int(rng.integers(0, pnl.size))
            length = min(block, pnl.size - cursor)
            indices = (start + np.arange(length)) % pnl.size
            result[path, cursor : cursor + length] = pnl[indices]
            cursor += length
    return result


def _longest_losing_streak(pnl: np.ndarray) -> int:
    longest = current = 0
    for value in pnl:
        current = current + 1 if value < 0 else 0
        longest = max(longest, current)
    return longest


def build_normal_analysis(
    run_id: str,
    verdict: Verdict,
    pnl_values: tuple[float, ...],
    *,
    paths: int = 240,
    seed: int = 20260901,
) -> NormalAnalysis:
    pnl = np.asarray(pnl_values, dtype=float)
    if paths < 20:
        raise ValueError("at least 20 paths are required")
    block = max(2, round(pnl.size ** (1 / 3)))
    samples = _stationary_blocks(pnl, paths, seed, block)
    equity = np.cumsum(samples, axis=1)
    median = np.percentile(equity, 50, axis=0)
    p05 = np.percentile(equity, 5, axis=0)
    p95 = np.percentile(equity, 95, axis=0)
    terminals = equity[:, -1]
    threshold = float(np.percentile(terminals, 5))
    tail = terminals[terminals <= threshold]
    warnings = ["EVT_SUPPRESSED_INADEQUATE_EXCEEDANCES"] if pnl.size < 100 else []
    risk = RiskAnalysis(
        simulation_id=stable_id(
            "risk", {"run": run_id, "seed": seed, "paths": paths, "method": "stationary-block"}
        ),
        seed=seed,
        method=f"stationary-block-{block}",
        path_count=paths,
        equity_paths=tuple(tuple(round(value, 4) for value in row) for row in equity[:80]),
        median_path=tuple(round(value, 4) for value in median),
        p05_path=tuple(round(value, 4) for value in p05),
        p95_path=tuple(round(value, 4) for value in p95),
        terminal_median=round(float(np.median(terminals)), 4),
        loss_probability=round(float(np.mean(terminals < 0)), 6),
        var_95=round(-threshold, 4),
        cvar_95=round(-float(np.mean(tail)), 4),
        longest_losing_streak=_longest_losing_streak(pnl),
        warnings=tuple(warnings),
    )
    # Four equal parts of the run, in order, named for what they are. See
    # RegimeSummary: these were previously labelled with market regimes they had
    # never been measured against.
    labels = ("Q1 (earliest)", "Q2", "Q3", "Q4 (latest)")
    chunks = np.array_split(pnl, len(labels))
    regimes = tuple(
        RegimeSummary(
            name=name,
            trade_count=len(chunk),
            net_pnl=round(float(chunk.sum()), 2),
            confidence="LOW" if len(chunk) < 20 else "MEDIUM",
        )
        for name, chunk in zip(labels, chunks, strict=True)
    )
    if not math.isfinite(risk.cvar_95):
        raise ValueError("non-finite risk output")
    return NormalAnalysis(run_id=run_id, verdict=verdict, regimes=regimes, risk=risk)
