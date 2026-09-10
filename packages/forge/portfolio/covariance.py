"""Covariance, estimated locally and honest about how thin the estimate is.

A sample covariance matrix over 40 observations of 20 assets is not an estimate
of anything: it is singular, and an optimiser handed it will happily produce a
portfolio sitting in its null space with an apparent volatility of nearly zero.
That failure does not look like a failure — it looks like a very good portfolio.

So two things happen here. The estimator shrinks toward a structured target
(Ledoit-Wolf, constant correlation), which is the standard local remedy and
needs no library beyond numpy. And the result carries `observations` and
`shrinkage`, so a caller can say "estimated from 60 days with 78% shrinkage"
instead of presenting a number with no provenance.

Nothing here needs a paid data vendor. It runs on whatever return series the
caller already has.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Below this many observations per asset, the sample estimate is reported as
#: unusable rather than shrunk. Shrinkage repairs a noisy estimate; it does not
#: manufacture one from nothing, and pretending otherwise is how a portfolio
#: gets built on eight days of data.
MINIMUM_OBSERVATIONS = 20


@dataclass(frozen=True)
class CovarianceEstimate:
    matrix: np.ndarray
    symbols: tuple[str, ...]
    observations: int
    method: str
    shrinkage: float
    usable: bool
    note: str = ""

    def volatility(self, weights: np.ndarray) -> float:
        """Portfolio standard deviation in the units the returns were given in."""
        variance = float(weights @ self.matrix @ weights)
        # Floating point can put a positive-semidefinite quadratic form a hair
        # below zero. Clamping is correct here; reporting a NaN volatility from
        # a sqrt of -1e-18 is not.
        return float(np.sqrt(max(variance, 0.0)))

    def correlation(self) -> np.ndarray:
        deviation = np.sqrt(np.clip(np.diag(self.matrix), 1e-18, None))
        return np.asarray(self.matrix / np.outer(deviation, deviation), dtype=float)


def estimate(
    returns: np.ndarray,
    symbols: tuple[str, ...],
    *,
    shrink: bool = True,
) -> CovarianceEstimate:
    """Estimate a covariance matrix from a `(observations, assets)` return array.

    `usable=False` is a real answer and callers must handle it. The alternative —
    returning a singular matrix and letting the optimiser find its null space —
    produces a confident portfolio built on an estimate that does not exist.
    """
    array = np.asarray(returns, dtype=float)
    if array.ndim != 2:
        raise ValueError("returns must be a two-dimensional (observations, assets) array")
    observations, assets = array.shape
    if assets != len(symbols):
        raise ValueError(
            f"returns has {assets} columns but {len(symbols)} symbols were named"
        )
    if not np.all(np.isfinite(array)):
        raise ValueError("returns contain non-finite values; clean them before estimating")

    if observations < MINIMUM_OBSERVATIONS:
        return CovarianceEstimate(
            matrix=np.zeros((assets, assets)),
            symbols=symbols,
            observations=observations,
            method="none",
            shrinkage=0.0,
            usable=False,
            note=(
                f"{observations} observations is below the {MINIMUM_OBSERVATIONS} needed "
                "for an estimate; no covariance was produced"
            ),
        )

    centred = array - array.mean(axis=0)
    sample = (centred.T @ centred) / observations
    if not shrink or assets == 1:
        return CovarianceEstimate(
            matrix=sample,
            symbols=symbols,
            observations=observations,
            method="sample",
            shrinkage=0.0,
            usable=True,
        )

    target, intensity = _constant_correlation_target(centred, sample, observations)
    shrunk = intensity * target + (1.0 - intensity) * sample
    note = ""
    # Ten observations per asset is the usual rule of thumb for a covariance
    # estimate anyone should size against. Below it the sample matrix is not
    # singular — `MINIMUM_OBSERVATIONS` already refused that case — but shrinkage
    # is supplying most of the structure, and the reader has to be told that the
    # correlations they are looking at are largely the target's.
    if observations < assets * 10:
        note = (
            f"{observations} observations for {assets} assets: the sample estimate is "
            "poorly conditioned and shrinkage is carrying most of the structure"
        )
    return CovarianceEstimate(
        matrix=shrunk,
        symbols=symbols,
        observations=observations,
        method="ledoit_wolf_constant_correlation",
        shrinkage=round(float(intensity), 4),
        usable=True,
        note=note,
    )


def _constant_correlation_target(
    centred: np.ndarray, sample: np.ndarray, observations: int
) -> tuple[np.ndarray, float]:
    """The Ledoit-Wolf constant-correlation target and its optimal intensity.

    The target keeps each asset's own variance and replaces every pairwise
    correlation with the average one. It is the right target for return data:
    variances differ enormously between instruments and correlations do not
    differ nearly as much, so the structure being imposed is the structure that
    is actually stable.
    """
    assets = sample.shape[0]
    variances = np.clip(np.diag(sample), 1e-18, None)
    deviation = np.sqrt(variances)
    correlation = sample / np.outer(deviation, deviation)
    off = ~np.eye(assets, dtype=bool)
    mean_correlation = float(correlation[off].mean()) if assets > 1 else 0.0

    target = mean_correlation * np.outer(deviation, deviation)
    np.fill_diagonal(target, variances)

    # pi: the summed variance of the sample covariance entries.
    squared = centred**2
    pi_matrix = (squared.T @ squared) / observations - sample**2
    pi = float(pi_matrix.sum())

    # rho: the covariance between the sample entries and the target's entries.
    # The diagonal contributes exactly pi's diagonal; the off-diagonal term is
    # the constant-correlation adjustment.
    term = (centred**3).T @ centred / observations - variances[:, None] * sample
    rho = float(np.diag(pi_matrix).sum())
    if assets > 1:
        ratio = np.outer(deviation, 1.0 / deviation)
        rho += float((mean_correlation / 2.0 * (ratio * term + ratio.T * term.T))[off].sum())

    # gamma: how far the target sits from the sample, in Frobenius norm.
    gamma = float(((target - sample) ** 2).sum())
    if gamma <= 0.0:
        return target, 0.0
    intensity = (pi - rho) / gamma / observations
    return target, float(np.clip(intensity, 0.0, 1.0))
