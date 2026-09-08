"""Saying what a verdict means, without changing what it says.

The judge already produces the right answer. What it does not produce is a
sentence a person can act on: a gate reading `G5 INCONCLUSIVE
DSR_NOT_MEASURABLE` is correct and nearly useless to someone deciding whether
to keep working on a strategy.

This module reads a finished `Verdict` and returns the same information ranked
and explained — what fired, how bad it is, why it matters, and what would
actually address it.

**It has no opinion.** Nothing here can change a decision, a grade, a gate
status or a metric. It is a pure function of a verdict that has already been
reached, which is the only safe place for a layer like this to sit: an
explanation that could adjust its own subject would be a second judge with
softer standards.

**Three failure modes it is built to avoid.**

*Collapsing INCONCLUSIVE into FAIL.* They are different states and the
difference is the whole architecture. A gate that did not measure something is
reported as unmeasured, with what it would take to measure it — never as a
strategy that fell short.

*Suggesting a fix that games the test.* This is the real hazard. The obvious
"fix" for a deflated-Sharpe shortfall is to declare fewer trials, and for a
sample-size gate to lower the threshold. Every suggestion here is about
improving the strategy or gathering more evidence, and
`test_no_suggested_fix_proposes_weakening_the_standard` enforces it.

*Reading as a score.* The verdict already carries a grade. This adds no second
one, because two grades from one body of evidence is an invitation to quote the
kinder one.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from forge.contracts.models import FrozenModel
from forge.judge.models import GateResult, Verdict


class Severity(StrEnum):
    """How much this should change what you do next."""

    #: The decision turns on this. Nothing downstream is worth reading until it
    #: is addressed.
    CRITICAL = "CRITICAL"
    #: A real shortfall that does not by itself sink the verdict.
    HIGH = "HIGH"
    #: Something was not measured. Not a shortfall — an absence.
    NOT_MEASURED = "NOT_MEASURED"
    #: Worth knowing, changes nothing.
    INFO = "INFO"


#: Which dimension of the verdict each gate speaks to. Gates that speak to
#: whether the evidence exists at all are grouped under `integrity`, because a
#: strategy with no data receipt has not scored badly on anything — it has not
#: been measured.
DIMENSION: dict[str, str] = {
    "G0": "integrity",
    "G1": "integrity",
    "G2": "integrity",
    "G3": "sample_adequacy",
    "G4": "edge",
    "G5": "edge",
    "G6": "robustness",
    "G7": "integrity",
    "G8": "risk",
    "G9": "edge",
    "G10": "integrity",
    "G11": "robustness",
    "G12": "generalisation",
    "G13": "generalisation",
}

#: Gates whose failure means the rest of the report is not worth reading. These
#: are the ones about whether there is evidence at all, rather than about how
#: good the evidence is.
FOUNDATIONAL = frozenset({"G0", "G1", "G2", "G7", "G10"})

_WHY: dict[str, str] = {
    "G0": (
        "The bars this ran on were not shown to be sound. Every number downstream "
        "is computed from those bars, so a data fault does not degrade the result "
        "gracefully — it invalidates it."
    ),
    "G1": (
        "A claim frozen after seeing the result is not a prediction. Without a "
        "preregistered hypothesis there is no way to distinguish a strategy that "
        "worked from an explanation fitted to what happened."
    ),
    "G2": (
        "The strategy's own conformance suite is what proves it cannot read a bar "
        "it should not have. Until it passes, an apparent edge and a lookahead bug "
        "are indistinguishable."
    ),
    "G3": (
        "Higher moments are not estimable from a short sample. Sharpe, skew and "
        "drawdown all inherit the noise, and every gate downstream inherits it "
        "from them."
    ),
    "G4": (
        "The run did not make money out of sample. Nothing about robustness or "
        "risk changes that."
    ),
    "G5": (
        "Searching many configurations produces a best one whether or not any of "
        "them work. The deflated Sharpe asks whether this result beats what the "
        "search itself would have produced from noise."
    ),
    "G6": (
        "A profit that does not survive a sign permutation is consistent with "
        "having got the order of the trades lucky."
    ),
    "G7": (
        "A run that does not reproduce is one draw from an unspecified "
        "distribution. Every statistic computed from it inherits that, and a "
        "reproducible-run snapshot of it records a result nobody can obtain again."
    ),
    "G8": (
        "Return per unit of drawdown is what determines whether a strategy is "
        "survivable at size, independently of whether it is profitable."
    ),
    "G9": (
        "The strategy declared why it should work. If that mechanism is falsified, "
        "any remaining profit is unexplained — which makes it a pattern in this "
        "sample rather than a reason to expect it again."
    ),
    "G10": (
        "In-sample and development results are exploration. They are not evidence "
        "about what would have happened, because the strategy was shaped by them."
    ),
    "G11": (
        "The probability of backtest overfitting asks how often the "
        "best-in-sample configuration underperforms out of sample across splits. "
        "A high value means the selection procedure, not the strategy, produced "
        "the result."
    ),
    "G12": (
        "Walk-forward efficiency asks whether the edge survives being re-fitted "
        "and re-tested through time. A strategy that only works when fitted to the "
        "whole sample has not been shown to work forward."
    ),
    "G13": (
        "Combinatorial purged cross-validation reconstructs many possible paths "
        "through the same data. A strategy positive only on the path that actually "
        "happened is a strategy that got one draw."
    ),
}

#: What would actually address each gate. Every one of these is about improving
#: the strategy or gathering more evidence. None of them proposes moving a
#: threshold, declaring fewer trials, or re-running until a test passes — see
#: the module docstring, and the test that enforces it.
_FIX: dict[str, str] = {
    "G0": (
        "Inspect the dataset's health report and fix or replace the archive, then "
        "re-run. Do not judge on bars whose receipt has findings."
    ),
    "G1": (
        "Write the hypothesis and its falsifiable prediction before the next run, "
        "and let the run freeze them. If the claim has moved, the honest record is "
        "a new experiment rather than an amended one."
    ),
    "G2": (
        "Fix the strategy until its conformance suite passes, including the "
        "lookahead trap. A failing suite is a defect in the strategy, not a "
        "formality."
    ),
    "G3": (
        "Gather more trades: a longer window, more instruments, or a strategy that "
        "trades more often. Treat every current number as provisional until the "
        "sample supports it."
    ),
    "G4": (
        "The idea did not work out of sample. Revisit the mechanism rather than "
        "the parameters — tuning a strategy that lost out of sample is fitting to "
        "the out-of-sample data."
    ),
    "G5": (
        "Improve the strategy's raw edge, or test it on genuinely new data where "
        "the search has not been run. The hurdle is a property of how much was "
        "searched and is not the thing to change."
    ),
    "G6": (
        "Look for a version of the entry or exit with a larger effect. A profit "
        "factor barely above one leaves nothing for costs to eat."
    ),
    "G7": (
        "Find the source of non-determinism: module-level mutable state, an "
        "unseeded RNG, iteration over a set, or a dependence on wall clock. All "
        "four are defects independent of performance."
    ),
    "G8": (
        "Address the drawdown itself — a tighter or differently placed stop, a "
        "regime filter, or smaller size. Improving return per unit of risk is a "
        "change to the strategy, not to how it is measured."
    ),
    "G9": (
        "Either the mechanism is wrong and the strategy should be abandoned, or "
        "the test of it is wrong and should be corrected. Both are real outcomes; "
        "trading it anyway is not."
    ),
    "G10": (
        "Run this on the reserved validation slice, or on the lineage's holdout if "
        "it is genuinely ready. A holdout is spent once, so spend it on a strategy "
        "you already believe."
    ),
    "G11": (
        "Reduce how much the result depends on selection: fewer, better-motivated "
        "configurations, or a mechanism that does not need a specific parameter to "
        "work at all."
    ),
    "G12": (
        "Look at which folds fail and what they have in common. A strategy that "
        "works in some periods and not others is a regime bet, and worth knowing "
        "as one."
    ),
    "G13": (
        "Study the paths where it loses. If they share a market condition, that "
        "condition is the strategy's real dependency."
    ),
}

#: What would let an unmeasured gate be measured. Distinct from `_FIX` because
#: an absence is not a shortfall and the response to it is different.
_TO_MEASURE: dict[str, str] = {
    "G0": "Re-run the backtest so it records a data-quality receipt.",
    "G1": "Freeze a hypothesis before the run, so there is a claim to check against.",
    "G2": "Give the strategy a conformance suite and run it.",
    "G5": (
        "Deflation needs the number of configurations actually tried. Run the "
        "search through the engine so the trial count is recorded rather than "
        "assumed."
    ),
    "G7": "Run the determinism check: execute the same run twice and compare.",
    "G8": "This run had no drawdown to divide by, so risk is unmeasured rather than good.",
    "G9": "Run the mechanism check, which tests the declared prediction directly.",
    "G11": (
        "PBO needs a matrix of configurations across splits. Run a parameter sweep "
        "through the engine."
    ),
    "G12": "Run walk-forward validation.",
    "G13": "Run combinatorial purged cross-validation.",
}


class Finding(FrozenModel):
    """One thing worth knowing about a verdict, and what to do about it."""

    gate: str
    name: str
    dimension: str
    severity: Severity
    status: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    #: One sentence saying what happened, with the number that says it.
    headline: str
    #: The rule the gate applied and the value it observed, verbatim.
    rule: str
    observed: float | int | str
    why_it_matters: str
    #: What would address it, or — for an unmeasured gate — what would measure it.
    what_would_help: str


class ExplainedVerdict(FrozenModel):
    """A verdict, ranked and explained. Carries no judgement of its own."""

    verdict_id: str
    run_id: str
    #: Copied from the verdict, never recomputed. Two grades from one body of
    #: evidence is an invitation to quote the kinder one.
    decision: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    grade: Literal["A", "B", "C", "D", "F"]
    headline: str
    findings: tuple[Finding, ...]
    strengths: tuple[Finding, ...]
    passed: int
    failed: int
    unmeasured: int
    dimensions: dict[str, int]
    limitations: tuple[str, ...]
    labels: tuple[str, ...]


_ORDER = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.NOT_MEASURED: 2,
    Severity.INFO: 3,
}


def _severity(gate: GateResult) -> Severity:
    if gate.status == "INCONCLUSIVE":
        # An absence, ranked below every real shortfall and above nothing. It is
        # never CRITICAL: a gate that did not measure something has not found
        # anything wrong.
        return Severity.NOT_MEASURED
    if gate.status == "FAIL":
        return Severity.CRITICAL if gate.gate in FOUNDATIONAL else Severity.HIGH
    return Severity.INFO


def _headline(gate: GateResult) -> str:
    if gate.status == "INCONCLUSIVE":
        return f"{gate.name} was not measured — observed {gate.observed}."
    if gate.status == "FAIL":
        return f"{gate.name} did not hold: observed {gate.observed}."
    return f"{gate.name} held: observed {gate.observed}."


def _finding(gate: GateResult) -> Finding:
    severity = _severity(gate)
    if severity is Severity.NOT_MEASURED:
        help_text = _TO_MEASURE.get(
            gate.gate,
            "This gate reports an absence rather than a shortfall. The evidence it "
            "reads was not produced by this run.",
        )
    elif gate.status == "FAIL":
        help_text = _FIX.get(gate.gate, "")
    else:
        help_text = ""
    return Finding(
        gate=gate.gate,
        name=gate.name,
        dimension=DIMENSION.get(gate.gate, "integrity"),
        severity=severity,
        status=gate.status,
        headline=_headline(gate),
        rule=gate.rule,
        observed=gate.observed,
        why_it_matters=_WHY.get(gate.gate, ""),
        what_would_help=help_text,
    )


def _summary(verdict: Verdict, failed: list[GateResult], unmeasured: list[GateResult]) -> str:
    if verdict.decision == "FAIL":
        critical = [g for g in failed if g.gate in FOUNDATIONAL]
        if critical:
            names = ", ".join(f"{g.gate} {g.name.lower()}" for g in critical)
            return (
                f"FAIL because the evidence itself does not stand: {names}. Nothing "
                "downstream of that is worth reading yet."
            )
        names = ", ".join(f"{g.gate} {g.name.lower()}" for g in failed[:3])
        more = f" and {len(failed) - 3} more" if len(failed) > 3 else ""
        return f"FAIL on {names}{more}."
    if verdict.decision == "INCONCLUSIVE":
        names = ", ".join(f"{g.gate} {g.name.lower()}" for g in unmeasured[:3])
        more = f" and {len(unmeasured) - 3} more" if len(unmeasured) > 3 else ""
        return (
            f"INCONCLUSIVE: nothing failed, but {len(unmeasured)} gate(s) were not "
            f"measured — {names}{more}. This is an absence of evidence, not evidence "
            "of a problem, and it does not become a PASS by waiting."
        )
    if unmeasured:
        return (
            f"PASS on every measured gate, with {len(unmeasured)} still unmeasured. "
            "The verdict is only as strong as the gates that ran."
        )
    return "PASS on every gate. Every limitation below still applies."


def explain(verdict: Verdict) -> ExplainedVerdict:
    """Rank and explain a verdict that has already been reached.

    Pure: the decision, the grade, every gate status and every metric are copied
    through unchanged. Nothing in this module can move any of them.
    """
    failed = [gate for gate in verdict.gates if gate.status == "FAIL"]
    unmeasured = [gate for gate in verdict.gates if gate.status == "INCONCLUSIVE"]
    passed = [gate for gate in verdict.gates if gate.status == "PASS"]

    findings = sorted(
        (_finding(gate) for gate in verdict.gates if gate.status != "PASS"),
        key=lambda item: (_ORDER[item.severity], item.gate),
    )

    return ExplainedVerdict(
        verdict_id=verdict.verdict_id,
        run_id=verdict.run_id,
        decision=verdict.decision,
        grade=verdict.grade,
        headline=_summary(verdict, failed, unmeasured),
        findings=tuple(findings),
        # Reported separately, and never as a counterweight. A strategy with
        # eight passing gates and a failed one has a failed gate.
        strengths=tuple(_finding(gate) for gate in passed),
        passed=len(passed),
        failed=len(failed),
        unmeasured=len(unmeasured),
        dimensions=dict(verdict.dimensions),
        limitations=verdict.limitations,
        labels=verdict.labels,
    )


def as_dict(explained: ExplainedVerdict) -> dict[str, Any]:
    return explained.model_dump(mode="json")
