"""A regime report, read out loud — by arithmetic, never by a model.

`forge.analytics.regime` answers *what happened in each regime*. It returns a
grid of numbers, which is the correct thing for it to return and the wrong
thing to put in front of somebody. A four-cell matrix of P&L, win rate,
exposure and trade share is twenty numbers, and the question a person actually
arrived with — "does this strategy depend on one market condition?" — is not
any one of them.

This module turns that grid into a small number of **findings**, each shaped:

    FACT            a restatement of numbers, and nothing else
    INTERPRETATION  what the fact suggests, hedged to what it can support
    IMPLICATION     the decision the fact bears on
    EVIDENCE        every number the fact rests on, carried with it

**Why this is arithmetic and not a prompt.** A language model asked to
summarise a regime table will produce fluent sentences in every case, including
the cases where the table supports nothing — and a sentence that reads the same
whether or not the evidence exists is worse than no sentence, because it is
indistinguishable from one that was earned. Everything here is derived by
comparison and division from the report it was given. A model may later put
these findings into prose; it cannot add a finding, remove one, or change the
numbers one rests on.

**What it refuses to say.** A cell below `MIN_TRADES_FOR_ESTIMATE` produces no
claim about rates. A report built on a full-sample volatility threshold is
`DESCRIPTIVE` throughout, because the labels used information from after the
bars they label — the arithmetic is the same and the standing is not. A regime
the strategy never traded produces a finding that says so rather than no
finding at all: the empty corner of the grid is itself the answer to "where has
this not been tested".
"""

from __future__ import annotations

from enum import StrEnum

from forge.analytics.regime import (
    MEASURED,
    MIN_TRADES_FOR_ESTIMATE,
    Basis,
    Regime,
    RegimeCell,
    RegimeReport,
)
from forge.contracts.models import FrozenModel

#: A regime holding at least this share of the classified bars is somewhere the
#: strategy demonstrably lives, rather than somewhere it passed through. Used
#: only to decide whether an exposure/contribution mismatch is worth stating.
MATERIAL_EXPOSURE = 0.15

#: Gross-profit share above which the edge is called concentrated. The same
#: threshold `forge.analytics.regime.summarise` uses for its own warning, named
#: here so the two cannot drift apart.
CONCENTRATION_LIMIT = 0.6

#: Bar-to-bar probability of staying in a regime, above which the regime is
#: called persistent. A 2x2 classified per bar is sticky by construction — the
#: trend length is 200 bars — so this is deliberately high.
PERSISTENCE_LIMIT = 0.9

#: Below this many observed transitions out of a regime, its persistence is an
#: order statistic of a handful of bars and is not reported.
MIN_TRANSITIONS = 200


class Standing(StrEnum):
    """How much weight a finding can carry.

    Three states rather than a score, because the distinctions are categorical:
    a thin sample is not a weak measurement, and a descriptive label is not an
    uncertain one. Collapsing them into a number would let them average.
    """

    #: Enough trades to estimate, on trailing (point-in-time) labels.
    MEASURED = "MEASURED"
    #: The arithmetic is exact; the sample behind it is too small to generalise.
    THIN = "THIN"
    #: Labels used information later than the bars they label. Describes results
    #: already produced; never evidence about what a strategy would have done.
    DESCRIPTIVE = "DESCRIPTIVE"


class Kind(StrEnum):
    """What question a finding answers. Stable, so a surface can order them."""

    SOURCE = "SOURCE"                 # where did the money come from?
    DRAG = "DRAG"                     # where was it lost?
    MISMATCH = "MISMATCH"             # time spent vs profit earned
    PERSISTENCE = "PERSISTENCE"       # does the helpful condition last?
    UNTESTED = "UNTESTED"             # where has this not been tried?
    COVERAGE = "COVERAGE"             # what did the classifier not place?


class Evidence(FrozenModel):
    """One number a claim rests on, with the name it is known by elsewhere.

    `value` is the number; `display` is how it should be shown. Carrying both
    stops a surface from having to guess whether 0.62 is a ratio, a percentage
    or a currency amount, and stops the claim's sentence from being the only
    place the figure appears.
    """

    label: str
    value: float
    display: str
    detail: str = ""


class Finding(FrozenModel):
    """One thing worth saying about a strategy's regime behaviour."""

    kind: Kind
    standing: Standing
    #: A restatement of numbers. Contains no inference.
    fact: str
    #: What the fact suggests. Hedged to what the standing supports.
    interpretation: str
    #: The decision the fact bears on. Never an instruction to trade.
    implication: str
    evidence: tuple[Evidence, ...]
    #: The regimes the finding is about, so a surface can highlight the cells
    #: rather than make the reader match names to a grid.
    regimes: tuple[Regime, ...] = ()


class Reading(FrozenModel):
    """Every finding drawn from one regime report, in the order to show them."""

    findings: tuple[Finding, ...]
    #: The weakest standing any finding carries. A surface that shows only a
    #: headline still has to show this, or it has dropped the caveat.
    standing: Standing
    #: Restated from the report so a reading can be checked against the report
    #: it came from without holding both.
    total_trades: int
    classified_trades: int
    attribution: str
    series_fingerprint: str


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def _standing(report: RegimeReport, thin: bool) -> Standing:
    if report.settings.basis is Basis.FULL_SAMPLE:
        return Standing.DESCRIPTIVE
    return Standing.THIN if thin else Standing.MEASURED


def _cell(report: RegimeReport, regime: Regime) -> RegimeCell | None:
    return next((cell for cell in report.cells if cell.regime is regime), None)


def _source(report: RegimeReport) -> Finding | None:
    """Where the gross profit came from, and how concentrated that is."""
    if report.concentration is None or report.concentration_regime is None:
        return None
    cell = _cell(report, report.concentration_regime)
    if cell is None:
        return None
    share = report.concentration
    concentrated = share > CONCENTRATION_LIMIT
    standing = _standing(report, cell.insufficient)
    return Finding(
        kind=Kind.SOURCE,
        standing=standing,
        fact=(
            f"{_pct(share)} of gross profit came from {cell.label}, over "
            f"{cell.trade_count} trade(s) taken in {_pct(cell.bar_exposure)} of "
            "classified bars."
        ),
        interpretation=(
            "The edge is concentrated in one market condition rather than spread "
            "across them."
            if concentrated
            else "Profit is spread across more than one market condition."
        ),
        implication=(
            "A result that lives in one regime is a bet that the regime recurs. "
            "Size and expectations belong against that regime's frequency, not "
            "against the whole sample."
            if concentrated
            else "No single condition carries the result, so a change in one "
            "regime's frequency moves the outcome less than the headline suggests."
        ),
        evidence=(
            Evidence(
                label="gross profit share",
                value=share,
                display=_pct(share),
                detail=f"of gross profit across regimes that made any, from {cell.label}",
            ),
            Evidence(
                label="trades in regime",
                value=float(cell.trade_count),
                display=f"{cell.trade_count}",
            ),
            Evidence(
                label="bar exposure",
                value=cell.bar_exposure,
                display=_pct(cell.bar_exposure),
                detail="share of classified bars spent in this regime",
            ),
            Evidence(
                label="net P&L in regime",
                value=cell.net_pnl,
                display=_money(cell.net_pnl),
            ),
        ),
        regimes=(cell.regime,),
    )


def _drag(report: RegimeReport) -> Finding | None:
    """The regime that cost the most per trade, where there is a sample to say so."""
    losers = [
        cell
        for cell in report.cells
        if cell.average_trade is not None and cell.average_trade < 0 and cell.trade_count > 0
    ]
    if not losers:
        return None
    worst = min(losers, key=lambda cell: cell.average_trade or 0.0)
    average = worst.average_trade or 0.0
    standing = _standing(report, worst.insufficient)
    return Finding(
        kind=Kind.DRAG,
        standing=standing,
        fact=(
            f"{worst.label} averaged {_money(average)} per trade over "
            f"{worst.trade_count} trade(s), for {_money(worst.net_pnl)} net."
        ),
        interpretation=(
            "This condition has cost money every time the strategy has met it, "
            "on a sample too small to say how reliably."
            if worst.insufficient
            else "The strategy loses in this condition rather than merely "
            "underperforming in it."
        ),
        implication=(
            "Whether this is worth avoiding depends on how often the condition "
            "recurs and on whether it can be identified before entry rather than "
            "after. A filter fitted to these same trades is not evidence that it "
            "would have helped."
        ),
        evidence=(
            Evidence(
                label="average trade",
                value=average,
                display=_money(average),
                detail=f"net, per trade, in {worst.label}",
            ),
            Evidence(
                label="trades",
                value=float(worst.trade_count),
                display=f"{worst.trade_count}",
            ),
            Evidence(label="net P&L", value=worst.net_pnl, display=_money(worst.net_pnl)),
            Evidence(
                label="bar exposure",
                value=worst.bar_exposure,
                display=_pct(worst.bar_exposure),
                detail="share of classified bars spent in this regime",
            ),
        ),
        regimes=(worst.regime,),
    )


def _mismatch(report: RegimeReport) -> Finding | None:
    """A regime the strategy spends real time in without earning from it.

    Exposure and contribution are separate facts, and the gap between them is
    the one a P&L column cannot show: a condition holding 40% of the clock and
    5% of the profit is a different problem from one the strategy rarely meets.
    """
    total_net = sum(cell.net_pnl for cell in report.cells)
    if total_net <= 0:
        return None
    candidates = [
        cell
        for cell in report.cells
        if cell.bar_exposure >= MATERIAL_EXPOSURE
        and cell.trade_count > 0
        and cell.net_pnl / total_net < cell.bar_exposure / 2
    ]
    if not candidates:
        return None
    cell = max(candidates, key=lambda item: item.bar_exposure)
    contribution = cell.net_pnl / total_net
    standing = _standing(report, cell.insufficient)
    return Finding(
        kind=Kind.MISMATCH,
        standing=standing,
        fact=(
            f"{cell.label} held {_pct(cell.bar_exposure)} of classified bars and "
            f"produced {_pct(contribution)} of net P&L, across "
            f"{cell.trade_count} trade(s)."
        ),
        interpretation=(
            "The strategy is exposed to this condition far more than it is paid "
            "for being there."
        ),
        implication=(
            "Time in a condition that does not pay is risk carried for nothing, "
            "and it is also where a small deterioration in costs or fills turns "
            "the total negative first."
        ),
        evidence=(
            Evidence(
                label="bar exposure",
                value=cell.bar_exposure,
                display=_pct(cell.bar_exposure),
                detail="share of classified bars spent in this regime",
            ),
            Evidence(
                label="net P&L share",
                value=contribution,
                display=_pct(contribution),
                detail="of the strategy's total net P&L",
            ),
            Evidence(label="net P&L", value=cell.net_pnl, display=_money(cell.net_pnl)),
            Evidence(label="trades", value=float(cell.trade_count), display=f"{cell.trade_count}"),
        ),
        regimes=(cell.regime,),
    )


def _persistence(report: RegimeReport) -> Finding | None:
    """How sticky the regime carrying the profit is, measured bar to bar.

    Only stated for the regime the SOURCE finding names, because persistence is
    interesting precisely where the result depends on it. Counts, not just the
    rate: a 95% built on forty transitions is not the same claim as one built on
    forty thousand, and `MIN_TRANSITIONS` refuses the first.
    """
    target = report.concentration_regime
    if target is None:
        return None
    try:
        row_index = MEASURED.index(target)
    except ValueError:
        return None
    if row_index >= len(report.transitions):
        return None
    row = report.transitions[row_index]
    observed = sum(row)
    if observed < MIN_TRANSITIONS:
        return None
    stayed = row[row_index] if row_index < len(row) else 0
    rate = stayed / observed
    cell = _cell(report, target)
    label = cell.label if cell else target.value
    persistent = rate >= PERSISTENCE_LIMIT
    # Expected run length, in bars, for a first-order chain at this rate. Only
    # meaningful while the rate is below 1; a regime that never left the sample
    # has no measured exit to average over.
    runs = 1.0 / (1.0 - rate) if rate < 1.0 else float("inf")
    standing = _standing(report, False)
    return Finding(
        kind=Kind.PERSISTENCE,
        standing=standing,
        fact=(
            f"After a {label} bar, the next bar was {label} {_pct(rate)} of the "
            f"time, over {observed:,} observed transitions."
        ),
        interpretation=(
            f"The condition the profit depends on tends to last — about "
            f"{runs:,.0f} bars per run at this rate."
            if persistent and runs != float("inf")
            else "The condition the profit depends on turns over quickly."
        ),
        implication=(
            "A persistent condition means the strategy gets long stretches of "
            "the environment it needs, and long stretches of the ones it does "
            "not. Both belong in a drawdown expectation."
            if persistent
            else "A condition that turns over quickly gives less opportunity per "
            "entry into it, and less warning when it ends."
        ),
        evidence=(
            Evidence(
                label="stay probability",
                value=rate,
                display=_pct(rate),
                detail=f"bar-to-bar, within {label}",
            ),
            Evidence(
                label="observed transitions",
                value=float(observed),
                display=f"{observed:,}",
                detail=f"bars that were {label} and had a classified successor",
            ),
        ),
        regimes=(target,),
    )


def _untested(report: RegimeReport) -> Finding | None:
    """The corners of the grid the strategy has not been through."""
    thin = [cell for cell in report.cells if cell.trade_count < MIN_TRADES_FOR_ESTIMATE]
    if not thin:
        return None
    never = [cell for cell in thin if cell.trade_count == 0]
    names = ", ".join(cell.label for cell in thin)
    exposure = sum(cell.bar_exposure for cell in thin)
    return Finding(
        kind=Kind.UNTESTED,
        standing=Standing.THIN,
        fact=(
            f"{names} hold fewer than {MIN_TRADES_FOR_ESTIMATE} trade(s) each "
            f"({'none at all in ' + ', '.join(c.label for c in never) + '; ' if never else ''}"
            f"{_pct(exposure)} of classified bars between them)."
        ),
        interpretation=(
            "The strategy has not been observed enough in these conditions for "
            "any rate from them to be an estimate."
        ),
        implication=(
            "These are not conditions the strategy has passed; they are "
            "conditions it has not been tried in. An unmeasured corner of the "
            "grid is a risk that has not been priced, not one that has been "
            "cleared."
        ),
        evidence=tuple(
            Evidence(
                label=f"trades in {cell.label}",
                value=float(cell.trade_count),
                display=f"{cell.trade_count}",
                detail=f"{_pct(cell.bar_exposure)} of classified bars",
            )
            for cell in thin
        ),
        regimes=tuple(cell.regime for cell in thin),
    )


def _coverage(report: RegimeReport) -> Finding | None:
    """What the classifier could not place, stated rather than absorbed."""
    if not report.unclassified_trades and report.coverage >= 0.9:
        return None
    share = (
        report.unclassified_trades / report.total_trades if report.total_trades else 0.0
    )
    return Finding(
        kind=Kind.COVERAGE,
        standing=Standing.THIN,
        fact=(
            f"{report.unclassified_trades} of {report.total_trades} trades "
            f"({_pct(share)}) ran in bars the classifier could not place; "
            f"{_pct(report.coverage)} of bars were classified."
        ),
        interpretation=(
            "Every share in this report is over what was classified, not over "
            "the whole run."
        ),
        implication=(
            "Unplaced trades are counted nowhere rather than folded into a "
            "regime. A grid that appears to account for the strategy is "
            "accounting for the part of it the classifier could reach."
        ),
        evidence=(
            Evidence(
                label="unclassified trades",
                value=float(report.unclassified_trades),
                display=f"{report.unclassified_trades}",
            ),
            Evidence(
                label="bar coverage",
                value=report.coverage,
                display=_pct(report.coverage),
            ),
        ),
    )


#: Display order. SOURCE and DRAG answer "what happened"; MISMATCH and
#: PERSISTENCE answer "why"; UNTESTED and COVERAGE bound what the rest can
#: claim, so they come last and are never dropped.
_ORDER: tuple[Kind, ...] = (
    Kind.SOURCE,
    Kind.DRAG,
    Kind.MISMATCH,
    Kind.PERSISTENCE,
    Kind.UNTESTED,
    Kind.COVERAGE,
)


def read(report: RegimeReport) -> Reading:
    """Every finding one regime report supports, in display order.

    Deterministic: the same report reads the same way, which is what lets a
    surface cache it and a test assert on it. A report supporting nothing
    returns an empty reading rather than a filler sentence.
    """
    built = {
        Kind.SOURCE: _source(report),
        Kind.DRAG: _drag(report),
        Kind.MISMATCH: _mismatch(report),
        Kind.PERSISTENCE: _persistence(report),
        Kind.UNTESTED: _untested(report),
        Kind.COVERAGE: _coverage(report),
    }
    findings: list[Finding] = [
        finding for kind in _ORDER if (finding := built[kind]) is not None
    ]

    if report.settings.basis is Basis.FULL_SAMPLE:
        standing = Standing.DESCRIPTIVE
    elif any(finding.standing is Standing.THIN for finding in findings):
        standing = Standing.THIN
    else:
        standing = Standing.MEASURED

    return Reading(
        findings=tuple(findings),
        standing=standing,
        total_trades=report.total_trades,
        classified_trades=report.classified_trades,
        attribution=report.attribution,
        series_fingerprint=report.series_fingerprint,
    )
