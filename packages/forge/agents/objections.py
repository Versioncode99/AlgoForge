"""The fourteen ways a promising result is wrong, checked one at a time.

D1 §6 asks for adversarial research whose output "must not simply be 'another
opinion'" -- it "must produce structured objections" -- and names fourteen
threats by name. `forge.agents.debate` already derives four specialists' claims
from the verdict rather than inventing them, which is the honest half. What it
could not do is say *which* of the fourteen were considered, because it did not
work from a list.

So this is the list, and the shape of the answer is the point:

**Every threat is answered on every review.** A threat nothing in the verdict
speaks to comes back `NOT_MEASURED`, never absent. A checklist that only shows
the boxes it ticked is not a checklist -- it is a summary, and the reader cannot
tell "we looked and it was fine" from "we never looked".

**Each answer names what settled it.** `RAISED` and `CLEARED` carry the gate
that decided, so an objection can be checked against the ladder rather than
believed. `NOT_MEASURED` carries the experiment that *would* settle it, which is
the only useful thing to say about a threat nobody measured.

**Nothing here moves a number.** The gate ladder is authoritative and this reads
it. An objection is a reading of evidence that already exists; if it could
change a verdict it would be a second judge, and the deterministic one would
stop being the judge.

The synthesis below is §6's other half -- supported and unsupported claims,
contradictions, unresolved questions, required experiments, confidence and
evidence references -- derived from the objections and the panel rather than
written by a model.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from forge.agents.models import AgentClaim
from forge.contracts.models import FrozenModel
from forge.judge.models import GateResult, Verdict


class Threat(StrEnum):
    """The fourteen §6 names, in the order it names them."""

    SELECTION_BIAS = "SELECTION_BIAS"
    MULTIPLE_TESTING = "MULTIPLE_TESTING"
    LEAKAGE = "LEAKAGE"
    REGIME_DEPENDENCE = "REGIME_DEPENDENCE"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    DATA_MINING = "DATA_MINING"
    ALTERNATIVE_EXPLANATION = "ALTERNATIVE_EXPLANATION"
    EXECUTION_COSTS = "EXECUTION_COSTS"
    INSTABILITY = "INSTABILITY"
    TEMPORAL_INSTABILITY = "TEMPORAL_INSTABILITY"
    SURVIVORSHIP = "SURVIVORSHIP"
    OVERFITTING = "OVERFITTING"
    CONSTRUCTION_DUPLICATION = "CONSTRUCTION_DUPLICATION"
    BENCHMARK_DEPENDENCE = "BENCHMARK_DEPENDENCE"


Finding = Literal["RAISED", "CLEARED", "NOT_MEASURED"]

#: Which gate settles each threat, and what would settle the ones no gate does.
#:
#: The second column is the honest half. Four of the fourteen have no gate in
#: this ladder, and saying so with the experiment that would close them is worth
#: more than a confident sentence derived from a gate that was measuring
#: something else.
SETTLED_BY: dict[Threat, tuple[str, str]] = {
    Threat.SELECTION_BIAS: ("G11", ""),
    Threat.MULTIPLE_TESTING: ("G5", ""),
    Threat.LEAKAGE: ("G2", ""),
    Threat.REGIME_DEPENDENCE: ("G6", ""),
    Threat.INSUFFICIENT_SAMPLE: ("G3", ""),
    Threat.DATA_MINING: ("G1", ""),
    Threat.ALTERNATIVE_EXPLANATION: ("G9", ""),
    Threat.EXECUTION_COSTS: (
        "G7",
        "A fill comparison against a venue-calibrated engine. G7 checks two "
        "engines agree with each other, which is not the same as either being "
        "right about a real venue.",
    ),
    Threat.INSTABILITY: ("G13", ""),
    Threat.TEMPORAL_INSTABILITY: ("G12", ""),
    Threat.SURVIVORSHIP: (
        "",
        "A run over a universe that includes delisted and expired instruments. "
        "This ladder judges one instrument at a time, so nothing in it can see "
        "a universe that was chosen after the fact.",
    ),
    Threat.OVERFITTING: ("G5", ""),
    Threat.CONSTRUCTION_DUPLICATION: (
        "",
        "A comparison against the strategies already promoted. Novelty is "
        "checked when a hypothesis is admitted to the frontier, not when a "
        "verdict is reached, so a verdict cannot answer it.",
    ),
    Threat.BENCHMARK_DEPENDENCE: (
        "",
        "A run against a declared benchmark. Nothing in this ladder compares a "
        "strategy to one, so 'better than what' is unanswered by construction.",
    ),
}

#: What each threat is, for a reader who does not already know. Deliberately
#: about the *failure*, not about the statistic that detects it.
MEANS: dict[Threat, str] = {
    Threat.SELECTION_BIAS: "the sample was chosen in a way that produced the result",
    Threat.MULTIPLE_TESTING: "enough things were tried that one of them had to look good",
    Threat.LEAKAGE: "the run could see something it would not have known at the time",
    Threat.REGIME_DEPENDENCE: "it works in one market condition and was only tested there",
    Threat.INSUFFICIENT_SAMPLE: "too few trades to tell the effect from the noise",
    Threat.DATA_MINING: "the claim was shaped to fit the data after seeing it",
    Threat.ALTERNATIVE_EXPLANATION: "something other than the stated mechanism explains it",
    Threat.EXECUTION_COSTS: "the edge does not survive what it costs to trade",
    Threat.INSTABILITY: "the result depends on the exact path rather than on the edge",
    Threat.TEMPORAL_INSTABILITY: "it worked then and does not work now",
    Threat.SURVIVORSHIP: "the universe quietly excludes what did not survive",
    Threat.OVERFITTING: "the parameters describe this sample rather than the market",
    Threat.CONSTRUCTION_DUPLICATION: "it is something already held, wearing new parameters",
    Threat.BENCHMARK_DEPENDENCE: "it beats nothing in particular",
}


class Objection(FrozenModel):
    """One threat, answered.

    `finding` is three-valued for the same reason every gate is: a threat that
    was checked and cleared and one that nobody could check are different
    statements, and collapsing them is how absent evidence starts reading as
    favourable.
    """

    threat: Threat
    means: str
    finding: Finding
    statement: str
    #: The gate that settled it, or empty when none does.
    gate: str
    #: What would settle it. Present exactly when `finding` is NOT_MEASURED.
    required_experiment: str
    evidence_ids: tuple[str, ...]


class Synthesis(FrozenModel):
    """§6's seven fields, derived rather than written.

    `confidence` is confidence in *this reading of the evidence* -- the share of
    the fourteen that a gate actually settled. It is not a probability that the
    strategy makes money, and nothing consumes it: the gate ladder decides, and
    a number produced downstream of a decision cannot be an input to it.
    """

    supported: tuple[str, ...]
    unsupported: tuple[str, ...]
    contradictions: tuple[str, ...]
    unresolved: tuple[str, ...]
    required_experiments: tuple[str, ...]
    confidence: float
    evidence_ids: tuple[str, ...]
    numeric_verdict_locked: bool = True


def _gates(verdict: Verdict) -> dict[str, GateResult]:
    return {gate.gate: gate for gate in verdict.gates}


def object_to(verdict: Verdict) -> tuple[Objection, ...]:
    """Answer all fourteen threats against one verdict. Always fourteen rows."""
    found = _gates(verdict)
    out: list[Objection] = []
    for threat in Threat:
        gate_name, fallback = SETTLED_BY[threat]
        gate = found.get(gate_name) if gate_name else None
        means = MEANS[threat]

        if gate is None:
            out.append(
                Objection(
                    threat=threat,
                    means=means,
                    finding="NOT_MEASURED",
                    statement=(
                        f"Not measured: {means}. "
                        + (
                            fallback
                            or f"{gate_name} would settle this and is not in this verdict."
                        )
                    ),
                    gate=gate_name,
                    required_experiment=(
                        fallback or f"Run the judge so {gate_name} is measured."
                    ),
                    evidence_ids=(verdict.verdict_id,),
                )
            )
            continue

        if gate.status == "FAIL":
            finding: Finding = "RAISED"
            statement = (
                f"Raised by {gate.gate} ({gate.name}): {means}. "
                f"Observed {gate.observed} against '{gate.rule}'."
            )
        elif gate.status == "PASS":
            finding = "CLEARED"
            statement = (
                f"Cleared by {gate.gate} ({gate.name}): observed {gate.observed} "
                f"against '{gate.rule}'."
                + (f" {fallback}" if fallback else "")
            )
        else:
            finding = "NOT_MEASURED"
            statement = (
                f"{gate.gate} ({gate.name}) is inconclusive, so this threat is unanswered: "
                f"{means}."
            )

        out.append(
            Objection(
                threat=threat,
                means=means,
                finding=finding,
                statement=statement,
                gate=gate.gate,
                required_experiment=(
                    (fallback or f"Measure {gate.gate} rather than leaving it inconclusive.")
                    if finding == "NOT_MEASURED"
                    else ""
                ),
                evidence_ids=(verdict.verdict_id, gate.gate),
            )
        )
    return tuple(out)


def synthesise(
    verdict: Verdict, objections: tuple[Objection, ...], claims: tuple[AgentClaim, ...]
) -> Synthesis:
    """§6's record, from the objections and the panel. Decides nothing."""
    supported = tuple(
        f"{item.threat}: {item.statement}" for item in objections if item.finding == "CLEARED"
    )
    unsupported = tuple(
        f"{item.threat}: {item.statement}" for item in objections if item.finding == "RAISED"
    )
    unresolved = tuple(
        f"{item.threat}: {item.means}" for item in objections if item.finding == "NOT_MEASURED"
    )
    required = tuple(
        dict.fromkeys(item.required_experiment for item in objections if item.required_experiment)
    )

    stances = {claim.stance for claim in claims}
    contradictions: list[str] = []
    if {"SUPPORT", "OPPOSE"} <= stances:
        opposing = [c for c in claims if c.stance == "OPPOSE"]
        supporting = [c for c in claims if c.stance == "SUPPORT"]
        contradictions.append(
            f"{', '.join(c.role_id for c in supporting)} support promotion while "
            f"{', '.join(c.role_id for c in opposing)} oppose it on the same verdict."
        )
    # A threat cleared by a gate while the panel calls the same evidence weak is
    # the contradiction worth surfacing: one of the two is reading it wrongly,
    # and which is a question for a person.
    if unsupported and verdict.decision == "PASS":
        contradictions.append(
            f"The verdict is PASS while {len(unsupported)} threat(s) are raised against it."
        )

    answered = sum(1 for item in objections if item.finding != "NOT_MEASURED")
    return Synthesis(
        supported=supported,
        unsupported=unsupported,
        contradictions=tuple(contradictions),
        unresolved=unresolved,
        required_experiments=required,
        confidence=round(answered / len(Threat), 4),
        evidence_ids=(verdict.verdict_id,),
    )
