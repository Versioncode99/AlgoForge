"""The Strategy Passport: every piece of evidence about a strategy, in one place.

The evidence for a strategy is currently spread across five stores — the
catalogue, the run ledger, the judge, the validation results, the data manifests
— and answering "should I run this" means visiting all of them and remembering
what each said. The passport composes them.

**It composes. It does not compute.** There is no arithmetic in this module and
no threshold. Every number comes from an artefact that already exists, and the
`source` on each section names which one. A passport that recomputed a Sharpe
would eventually disagree with the judge about a strategy's Sharpe, and the
disagreement would be discovered by somebody making a decision.

**It is progressive, which is a structural property rather than a CSS one.**
Sections carry a `depth`, and `at_depth` returns the subset. The interface does
not decide what a beginner should see by hiding things after rendering them: it
asks for the depth and receives that much. `forge.modes.expertise` owns the
three levels.

**Absence is a section, not a gap.** A strategy with no walk-forward result gets
a `WalkForward` section marked `measured=False` with what it would take. The
alternative — omitting the section — makes an unvalidated strategy's passport
look shorter rather than weaker.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from forge.contracts.models import FrozenModel
from forge.explain.metrics import MetricValue
from forge.judge.explain import explain as explain_verdict
from forge.judge.models import Verdict


class Depth(StrEnum):
    """How much of the passport to show. Ordered.

    Matches `forge.modes.expertise.ExpertiseLevel`, and `tests/explain` asserts
    the two vocabularies stay the same — two ladders with different rungs would
    mean a "Quant" operator seeing an "Advanced" passport.
    """

    GUIDED = "guided"
    ADVANCED = "advanced"
    QUANT = "quant"


_DEPTH_ORDER: dict[Depth, int] = {Depth.GUIDED: 0, Depth.ADVANCED: 1, Depth.QUANT: 2}


class SectionKind(StrEnum):
    IDENTITY = "identity"
    HYPOTHESIS = "hypothesis"
    MECHANISM = "mechanism"
    DATA = "data"
    ASSUMPTIONS = "assumptions"
    VERDICT = "verdict"
    GATES = "gates"
    VALIDATION = "validation"
    ROBUSTNESS = "robustness"
    PARAMETER_SURFACE = "parameter_surface"
    MONTE_CARLO = "monte_carlo"
    WALK_FORWARD = "walk_forward"
    DRAWDOWN = "drawdown"
    RISK = "risk"
    FAILURES = "failures"
    EVIDENCE = "evidence"
    LINEAGE = "lineage"
    DEPLOYMENT = "deployment"


#: The shallowest level at which each section appears. A Guided operator sees
#: what the strategy claims, whether it passed, and what it would cost them; the
#: statistical machinery appears when they ask for it.
SECTION_DEPTH: dict[SectionKind, Depth] = {
    SectionKind.IDENTITY: Depth.GUIDED,
    SectionKind.HYPOTHESIS: Depth.GUIDED,
    SectionKind.MECHANISM: Depth.ADVANCED,
    SectionKind.DATA: Depth.GUIDED,
    SectionKind.ASSUMPTIONS: Depth.ADVANCED,
    SectionKind.VERDICT: Depth.GUIDED,
    SectionKind.GATES: Depth.ADVANCED,
    SectionKind.VALIDATION: Depth.ADVANCED,
    SectionKind.ROBUSTNESS: Depth.ADVANCED,
    SectionKind.PARAMETER_SURFACE: Depth.QUANT,
    SectionKind.MONTE_CARLO: Depth.QUANT,
    SectionKind.WALK_FORWARD: Depth.QUANT,
    SectionKind.DRAWDOWN: Depth.GUIDED,
    SectionKind.RISK: Depth.GUIDED,
    SectionKind.FAILURES: Depth.GUIDED,
    SectionKind.EVIDENCE: Depth.ADVANCED,
    SectionKind.LINEAGE: Depth.QUANT,
    SectionKind.DEPLOYMENT: Depth.GUIDED,
}


class Section(FrozenModel):
    """One part of the passport, present whether or not it was measured."""

    kind: SectionKind
    title: str
    measured: bool = True
    #: Prose the caller supplied from the artefact, or the empty string.
    summary: str = ""
    #: Ordered points. Each is a fact from a source, never a conclusion.
    points: tuple[str, ...] = ()
    metrics: tuple[MetricValue, ...] = ()
    #: What would measure this, when `measured` is false. The whole value of a
    #: present-but-unmeasured section.
    what_would_measure_it: str = ""
    #: Where the content came from: a verdict id, a run id, a manifest hash.
    source: str = ""

    @property
    def depth(self) -> Depth:
        return SECTION_DEPTH[self.kind]

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["depth"] = self.depth.value
        payload["metrics"] = [
            {**value.model_dump(mode="json"), "explained": value.explained().as_dict()}
            for value in self.metrics
        ]
        return payload


class Passport(FrozenModel):
    """One strategy, everything known about it, at a chosen depth."""

    strategy_id: str
    name: str = ""
    stage: str = ""
    sections: tuple[Section, ...] = ()
    #: Stated limitations, copied from the verdict rather than restated.
    limitations: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    assembled_at: datetime | None = None

    def at_depth(self, depth: Depth) -> Passport:
        """The passport as this depth renders it."""
        ceiling = _DEPTH_ORDER[depth]
        return self.model_copy(
            update={
                "sections": tuple(
                    section
                    for section in self.sections
                    if _DEPTH_ORDER[section.depth] <= ceiling
                )
            }
        )

    @property
    def unmeasured(self) -> tuple[Section, ...]:
        return tuple(section for section in self.sections if not section.measured)

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["sections"] = [section.as_dict() for section in self.sections]
        payload["unmeasured"] = [section.kind.value for section in self.unmeasured]
        return payload


class PassportSources(FrozenModel):
    """What the caller managed to gather. Every field optional, absence honest."""

    strategy_id: str
    name: str = ""
    stage: str = ""
    hypothesis: str = ""
    mechanism: str = ""
    falsification: str = ""
    preregistration_id: str = ""
    instruments: tuple[str, ...] = ()
    timeframe: str = ""
    data_summary: str = ""
    data_findings: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    verdict: Verdict | None = None
    walk_forward: dict[str, Any] | None = None
    monte_carlo: dict[str, Any] | None = None
    parameter_surface: dict[str, Any] | None = None
    max_drawdown: float | None = None
    expected_drawdown_p95: float | None = None
    risk_fraction: float | None = None
    permitted_contracts: int | None = None
    lineage: tuple[str, ...] = ()
    deployment_state: str = ""
    deployment_detail: str = ""
    assembled_at: datetime | None = Field(default=None)


def _verdict_sections(verdict: Verdict) -> list[Section]:
    explained = explain_verdict(verdict)
    sections = [
        Section(
            kind=SectionKind.VERDICT,
            title="Verdict",
            summary=explained.headline,
            points=(
                f"Decision: {verdict.decision}. Grade: {verdict.grade}.",
                f"{explained.passed} gates passed, {explained.failed} failed, "
                f"{explained.unmeasured} could not be measured.",
            ),
            source=f"verdict {verdict.verdict_id}",
        ),
        Section(
            kind=SectionKind.GATES,
            title="G0-G13",
            points=tuple(
                f"{gate.gate} {gate.name}: {gate.status} — {gate.finding}"
                for gate in verdict.gates
            ),
            source=f"verdict {verdict.verdict_id}",
        ),
    ]
    failures = [f for f in explained.findings if f.status in {"FAIL", "INCONCLUSIVE"}]
    sections.append(
        Section(
            kind=SectionKind.FAILURES,
            title="What did not hold",
            measured=bool(failures),
            summary=(
                "Nothing failed and nothing was left unmeasured."
                if not failures
                else ""
            ),
            points=tuple(
                f"{f.gate} {f.name} ({f.status}): {f.headline} "
                f"{f.why_it_matters} {f.what_would_help}".strip()
                for f in failures
            ),
            source=f"verdict {verdict.verdict_id}",
        )
    )
    metrics = []
    for key, metric_key in (
        ("sharpe", "sharpe"),
        ("deflated_sharpe_ratio", "deflated_sharpe"),
        ("probabilistic_sharpe_ratio", "psr"),
        ("profit_factor", "profit_factor"),
        ("calmar", "calmar"),
        ("sortino", "sortino"),
        ("max_drawdown", "max_drawdown"),
    ):
        value = verdict.metrics.get(key)
        if value is not None:
            metrics.append(
                MetricValue(key=metric_key, value=float(value), basis="out-of-sample run")
            )
    if metrics:
        sections.append(
            Section(
                kind=SectionKind.EVIDENCE,
                title="Measurements",
                metrics=tuple(metrics),
                source=f"verdict {verdict.verdict_id}",
            )
        )
    return sections


def build(sources: PassportSources) -> Passport:
    """Compose a passport from whatever the caller gathered."""
    sections: list[Section] = [
        Section(
            kind=SectionKind.IDENTITY,
            title="Strategy",
            points=tuple(
                point
                for point in (
                    f"Identifier: {sources.strategy_id}",
                    f"Stage: {sources.stage}" if sources.stage else "",
                    f"Instruments: {', '.join(sources.instruments)}"
                    if sources.instruments
                    else "",
                    f"Timeframe: {sources.timeframe}" if sources.timeframe else "",
                )
                if point
            ),
            source="strategy catalogue",
        ),
        Section(
            kind=SectionKind.HYPOTHESIS,
            title="Hypothesis",
            measured=bool(sources.hypothesis),
            summary=sources.hypothesis,
            points=(
                (f"Falsified by: {sources.falsification}",) if sources.falsification else ()
            ),
            what_would_measure_it=(
                ""
                if sources.hypothesis
                else "freeze a hypothesis before the run; without one, gate G1 cannot pass"
            ),
            source=(
                f"preregistration {sources.preregistration_id}"
                if sources.preregistration_id
                else ""
            ),
        ),
        Section(
            kind=SectionKind.MECHANISM,
            title="Mechanism",
            measured=bool(sources.mechanism),
            summary=sources.mechanism,
            what_would_measure_it=(
                ""
                if sources.mechanism
                else "state why this edge should exist; gate G9 tests the stated mechanism"
            ),
        ),
        Section(
            kind=SectionKind.DATA,
            title="Data",
            measured=bool(sources.data_summary),
            summary=sources.data_summary,
            points=sources.data_findings,
            what_would_measure_it=(
                "" if sources.data_summary else "resolve the datasets this strategy runs over"
            ),
            source="data manifests",
        ),
        Section(
            kind=SectionKind.ASSUMPTIONS,
            title="Assumptions",
            measured=bool(sources.assumptions),
            points=sources.assumptions,
            what_would_measure_it=(
                "" if sources.assumptions else "nobody has recorded what this result assumes"
            ),
        ),
    ]

    if sources.verdict is not None:
        sections.extend(_verdict_sections(sources.verdict))
    else:
        sections.append(
            Section(
                kind=SectionKind.VERDICT,
                title="Verdict",
                measured=False,
                what_would_measure_it="run the judge over a completed out-of-sample run",
            )
        )

    sections.append(
        Section(
            kind=SectionKind.WALK_FORWARD,
            title="Walk-forward",
            measured=sources.walk_forward is not None,
            points=_points(sources.walk_forward),
            what_would_measure_it=(
                ""
                if sources.walk_forward is not None
                else "run walk-forward validation; gate G12 reports NOT_MEASURED without it"
            ),
        )
    )
    sections.append(
        Section(
            kind=SectionKind.MONTE_CARLO,
            title="Path robustness",
            measured=sources.monte_carlo is not None,
            points=_points(sources.monte_carlo),
            what_would_measure_it=(
                ""
                if sources.monte_carlo is not None
                else "run CPCV; gate G13 reports NOT_MEASURED without it"
            ),
        )
    )
    sections.append(
        Section(
            kind=SectionKind.PARAMETER_SURFACE,
            title="Parameter surface",
            measured=sources.parameter_surface is not None,
            points=_points(sources.parameter_surface),
            what_would_measure_it=(
                ""
                if sources.parameter_surface is not None
                else "sweep the parameters to see whether the result sits on a plateau or a spike"
            ),
        )
    )

    drawdown_metrics = tuple(
        MetricValue(key=key, value=value, basis=basis)
        for key, value, basis in (
            ("max_drawdown", sources.max_drawdown, "realised, out-of-sample"),
            (
                "expected_drawdown_p95",
                sources.expected_drawdown_p95,
                "bootstrapped, per contract",
            ),
        )
        if value is not None
    )
    sections.append(
        Section(
            kind=SectionKind.DRAWDOWN,
            title="Drawdown",
            measured=bool(drawdown_metrics),
            metrics=drawdown_metrics,
            what_would_measure_it=(
                "" if drawdown_metrics else "no drawdown has been measured for this strategy"
            ),
        )
    )

    risk_metrics = tuple(
        MetricValue(key="risk_fraction", value=sources.risk_fraction, basis="current setting")
        for _ in (1,)
        if sources.risk_fraction is not None
    )
    sections.append(
        Section(
            kind=SectionKind.RISK,
            title="Risk",
            measured=sources.risk_fraction is not None or sources.permitted_contracts is not None,
            metrics=risk_metrics,
            points=tuple(
                point
                for point in (
                    f"Permitted size right now: {sources.permitted_contracts} contract(s)"
                    if sources.permitted_contracts is not None
                    else "",
                )
                if point
            ),
            what_would_measure_it=(
                ""
                if sources.risk_fraction is not None
                else "no account has a risk configuration for this strategy yet"
            ),
        )
    )

    sections.append(
        Section(
            kind=SectionKind.LINEAGE,
            title="Lineage",
            measured=bool(sources.lineage),
            points=sources.lineage,
            what_would_measure_it=(
                "" if sources.lineage else "no ancestry has been recorded for this strategy"
            ),
        )
    )
    sections.append(
        Section(
            kind=SectionKind.DEPLOYMENT,
            title="Deployment",
            measured=bool(sources.deployment_state),
            summary=sources.deployment_detail,
            points=((sources.deployment_state,) if sources.deployment_state else ()),
            what_would_measure_it=(
                "" if sources.deployment_state else "this strategy is not allocated to any account"
            ),
        )
    )

    verdict = sources.verdict
    return Passport(
        strategy_id=sources.strategy_id,
        name=sources.name,
        stage=sources.stage,
        sections=tuple(sections),
        limitations=verdict.limitations if verdict is not None else (),
        labels=verdict.labels if verdict is not None else (),
        assembled_at=sources.assembled_at,
    )


def _points(payload: dict[str, Any] | None) -> tuple[str, ...]:
    """Render a result dictionary as ordered facts, without interpreting it."""
    if not payload:
        return ()
    return tuple(f"{key.replace('_', ' ')}: {value}" for key, value in payload.items())
