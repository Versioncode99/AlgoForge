"""Progressive disclosure: three depths over one engine, declared server-side.

§24 is emphatic that the answer to complexity is not simplification. The
distinction this module draws is between *hiding a control* and *not offering
it yet*: Guided has the same engine underneath and the same refusals; it shows
fewer knobs and more sentences.

    GUIDED    for somebody who wants a decision and the reason for it.
    ADVANCED  for somebody who will change the inputs.
    QUANT     for somebody who will argue with the method.

**It lives here rather than in the interface, for the same reason the mode
manifest does.** An agent asked "what can the operator see right now" has to be
able to answer without a person, and two copies of this mapping — one in React
and one in the API — is a mapping that drifts.

**It never hides a refusal.** `ALWAYS_VISIBLE` names the surfaces that appear at
every depth: what was refused and why, what is unmeasured, what is simulated,
and what the firm has not permitted. Simplifying a product by hiding its
warnings is the specific failure this whole repository is written against, and
`test_no_level_hides_a_refusal_or_a_limitation` asserts it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from forge.contracts.models import FrozenModel


class ExpertiseLevel(StrEnum):
    GUIDED = "guided"
    ADVANCED = "advanced"
    QUANT = "quant"


LEVEL_ORDER: dict[ExpertiseLevel, int] = {
    ExpertiseLevel.GUIDED: 0,
    ExpertiseLevel.ADVANCED: 1,
    ExpertiseLevel.QUANT: 2,
}

LEVEL_LABEL: dict[ExpertiseLevel, str] = {
    ExpertiseLevel.GUIDED: "Guided",
    ExpertiseLevel.ADVANCED: "Advanced",
    ExpertiseLevel.QUANT: "Quant",
}

LEVEL_DETAIL: dict[ExpertiseLevel, str] = {
    ExpertiseLevel.GUIDED: (
        "Decisions, and the reason for each one. The same engine, the same refusals, "
        "fewer controls in the way. Everything hidden here is reachable by changing "
        "level; nothing is removed."
    ),
    ExpertiseLevel.ADVANCED: (
        "The inputs as well as the outputs: parameters, validation methods, risk "
        "boundaries, allocation constraints and the gate ladder in full."
    ),
    ExpertiseLevel.QUANT: (
        "The method itself: trial matrices, parameter surfaces, CSCV and CPCV internals, "
        "the deflation benchmark, bootstrap construction and every calculation trace."
    ),
}


class Surface(StrEnum):
    """A thing the interface can show. Named so the mapping can be asserted."""

    # Always visible, at every level.
    REFUSAL_LADDER = "refusal_ladder"
    LIMITATIONS = "limitations"
    UNMEASURED = "unmeasured"
    SIMULATION_NOTICE = "simulation_notice"
    FIRM_PERMISSIONS = "firm_permissions"
    DISCLOSURES = "disclosures"
    # Guided and up.
    VERDICT = "verdict"
    HEADLINE_METRICS = "headline_metrics"
    RISK_MODE_SELECTOR = "risk_mode_selector"
    APPETITE_METER = "appetite_meter"
    WHY_ANSWERS = "why_answers"
    ACCOUNT_SUMMARY = "account_summary"
    RECOMMENDED_STRATEGY = "recommended_strategy"
    # Advanced and up.
    GATE_LADDER = "gate_ladder"
    RISK_BOUNDARIES = "risk_boundaries"
    RISK_DRIVERS = "risk_drivers"
    ALLOCATION_CONSTRAINTS = "allocation_constraints"
    PARAMETERS = "parameters"
    VALIDATION_METHODS = "validation_methods"
    COPY_SIZING = "copy_sizing"
    ADAPTER_DETAIL = "adapter_detail"
    AUDIT_TRAIL = "audit_trail"
    # Quant only.
    TRIAL_MATRIX = "trial_matrix"
    PARAMETER_SURFACE = "parameter_surface"
    DEFLATION_BENCHMARK = "deflation_benchmark"
    CSCV_INTERNALS = "cscv_internals"
    CPCV_PATHS = "cpcv_paths"
    BOOTSTRAP_CONSTRUCTION = "bootstrap_construction"
    CALCULATION_TRACES = "calculation_traces"
    IDEMPOTENCY_KEYS = "idempotency_keys"


#: Shown at every level, without exception. A product that hides its warnings
#: from beginners has made its beginners less safe, not less confused.
ALWAYS_VISIBLE: frozenset[Surface] = frozenset(
    {
        Surface.REFUSAL_LADDER,
        Surface.LIMITATIONS,
        Surface.UNMEASURED,
        Surface.SIMULATION_NOTICE,
        Surface.FIRM_PERMISSIONS,
        Surface.DISCLOSURES,
    }
)

#: The shallowest level at which each surface appears.
SURFACE_LEVEL: dict[Surface, ExpertiseLevel] = {
    **{surface: ExpertiseLevel.GUIDED for surface in ALWAYS_VISIBLE},
    Surface.VERDICT: ExpertiseLevel.GUIDED,
    Surface.HEADLINE_METRICS: ExpertiseLevel.GUIDED,
    Surface.RISK_MODE_SELECTOR: ExpertiseLevel.GUIDED,
    Surface.APPETITE_METER: ExpertiseLevel.GUIDED,
    Surface.WHY_ANSWERS: ExpertiseLevel.GUIDED,
    Surface.ACCOUNT_SUMMARY: ExpertiseLevel.GUIDED,
    Surface.RECOMMENDED_STRATEGY: ExpertiseLevel.GUIDED,
    Surface.GATE_LADDER: ExpertiseLevel.ADVANCED,
    Surface.RISK_BOUNDARIES: ExpertiseLevel.ADVANCED,
    Surface.RISK_DRIVERS: ExpertiseLevel.ADVANCED,
    Surface.ALLOCATION_CONSTRAINTS: ExpertiseLevel.ADVANCED,
    Surface.PARAMETERS: ExpertiseLevel.ADVANCED,
    Surface.VALIDATION_METHODS: ExpertiseLevel.ADVANCED,
    Surface.COPY_SIZING: ExpertiseLevel.ADVANCED,
    Surface.ADAPTER_DETAIL: ExpertiseLevel.ADVANCED,
    Surface.AUDIT_TRAIL: ExpertiseLevel.ADVANCED,
    Surface.TRIAL_MATRIX: ExpertiseLevel.QUANT,
    Surface.PARAMETER_SURFACE: ExpertiseLevel.QUANT,
    Surface.DEFLATION_BENCHMARK: ExpertiseLevel.QUANT,
    Surface.CSCV_INTERNALS: ExpertiseLevel.QUANT,
    Surface.CPCV_PATHS: ExpertiseLevel.QUANT,
    Surface.BOOTSTRAP_CONSTRUCTION: ExpertiseLevel.QUANT,
    Surface.CALCULATION_TRACES: ExpertiseLevel.QUANT,
    Surface.IDEMPOTENCY_KEYS: ExpertiseLevel.QUANT,
}

SURFACE_LABEL: dict[Surface, str] = {
    Surface.REFUSAL_LADDER: "Why an order was refused",
    Surface.LIMITATIONS: "What this does not tell you",
    Surface.UNMEASURED: "What was not measured",
    Surface.SIMULATION_NOTICE: "Simulated execution notice",
    Surface.FIRM_PERMISSIONS: "Firm permissions",
    Surface.DISCLOSURES: "Safety disclosures",
    Surface.VERDICT: "Verdict and grade",
    Surface.HEADLINE_METRICS: "Headline measurements",
    Surface.RISK_MODE_SELECTOR: "Risk mode",
    Surface.APPETITE_METER: "Risk appetite",
    Surface.WHY_ANSWERS: "Why? explanations",
    Surface.ACCOUNT_SUMMARY: "Account summary",
    Surface.RECOMMENDED_STRATEGY: "Recommended strategy",
    Surface.GATE_LADDER: "The full G0-G13 ladder",
    Surface.RISK_BOUNDARIES: "Risk boundaries",
    Surface.RISK_DRIVERS: "What moved the risk",
    Surface.ALLOCATION_CONSTRAINTS: "Allocation constraints",
    Surface.PARAMETERS: "Strategy parameters",
    Surface.VALIDATION_METHODS: "Validation methods",
    Surface.COPY_SIZING: "Copy sizing policy",
    Surface.ADAPTER_DETAIL: "Provider adapter detail",
    Surface.AUDIT_TRAIL: "Audit trail",
    Surface.TRIAL_MATRIX: "Trial matrix",
    Surface.PARAMETER_SURFACE: "Parameter surface",
    Surface.DEFLATION_BENCHMARK: "Deflation benchmark",
    Surface.CSCV_INTERNALS: "CSCV partitions",
    Surface.CPCV_PATHS: "CPCV reconstructed paths",
    Surface.BOOTSTRAP_CONSTRUCTION: "Bootstrap construction",
    Surface.CALCULATION_TRACES: "Calculation traces",
    Surface.IDEMPOTENCY_KEYS: "Idempotency keys",
}


class LevelDescriptor(FrozenModel):
    """One level, and everything it shows."""

    level: ExpertiseLevel
    label: str
    detail: str
    surfaces: tuple[Surface, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "label": self.label,
            "detail": self.detail,
            "surfaces": [
                {
                    "surface": surface.value,
                    "label": SURFACE_LABEL[surface],
                    "always_visible": surface in ALWAYS_VISIBLE,
                }
                for surface in self.surfaces
            ],
        }


def visible(level: ExpertiseLevel) -> tuple[Surface, ...]:
    """Every surface this level shows, in declaration order."""
    ceiling = LEVEL_ORDER[level]
    return tuple(
        surface
        for surface in Surface
        if surface in ALWAYS_VISIBLE or LEVEL_ORDER[SURFACE_LEVEL[surface]] <= ceiling
    )


def shows(level: ExpertiseLevel, surface: Surface) -> bool:
    return surface in ALWAYS_VISIBLE or LEVEL_ORDER[SURFACE_LEVEL[surface]] <= LEVEL_ORDER[level]


def descriptor(level: ExpertiseLevel | str) -> LevelDescriptor:
    try:
        parsed = ExpertiseLevel(level)
    except ValueError:
        valid = ", ".join(item.value for item in ExpertiseLevel)
        raise KeyError(f"no expertise level '{level}'. Levels: {valid}") from None
    return LevelDescriptor(
        level=parsed,
        label=LEVEL_LABEL[parsed],
        detail=LEVEL_DETAIL[parsed],
        surfaces=visible(parsed),
    )


def catalogue() -> list[dict[str, Any]]:
    """The three levels, in order, as the selector renders them."""
    return [descriptor(level).as_dict() for level in ExpertiseLevel]
