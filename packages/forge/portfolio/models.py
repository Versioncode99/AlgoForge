"""A signal is not a trade. These are the objects in between.

The distinction this package exists to enforce: research produces a *forecast*,
and a forecast has to survive covariance, constraints, costs and capacity before
it becomes a size. Everything here is the vocabulary for that step.

**What is deliberately absent.** No commercial factor model is installed, so
there is no `factor_returns` table pretending to be Barra or Axioma. Factor
exposure is reported only against loadings the operator supplies, and a
portfolio with no loadings supplied says "no factor model configured" rather
than showing zeros — a zero exposure is a measurement, and this would not be one.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from forge.contracts.models import FrozenModel


class Instrument(FrozenModel):
    """What is known about something tradable.

    `adv_notional` — average daily traded notional — is what makes capacity a
    real constraint rather than a slogan. `None` means unknown, and the
    construction report says the liquidity constraint could not be applied
    rather than applying it against a guess.
    """

    symbol: str = Field(min_length=1, max_length=32)
    asset_class: str = "future"
    sector: str = ""
    region: str = ""
    #: Currency value of one point of price movement, for futures. 1.0 for cash
    #: instruments, which is why it is not optional: a missing multiplier that
    #: defaults to nothing silently sizes a position at zero.
    multiplier: float = Field(default=1.0, gt=0)
    price: float | None = Field(default=None, gt=0)
    adv_notional: float | None = Field(default=None, gt=0)
    #: Whether the instrument can be sold short. `None` means unknown; the gate
    #: treats unknown as "not established" rather than as permission.
    shortable: bool | None = None
    borrow_cost_bps: float | None = None


class AlphaSignal(FrozenModel):
    """One forecast, with the evidence that produced it attached.

    `verdict` and `run_id` are not decoration. Portfolio construction refuses to
    size a signal whose strategy the judge failed, and it cannot do that unless
    the signal carries which judgement it came from.
    """

    strategy_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1, max_length=32)
    #: Expected return over `horizon_days`, as a decimal fraction.
    expected_return: float
    #: 0-1. Scales the forecast; it does not authorise anything on its own.
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    horizon_days: int = Field(default=1, gt=0)
    run_id: str = ""
    #: The judge's decision for the run behind this signal, when there is one.
    #: `None` means never judged, which construction treats as ineligible.
    verdict: Literal["PASS", "FAIL", "INCONCLUSIVE"] | None = None

    @property
    def scaled_return(self) -> float:
        return self.expected_return * self.confidence


class Constraints(FrozenModel):
    """The box the optimiser must stay inside.

    Every bound has a finite default. A constraint set that permits unlimited
    gross exposure is not a constraint set, and the same reasoning that makes
    `forge.risk.limits.RiskProfile` refuse an absent ceiling applies here.
    """

    max_gross: float = Field(default=1.0, gt=0)
    max_net: float = Field(default=1.0, ge=0)
    max_leverage: float = Field(default=1.0, gt=0)
    #: Largest absolute weight in any one name.
    max_weight: float = Field(default=0.25, gt=0, le=1.0)
    max_sector_weight: float = Field(default=0.5, gt=0, le=1.0)
    max_region_weight: float = Field(default=1.0, gt=0, le=1.0)
    #: One-way turnover ceiling, as a fraction of capital, per rebalance.
    max_turnover: float = Field(default=1.0, gt=0)
    long_only: bool = False
    #: Risk aversion in the mean-variance objective. Higher trades expected
    #: return away for lower variance.
    risk_aversion: float = Field(default=8.0, gt=0)
    #: Round-trip cost charged against turnover in the objective, in basis
    #: points. Costs belong in the objective, not in a report afterwards: an
    #: optimiser that ignores them proposes a portfolio nobody can afford to
    #: hold.
    cost_bps: float = Field(default=5.0, ge=0)
    #: The share of an instrument's average daily notional this portfolio may
    #: represent. Applied only where `adv_notional` is known.
    max_participation: float = Field(default=0.05, gt=0, le=1.0)
    #: Verdicts a signal's strategy may carry and still be sized. `PASS` alone is
    #: the default, and widening it is a decision the operator makes explicitly.
    eligible_verdicts: tuple[str, ...] = ("PASS",)

    @model_validator(mode="after")
    def _coherent(self) -> Constraints:
        if self.max_net > self.max_gross:
            raise ValueError("max_net cannot exceed max_gross")
        if self.long_only and self.max_net < self.max_gross:
            raise ValueError(
                "a long-only book has net equal to gross, so max_net below max_gross "
                "cannot be satisfied"
            )
        return self


class Holding(FrozenModel):
    """One line of the proposed portfolio."""

    symbol: str
    weight: float
    target_notional: float
    contracts: int | None = None
    strategy_ids: tuple[str, ...] = ()
    expected_return: float = 0.0

    @property
    def side(self) -> str:
        if self.weight > 0:
            return "LONG"
        return "SHORT" if self.weight < 0 else "FLAT"


class ConstraintReport(FrozenModel):
    """Whether each constraint binds, and by how much.

    Reported per constraint rather than as one boolean, because "the optimiser
    hit the gross limit" and "the optimiser hit the single-name cap" lead to
    different decisions and an operator cannot act on `infeasible=True`.
    """

    name: str
    limit: float
    observed: float
    binding: bool
    applied: bool = True
    detail: str = ""


class Exposures(FrozenModel):
    gross: float
    net: float
    long: float
    short: float
    leverage: float
    by_sector: dict[str, float] = Field(default_factory=dict)
    by_region: dict[str, float] = Field(default_factory=dict)
    by_asset_class: dict[str, float] = Field(default_factory=dict)
    #: Herfindahl index over absolute weights: 1.0 is one position, 1/n is even.
    concentration: float = 0.0


class PortfolioProposal(FrozenModel):
    """What construction produced, and what it rests on."""

    portfolio_id: str
    capital: float
    holdings: tuple[Holding, ...]
    exposures: Exposures
    expected_return: float
    expected_volatility: float
    #: Expected return divided by expected volatility. `None` when volatility is
    #: zero or the covariance estimate was unusable — a division that would
    #: produce an impressive infinity is not reported as a ratio.
    expected_sharpe: float | None
    turnover: float
    estimated_cost: float
    constraints: tuple[ConstraintReport, ...]
    #: Signals that were not sized, and why. Every rejection is named: a signal
    #: that silently vanishes between research and the book is the failure this
    #: field exists to prevent.
    excluded: tuple[dict[str, str], ...] = ()
    covariance_method: str = ""
    observations: int = 0
    limitations: tuple[str, ...] = ()
    optimiser: str = ""
    feasible: bool = True

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
