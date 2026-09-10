"""Selection-bias-aware performance statistics.

The gates in :mod:`forge.judge.engine` decide whether a strategy is evidence or
noise. That decision is only as good as the statistics underneath it, so this
module implements the published estimators rather than home-made proxies:

* **PSR** — Probabilistic Sharpe Ratio, Bailey & López de Prado (2012).
  ``P(true SR > benchmark)`` given track-record length, skewness and kurtosis.
* **DSR** — Deflated Sharpe Ratio, Bailey & López de Prado (2014). PSR measured
  against the Sharpe you would *expect* to see as the best of ``N`` trials on
  pure noise, so a search that tried thousands of variants pays for it.
* **PBO** — Probability of Backtest Overfitting via Combinatorially Symmetric
  Cross-Validation, Bailey, Borwein, López de Prado & Zhu (2014). Asks whether
  the *selection procedure* generalises, without distributional assumptions.

Two conventions are enforced throughout, because mixing them is the usual way
these numbers get quietly corrupted:

1. Sharpe ratios are **per period** unless a function name says ``annualised``.
   PSR and DSR are only valid when the Sharpe and the observation count ``T``
   describe the same series, so the per-period form is the one that composes.
2. Kurtosis is **non-excess** (3.0 for a normal distribution), matching the
   ``γ₄`` in the source papers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from typing import Any

import numpy as np

# Euler-Mascheroni constant, from the Gumbel approximation to the expected
# maximum of N independent draws.
_EULER_MASCHERONI = 0.5772156649015329

_normal_cache: Any = None


def _normal() -> Any:
    """``scipy.stats.norm``, imported the first time a gate needs it.

    Importing ``scipy.stats`` costs 1.08 seconds and drags ``scipy.optimize``,
    ``scipy.spatial`` and ``scipy.sparse`` in behind it. Nothing evaluates a
    gate while the application is starting, so paying that at import time made
    every launch wait for a distribution object the first backtest would not
    reach for minutes.

    This defers the import; it reimplements nothing. The CDF and the quantile
    function are scipy's own, to the last bit. Substituting an approximation
    here would silently change PSR and DSR, which is exactly the kind of
    corruption these estimators exist to detect.
    """
    global _normal_cache
    if _normal_cache is None:
        from scipy.stats import norm

        _normal_cache = norm
    return _normal_cache

# Trading periods per year for the frequencies the platform actually loads.
PERIODS_PER_YEAR: dict[str, float] = {
    "1m": 252 * 6.5 * 60,
    "5m": 252 * 6.5 * 12,
    "1h": 252 * 6.5,
    "1d": 252,
    "trade": 252,
}


def _clean(values: np.ndarray | tuple[float, ...]) -> np.ndarray:
    series = np.asarray(values, dtype=np.float64).ravel()
    if series.size == 0:
        raise ValueError("empty return series")
    if not np.isfinite(series).all():
        raise ValueError("return series contains non-finite values")
    return series


def per_period_sharpe(returns: np.ndarray | tuple[float, ...]) -> float:
    """Mean over standard deviation, in the units of one observation.

    Deliberately *not* multiplied by ``sqrt(n)``: that product is a
    t-statistic, and using it as a Sharpe makes a strategy look better simply
    for trading more often.
    """
    series = _clean(returns)
    if series.size < 2:
        return 0.0
    deviation = float(np.std(series, ddof=1))
    return 0.0 if deviation == 0.0 else float(np.mean(series)) / deviation


def annualised_sharpe(
    returns: np.ndarray | tuple[float, ...], periods_per_year: float = 252.0
) -> float:
    """Per-period Sharpe scaled to a year. Reporting only — never feed to PSR."""
    return per_period_sharpe(returns) * math.sqrt(max(periods_per_year, 0.0))


def t_statistic(returns: np.ndarray | tuple[float, ...]) -> float:
    """``mean / stderr``. Grows with sample size; this is the honest name for it."""
    series = _clean(returns)
    if series.size < 2:
        return 0.0
    return per_period_sharpe(series) * math.sqrt(series.size)


def skewness(returns: np.ndarray | tuple[float, ...]) -> float:
    series = _clean(returns)
    deviation = float(np.std(series, ddof=0))
    if deviation == 0.0 or series.size < 3:
        return 0.0
    centred = (series - float(np.mean(series))) / deviation
    return float(np.mean(centred**3))


def kurtosis(returns: np.ndarray | tuple[float, ...]) -> float:
    """Non-excess kurtosis: 3.0 for a normal distribution."""
    series = _clean(returns)
    deviation = float(np.std(series, ddof=0))
    if deviation == 0.0 or series.size < 4:
        return 3.0
    centred = (series - float(np.mean(series))) / deviation
    return float(np.mean(centred**4))


def _psr_denominator(sharpe: float, skew: float, kurt: float) -> float:
    """Standard error term of the Sharpe estimator under non-normal returns."""
    return 1.0 - skew * sharpe + (kurt - 1.0) / 4.0 * sharpe**2


def probabilistic_sharpe_ratio(
    returns: np.ndarray | tuple[float, ...], benchmark_sharpe: float = 0.0
) -> float:
    """``P(true per-period SR > benchmark_sharpe)``.

    Corrects the raw Sharpe for three things it ignores: how long the track
    record is, whether losses are fat-tailed, and whether the return
    distribution is skewed.
    """
    series = _clean(returns)
    if series.size < 2:
        return 0.0
    sharpe = per_period_sharpe(series)
    variance = _psr_denominator(sharpe, skewness(series), kurtosis(series))
    if variance <= 0.0:
        # Non-normality overwhelms the estimator; refuse to claim significance.
        return 0.0
    statistic = (sharpe - benchmark_sharpe) * math.sqrt(series.size - 1) / math.sqrt(variance)
    return float(_normal().cdf(statistic))


def expected_max_sharpe(trials: int, sharpe_variance: float = 1.0) -> float:
    """Expected best per-period Sharpe from ``trials`` independent noise runs.

    This is the hurdle a genuine discovery must clear. It rises with the number
    of things tried, which is precisely why an unrecorded search is worthless
    as evidence.
    """
    count = max(int(trials), 1)
    deviation = math.sqrt(max(sharpe_variance, 0.0))
    if count == 1 or deviation == 0.0:
        return 0.0
    gamma = _EULER_MASCHERONI
    # Gumbel approximation to the maximum of N standard normals.
    first = _normal().ppf(1.0 - 1.0 / count)
    second = _normal().ppf(1.0 - 1.0 / (count * math.e))
    return float(deviation * ((1.0 - gamma) * first + gamma * second))


@dataclass(frozen=True)
class DeflatedSharpe:
    """A DSR together with every quantity that produced it, for the trace."""

    deflated_sharpe_ratio: float
    probabilistic_sharpe_ratio: float
    observed_sharpe: float
    benchmark_sharpe: float
    trials: int
    sharpe_variance: float
    observations: int
    skewness: float
    kurtosis: float
    # Why the spread could not be used, if it could not. Empty when the
    # deflation rests on a measured spread.
    spread_problem: str = ""

    @property
    def spread_is_measured(self) -> bool:
        """Whether ``benchmark_sharpe`` is a hurdle or a placeholder.

        A search whose configurations all scored the same gives ``V[SR] = 0``,
        which makes ``expected_max_sharpe`` return 0.0 and silently turns the
        Deflated Sharpe back into a plain Probabilistic Sharpe — no deflation at
        all, for a search that may have tried a thousand things. That is the
        single most dangerous way for this estimator to fail, because it fails
        *permissively* and looks identical to a well-deflated result.
        """
        return not self.spread_problem

    @property
    def credible(self) -> bool:
        """The conventional 95% bar for treating a discovery as real."""
        return self.spread_is_measured and self.deflated_sharpe_ratio >= 0.95


def deflated_sharpe_ratio(
    returns: np.ndarray | tuple[float, ...],
    trials: int,
    trial_sharpes: np.ndarray | tuple[float, ...] | None = None,
) -> DeflatedSharpe:
    """PSR measured against the best-of-``trials`` noise hurdle.

    ``trial_sharpes`` are the per-period Sharpes of every configuration in the
    search. Their spread estimates ``V[SR]``; passing them is strongly
    preferred, because the fallback of ``V[SR] = 1`` is an assumption, not a
    measurement.
    """
    series = _clean(returns)
    variance, problem = _trial_spread(trial_sharpes)
    benchmark = expected_max_sharpe(trials, variance)
    return DeflatedSharpe(
        deflated_sharpe_ratio=probabilistic_sharpe_ratio(series, benchmark),
        probabilistic_sharpe_ratio=probabilistic_sharpe_ratio(series, 0.0),
        observed_sharpe=per_period_sharpe(series),
        benchmark_sharpe=benchmark,
        trials=max(int(trials), 1),
        sharpe_variance=variance,
        observations=int(series.size),
        skewness=skewness(series),
        kurtosis=kurtosis(series),
        spread_problem=problem,
    )


# Below this, the spread across the search is treated as no spread at all. It is
# relative rather than absolute because Sharpes are scale-free but not
# magnitude-free: a search clustered around 3.0 and one clustered around 0.001
# should both count as degenerate when their configurations agree to twelve
# digits.
_DEGENERATE_SPREAD = 1e-12


def _trial_spread(trial_sharpes: np.ndarray | tuple[float, ...] | None) -> tuple[float, str]:
    """``V[SR]`` from the search, plus why it is unusable when it is.

    Three failures are distinguished, because they mean different things:

    * **absent** — nobody recorded the search. ``V[SR] = 1`` is then an
      assumption in per-trade units, not a measurement, and is flagged as one.
    * **non-finite** — a NaN or infinity got into the trial Sharpes. Previously
      this propagated straight through ``np.var`` into the DSR, producing a
      ``nan`` that ``nan >= 0.95`` then quietly turned into a gate failure, and
      a bare ``NaN`` token in the JSON response that no strict parser accepts.
    * **degenerate** — every configuration scored the same, so the estimated
      spread is zero and the best-of-N hurdle collapses to zero with it.
    """
    if trial_sharpes is None:
        return 1.0, "V[SR]_ASSUMED_NOT_MEASURED"
    candidates = np.asarray(trial_sharpes, dtype=np.float64).ravel()
    if candidates.size <= 1:
        return 1.0, "V[SR]_ASSUMED_NOT_MEASURED"
    if not np.isfinite(candidates).all():
        return 1.0, "TRIAL_SHARPES_NON_FINITE"
    spread = float(np.ptp(candidates))
    scale = max(1.0, float(np.max(np.abs(candidates))))
    if spread <= _DEGENERATE_SPREAD * scale:
        return 0.0, "TRIAL_SHARPES_HAVE_NO_SPREAD"
    variance = float(np.var(candidates, ddof=1))
    if not math.isfinite(variance) or variance <= 0.0:
        return 0.0, "TRIAL_SHARPES_HAVE_NO_SPREAD"
    return variance, ""


def minimum_track_record_length(
    returns: np.ndarray | tuple[float, ...],
    benchmark_sharpe: float = 0.0,
    confidence: float = 0.95,
) -> float:
    """Observations needed before the Sharpe could clear ``benchmark`` at ``confidence``.

    Returns ``inf`` when the observed Sharpe does not exceed the benchmark at
    all — no amount of further data rescues a strategy that is not ahead.
    """
    series = _clean(returns)
    sharpe = per_period_sharpe(series)
    excess = sharpe - benchmark_sharpe
    if excess <= 0.0:
        return math.inf
    variance = _psr_denominator(sharpe, skewness(series), kurtosis(series))
    if variance <= 0.0:
        return math.inf
    return float(1.0 + variance * (_normal().ppf(confidence) / excess) ** 2)


@dataclass(frozen=True)
class BacktestOverfitting:
    """CSCV result: how often in-sample selection fails out of sample."""

    probability: float
    splits: int
    trials: int
    blocks: int
    logits: tuple[float, ...]
    median_logit: float

    @property
    def selection_is_unreliable(self) -> bool:
        """At or above 50% the search is no better than picking at random."""
        return self.probability >= 0.5


def probability_of_backtest_overfitting(
    trial_returns: np.ndarray, blocks: int = 8
) -> BacktestOverfitting:
    """PBO via Combinatorially Symmetric Cross-Validation.

    ``trial_returns`` is a ``T x N`` matrix: one column per configuration
    tried, one row per aligned time period. Time is cut into ``blocks``
    contiguous pieces; for every symmetric way of dealing half the blocks to
    in-sample and half to out-of-sample, the in-sample winner is found and its
    rank among the out-of-sample scores recorded. PBO is the share of splits
    where that winner lands in the bottom half.

    Unlike the Deflated Sharpe Ratio this makes no distributional assumption —
    it tests the selection rule itself. The two answer different questions and
    a serious result should survive both.
    """
    matrix = np.asarray(trial_returns, dtype=np.float64)
    if matrix.ndim != 2:
        raise ValueError("trial_returns must be a 2-D T x N matrix")
    periods, trials = matrix.shape
    if trials < 2:
        raise ValueError("PBO needs at least 2 configurations to choose between")
    if not np.isfinite(matrix).all():
        raise ValueError("trial_returns contains non-finite values")
    if blocks < 2 or blocks % 2 != 0:
        raise ValueError("blocks must be an even number of at least 2")
    if periods < blocks * 2:
        raise ValueError(
            f"INSUFFICIENT_PERIODS: {periods} rows cannot fill {blocks} blocks "
            "with at least 2 observations each"
        )

    groups = np.array_split(np.arange(periods), blocks)
    half = blocks // 2
    logits: list[float] = []
    for in_sample_blocks in combinations(range(blocks), half):
        chosen = set(in_sample_blocks)
        in_rows = np.concatenate([groups[b] for b in sorted(chosen)])
        out_rows = np.concatenate([groups[b] for b in range(blocks) if b not in chosen])
        in_scores = _column_sharpes(matrix[in_rows])
        out_scores = _column_sharpes(matrix[out_rows])
        winner = int(np.argmax(in_scores))
        # Average rank so an exactly-median winner counts as neutral, not overfit.
        rank = float(
            np.sum(out_scores < out_scores[winner])
            + 0.5 * (np.sum(out_scores == out_scores[winner]) - 1)
            + 1.0
        )
        relative = rank / (trials + 1.0)
        relative = min(max(relative, 1e-12), 1.0 - 1e-12)
        logits.append(math.log(relative / (1.0 - relative)))

    values = np.asarray(logits, dtype=np.float64)
    # A split whose winner lands exactly on the out-of-sample median counts as
    # half, for the same reason the rank above uses mid-ranks: a tie is neither
    # a success nor a failure of the selection rule.
    #
    # `<= 0.0` counted every one of them as overfit, and they are not rare.
    # `rank / (N + 1)` is exactly 0.5 whenever the winner takes the middle rank,
    # which is an integer rank only when N is odd -- so the error was invisible
    # for an even number of configurations and large for an odd one. Measured on
    # pure noise, where the true PBO is 0.5 by construction: 3 configurations
    # read 0.70, 5 read 0.66, 15 read 0.59, while 4, 6, 8 and 10 all read within
    # 0.03 of 0.5. The existing regression test used 30 columns and a 0.15
    # tolerance, so it sat in the blind spot.
    #
    # It failed in the punishing direction. PBO gates promotion in
    # `judge.engine` and in `/strategies`, so an inflated value withholds a
    # verdict from a search that was merely neutral. Six identical
    # configurations -- selection among indistinguishable things, the textbook
    # coin flip -- scored 1.0, the most damning value available.
    below = float(np.mean(values < 0.0))
    tied = float(np.mean(values == 0.0))
    return BacktestOverfitting(
        probability=below + 0.5 * tied,
        splits=len(logits),
        trials=trials,
        blocks=blocks,
        logits=tuple(round(float(value), 6) for value in values),
        median_logit=float(np.median(values)),
    )


def _column_sharpes(block: np.ndarray) -> np.ndarray:
    """Per-period Sharpe of every column, with zero-variance columns scored 0."""
    means = block.mean(axis=0)
    deviations = block.std(axis=0, ddof=1) if block.shape[0] > 1 else np.zeros(block.shape[1])
    return np.divide(means, deviations, out=np.zeros_like(means), where=deviations > 0)


def sortino_ratio(returns: np.ndarray | tuple[float, ...], target: float = 0.0) -> float:
    """Return over downside deviation. Upside volatility is not risk."""
    series = _clean(returns)
    shortfall = np.minimum(series - target, 0.0)
    downside = float(np.sqrt(np.mean(shortfall**2)))
    if downside == 0.0:
        return 0.0
    return (float(np.mean(series)) - target) / downside


def calmar_ratio(returns: np.ndarray | tuple[float, ...]) -> float:
    """Total return over maximum drawdown, both in currency units."""
    series = _clean(returns)
    equity = np.concatenate(([0.0], np.cumsum(series)))
    drawdown = float(np.max(np.maximum.accumulate(equity) - equity))
    if drawdown == 0.0:
        return 0.0
    return float(np.sum(series)) / drawdown


def permutation_pvalue(
    returns: np.ndarray | tuple[float, ...], samples: int = 2000, seed: int = 20260906
) -> float:
    """Share of sign-flipped resamples whose Sharpe beats the observed one.

    A distribution-free significance check: under the null that the sequence
    carries no edge, flipping the sign of each observation is equally likely,
    so the observed Sharpe should sit unremarkably inside that spread.
    """
    series = _clean(returns)
    if series.size < 2:
        return 1.0
    observed = per_period_sharpe(series)
    rng = np.random.default_rng(seed)
    signs = rng.choice((-1.0, 1.0), size=(samples, series.size))
    permuted = signs * series
    means = permuted.mean(axis=1)
    deviations = permuted.std(axis=1, ddof=1)
    sharpes = np.divide(means, deviations, out=np.zeros_like(means), where=deviations > 0)
    # +1 in both terms keeps the estimate unbiased and never reports p = 0.
    return float((np.sum(sharpes >= observed) + 1) / (samples + 1))
