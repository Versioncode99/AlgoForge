"""What a metric is, why it matters, what a particular value means, and its caveats.

§27 asks for four answers per metric, and the fourth — *what does this value
mean here* — is the one that makes the other three worth writing. A glossary
that explains the deflated Sharpe ratio in general is a textbook. One that says
"0.41 is below the 0.50 the judge requires, against a best-of-142-trials noise
hurdle of 0.33" is an explanation.

**Every entry describes a metric this repository actually computes.** The
`computed_by` field names the function, and
`tests/explain/test_metrics.py::test_every_metric_names_a_function_that_exists`
imports each one. A glossary that drifts from the code is worse than no
glossary, because it is believed.

**Interpretation is bounded, not generated.** `interpret` compares the value
against thresholds that are imported from the modules that enforce them —
`forge.judge.engine.DEFLATED_SHARPE_THRESHOLD`, not a number retyped here — and
returns one of a fixed set of readings. Nothing here writes a sentence about a
value it was not given.

**Caveats are not disclaimers.** Each one names a specific way the number
misleads: a Sharpe over 40 trades, a Calmar with no drawdown in the denominator,
a PBO computed over too few configurations. They are the failure modes the
judge's own INCONCLUSIVE states exist for, stated in the place somebody is
looking at the number.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from forge.contracts.models import FrozenModel
from forge.judge.engine import (
    DEFLATED_SHARPE_THRESHOLD,
    MINIMUM_TRADES,
    MINIMUM_TRIAL_CONFIGURATIONS,
    OVERFITTING_THRESHOLD,
)


class Reading(StrEnum):
    """How a value sits against the standard, when there is a standard."""

    MEETS = "meets"
    BELOW = "below"
    #: There is a threshold, and this value cannot be compared against it — a
    #: Calmar with no drawdown, a Sharpe over four trades.
    NOT_COMPARABLE = "not_comparable"
    #: No threshold exists. Reported so the interface does not draw a verdict
    #: pill against a number nobody set a bar for.
    NO_STANDARD = "no_standard"


class Metric(FrozenModel):
    """One measurement, explained four ways."""

    key: str
    name: str
    #: §27's first question. One sentence, no formula.
    what: str
    #: §27's second. Why a decision would differ if this number differed.
    why: str
    #: The technical definition, for the operator who wants it. Shown at the
    #: Quant disclosure level and behind a control at the others.
    definition: str
    caveats: tuple[str, ...] = ()
    #: Dotted path to the function that computes it.
    computed_by: str = ""
    #: The bar this metric is held to, when one exists, and where it is set.
    threshold: float | None = None
    threshold_source: str = ""
    #: True when a *lower* value is better, so `interpret` compares the right way.
    lower_is_better: bool = False
    unit: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class Interpretation(FrozenModel):
    """What one value of one metric means, here."""

    key: str
    value: float | None = None
    reading: Reading
    #: Assembled from the metric and the value. Never generated free-hand.
    sentence: str
    caveats: tuple[str, ...] = ()
    threshold: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


METRICS: dict[str, Metric] = {
    metric.key: metric
    for metric in (
        Metric(
            key="sharpe",
            name="Sharpe ratio",
            what="Return per unit of the variability that produced it.",
            why=(
                "Two strategies with the same profit are not the same strategy if one "
                "got there smoothly and the other by swinging. Sharpe is the crudest "
                "way to tell them apart, and the one every other risk metric is "
                "compared against."
            ),
            definition=(
                "Mean of the per-period returns divided by their standard deviation. "
                "AlgoForge reports the per-period figure and annualises it only when "
                "the periods per year are known, because annualising an unknown "
                "frequency invents a number."
            ),
            caveats=(
                "It says nothing about the shape of the losses. A strategy that loses "
                "slowly and then all at once can have a high Sharpe until the day it "
                "does not.",
                "Over a short sample it is mostly noise: the judge requires "
                f"{MINIMUM_TRADES} trades before it estimates higher moments at all.",
                "A Sharpe from an in-sample fit is a description of the fit, not a "
                "prediction.",
            ),
            computed_by="forge.judge.statistics.per_period_sharpe",
        ),
        Metric(
            key="deflated_sharpe",
            name="Deflated Sharpe ratio",
            what=(
                "The probability that a strategy's Sharpe is genuinely above zero, "
                "after accounting for how many variants were tried to find it."
            ),
            why=(
                "Try enough strategies and one of them looks excellent by luck alone. "
                "This is the metric that asks how excellent it would have had to look "
                "to be surprising, given the search."
            ),
            definition=(
                "Bailey and Lopez de Prado's deflated Sharpe: the probabilistic Sharpe "
                "ratio evaluated against a benchmark equal to the expected maximum "
                "Sharpe of N independent random trials, using the observed spread of "
                "trial Sharpes where it is known."
            ),
            caveats=(
                "It depends on the trial count being honest. Under-reporting the "
                "number of variants tried inflates this number, which is why the judge "
                "reads the count from the experiment record rather than from a "
                "parameter.",
                "With one trial and no spread it cannot be computed, and the judge "
                "reports INCONCLUSIVE rather than assuming a benchmark.",
            ),
            computed_by="forge.judge.statistics.deflated_sharpe_ratio",
            threshold=DEFLATED_SHARPE_THRESHOLD,
            threshold_source="forge.judge.engine.DEFLATED_SHARPE_THRESHOLD (gate G5)",
        ),
        Metric(
            key="psr",
            name="Probabilistic Sharpe ratio",
            what=(
                "The probability that the true Sharpe is above a benchmark, given the "
                "sample's length, skew and fat tails."
            ),
            why=(
                "A Sharpe of 1.5 over 40 trades and one over 400 are different claims. "
                "This turns the point estimate into a probability that reflects how "
                "much evidence is behind it."
            ),
            definition=(
                "Normal CDF of the standardised difference between the observed and "
                "benchmark Sharpe, with the denominator adjusted for skewness and "
                "kurtosis of the return series."
            ),
            caveats=(
                "It is a statement about the sample, not about the future. A high PSR "
                "on out-of-sample data is evidence; on in-sample data it is arithmetic.",
            ),
            computed_by="forge.judge.statistics.probabilistic_sharpe_ratio",
        ),
        Metric(
            key="pbo",
            name="Probability of backtest overfitting",
            what=(
                "How often the configuration that looked best in one half of the data "
                "underperformed the median in the other half."
            ),
            why=(
                "It measures the selection process rather than the strategy. A low PBO "
                "says that picking the winner was not itself the source of the result."
            ),
            definition=(
                "Combinatorially symmetric cross-validation: split the trial matrix "
                "into S groups, form every balanced train/test partition, and count the "
                "fraction of partitions where the in-sample best ranks below the median "
                "out of sample."
            ),
            caveats=(
                f"It needs at least {MINIMUM_TRIAL_CONFIGURATIONS} configurations to "
                "have anything to rank. Below that the judge reports INCONCLUSIVE "
                "rather than a number.",
                "It cannot see overfitting that happened before the trial matrix was "
                "recorded — a hypothesis chosen after looking at the data is invisible "
                "to it.",
            ),
            computed_by="forge.judge.statistics.probability_of_backtest_overfitting",
            threshold=OVERFITTING_THRESHOLD,
            threshold_source="forge.judge.engine.OVERFITTING_THRESHOLD (gate G11)",
            lower_is_better=True,
        ),
        Metric(
            key="max_drawdown",
            name="Maximum drawdown",
            what="The largest peak-to-trough fall in cumulative profit.",
            why=(
                "On a prop account it is the number that ends the account. Every other "
                "measure is about whether the strategy works; this one is about whether "
                "you are still there when it does."
            ),
            definition=(
                "Maximum over time of (running peak of cumulative PnL) minus "
                "(cumulative PnL), in account currency."
            ),
            caveats=(
                "It is a single realised path. The worst drawdown that *could* have "
                "happened is usually larger, which is what the bootstrapped p95 "
                "estimate is for.",
                "It depends on the sample window: a longer backtest almost always has "
                "a bigger maximum drawdown, so comparing two over different periods "
                "compares the periods.",
            ),
            computed_by="forge.judge.metrics.max_drawdown",
            lower_is_better=True,
        ),
        Metric(
            key="profit_factor",
            name="Profit factor",
            what="Gross profit divided by gross loss.",
            why=(
                "It separates a strategy that wins often and small from one that wins "
                "rarely and large, which Sharpe alone can hide."
            ),
            definition="Sum of winning trade PnL over the absolute sum of losing trade PnL.",
            caveats=(
                "Undefined with no losing trades, which is a sign of too short a "
                "sample rather than of a perfect strategy.",
                "Insensitive to ordering: the same trades in a different sequence give "
                "the same profit factor and a very different experience.",
            ),
            computed_by="forge.judge.metrics.profit_factor",
            threshold=1.1,
            threshold_source="forge.judge.engine (gate G6)",
        ),
        Metric(
            key="calmar",
            name="Calmar ratio",
            what="Net profit divided by the worst drawdown that produced it.",
            why=(
                "It answers the question a prop account actually asks: how much did I "
                "have to be willing to lose to earn this."
            ),
            definition="Cumulative PnL over maximum drawdown, over the same window.",
            caveats=(
                "With no drawdown there is no denominator. The judge reports "
                "NO_DRAWDOWN_TO_MEASURE rather than a zero, because a zero here reads "
                "as a terrible strategy rather than as an unmeasured one.",
            ),
            computed_by="forge.judge.statistics.calmar_ratio",
            threshold=0.5,
            threshold_source="forge.judge.engine (gate G8)",
        ),
        Metric(
            key="sortino",
            name="Sortino ratio",
            what="Return per unit of *downside* variability only.",
            why=(
                "Sharpe penalises upside swings as much as downside ones. Sortino does "
                "not, which matters for strategies whose good days are large."
            ),
            definition=(
                "Mean return divided by the standard deviation of returns below a "
                "target, by default zero."
            ),
            caveats=(
                "With few losing periods the denominator is estimated from a handful "
                "of points and the ratio is unstable.",
            ),
            computed_by="forge.judge.statistics.sortino_ratio",
        ),
        Metric(
            key="permutation_p",
            name="Sign-permutation p-value",
            what=(
                "How often randomly flipping the sign of each trade's result would "
                "have produced a total at least this good."
            ),
            why=(
                "It tests the result against the null that the entry timing carried no "
                "information, without assuming the returns are normal."
            ),
            definition=(
                "Fraction of random sign assignments over the observed trade PnL whose "
                "sum is greater than or equal to the observed sum."
            ),
            caveats=(
                "It tests one specific null. Passing it does not establish that the "
                "declared mechanism is the reason — that is what G9 is for.",
            ),
            computed_by="forge.judge.statistics.permutation_pvalue",
            threshold=0.05,
            threshold_source="forge.judge.engine (gate G6)",
            lower_is_better=True,
        ),
        Metric(
            key="min_track_record",
            name="Minimum track record length",
            what=(
                "How many observations would be needed before this Sharpe is "
                "distinguishable from zero at the chosen confidence."
            ),
            why=(
                "It turns 'the sample is small' into a number, and often the number is "
                "larger than the sample."
            ),
            definition=(
                "The sample length at which the probabilistic Sharpe ratio would reach "
                "the target confidence, given the observed Sharpe, skew and kurtosis."
            ),
            caveats=(
                "It assumes the observed moments are the true ones, which is exactly "
                "what is in doubt when the sample is short.",
            ),
            computed_by="forge.judge.statistics.minimum_track_record_length",
        ),
        Metric(
            key="expected_drawdown_p95",
            name="Expected drawdown (95th percentile)",
            what=(
                "The drawdown per contract that a bootstrapped 21-day window exceeded "
                "on five percent of paths."
            ),
            why=(
                "It is what position size is actually computed against. The realised "
                "maximum drawdown is one path; this is the distribution that path came "
                "from."
            ),
            definition=(
                "Stationary block bootstrap over out-of-sample per-contract daily PnL, "
                "worst peak-to-trough within each resampled horizon, 95th percentile."
            ),
            caveats=(
                "It resamples history, so it cannot produce a drawdown worse than the "
                "sequences history contained.",
                "It needs enough out-of-sample days to have a tail. Below the minimum "
                "there is no estimate, and sizing falls to your configured minimum "
                "rather than to a guess.",
            ),
            computed_by="forge.propdesk.survival.drawdown_distribution",
        ),
        Metric(
            key="risk_fraction",
            name="Risk fraction",
            what=(
                "The share of this account's buffer to its loss floor that one "
                "allocation is permitted to cost."
            ),
            why=(
                "It is the single number the risk modes move, and the only input the "
                "advisory layer can influence. Everything from here to an order is "
                "deterministic."
            ),
            definition=(
                "Contracts = floor(buffer x risk_fraction / expected drawdown per "
                "contract), then the smallest of that, the account's contract cap and "
                "the allocator's ceiling."
            ),
            caveats=(
                "It is a fraction of the buffer, not of equity. An account close to "
                "its floor has a small buffer and therefore a small position at the "
                "same fraction, which is the intent.",
            ),
            computed_by="forge.propdesk.allocation.Allocator",
        ),
    )
}


def metric(key: str) -> Metric | None:
    return METRICS.get(key)


def interpret(key: str, value: float | None, *, context: str = "") -> Interpretation:
    """What this value of this metric means, assembled from the entry.

    `context` is appended verbatim when supplied — the caller has state this
    module does not, such as the trial count behind a deflated Sharpe. It is
    never invented here.
    """
    entry = METRICS.get(key)
    if entry is None:
        raise KeyError(f"no metric '{key}'. Known: {', '.join(sorted(METRICS))}")

    if value is None:
        return Interpretation(
            key=key,
            value=None,
            reading=Reading.NOT_COMPARABLE,
            sentence=f"{entry.name} was not measured.",
            caveats=entry.caveats,
            threshold=entry.threshold,
        )

    if entry.threshold is None:
        return Interpretation(
            key=key,
            value=value,
            reading=Reading.NO_STANDARD,
            sentence=f"{entry.name} is {value:,.4g}. {entry.what}"
            + (f" {context}" if context else ""),
            caveats=entry.caveats,
        )

    meets = value <= entry.threshold if entry.lower_is_better else value >= entry.threshold
    comparison = "at or below" if entry.lower_is_better else "at or above"
    shortfall = "above" if entry.lower_is_better else "below"
    sentence = (
        f"{entry.name} is {value:,.4g}, {comparison} the {entry.threshold:,.4g} "
        f"required by {entry.threshold_source}."
        if meets
        else f"{entry.name} is {value:,.4g}, {shortfall} the {entry.threshold:,.4g} "
        f"required by {entry.threshold_source}."
    )
    return Interpretation(
        key=key,
        value=value,
        reading=Reading.MEETS if meets else Reading.BELOW,
        sentence=sentence + (f" {context}" if context else ""),
        caveats=entry.caveats,
        threshold=entry.threshold,
    )


def catalogue(keys: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Every metric, or the named ones, as the glossary renders them."""
    selected = keys or tuple(METRICS)
    return [METRICS[key].as_dict() for key in selected if key in METRICS]


class MetricValue(FrozenModel):
    """A metric and its value together, for a caller assembling a panel."""

    key: str
    value: float | None = None
    context: str = ""
    #: Where this number came from, so a reader can tell an out-of-sample figure
    #: from an in-sample one without asking.
    basis: str = Field(default="")

    def explained(self) -> Interpretation:
        return interpret(self.key, self.value, context=self.context)
