"""Which slice of history an experiment runs on, chosen before the result exists.

**What this replaces.** Nothing, which is the problem. `AutonomousEngine` loaded
bars once per run as ``market.load(dataset, limit=max_bars)``, `MarketService`
resolved that limit as ``frame.tail(limit)``, and `EngineConfig.max_bars`
defaults to 250,000 -- about nine months of one-minute futures bars, always the
most recent nine months, cached for the life of the run and shared by every
worker and every experiment in the campaign. A day-of-week seasonality
hypothesis got nine months and nothing said so.

`Campaign.start_date` and `Campaign.end_date` existed, were stored, were
returned by the API, and were read by no code at all.

So the sixteen-year archive was not being brute-forced. It was being ignored,
silently, and one window was being used for research questions that need very
different ones.

**The rule this module exists to enforce.** A window is part of the claim. An
experiment that tries two years, dislikes the answer, tries five, and reports
the five-year number has not run one experiment -- it has run two and reported
the better one, which is selection. So:

* the scope is chosen *before* execution and carries its own rationale;
* the scope is content-hashed and goes into the preregistration, where G1
  already re-derives the hash at judge time and fails ``CLAIM_MOVED`` when
  anything shifted. **No second freeze mechanism, and no new gate.**
* changing the window means a new experiment, and every window tried counts
  toward selection exposure.

**What this module does not do.** It does not read data, does not know which
datasets exist, and does not decide whether a hypothesis is good. It describes a
selection and refuses incoherent ones. `MarketService` turns a scope into bars;
the plan gate decides whether a plan carrying one may run.
"""

from __future__ import annotations

import builtins
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from forge.contracts.hashing import content_hash, stable_id
from forge.contracts.models import FrozenModel

#: Deliberately absent: a minimum window *length*.
#:
#: The obvious guard is "refuse a window too short to support a purged
#: three-way split", and the obvious unit is days. Both are wrong here. Twenty
#: days of one-minute bars is nearly six thousand observations and is plenty;
#: twenty days of daily bars is twenty and is useless. Sufficiency is a
#: question about *bars*, and this module does not read data and does not know
#: the interval -- saying so in its own docstring and then assuming a bar rate
#: anyway would be the assumption it claims not to make.
#:
#: So the count is checked where it can be counted: `MarketService.load_scope`
#: refuses a window that returns fewer bars than the caller needs, and
#: `chronological_split` already refuses one it cannot partition, by bar count,
#: with `INSUFFICIENT_SPLIT_BARS`.

#: A rationale shorter than this is not a reason, it is a label. The whole point
#: of recording the selection is that somebody can later ask "why this window?"
#: and get an answer rather than a method name they can already see.
MIN_RATIONALE = 40


class ScopeError(ValueError):
    """A scope that could not be built, with the reason a caller can show."""


class SelectionMethod(StrEnum):
    """How the window was chosen. Recorded, because it is part of the claim."""

    #: "The last N years." The commonest honest default for microstructure work.
    RECENT_N_YEARS = "RECENT_N_YEARS"
    #: Explicit dates, chosen for a stated reason.
    FIXED_DATE_RANGE = "FIXED_DATE_RANGE"
    #: Everything admissible. A deliberate choice, not a fallback.
    FULL_AVAILABLE_HISTORY = "FULL_AVAILABLE_HISTORY"
    #: Repeated train/test pairs that both move forward.
    ROLLING = "ROLLING"
    #: Train start fixed, train end and test both move forward.
    ANCHORED = "ANCHORED"
    #: One regime, defined by a stated measurable rule.
    REGIME_SELECTED = "REGIME_SELECTED"
    #: Several regimes, deliberately different, tested separately.
    CROSS_REGIME = "CROSS_REGIME"
    #: Windows around stated events.
    EVENT_SELECTED = "EVENT_SELECTED"
    #: Anything else, which still has to say what it is.
    CUSTOM = "CUSTOM"


#: Methods that produce a sequence of train/test pairs rather than one span.
SEQUENTIAL: frozenset[SelectionMethod] = frozenset(
    {SelectionMethod.ROLLING, SelectionMethod.ANCHORED}
)

#: Methods that must name what they selected on. A scope claiming to be
#: regime-selected without saying which regime is not a selection, it is a
#: gesture at one.
MUST_NAME_SEGMENTS: frozenset[SelectionMethod] = frozenset(
    {
        SelectionMethod.REGIME_SELECTED,
        SelectionMethod.CROSS_REGIME,
        SelectionMethod.EVENT_SELECTED,
    }
)


class Window(FrozenModel):
    """One contiguous span of history, with what it is for."""

    #: ``train``, ``validation``, ``holdout``, or ``fold-1-train`` and so on.
    role: str = Field(min_length=1, max_length=40)
    start: datetime
    end: datetime
    #: For a regime or event window: the rule that selected it. Free text,
    #: because the rule lives in the analysis that produced it -- but present,
    #: so "high volatility" can be checked against what was actually measured.
    selector: str = ""

    @model_validator(mode="after")
    def _ordered(self) -> Window:
        if self.end <= self.start:
            raise ValueError(f"window '{self.role}' ends at or before it starts")
        return self

    @property
    def days(self) -> float:
        return (self.end - self.start).total_seconds() / 86400.0

    def overlaps(self, other: Window) -> bool:
        return self.start < other.end and other.start < self.end

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class TimeScope(FrozenModel):
    """The temporal design of one experiment, frozen before it runs.

    `available_*` is the reservoir; `selected_*` is what this experiment uses.
    Keeping both is the point: an experiment that used two years out of sixteen
    made a choice, and a record that shows only the two years has lost the
    choice.
    """

    dataset: str = Field(min_length=1, max_length=120)
    #: The reservoir this was drawn from.
    available_start: datetime
    available_end: datetime
    #: What this experiment actually runs on.
    selected_start: datetime
    selected_end: datetime
    method: SelectionMethod
    #: Why this window, for this hypothesis. Refused if it says nothing.
    rationale: str = Field(min_length=MIN_RATIONALE, max_length=2000)
    #: The partitions, when the scope supplies its own. Empty means the caller
    #: should split the selected span itself -- `chronological_split` already
    #: does that, and a scope that also split it would be a second opinion.
    windows: tuple[Window, ...] = ()
    #: Regimes or events named by a selecting method.
    segments: tuple[str, ...] = ()
    selected_at: datetime
    #: Who chose it: an operator, a role, an agent id. Provenance, not policy.
    selected_by: str = Field(default="", max_length=120)
    #: What this scope was chosen *instead of*, when it was one of several
    #: considered. Selection exposure is only countable if it is recorded.
    alternatives_considered: tuple[str, ...] = ()

    # ── coherence ────────────────────────────────────────────────────────────

    @model_validator(mode="after")
    def _coherent(self) -> TimeScope:
        if self.available_end <= self.available_start:
            raise ValueError("the available history ends at or before it starts")
        if self.selected_end <= self.selected_start:
            raise ValueError("the selected window ends at or before it starts")
        if self.selected_start < self.available_start:
            raise ValueError(
                f"the selected window starts at {self.selected_start.date()}, before the "
                f"available history begins at {self.available_start.date()}. "
                "A window cannot reach data that does not exist."
            )
        if self.selected_end > self.available_end:
            raise ValueError(
                f"the selected window ends at {self.selected_end.date()}, after the "
                f"available history ends at {self.available_end.date()}."
            )
        if self.method in MUST_NAME_SEGMENTS and not self.segments:
            raise ValueError(
                f"{self.method} must name the regimes or events it selected on. "
                "A selection that does not say what it selected is not a selection."
            )
        if self.method in SEQUENTIAL and not self.windows:
            raise ValueError(f"{self.method} needs its folds as windows")
        self._check_windows()
        return self

    def _check_windows(self) -> None:
        for window in self.windows:
            if window.start < self.selected_start or window.end > self.selected_end:
                raise ValueError(
                    f"window '{window.role}' runs outside the selected span"
                )
        roles = [w.role for w in self.windows]
        duplicates = {r for r in roles if roles.count(r) > 1}
        if duplicates:
            raise ValueError(f"duplicate window roles: {', '.join(sorted(duplicates))}")

        # Leakage control, and the reason this class exists rather than a tuple
        # of dates. Training on bars that a holdout also contains is not a
        # weaker experiment, it is a different and untrue one, and it is the
        # single easiest mistake to make when windows are chosen by hand.
        train = self.window_for("train")
        for name in ("validation", "holdout"):
            later = self.window_for(name)
            if train is not None and later is not None:
                if train.overlaps(later):
                    raise ValueError(
                        f"the train window overlaps the {name} window; "
                        "an experiment cannot be tested on bars it was fitted on"
                    )
                if later.start < train.end:
                    raise ValueError(
                        f"the {name} window starts before the train window ends"
                    )
        validation, holdout = self.window_for("validation"), self.window_for("holdout")
        if validation is not None and holdout is not None and validation.overlaps(holdout):
            raise ValueError("the validation and holdout windows overlap")

    # ── reading ──────────────────────────────────────────────────────────────

    @property
    def selected_days(self) -> float:
        return (self.selected_end - self.selected_start).total_seconds() / 86400.0

    @property
    def available_days(self) -> float:
        return (self.available_end - self.available_start).total_seconds() / 86400.0

    @property
    def selected_years(self) -> float:
        return self.selected_days / 365.25

    @property
    def coverage(self) -> float:
        """The share of the reservoir this experiment uses, 0-1.

        The number that makes "we used two years of a sixteen-year archive"
        visible as a decision rather than invisible as a default.
        """
        return min(1.0, self.selected_days / max(1e-9, self.available_days))

    def window_for(self, role: str) -> Window | None:
        return next((w for w in self.windows if w.role == role), None)

    def covers(self, moment: datetime) -> bool:
        return self.selected_start <= moment <= self.selected_end

    # ── identity ─────────────────────────────────────────────────────────────

    def fingerprint(self) -> str:
        """The content this scope *is*, for the preregistered claim.

        Deliberately excludes `selected_at`, `selected_by`, `rationale` and
        `alternatives_considered`. Two experiments run on the same dataset over
        the same dates with the same partitions are temporally identical
        whoever chose them and whenever; but a moved *date* is a moved claim,
        and that is what G1 must catch.
        """
        return content_hash(
            {
                "dataset": self.dataset,
                "selected_start": self.selected_start.astimezone(UTC).isoformat(),
                "selected_end": self.selected_end.astimezone(UTC).isoformat(),
                "method": str(self.method),
                "windows": [
                    {
                        "role": w.role,
                        "start": w.start.astimezone(UTC).isoformat(),
                        "end": w.end.astimezone(UTC).isoformat(),
                    }
                    for w in self.windows
                ],
                "segments": list(self.segments),
            }
        )

    @property
    def scope_id(self) -> str:
        return stable_id("timescope", {"fingerprint": self.fingerprint()})

    def describe(self) -> str:
        """One line a person can read, with the choice visible in it."""
        span = (
            f"{self.selected_start.date()} to {self.selected_end.date()} "
            f"({self.selected_years:.1f}y"
        )
        if self.coverage < 0.999:
            span += f", {self.coverage:.0%} of {self.available_days / 365.25:.1f}y available"
        span += ")"
        extra = f" over {', '.join(self.segments)}" if self.segments else ""
        return f"{self.method}: {span}{extra}"

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload.update(
            {
                "scope_id": self.scope_id,
                "fingerprint": self.fingerprint(),
                "selected_days": round(self.selected_days, 2),
                "selected_years": round(self.selected_years, 3),
                "coverage": round(self.coverage, 4),
                "summary": self.describe(),
            }
        )
        return payload


# ── construction ─────────────────────────────────────────────────────────────
#
# Constructors rather than a builder, because each method needs different
# arguments and a single function taking all of them would accept combinations
# that mean nothing. Every one requires a rationale: a window with no stated
# reason is exactly the thing this module exists to stop.


def _now() -> datetime:
    return datetime.now(UTC)


def recent_years(
    *,
    dataset: str,
    available_start: datetime,
    available_end: datetime,
    years: float,
    rationale: str,
    selected_by: str = "",
    alternatives: tuple[str, ...] = (),
) -> TimeScope:
    """The last N years of the reservoir."""
    if years <= 0:
        raise ScopeError("a window of zero or negative years is not a window")
    start = available_end - timedelta(days=years * 365.25)
    if start < available_start:
        raise ScopeError(
            f"{years:g} years were requested but only "
            f"{(available_end - available_start).days / 365.25:.2f} are available. "
            "Ask for the full history instead of silently getting less than requested."
        )
    return TimeScope(
        dataset=dataset,
        available_start=available_start,
        available_end=available_end,
        selected_start=start,
        selected_end=available_end,
        method=SelectionMethod.RECENT_N_YEARS,
        rationale=rationale,
        selected_at=_now(),
        selected_by=selected_by,
        alternatives_considered=alternatives,
    )


def fixed_range(
    *,
    dataset: str,
    available_start: datetime,
    available_end: datetime,
    start: datetime,
    end: datetime,
    rationale: str,
    selected_by: str = "",
    alternatives: tuple[str, ...] = (),
) -> TimeScope:
    return TimeScope(
        dataset=dataset,
        available_start=available_start,
        available_end=available_end,
        selected_start=start,
        selected_end=end,
        method=SelectionMethod.FIXED_DATE_RANGE,
        rationale=rationale,
        selected_at=_now(),
        selected_by=selected_by,
        alternatives_considered=alternatives,
    )


def full_history(
    *,
    dataset: str,
    available_start: datetime,
    available_end: datetime,
    rationale: str,
    selected_by: str = "",
    alternatives: tuple[str, ...] = (),
) -> TimeScope:
    """Everything admissible -- as a decision with a reason, not as a default."""
    return TimeScope(
        dataset=dataset,
        available_start=available_start,
        available_end=available_end,
        selected_start=available_start,
        selected_end=available_end,
        method=SelectionMethod.FULL_AVAILABLE_HISTORY,
        rationale=rationale,
        selected_at=_now(),
        selected_by=selected_by,
        alternatives_considered=alternatives,
    )


def rolling(
    *,
    dataset: str,
    available_start: datetime,
    available_end: datetime,
    train_months: int,
    test_months: int,
    folds: int,
    rationale: str,
    anchored: bool = False,
    selected_by: str = "",
    alternatives: tuple[str, ...] = (),
) -> TimeScope:
    """Repeated train/test pairs walking forward.

    ``anchored`` keeps every train window starting at the same instant and only
    moves its end, which is the other standard design and differs from rolling
    in exactly one line -- so it is one function, with the difference named.
    """
    if folds < 1:
        raise ScopeError("a walk-forward needs at least one fold")
    if train_months < 1 or test_months < 1:
        raise ScopeError("train and test spans must be at least one month")

    month = timedelta(days=30.44)
    needed = (train_months + test_months * folds) * month
    if available_end - available_start < needed:
        raise ScopeError(
            f"{folds} fold(s) of {train_months}m train and {test_months}m test need "
            f"{needed.days} days; {(available_end - available_start).days} are available."
        )

    windows: list[Window] = []
    cursor = available_start
    for fold in range(1, folds + 1):
        train_start = available_start if anchored else cursor
        train_end = cursor + train_months * month
        test_end = train_end + test_months * month
        windows.append(Window(role=f"fold-{fold}-train", start=train_start, end=train_end))
        windows.append(Window(role=f"fold-{fold}-test", start=train_end, end=test_end))
        cursor = cursor + test_months * month

    return TimeScope(
        dataset=dataset,
        available_start=available_start,
        available_end=available_end,
        selected_start=windows[0].start,
        selected_end=windows[-1].end,
        method=SelectionMethod.ANCHORED if anchored else SelectionMethod.ROLLING,
        rationale=rationale,
        windows=tuple(windows),
        selected_at=_now(),
        selected_by=selected_by,
        alternatives_considered=alternatives,
    )


def partitioned(
    *,
    dataset: str,
    available_start: datetime,
    available_end: datetime,
    train: tuple[datetime, datetime],
    validation: tuple[datetime, datetime],
    holdout: tuple[datetime, datetime],
    rationale: str,
    selected_by: str = "",
    alternatives: tuple[str, ...] = (),
) -> TimeScope:
    """An explicit three-way split, checked for leakage and ordering."""
    windows = (
        Window(role="train", start=train[0], end=train[1]),
        Window(role="validation", start=validation[0], end=validation[1]),
        Window(role="holdout", start=holdout[0], end=holdout[1]),
    )
    return TimeScope(
        dataset=dataset,
        available_start=available_start,
        available_end=available_end,
        selected_start=min(w.start for w in windows),
        selected_end=max(w.end for w in windows),
        method=SelectionMethod.FIXED_DATE_RANGE,
        rationale=rationale,
        windows=windows,
        selected_at=_now(),
        selected_by=selected_by,
        alternatives_considered=alternatives,
    )


def regimes(
    *,
    dataset: str,
    available_start: datetime,
    available_end: datetime,
    named: tuple[str, ...],
    spans: tuple[tuple[datetime, datetime], ...],
    rationale: str,
    selected_by: str = "",
    alternatives: tuple[str, ...] = (),
) -> TimeScope:
    """Several deliberately different historical environments, named and dated."""
    if len(named) != len(spans):
        raise ScopeError("every regime needs a span and every span needs a name")
    if not named:
        raise ScopeError("a cross-regime scope needs at least one regime")
    windows = tuple(
        Window(role=f"regime-{index + 1}", start=span[0], end=span[1], selector=name)
        for index, (name, span) in enumerate(zip(named, spans, strict=True))
    )
    return TimeScope(
        dataset=dataset,
        available_start=available_start,
        available_end=available_end,
        selected_start=min(w.start for w in windows),
        selected_end=max(w.end for w in windows),
        method=(
            SelectionMethod.CROSS_REGIME if len(named) > 1 else SelectionMethod.REGIME_SELECTED
        ),
        rationale=rationale,
        windows=windows,
        segments=named,
        selected_at=_now(),
        selected_by=selected_by,
        alternatives_considered=alternatives,
    )


# ── selection exposure ───────────────────────────────────────────────────────


def exposure(scopes: builtins.list[TimeScope]) -> dict[str, Any]:
    """How many distinct windows were tried for one claim.

    Searching windows is searching. A programme that tried two years, then
    five, then ten and reported the ten-year number ran three experiments, and
    the deflated Sharpe has to see three. This counts distinct fingerprints so
    that re-running the identical window does not inflate the figure.
    """
    seen = {scope.fingerprint(): scope for scope in scopes}
    return {
        "windows_tried": len(seen),
        "distinct_methods": sorted({str(s.method) for s in seen.values()}),
        "spans": [s.describe() for s in seen.values()],
        # Stated rather than left to be inferred, because the inference is the
        # one people skip.
        "counts_as_selection": len(seen) > 1,
        "note": (
            "Each distinct window is a separate look at the data. Multiple-testing "
            "corrections must include them, or the reported result is selected."
            if len(seen) > 1
            else "One window was tried, so window selection adds no exposure."
        ),
    }
