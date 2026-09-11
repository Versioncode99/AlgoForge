"""Explanation: saying what the system found, without changing what it found.

Four modules, one rule. Nothing here computes a result, adjusts one, or reaches
a conclusion the thing being explained did not already reach. They read finished
artefacts — a verdict, a proposal, a decision ladder — and render them.

That rule is architectural rather than stylistic. An explanation layer that
could adjust its subject would be a second judge with softer standards, and the
softer one is the one that gets quoted.

    metrics    what a measurement is, and what this value of it means here.
    why        a closed set of answerable questions, answered from state.
    preview    what an experiment is about to do, before it does it.
    passport   every piece of evidence about a strategy, in one document.

`forge.judge.explain` came first and does the same job for a verdict
specifically; these build on it rather than restating it.
"""

from __future__ import annotations

from forge.explain.metrics import (
    METRICS,
    Interpretation,
    Metric,
    MetricValue,
    Reading,
    interpret,
    metric,
)
from forge.explain.metrics import (
    catalogue as metric_catalogue,
)
from forge.explain.passport import (
    SECTION_DEPTH,
    Depth,
    Passport,
    PassportSources,
    Section,
    SectionKind,
    build,
)
from forge.explain.preview import (
    Availability,
    CheckedWork,
    Concern,
    DataWindow,
    Preview,
    Severity,
    check,
)
from forge.explain.why import (
    ANSWERS,
    Answer,
    Question,
    why_cant_i_deploy_this,
    why_did_allocation_change,
    why_did_risk_change,
    why_did_this_strategy_fail,
    why_is_this_account_blocked,
    why_is_this_validation_insufficient,
    why_was_this_strategy_recommended,
)
from forge.explain.why import (
    catalogue as question_catalogue,
)

__all__ = [
    "ANSWERS",
    "METRICS",
    "SECTION_DEPTH",
    "Answer",
    "Availability",
    "CheckedWork",
    "Concern",
    "DataWindow",
    "Depth",
    "Interpretation",
    "Metric",
    "MetricValue",
    "Passport",
    "PassportSources",
    "Preview",
    "Question",
    "Reading",
    "Section",
    "SectionKind",
    "Severity",
    "build",
    "check",
    "interpret",
    "metric",
    "metric_catalogue",
    "question_catalogue",
    "why_cant_i_deploy_this",
    "why_did_allocation_change",
    "why_did_risk_change",
    "why_did_this_strategy_fail",
    "why_is_this_account_blocked",
    "why_is_this_validation_insufficient",
    "why_was_this_strategy_recommended",
]
