"""A closed set of questions, each answered from state that was passed in.

§28 asks for "WHY?" wherever it would help. The hazard in that request is
obvious once stated: a "why" button that always produces a paragraph will
produce one when there is nothing to say, and a fluent paragraph about nothing
is worse than an empty panel.

So every answerer here takes the artefact that would contain the answer, and
returns `Answer(answered=False, ...)` when it was not given one. There is no
branch that composes a sentence from an absent input, and
`test_every_question_declines_when_it_has_no_state` asserts it by calling all of
them with nothing.

**The questions are closed.** Seven of them, each with a function. A registry
with free-text questions would need a model to route them, and a model routing
"why did my risk change" to a plausible-sounding generator is exactly the
product this repository is not.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from forge.contracts.models import FrozenModel
from forge.judge.explain import explain as explain_verdict
from forge.judge.models import Verdict
from forge.propdesk.allocation import AllocationChange, Candidate
from forge.propdesk.autonomy import DeploymentDecision
from forge.propdesk.desk import DeskDecision
from forge.propdesk.scaling import RiskProposal
from forge.propdesk.scaling import explain as explain_proposal


class Question(StrEnum):
    STRATEGY_FAILED = "why_did_this_strategy_fail"
    STRATEGY_RECOMMENDED = "why_was_this_strategy_recommended"
    RISK_CHANGED = "why_did_risk_change"
    CANNOT_DEPLOY = "why_cant_i_deploy_this"
    ACCOUNT_BLOCKED = "why_is_this_account_blocked"
    VALIDATION_INSUFFICIENT = "why_is_this_validation_insufficient"
    ALLOCATION_CHANGED = "why_did_allocation_change"


QUESTION_LABEL: dict[Question, str] = {
    Question.STRATEGY_FAILED: "Why did this strategy fail?",
    Question.STRATEGY_RECOMMENDED: "Why was this strategy recommended?",
    Question.RISK_CHANGED: "Why did AlgoForge change my risk?",
    Question.CANNOT_DEPLOY: "Why can't I deploy this?",
    Question.ACCOUNT_BLOCKED: "Why is this account blocked?",
    Question.VALIDATION_INSUFFICIENT: "Why is this validation insufficient?",
    Question.ALLOCATION_CHANGED: "Why did allocation change?",
}


class Answer(FrozenModel):
    """One answer, or an honest statement that there is not one."""

    question: Question
    answered: bool
    #: The one-line answer. Empty when `answered` is false.
    headline: str = ""
    #: The supporting points, in the order they matter. Each one is read off the
    #: artefact; none is generated.
    points: tuple[str, ...] = ()
    #: What the answer was derived from, named so a reader can go and look.
    source: str = ""
    #: Why there is no answer. Empty when there is one.
    unavailable: str = ""

    @property
    def label(self) -> str:
        return QUESTION_LABEL[self.question]

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["label"] = self.label
        return payload


def _nothing(question: Question, reason: str) -> Answer:
    return Answer(question=question, answered=False, unavailable=reason)


def why_did_this_strategy_fail(verdict: Verdict | None) -> Answer:
    """The judge's own findings, ranked, with what would address them.

    Delegates to `forge.judge.explain`, which already refuses to suggest a fix
    that games the test. Nothing is added here beyond selecting the failures.
    """
    question = Question.STRATEGY_FAILED
    if verdict is None:
        return _nothing(question, "this strategy has never been judged.")
    if verdict.decision == "PASS":
        return _nothing(
            question,
            "this strategy did not fail: the judge returned PASS. Its limitations are "
            "on the Evidence screen.",
        )
    explained = explain_verdict(verdict)
    failures = [finding for finding in explained.findings if finding.status == "FAIL"]
    unmeasured = [finding for finding in explained.findings if finding.status == "INCONCLUSIVE"]
    if not failures and unmeasured:
        return Answer(
            question=question,
            answered=True,
            headline=(
                "It did not fail. The judge could not measure "
                f"{len(unmeasured)} of the things it needs, so the verdict is "
                "INCONCLUSIVE rather than a rejection."
            ),
            points=tuple(f"{f.gate} {f.name}: {f.headline}" for f in unmeasured),
            source=f"verdict {verdict.verdict_id}",
        )
    return Answer(
        question=question,
        answered=True,
        headline=explained.headline,
        points=tuple(
            f"{f.gate} {f.name}: {f.headline} — {f.what_would_help}"
            if f.what_would_help
            else f"{f.gate} {f.name}: {f.headline}"
            for f in failures
        ),
        source=f"verdict {verdict.verdict_id}",
    )


def why_was_this_strategy_recommended(candidate: Candidate | None) -> Answer:
    """The feasibility checks that passed, and what bound the size."""
    question = Question.STRATEGY_RECOMMENDED
    if candidate is None:
        return _nothing(question, "no allocation candidate was supplied.")
    if not candidate.feasible:
        return Answer(
            question=question,
            answered=True,
            headline="It was not recommended.",
            points=tuple(
                f"{reason.check.value}: {reason.detail}"
                for reason in candidate.reasons
                if not reason.passed
            ),
            source=f"candidate {candidate.strategy_id} on {candidate.account_uid}",
        )
    points = [f"{reason.check.value}: {reason.detail}" for reason in candidate.reasons]
    headline = (
        f"{candidate.strategy_id} is graded {candidate.health_grade.value} and every "
        f"feasibility check passed, at up to {candidate.max_contracts} contract(s)."
    )
    if candidate.requires_confirmation:
        headline += " It needs your confirmation before it may be allocated."
    return Answer(
        question=question,
        answered=True,
        headline=headline,
        points=tuple(points),
        source=f"candidate {candidate.strategy_id} on {candidate.account_uid}",
    )


def why_did_risk_change(proposal: RiskProposal | None) -> Answer:
    """The drivers that cut, the ones that could not be measured, and the clamp."""
    question = Question.RISK_CHANGED
    if proposal is None:
        return _nothing(question, "no risk evaluation has been recorded for this account.")
    lines = explain_proposal(proposal)
    if proposal.changed:
        headline = (
            f"Risk moved from {proposal.current_fraction:.2%} to "
            f"{proposal.applied_fraction:.2%} of this account's buffer."
        )
    else:
        headline = f"Risk was held at {proposal.applied_fraction:.2%} of this account's buffer."
    return Answer(
        question=question,
        answered=True,
        headline=headline,
        points=tuple(lines),
        source=f"risk evaluation at {proposal.at.isoformat()}",
    )


def why_cant_i_deploy_this(decision: DeploymentDecision | None) -> Answer:
    """Every mandatory gate that did not pass, with what it asked."""
    question = Question.CANNOT_DEPLOY
    if decision is None:
        return _nothing(question, "no deployment has been evaluated for this pairing.")
    if decision.cleared:
        return Answer(
            question=question,
            answered=True,
            headline="Every mandatory control passed.",
            points=(f"The autonomy level for this account is {decision.level.value}.",),
            source=f"deployment evaluation at {decision.at.isoformat()}",
        )
    return Answer(
        question=question,
        answered=True,
        headline=(
            f"{len(decision.blocking)} of {len(decision.gates)} mandatory controls did "
            "not pass. Every one of them has to."
        ),
        points=tuple(
            f"{gate.name}: {gate.detail}"
            + (" (this could not be determined, which is not a pass)" if gate.unknown else "")
            for gate in decision.blocking
        ),
        source=f"deployment evaluation at {decision.at.isoformat()}",
    )


def why_is_this_account_blocked(decision: DeskDecision | None) -> Answer:
    """The rung of the desk funnel that refused, in its own words."""
    question = Question.ACCOUNT_BLOCKED
    if decision is None:
        return _nothing(question, "no order has been screened for this account.")
    if decision.cleared:
        return _nothing(
            question,
            "this account is not blocked: the last order screened for it cleared every "
            "rung.",
        )
    return Answer(
        question=question,
        answered=True,
        headline=f"The last order for this account was refused at {len(decision.blocking_stages)} "
        f"rung(s) of {len(decision.stages)}.",
        points=tuple(
            f"{stage.stage.replace('_', ' ')}: {stage.detail}"
            for stage in decision.stages
            if not stage.passed
        ),
        source=f"desk decision {decision.decision_id}",
    )


def why_is_this_validation_insufficient(verdict: Verdict | None) -> Answer:
    """The gates that could not be measured, and what measuring them would take.

    Deliberately separate from "why did it fail": an unmeasured gate is not a
    rejection, and collapsing the two is the failure mode `forge.judge.explain`
    was written to avoid.
    """
    question = Question.VALIDATION_INSUFFICIENT
    if verdict is None:
        return _nothing(question, "this strategy has never been judged.")
    explained = explain_verdict(verdict)
    unmeasured = [finding for finding in explained.findings if finding.status == "INCONCLUSIVE"]
    if not unmeasured:
        return _nothing(
            question,
            "nothing is unmeasured: every gate the judge ran reached a PASS or a FAIL.",
        )
    return Answer(
        question=question,
        answered=True,
        headline=(
            f"{len(unmeasured)} gate(s) could not be measured. None of them is a "
            "judgement about the strategy; each is something the evidence does not yet "
            "support."
        ),
        points=tuple(
            f"{f.gate} {f.name}: {f.headline} — {f.what_would_help}"
            if f.what_would_help
            else f"{f.gate} {f.name}: {f.headline}"
            for f in unmeasured
        ),
        source=f"verdict {verdict.verdict_id}",
    )


def why_did_allocation_change(change: AllocationChange | None) -> Answer:
    """What moved, and the reason recorded at the time rather than inferred now."""
    question = Question.ALLOCATION_CHANGED
    if change is None:
        return _nothing(question, "no allocation change has been recorded for this account.")
    points = []
    if change.previous_strategy_id and change.strategy_id:
        points.append(f"{change.previous_strategy_id} was replaced by {change.strategy_id}.")
    elif change.strategy_id:
        points.append(f"{change.strategy_id} was allocated.")
    elif change.previous_strategy_id:
        points.append(f"{change.previous_strategy_id} was withdrawn.")
    if change.previous_contracts != change.contracts:
        points.append(
            f"Size moved from {change.previous_contracts} to {change.contracts} contract(s)."
        )
    if change.rationale:
        points.append(change.rationale)
    points.append(
        f"Recorded as a {change.kind} by {change.actor or 'the ' + change.source} layer."
        if change.source != "deterministic"
        else f"Recorded as a {change.kind} by the deterministic allocator."
    )
    return Answer(
        question=question,
        answered=True,
        headline=f"Allocation on {change.account_uid} changed at {change.at.isoformat()}.",
        points=tuple(points),
        source="allocation history",
    )


#: Every question, with the artefact its answerer needs. Read by the API so the
#: interface can offer only the questions it holds state for.
ANSWERS: dict[Question, str] = {
    Question.STRATEGY_FAILED: "verdict",
    Question.STRATEGY_RECOMMENDED: "candidate",
    Question.RISK_CHANGED: "risk_proposal",
    Question.CANNOT_DEPLOY: "deployment_decision",
    Question.ACCOUNT_BLOCKED: "desk_decision",
    Question.VALIDATION_INSUFFICIENT: "verdict",
    Question.ALLOCATION_CHANGED: "allocation_change",
}


def catalogue() -> list[dict[str, str]]:
    """Every question, its label, and what it needs to be answerable."""
    return [
        {
            "question": question.value,
            "label": QUESTION_LABEL[question],
            "requires": ANSWERS[question],
        }
        for question in Question
    ]
