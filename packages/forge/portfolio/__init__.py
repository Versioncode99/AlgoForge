"""Portfolio construction: from forecasts, through covariance and constraints, to sizes.

A signal is a forecast. A holding is a decision. This package is the step
between them, and it is deliberately separate from research so that neither can
quietly become the other.
"""

from forge.portfolio.construction import FLAT, ConstructionError, construct
from forge.portfolio.covariance import (
    MINIMUM_OBSERVATIONS,
    CovarianceEstimate,
    estimate,
)
from forge.portfolio.models import (
    AlphaSignal,
    ConstraintReport,
    Constraints,
    Exposures,
    Holding,
    Instrument,
    PortfolioProposal,
)

__all__ = [
    "FLAT",
    "MINIMUM_OBSERVATIONS",
    "AlphaSignal",
    "ConstraintReport",
    "Constraints",
    "ConstructionError",
    "CovarianceEstimate",
    "Exposures",
    "Holding",
    "Instrument",
    "PortfolioProposal",
    "construct",
    "estimate",
]
