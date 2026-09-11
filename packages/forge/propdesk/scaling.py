"""Controlled risk scaling: drivers that may only cut, and a governor that paces.

§11 of the specification names the failure mode to avoid before it names
anything to build: `balance up = risk up`. That rule is wrong in a specific way
worth stating, because the wrongness is the design constraint. An account whose
balance rose because a degraded strategy got lucky for a fortnight has *more*
reason to size down, not less. Balance is not evidence.

So balance is not a driver here. It enters twice, in both cases as a
constraint rather than as a reason:

- through `buffer`, the distance to the loss floor, which caps what can be
  risked;
- through the band in `forge.propdesk.survival`, where a larger buffer pays for
  more contracts at the *same* fraction.

Neither of those is "risk went up because the number went up".

**Two rules make the rest safe.**

*A driver's factor is at most one.* Eight drivers multiply the band's target and
every one of them lies in `[0, 1]`, so no driver can raise anything. Risk rises
only when the band itself rises, and then only as fast as the governor allows.

*An increase requires every driver to be measured; a decrease requires none.*
An unmeasured input is never a reason to take more risk and never an obstacle to
taking less. This is the same discipline as the desk's UNKNOWN permissions, and
it is the reason a fresh installation with no regime classification cannot drift
upwards while nobody is looking.

**The explanation is the record, not a retelling.** `RiskProposal` carries the
drivers that were measured with their observed values and effects, the one that
bound, the governor clamp that applied, and the contract counts before and
after. The sentence the panel shows is assembled from those fields. There is no
function here that produces prose without a proposal to produce it from.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from forge.contracts.models import FrozenModel
from forge.propdesk.allocation import (
    MIN_OOS_TRADES,
    HealthGrade,
    StrategyHealth,
    grade_health,
)
from forge.propdesk.risk import AiCapability, RiskBoundaries, RiskMode, RiskSettings
from forge.propdesk.survival import RiskBand, Unmeasurable, derive_band


class DriverKind(StrEnum):
    BUFFER = "buffer"
    STRATEGY_HEALTH = "strategy_health"
    REALISED_VOLATILITY = "realised_volatility"
    REGIME_FIT = "regime_fit"
    CORRELATION = "correlation"
    PROP_CONSTRAINT = "prop_constraint"
    NEWS = "news"
    EVIDENCE = "evidence"
    #: The advisory layer's own contribution, present only when the mode is
    #: AI_MANAGED and the operator granted position sizing. A separate kind so a
    #: reader can always tell a measured input from a recommendation.
    ADVISORY = "advisory"


DRIVER_LABEL: dict[DriverKind, str] = {
    DriverKind.BUFFER: "Drawdown buffer",
    DriverKind.STRATEGY_HEALTH: "Strategy health",
    DriverKind.REALISED_VOLATILITY: "Realised volatility",
    DriverKind.REGIME_FIT: "Regime fit",
    DriverKind.CORRELATION: "Portfolio correlation",
    DriverKind.PROP_CONSTRAINT: "Account rules",
    DriverKind.NEWS: "Scheduled news",
    DriverKind.EVIDENCE: "Evidence",
    DriverKind.ADVISORY: "AI recommendation",
}


class Driver(FrozenModel):
    """One input, what was observed, and what it did to the target.

    `effect` is a multiplier in `[0, 1]`. It is not a weight and not a score:
    0.7 means the target was multiplied by 0.7, and the panel can say so.
    """

    kind: DriverKind
    measured: bool
    #: What was actually observed, in words, including units. Empty only when
    #: `measured` is false.
    observed: str = ""
    effect: float = Field(default=1.0, ge=0.0, le=1.0)
    detail: str = ""

    @model_validator(mode="after")
    def _unmeasured_drivers_do_not_act(self) -> Driver:
        if not self.measured and self.effect != 1.0:
            raise ValueError(
                f"{self.kind.value} was not measured but carries an effect of "
                f"{self.effect}; an unmeasured input must not move the number"
            )
        return self

    @property
    def label(self) -> str:
        return DRIVER_LABEL[self.kind]

    @property
    def cuts(self) -> bool:
        return self.measured and self.effect < 1.0

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["label"] = self.label
        payload["cuts"] = self.cuts
        return payload


class RiskObservation(FrozenModel):
    """Everything the scaler is allowed to look at.

    Every field is optional and every absent one reads as *unmeasured* rather
    than as neutral, which is what makes the "an increase needs every driver"
    rule enforceable: a caller cannot accidentally grant an increase by omitting
    an input.

    Assembled by `forge_api.propdesk` from the account rule assessment, the
    provider's reported state, the strategy health record and the news
    assessment. This module reads one shape and knows about none of those.
    """

    account_uid: str
    strategy_id: str = ""
    #: Equity minus the loss floor, now, and at the start of the account.
    buffer: float | None = None
    starting_buffer: float | None = None
    #: Realised standard deviation of the account's recent daily PnL, per
    #: contract, and what the out-of-sample evidence modelled it at.
    realised_daily_volatility: float | None = None
    modelled_daily_volatility: float | None = None
    #: The largest pairwise correlation among the strategies currently running
    #: for this owner. `None` means it was not computed, which is common with a
    #: single strategy and is reported as unmeasured rather than as zero.
    max_pairwise_correlation: float | None = None
    #: `forge.prop.account.assess`'s worst level, verbatim: OK / WATCH / BREACH.
    rules_level: str = ""
    #: True when a news blackout is in force for this account right now.
    news_restricted: bool | None = None
    news_detail: str = ""
    #: Out-of-sample per-contract daily PnL, for the drawdown bootstrap.
    oos_daily_pnl: tuple[float, ...] = ()
    health: StrategyHealth | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ScalingState(FrozenModel):
    """What the governor needs to remember between evaluations."""

    account_uid: str
    current_fraction: float = Field(gt=0, le=1)
    last_change_at: datetime | None = None
    #: Signed movement applied so far today, absolute values summed.
    change_today: float = Field(default=0.0, ge=0)
    #: The day `change_today` counts. A different day resets it, and the reset
    #: happens on read rather than on a schedule so nothing depends on a job
    #: having run.
    change_day: str = ""
    last_direction: str = ""

    def today(self, now: datetime) -> float:
        stamp = now.astimezone(UTC).date().isoformat()
        return self.change_today if stamp == self.change_day else 0.0


class Clamp(StrEnum):
    """Why an applied change differs from the proposed one."""

    NONE = "none"
    HYSTERESIS = "hysteresis"
    BOUNDARY = "boundary"
    STEP = "step"
    COOLDOWN = "cooldown"
    DAILY_LIMIT = "daily_limit"
    #: An increase was refused because at least one driver was not measured.
    UNMEASURED = "unmeasured"


CLAMP_DETAIL: dict[Clamp, str] = {
    Clamp.NONE: "nothing limited the change",
    Clamp.HYSTERESIS: "the change was smaller than your hysteresis band, so nothing moved",
    Clamp.BOUNDARY: "the proposal was outside the boundaries you set",
    Clamp.STEP: "the change was larger than your single-step limit",
    Clamp.COOLDOWN: "the last change was too recent",
    Clamp.DAILY_LIMIT: "today's total movement had reached the limit you set",
    Clamp.UNMEASURED: (
        "an increase requires every driver to be measured, and at least one was not"
    ),
}


class Direction(StrEnum):
    HOLD = "hold"
    INCREASE = "increase"
    DECREASE = "decrease"


class RiskProposal(FrozenModel):
    """One evaluation: what was measured, what it implied, what was applied."""

    account_uid: str
    strategy_id: str = ""
    mode: RiskMode
    at: datetime
    current_fraction: float
    #: What the drivers and the band implied, before the governor.
    proposed_fraction: float
    #: What the governor permitted. Equal to `current_fraction` when nothing moved.
    applied_fraction: float
    direction: Direction
    drivers: tuple[Driver, ...] = ()
    #: The band the appetite selected, or why none could be derived.
    band: RiskBand | None = None
    unmeasurable: Unmeasurable | None = None
    clamp: Clamp = Clamp.NONE
    #: The measured driver with the largest cut. Empty when nothing cut.
    binding_driver: DriverKind | None = None
    emergency: bool = False
    #: Contracts before and after, by the band's own arithmetic. `None` when the
    #: band could not be derived and there is therefore no contract count to
    #: report — deliberately not zero, which would read as "flatten".
    contracts_before: int | None = None
    contracts_after: int | None = None

    @property
    def changed(self) -> bool:
        return self.applied_fraction != self.current_fraction

    @property
    def measured(self) -> tuple[Driver, ...]:
        return tuple(d for d in self.drivers if d.measured)

    @property
    def unmeasured(self) -> tuple[Driver, ...]:
        return tuple(d for d in self.drivers if not d.measured)

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["drivers"] = [d.as_dict() for d in self.drivers]
        payload["changed"] = self.changed
        payload["clamp_detail"] = CLAMP_DETAIL[self.clamp]
        payload["why"] = explain(self)
        return payload


# ── the drivers ──────────────────────────────────────────────────────────────
# Each returns exactly one `Driver`. They are separate functions rather than
# branches in one loop so that a reader checking "what can reduce my risk" gets
# one named thing per answer, and so that each can be tested alone.


def _buffer_driver(observation: RiskObservation, boundaries: RiskBoundaries) -> Driver:
    buffer, start = observation.buffer, observation.starting_buffer
    if buffer is None or start is None or start <= 0:
        return Driver(
            kind=DriverKind.BUFFER,
            measured=False,
            detail=(
                "the buffer to the loss floor, or the buffer this account started with, "
                "is unknown"
            ),
        )
    ratio = max(0.0, buffer / start)
    if ratio >= 1.0:
        effect = 1.0
        detail = "the buffer is at or above where this account started"
    elif ratio <= boundaries.emergency_buffer_ratio:
        effect = 0.0
        detail = (
            f"the buffer is at {ratio:.0%} of its starting level, at or below your "
            f"emergency threshold of {boundaries.emergency_buffer_ratio:.0%}"
        )
    else:
        # Linear from the emergency threshold up to whole. Not a curve: a curve
        # would imply a model of how buffer relates to ruin, and there isn't one
        # here — the model is in the band, which is measured.
        span = 1.0 - boundaries.emergency_buffer_ratio
        effect = (ratio - boundaries.emergency_buffer_ratio) / span
        detail = f"the buffer is at {ratio:.0%} of its starting level"
    return Driver(
        kind=DriverKind.BUFFER,
        measured=True,
        observed=f"{buffer:,.2f} of {start:,.2f} ({ratio:.0%})",
        effect=round(effect, 4),
        detail=detail,
    )


#: What each health grade permits, as a share of the band. UNPROVEN and FAILING
#: are zero: allocating to a strategy the judge has not passed is what the
#: deterministic layer already refuses, and this driver agreeing with it means
#: the refusal shows up as a reason here too rather than only as a later block.
_HEALTH_EFFECT: dict[HealthGrade, float] = {
    HealthGrade.HEALTHY: 1.0,
    HealthGrade.WATCH: 0.7,
    HealthGrade.DEGRADED: 0.4,
    HealthGrade.UNPROVEN: 0.0,
    HealthGrade.FAILING: 0.0,
}


def _health_driver(observation: RiskObservation) -> Driver:
    health = observation.health
    if health is None:
        return Driver(
            kind=DriverKind.STRATEGY_HEALTH,
            measured=False,
            detail="no strategy health record was supplied",
        )
    grade = grade_health(health)
    return Driver(
        kind=DriverKind.STRATEGY_HEALTH,
        measured=True,
        observed=grade.value,
        effect=_HEALTH_EFFECT[grade],
        detail={
            HealthGrade.HEALTHY: "the strategy is passing and not drifting from its baseline",
            HealthGrade.WATCH: "live expectancy has drifted from the out-of-sample baseline",
            HealthGrade.DEGRADED: "live expectancy has drifted well below the baseline",
            HealthGrade.UNPROVEN: (
                "the judge has not passed this strategy, or the sample is too small"
            ),
            HealthGrade.FAILING: (
                "the judge failed this strategy, or its out-of-sample Sharpe is not positive"
            ),
        }[grade],
    )


def _volatility_driver(observation: RiskObservation) -> Driver:
    realised, modelled = (
        observation.realised_daily_volatility,
        observation.modelled_daily_volatility,
    )
    if realised is None or modelled is None or modelled <= 0:
        return Driver(
            kind=DriverKind.REALISED_VOLATILITY,
            measured=False,
            detail=(
                "realised daily volatility, or what the out-of-sample evidence modelled "
                "it at, is unknown"
            ),
        )
    ratio = realised / modelled
    effect = min(1.0, 1.0 / ratio) if ratio > 0 else 1.0
    return Driver(
        kind=DriverKind.REALISED_VOLATILITY,
        measured=True,
        observed=f"{realised:,.2f} against a modelled {modelled:,.2f} ({ratio:.0%})",
        effect=round(effect, 4),
        detail=(
            f"realised volatility is {ratio - 1:.0%} above what the evidence modelled, "
            "so the same number of contracts carries more risk than the sizing assumed"
            if ratio > 1
            else "realised volatility is at or below what the evidence modelled"
        ),
    )


#: What each regime verdict permits. `uncovered` is 0.6 rather than 0: the
#: strategy is not disqualified by trading in a regime its evidence does not
#: cover, but the evidence is weaker there and the size should say so.
_REGIME_EFFECT: dict[str, float] = {"covered": 1.0, "uncovered": 0.6}


def _regime_driver(observation: RiskObservation) -> Driver:
    health = observation.health
    fit = health.regime_fit if health is not None else "unclassified"
    if fit not in _REGIME_EFFECT:
        return Driver(
            kind=DriverKind.REGIME_FIT,
            measured=False,
            detail=(
                "the current regime has not been classified"
                if fit == "unclassified"
                else "the regimes this strategy's evidence covers are not recorded"
            ),
        )
    return Driver(
        kind=DriverKind.REGIME_FIT,
        measured=True,
        observed=fit,
        effect=_REGIME_EFFECT[fit],
        detail=(
            "the strategy's evidence covers the regime the market is in now"
            if fit == "covered"
            else "the market is in a regime this strategy's evidence does not cover"
        ),
    )


def _correlation_driver(observation: RiskObservation) -> Driver:
    correlation = observation.max_pairwise_correlation
    if correlation is None:
        return Driver(
            kind=DriverKind.CORRELATION,
            measured=False,
            detail=(
                "correlation across the strategies running for this owner has not been "
                "computed"
            ),
        )
    # Below 0.5, independent enough to treat as separate bets. Above it, the
    # effective number of bets falls and so should the size of each.
    effect = 1.0 if correlation <= 0.5 else max(0.0, 1.0 - (correlation - 0.5) * 2.0)
    return Driver(
        kind=DriverKind.CORRELATION,
        measured=True,
        observed=f"{correlation:.2f}",
        effect=round(effect, 4),
        detail=(
            f"the most correlated pair of running strategies sits at {correlation:.2f}; "
            "correlated positions are one bet, not several"
            if correlation > 0.5
            else "running strategies are not strongly correlated"
        ),
    )


_RULES_EFFECT: dict[str, float] = {"OK": 1.0, "WATCH": 0.5, "BREACH": 0.0}


def _rules_driver(observation: RiskObservation) -> Driver:
    level = observation.rules_level.upper()
    if level not in _RULES_EFFECT:
        return Driver(
            kind=DriverKind.PROP_CONSTRAINT,
            measured=False,
            detail=(
                "this account's rules have not been assessed, so how close it is to a "
                "breach is unknown"
            ),
        )
    return Driver(
        kind=DriverKind.PROP_CONSTRAINT,
        measured=True,
        observed=level,
        effect=_RULES_EFFECT[level],
        detail={
            "OK": "no configured account rule is close to breaching",
            "WATCH": "at least one configured account rule is close to breaching",
            "BREACH": "at least one configured account rule is breached",
        }[level],
    )


def _news_driver(observation: RiskObservation) -> Driver:
    restricted = observation.news_restricted
    if restricted is None:
        return Driver(
            kind=DriverKind.NEWS,
            measured=False,
            detail="no news policy is enabled, so no blackout was evaluated",
        )
    return Driver(
        kind=DriverKind.NEWS,
        measured=True,
        observed="restricted" if restricted else "clear",
        effect=0.0 if restricted else 1.0,
        detail=observation.news_detail
        or ("a scheduled-event blackout is in force" if restricted else "no blackout is in force"),
    )


def _evidence_driver(observation: RiskObservation) -> Driver:
    health = observation.health
    if health is None or health.oos_trades is None:
        return Driver(
            kind=DriverKind.EVIDENCE,
            measured=False,
            detail="the number of out-of-sample trades behind this strategy is unknown",
        )
    enough = health.oos_trades >= MIN_OOS_TRADES
    return Driver(
        kind=DriverKind.EVIDENCE,
        measured=True,
        observed=f"{health.oos_trades} out-of-sample trades",
        effect=1.0 if enough else 0.0,
        detail=(
            f"at or above the {MIN_OOS_TRADES}-trade minimum"
            if enough
            else f"below the {MIN_OOS_TRADES}-trade minimum, so the estimates behind the "
            "sizing rest on too few outcomes"
        ),
    )


def drivers_for(observation: RiskObservation, boundaries: RiskBoundaries) -> tuple[Driver, ...]:
    """Every driver, measured or not, in the order the panel lists them."""
    return (
        _buffer_driver(observation, boundaries),
        _health_driver(observation),
        _volatility_driver(observation),
        _regime_driver(observation),
        _correlation_driver(observation),
        _rules_driver(observation),
        _news_driver(observation),
        _evidence_driver(observation),
    )


# ── the governor ─────────────────────────────────────────────────────────────


def _emergency(observation: RiskObservation, boundaries: RiskBoundaries) -> bool:
    """Whether a decrease may skip cooldown and step.

    Two triggers, both from measured state: the buffer has fallen to the
    operator's own emergency threshold, or the account rule engine reports a
    breach. Neither can cause an increase — `apply` only consults this on the
    decreasing branch.
    """
    if observation.rules_level.upper() == "BREACH":
        return True
    buffer, start = observation.buffer, observation.starting_buffer
    if buffer is None or start is None or start <= 0:
        return False
    return buffer / start <= boundaries.emergency_buffer_ratio


def apply(
    *,
    proposed: float,
    state: ScalingState,
    boundaries: RiskBoundaries,
    all_measured: bool,
    emergency: bool,
    now: datetime,
) -> tuple[float, Clamp]:
    """What the governor permits, and which limit bound.

    Order matters and is deliberate. Boundaries first, so a clamp is reported
    against the number the operator actually set rather than against an
    intermediate. Then hysteresis, which decides whether anything moves at all.
    Then the asymmetric part: a decrease passes; an increase must survive the
    unmeasured check, cooldown, step and the daily cap in that order, so the
    reason given is the first thing that would have stopped it rather than the
    last.
    """
    current = state.current_fraction
    bounded = boundaries.clamp(proposed)
    clamp = Clamp.BOUNDARY if bounded != proposed else Clamp.NONE

    delta = bounded - current
    if abs(delta) < boundaries.hysteresis:
        return current, Clamp.HYSTERESIS

    if delta < 0:
        if emergency:
            return bounded, clamp
        # A decrease is still paced by the step, but never by cooldown or the
        # daily cap: those exist to stop churn upward, and a governor that
        # delays de-risking causes the loss it was installed to prevent.
        if abs(delta) > boundaries.max_step:
            return round(current - boundaries.max_step, 6), Clamp.STEP
        return bounded, clamp

    if not all_measured:
        return current, Clamp.UNMEASURED
    if state.last_change_at is not None:
        elapsed = now - state.last_change_at
        if elapsed < timedelta(minutes=boundaries.cooldown_minutes):
            return current, Clamp.COOLDOWN
    used = state.today(now)
    remaining = boundaries.max_daily_change - used
    if remaining <= 0:
        return current, Clamp.DAILY_LIMIT
    allowed = min(delta, boundaries.max_step, remaining)
    if allowed < delta:
        limit = Clamp.STEP if allowed == boundaries.max_step else Clamp.DAILY_LIMIT
        return round(current + allowed, 6), limit
    return bounded, clamp


def propose(
    *,
    settings: RiskSettings,
    observation: RiskObservation,
    state: ScalingState,
    now: datetime | None = None,
    advisory: float | None = None,
    advisory_note: str = "",
) -> RiskProposal:
    """Evaluate one account, and return what would be applied and why.

    `advisory` is the AI layer's contribution and is a multiplier in `[0, 1]`.
    It is read only when the mode is AI_MANAGED and the operator granted the
    capability; values above one are not clamped silently but refused, because
    a caller passing 1.4 has misunderstood what this parameter is and should be
    told rather than quietly obeyed at 1.0.
    """
    moment = now or datetime.now(UTC)
    boundaries = settings.boundaries

    if settings.mode is RiskMode.MANUAL:
        manual = settings.manual
        fraction = manual.risk_fraction if manual else state.current_fraction
        return RiskProposal(
            account_uid=observation.account_uid,
            strategy_id=observation.strategy_id,
            mode=settings.mode,
            at=moment,
            current_fraction=state.current_fraction,
            proposed_fraction=fraction,
            applied_fraction=fraction,
            direction=Direction.HOLD,
            clamp=Clamp.NONE,
        )

    if advisory is not None and not 0.0 <= advisory <= 1.0:
        raise ValueError(
            f"an advisory factor is a multiplier between 0 and 1; got {advisory}. "
            "The advisory layer may narrow a proposal and may never widen one."
        )

    drivers = drivers_for(observation, boundaries)
    band = derive_band(
        appetite=settings.appetite,
        boundaries=boundaries,
        buffer=observation.buffer,
        daily_pnl=observation.oos_daily_pnl,
    )

    derived: RiskBand | None = None
    unmeasurable: Unmeasurable | None = None
    if isinstance(band, Unmeasurable):
        # No band means no target. The fraction falls to the operator's own
        # minimum rather than to a default, and the governor still paces it.
        unmeasurable = band
        target = boundaries.minimum_fraction
    else:
        derived = band
        target = band.target_fraction

    factor = 1.0
    for driver in drivers:
        factor *= driver.effect
    proposed = target * factor
    # Computed here, before the advisory driver is appended: a recommendation is
    # not a measurement, and an increase must rest on measurements alone.
    all_measured = all(driver.measured for driver in drivers) and unmeasurable is None

    if (
        settings.mode is RiskMode.AI_MANAGED
        and advisory is not None
        and settings.may(AiCapability.POSITION_SIZING)
    ):
        proposed *= advisory
        drivers = (
            *drivers,
            Driver(
                kind=DriverKind.ADVISORY,
                measured=True,
                observed=f"factor {advisory:.2f}",
                effect=advisory,
                detail=advisory_note
                or "the advisory layer proposed a further reduction of the target",
            ),
        )

    emergency = _emergency(observation, boundaries)
    applied, clamp = apply(
        proposed=proposed,
        state=state,
        boundaries=boundaries,
        all_measured=all_measured,
        emergency=emergency,
        now=moment,
    )

    cutting = [d for d in drivers if d.cuts]
    binding = min(cutting, key=lambda d: d.effect).kind if cutting else None

    direction = (
        Direction.HOLD
        if applied == state.current_fraction
        else Direction.INCREASE
        if applied > state.current_fraction
        else Direction.DECREASE
    )
    before = after = None
    if derived is not None and observation.buffer is not None:
        per = derived.drawdown_per_contract
        before = min(
            boundaries.max_contracts, int((observation.buffer * state.current_fraction) // per)
        )
        after = min(boundaries.max_contracts, int((observation.buffer * applied) // per))

    return RiskProposal(
        account_uid=observation.account_uid,
        strategy_id=observation.strategy_id,
        mode=settings.mode,
        at=moment,
        current_fraction=state.current_fraction,
        proposed_fraction=round(proposed, 6),
        applied_fraction=applied,
        direction=direction,
        drivers=drivers,
        band=derived,
        unmeasurable=unmeasurable,
        clamp=clamp,
        binding_driver=binding,
        emergency=emergency,
        contracts_before=before,
        contracts_after=after,
    )


def explain(proposal: RiskProposal) -> list[str]:
    """Why the risk changed, or why it did not, in sentences from the record.

    Every line here is read off `proposal`. Nothing is generated, nothing is
    inferred, and there is no branch that produces a sentence when the
    corresponding field is absent — which is what makes
    `test_every_explanation_line_traces_to_a_field` possible to write.
    """
    lines: list[str] = []
    if proposal.mode is RiskMode.MANUAL:
        return ["Risk is set manually. Nothing adjusts it."]

    if proposal.unmeasurable is not None:
        lines.append(
            f"No risk band could be derived: {proposal.unmeasurable.reason}. "
            "Risk is held at your configured minimum."
        )
    for driver in proposal.drivers:
        if driver.cuts:
            lines.append(f"{driver.label}: {driver.detail} (x{driver.effect:.2f}).")
    for driver in proposal.unmeasured:
        lines.append(f"{driver.label}: not measured — {driver.detail}.")

    if proposal.emergency and proposal.direction is Direction.DECREASE:
        lines.append(
            "This reduction bypassed the cooldown and step limits because the account "
            "is at or past your emergency threshold."
        )
    if proposal.clamp is not Clamp.NONE:
        lines.append(
            f"Proposed {proposal.proposed_fraction:.2%}, applied "
            f"{proposal.applied_fraction:.2%}: {CLAMP_DETAIL[proposal.clamp]}."
        )
    if proposal.contracts_before is not None and proposal.contracts_after is not None:
        if proposal.contracts_before != proposal.contracts_after:
            lines.append(
                f"Sizing moves from {proposal.contracts_before} to "
                f"{proposal.contracts_after} contracts."
            )
        else:
            lines.append(f"Sizing stays at {proposal.contracts_after} contracts.")
    if not lines:
        lines.append(
            "Every driver was measured and none of them reduced the target, so the "
            "risk band is being used in full."
        )
    return lines
