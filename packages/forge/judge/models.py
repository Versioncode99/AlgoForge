from __future__ import annotations

from typing import Literal

from pydantic import Field

from forge.contracts.models import CalculationTrace, FrozenModel


class GateResult(FrozenModel):
    gate: str
    name: str
    status: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    observed: float | int | str
    rule: str
    finding: str


class Verdict(FrozenModel):
    verdict_id: str
    run_id: str
    decision: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    grade: Literal["A", "B", "C", "D", "F"]
    gates: tuple[GateResult, ...]
    dimensions: dict[str, int] = Field(min_length=4)
    metrics: dict[str, float | int]
    traces: tuple[CalculationTrace, ...]
    labels: tuple[str, ...] = ("SAMPLE_DATA", "UNCALIBRATED")
    limitations: tuple[str, ...] = (
        "Vertical-slice statistics require calibration against production engines.",
    )
