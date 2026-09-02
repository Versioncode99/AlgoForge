from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from forge.contracts.hashing import stable_id
from forge.contracts.models import CalculationTrace
from forge.judge.metrics import adjusted_sharpe, max_drawdown, profit_factor, safe_sharpe
from forge.judge.models import GateResult, Verdict


@dataclass(frozen=True)
class JudgeInput:
    run_id: str
    tier: str
    pnl: tuple[float, ...]
    trial_count: int
    data_gate_passed: bool
    preregistered: bool
    implementation_tests_passed: bool
    lookahead_detected: bool = False
    engine_consistent: bool = True
    mechanism_aligned: bool = True


class Judge:
    def evaluate(self, item: JudgeInput) -> Verdict:
        pnl = np.asarray(item.pnl, dtype=np.float64)
        if pnl.size == 0 or not np.isfinite(pnl).all():
            raise ValueError("judge requires finite non-empty pnl")
        sharpe = safe_sharpe(pnl)
        adjusted = adjusted_sharpe(sharpe, item.trial_count)
        drawdown = max_drawdown(pnl)
        factor = profit_factor(pnl)
        gates = (
            self._gate("G0", "Data integrity", item.data_gate_passed, 1, "G0 receipt passes"),
            self._gate("G1", "Preregistration", item.preregistered, 1, "frozen before run"),
            self._gate(
                "G2",
                "Implementation",
                item.implementation_tests_passed and not item.lookahead_detected,
                "LOOKAHEAD" if item.lookahead_detected else 1,
                "tests pass and no lookahead",
            ),
            self._gate(
                "G3",
                "OOS expectancy",
                float(pnl.sum()) > 0,
                round(float(pnl.sum()), 2),
                "net pnl > 0",
            ),
            self._gate(
                "G4",
                "Multiple testing",
                adjusted > 0.75,
                round(adjusted, 3),
                "adjusted Sharpe > 0.75",
            ),
            self._gate("G5", "Robustness", factor > 1.1, round(factor, 3), "profit factor > 1.10"),
            self._gate(
                "G6", "Engine consistency", item.engine_consistent, 1, "oracle tolerance passes"
            ),
            self._gate(
                "G7",
                "Risk",
                drawdown <= max(abs(float(pnl.sum())) * 0.75, 100.0),
                round(drawdown, 2),
                "drawdown within budget",
            ),
            self._gate("G8", "Mechanism", item.mechanism_aligned, 1, "mechanism not falsified"),
            self._gate(
                "G9",
                "Evidence tier",
                item.tier in {"TRUTH_OOS", "HOLDOUT", "FORWARD"},
                item.tier,
                "validation OOS, holdout, or forward only",
            ),
        )
        decision: Literal["PASS", "FAIL", "INCONCLUSIVE"] = (
            "FAIL" if any(gate.status == "FAIL" for gate in gates) else "PASS"
        )
        dimensions = {
            "edge": max(0, min(100, round((adjusted + 1) * 35))),
            "robustness": max(0, min(100, round(factor * 45))),
            "risk": max(0, min(100, round(100 - drawdown / 20))),
            "sample_adequacy": max(0, min(100, len(item.pnl) * 4)),
        }
        score = sum(dimensions.values()) / len(dimensions)
        grade: Literal["A", "B", "C", "D", "F"] = (
            "F" if decision == "FAIL" and score < 40 else self._grade(score)
        )
        metrics: dict[str, float | int] = {
            "net_pnl": round(float(pnl.sum()), 2),
            "win_rate": round(float(np.mean(pnl > 0)), 6),
            "sharpe": round(sharpe, 4),
            "adjusted_sharpe": round(adjusted, 4),
            "profit_factor": round(factor, 4),
            "max_drawdown": round(drawdown, 2),
            "trades": len(item.pnl),
            "trial_count": item.trial_count,
        }
        traces = tuple(
            CalculationTrace(
                trace_id=stable_id("trace", {"run": item.run_id, "metric": metric}),
                metric=metric,
                formula=self._formula(metric),
                inputs={"run_id": item.run_id, "trial_count": item.trial_count},
                value=value,
                source_tier=item.tier,
                limitations=("Sample fixture; external engine calibration pending.",),
            )
            for metric, value in metrics.items()
        )
        payload = {
            "run": item.run_id,
            "decision": decision,
            "gates": [gate.model_dump() for gate in gates],
        }
        return Verdict(
            verdict_id=stable_id("verdict", payload),
            run_id=item.run_id,
            decision=decision,
            grade=grade,
            gates=gates,
            dimensions=dimensions,
            metrics=metrics,
            traces=traces,
        )

    @staticmethod
    def _gate(
        gate: str, name: str, passed: bool, observed: float | int | str, rule: str
    ) -> GateResult:
        return GateResult(
            gate=gate,
            name=name,
            status="PASS" if passed else "FAIL",
            observed=observed,
            rule=rule,
            finding="Criterion satisfied." if passed else f"{name} criterion failed.",
        )

    @staticmethod
    def _grade(score: float) -> Literal["A", "B", "C", "D", "F"]:
        if score >= 85:
            return "A"
        if score >= 70:
            return "B"
        if score >= 55:
            return "C"
        if score >= 40:
            return "D"
        return "F"

    @staticmethod
    def _formula(metric: str) -> str:
        formulas = {
            "net_pnl": "sum(net trade pnl)",
            "win_rate": "count(positive trade pnl) / count(trade pnl)",
            "sharpe": "mean(pnl) / sample_std(pnl) * sqrt(trades)",
            "adjusted_sharpe": "sharpe - 0.35 * sqrt(log(max(trials, 1)))",
            "profit_factor": "gross gains / absolute gross losses",
            "max_drawdown": "max(running_peak - cumulative_pnl)",
            "trades": "count(trade pnl)",
            "trial_count": "count(all evaluated parameter combinations)",
        }
        return formulas[metric]
