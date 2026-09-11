"""What an experiment is about to do, stated before it does it.

§26 asks for a preview and a "check my work". §31 asks that data availability be
communicated honestly before an experiment rather than discovered in its
results. They are the same screen: the reason to show somebody what is about to
run is so they can notice the thing that is wrong with it, and the most common
wrong thing is the data.

**The preview asserts nothing it was not told.** Every field is supplied by the
caller from the request it is about to execute, and a field the caller does not
have is absent rather than filled with a plausible default. A preview that says
"1-minute bars, 2019-2026" when nobody checked is worse than no preview: it is a
claim, made by the machine, that the operator will reasonably believe.

**"Check my work" is a list of concerns, not a verdict.** `check` returns
`Concern`s, each naming something about *this* request that would weaken *this*
result — a test window shorter than the minimum trade count needs, a data gap
inside it, an instrument with no verified contract specification, parameters
declared after the hypothesis was frozen. It does not score the experiment and
it cannot refuse one: refusing is the judge's job, afterwards, with the results
in hand.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from forge.contracts.models import FrozenModel
from forge.judge.engine import MINIMUM_TRADES, MINIMUM_TRIAL_CONFIGURATIONS


class Availability(StrEnum):
    """What is known about the data this experiment would run on."""

    AVAILABLE = "available"
    #: Present, with a quality finding attached.
    DEGRADED = "degraded"
    MISSING = "missing"
    #: Nobody checked. Never rendered as available.
    UNKNOWN = "unknown"


class DataWindow(FrozenModel):
    """One instrument and timeframe the experiment needs."""

    symbol: str
    timeframe: str
    requested_start: datetime | None = None
    requested_end: datetime | None = None
    #: What the archive actually holds, when it has been checked.
    available_start: datetime | None = None
    available_end: datetime | None = None
    rows: int | None = None
    availability: Availability = Availability.UNKNOWN
    #: Verbatim from `forge.data.health`, when a report exists.
    quality: str = ""
    findings: tuple[str, ...] = ()
    provider: str = ""

    @property
    def covers_request(self) -> bool | None:
        """Whether the archive covers what was asked for. `None` when unknown."""
        if self.availability is Availability.UNKNOWN:
            return None
        if self.availability is Availability.MISSING:
            return False
        if self.requested_start is None or self.requested_end is None:
            return None
        if self.available_start is None or self.available_end is None:
            return None
        return self.available_start <= self.requested_start and (
            self.available_end >= self.requested_end
        )

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["covers_request"] = self.covers_request
        return payload


class Preview(FrozenModel):
    """Everything about to happen, before it does.

    Nothing in here is inferred. A caller that cannot fill a field leaves it
    empty and the interface says so, which is the honest rendering of "we did
    not check".
    """

    strategy_id: str = ""
    strategy_name: str = ""
    #: The frozen claim, when one exists. Its absence is itself worth showing:
    #: an experiment run without a preregistration cannot pass G1.
    hypothesis: str = ""
    mechanism: str = ""
    falsification: str = ""
    preregistration_id: str = ""
    instruments: tuple[str, ...] = ()
    timeframe: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    #: How many parameter configurations will be tried. The number G5 deflates
    #: against, so it is shown before the run rather than discovered after.
    trial_count: int | None = None
    data: tuple[DataWindow, ...] = ()
    #: Which validation will run: walk-forward, CSCV, CPCV, or none of them.
    validation_methods: tuple[str, ...] = ()
    evidence_tier: str = ""
    cost_model: str = ""
    #: What the operator is accepting by running this, in their own terms.
    assumptions: tuple[str, ...] = ()
    expected_risk: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["data"] = [window.as_dict() for window in self.data]
        return payload


class Severity(StrEnum):
    #: This will make the result unusable for a decision.
    BLOCKING = "blocking"
    #: This will weaken the result in a way the judge will notice.
    WEAKENS = "weakens"
    #: Worth knowing before you spend the time.
    NOTE = "note"


class Concern(FrozenModel):
    """One thing about this request that would weaken this result."""

    severity: Severity
    subject: str
    finding: str
    #: What to change. Never "lower the threshold": the suggestions here are
    #: about the experiment, and `test_no_concern_suggests_weakening_a_standard`
    #: asserts it.
    suggestion: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class CheckedWork(FrozenModel):
    """The concerns, and an honest statement of what was not checked."""

    concerns: tuple[Concern, ...] = ()
    #: Things this check could not look at, because the preview did not carry
    #: them. Shown so an empty concern list does not read as a clean bill.
    not_checked: tuple[str, ...] = ()

    @property
    def blocking(self) -> tuple[Concern, ...]:
        return tuple(c for c in self.concerns if c.severity is Severity.BLOCKING)

    def as_dict(self) -> dict[str, Any]:
        return {
            "concerns": [c.as_dict() for c in self.concerns],
            "not_checked": list(self.not_checked),
            "blocking": len(self.blocking),
        }


def check(preview: Preview) -> CheckedWork:
    """Read a preview and name what would weaken the result it produces."""
    concerns: list[Concern] = []
    unchecked: list[str] = []

    if not preview.hypothesis:
        concerns.append(
            Concern(
                severity=Severity.WEAKENS,
                subject="Preregistration",
                finding=(
                    "no hypothesis is frozen for this run, so gate G1 cannot pass and "
                    "the result will carry no evidence that the claim preceded it"
                ),
                suggestion="freeze a hypothesis, mechanism and falsification before running",
            )
        )
    if preview.hypothesis and not preview.falsification:
        concerns.append(
            Concern(
                severity=Severity.WEAKENS,
                subject="Falsification",
                finding=(
                    "the hypothesis has no stated falsification, so no result can "
                    "disconfirm it"
                ),
                suggestion="state what outcome would show the hypothesis is wrong",
            )
        )

    if not preview.instruments:
        concerns.append(
            Concern(
                severity=Severity.BLOCKING,
                subject="Instruments",
                finding="no instrument is named, so there is nothing to run over",
            )
        )

    if not preview.data:
        unchecked.append("data availability: no data windows were resolved for this request")
    for window in preview.data:
        label = f"{window.symbol} {window.timeframe}".strip()
        if window.availability is Availability.MISSING:
            concerns.append(
                Concern(
                    severity=Severity.BLOCKING,
                    subject=label,
                    finding="this data is not in the local archive",
                    suggestion="import it, or choose an instrument and timeframe you hold",
                )
            )
            continue
        if window.availability is Availability.UNKNOWN:
            unchecked.append(f"{label}: availability was not checked")
            continue
        if window.covers_request is False:
            concerns.append(
                Concern(
                    severity=Severity.BLOCKING,
                    subject=label,
                    finding=(
                        "the archive does not cover the whole requested period; the run "
                        "would silently test a shorter window than you asked for"
                    ),
                    suggestion="narrow the test period to what is held, or import the rest",
                )
            )
        if window.availability is Availability.DEGRADED:
            concerns.append(
                Concern(
                    severity=Severity.WEAKENS,
                    subject=label,
                    finding=(
                        "the data-quality report on this dataset has findings: "
                        + "; ".join(window.findings)
                        if window.findings
                        else "the data-quality report on this dataset has findings"
                    ),
                    suggestion="read the data-health report before trusting the result",
                )
            )

    if preview.trial_count is not None:
        if 1 < preview.trial_count < MINIMUM_TRIAL_CONFIGURATIONS:
            concerns.append(
                Concern(
                    severity=Severity.WEAKENS,
                    subject="Selection integrity",
                    finding=(
                        f"{preview.trial_count} configurations is below the "
                        f"{MINIMUM_TRIAL_CONFIGURATIONS} that CSCV needs, so gate G11 "
                        "will report INCONCLUSIVE rather than a probability"
                    ),
                    suggestion=(
                        "widen the parameter grid if selection integrity matters for "
                        "this result"
                    ),
                )
            )
    else:
        unchecked.append("trial count: how many configurations will be tried was not stated")

    if not preview.validation_methods:
        concerns.append(
            Concern(
                severity=Severity.WEAKENS,
                subject="Validation",
                finding=(
                    "no validation method is selected, so gates G12 and G13 will report "
                    "NOT_MEASURED"
                ),
                suggestion="add walk-forward and CPCV if this result will inform a decision",
            )
        )

    if preview.evidence_tier and preview.evidence_tier == "SWEEP":
        concerns.append(
            Concern(
                severity=Severity.NOTE,
                subject="Evidence tier",
                finding=(
                    "this is a sweep. Its results are for exploration and cannot satisfy "
                    "gate G10, which requires out-of-sample, holdout or forward evidence"
                ),
            )
        )

    if not preview.cost_model:
        concerns.append(
            Concern(
                severity=Severity.WEAKENS,
                subject="Costs",
                finding=(
                    "no transaction-cost model is named. A result with no costs is an "
                    "upper bound, not an estimate"
                ),
                suggestion="attach a cost model before comparing this to anything",
            )
        )

    unchecked.append(
        f"trade count: whether the run will produce the {MINIMUM_TRADES} trades gate G3 "
        "requires cannot be known until it has run"
    )

    return CheckedWork(concerns=tuple(concerns), not_checked=tuple(unchecked))
