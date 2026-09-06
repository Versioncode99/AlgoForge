from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from forge.contracts.hashing import stable_id
from forge.contracts.models import CalculationTrace
from forge.judge.metrics import max_drawdown, profit_factor
from forge.judge.models import GateResult, Verdict
from forge.judge.statistics import (
    BacktestOverfitting,
    DeflatedSharpe,
    annualised_sharpe,
    calmar_ratio,
    deflated_sharpe_ratio,
    minimum_track_record_length,
    per_period_sharpe,
    permutation_pvalue,
    probabilistic_sharpe_ratio,
    sortino_ratio,
)
from forge.research.cpcv import PathDistribution
from forge.research.walkforward import WalkForwardResult

# The conventional bar for treating a deflated result as a real discovery.
DEFLATED_SHARPE_THRESHOLD = 0.95
# At or above this, in-sample selection is no better than choosing at random.
OVERFITTING_THRESHOLD = 0.5
# Below this many trades the higher moments PSR depends on are not estimable.
MINIMUM_TRADES = 30


@dataclass(frozen=True)
class JudgeInput:
    """Everything the gate ladder is allowed to consider.

    The evidence fields at the bottom default to ``None`` and their gates
    return ``INCONCLUSIVE`` when they are missing. That is deliberate: absent
    evidence must never read as a pass. A strategy reaches ``PASS`` only when
    the full validation stack has actually been run against it.
    """

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
    periods_per_year: float = 252.0
    # Per-period Sharpe of every configuration in the search. Their spread is
    # the honest estimate of V[SR] the Deflated Sharpe Ratio needs. Without it,
    # and with more than one trial, G5 reports INCONCLUSIVE rather than guessing.
    trial_sharpes: tuple[float, ...] | None = None
    # Selection integrity, walk-forward stability, path robustness.
    overfitting: BacktestOverfitting | None = None
    walk_forward: WalkForwardResult | None = None
    paths: PathDistribution | None = None
    labels: tuple[str, ...] = field(default_factory=tuple)


class Judge:
    """Deterministic gate ladder. Contains no model call, by contract."""

    def evaluate(self, item: JudgeInput) -> Verdict:
        pnl = np.asarray(item.pnl, dtype=np.float64)
        if pnl.size == 0 or not np.isfinite(pnl).all():
            raise ValueError("judge requires finite non-empty pnl")

        sharpe = per_period_sharpe(pnl)
        annualised = annualised_sharpe(pnl, item.periods_per_year)
        deflated = deflated_sharpe_ratio(pnl, item.trial_count, item.trial_sharpes)
        drawdown = max_drawdown(pnl)
        factor = profit_factor(pnl)
        calmar = calmar_ratio(pnl)
        sortino = sortino_ratio(pnl)
        p_value = permutation_pvalue(pnl)
        psr = probabilistic_sharpe_ratio(pnl)
        track_record = minimum_track_record_length(pnl)

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
                "Sample adequacy",
                pnl.size >= MINIMUM_TRADES,
                int(pnl.size),
                f"at least {MINIMUM_TRADES} trades before higher moments are estimable",
            ),
            self._gate(
                "G4",
                "OOS expectancy",
                float(pnl.sum()) > 0.0,
                round(float(pnl.sum()), 2),
                "net pnl > 0",
            ),
            self._evidence_gate(
                "G5",
                "Multiple testing",
                self._deflation_verdict(item, deflated),
                round(deflated.deflated_sharpe_ratio, 4)
                if self._deflation_measurable(item)
                else "V[SR]_NOT_MEASURED",
                f"deflated Sharpe >= {DEFLATED_SHARPE_THRESHOLD} "
                f"against best-of-{deflated.trials} noise hurdle "
                f"{deflated.benchmark_sharpe:.4f}",
            ),
            self._gate(
                "G6",
                "Robustness",
                factor > 1.1 and p_value < 0.05,
                round(factor, 3),
                "profit factor > 1.10 and sign-permutation p < 0.05",
            ),
            self._gate(
                "G7", "Engine consistency", item.engine_consistent, 1, "oracle tolerance passes"
            ),
            self._gate(
                "G8",
                "Risk",
                calmar >= 0.5,
                round(calmar, 3),
                "Calmar >= 0.50 (net pnl at least half the worst drawdown)",
            ),
            self._gate("G9", "Mechanism", item.mechanism_aligned, 1, "mechanism not falsified"),
            self._gate(
                "G10",
                "Evidence tier",
                item.tier in {"TRUTH_OOS", "HOLDOUT", "FORWARD"},
                item.tier,
                "validation OOS, holdout, or forward only",
            ),
            self._evidence_gate(
                "G11",
                "Selection integrity",
                None
                if item.overfitting is None
                else item.overfitting.probability < OVERFITTING_THRESHOLD,
                "NOT_MEASURED"
                if item.overfitting is None
                else round(item.overfitting.probability, 4),
                f"PBO < {OVERFITTING_THRESHOLD} via CSCV over the full trial matrix",
            ),
            self._evidence_gate(
                "G12",
                "Walk-forward stability",
                None if item.walk_forward is None else item.walk_forward.survives,
                "NOT_MEASURED"
                if item.walk_forward is None
                else round(item.walk_forward.efficiency, 4),
                "out-of-sample Sharpe > 0, majority of folds positive, efficiency >= 0.50",
            ),
            self._evidence_gate(
                "G13",
                "Path robustness",
                None if item.paths is None else item.paths.robust,
                "NOT_MEASURED" if item.paths is None else round(item.paths.sharpe_p05, 4),
                "CPCV: positive on >= 80% of reconstructed paths and at the 5th percentile",
            ),
        )

        decision = self._decide(gates)
        dimensions = {
            # Edge is the deflated probability, not the raw Sharpe: the whole
            # point is that an undeflated number is not evidence.
            "edge": self._scale(
                (deflated.deflated_sharpe_ratio if self._deflation_measurable(item) else 0.0)
                * 100.0
            ),
            "robustness": self._scale(
                0.0 if item.overfitting is None else (1.0 - item.overfitting.probability) * 100.0
            ),
            "risk": self._scale(min(calmar, 3.0) / 3.0 * 100.0),
            "sample_adequacy": self._scale(
                0.0 if track_record == float("inf") else min(pnl.size / track_record, 1.0) * 100.0
            ),
            "generalisation": self._scale(
                0.0 if item.walk_forward is None else item.walk_forward.efficiency * 100.0
            ),
        }
        score = sum(dimensions.values()) / len(dimensions)
        grade: Literal["A", "B", "C", "D", "F"] = (
            "F" if decision == "FAIL" else self._grade(score if decision == "PASS" else score * 0.6)
        )

        metrics: dict[str, float | int] = {
            "net_pnl": round(float(pnl.sum()), 2),
            "win_rate": round(float(np.mean(pnl > 0)), 6),
            "sharpe_per_trade": round(sharpe, 6),
            "sharpe_annualised": round(annualised, 4),
            "probabilistic_sharpe": round(psr, 6),
            "deflated_sharpe": round(deflated.deflated_sharpe_ratio, 6),
            "expected_max_sharpe": round(deflated.benchmark_sharpe, 6),
            "sharpe_variance": round(deflated.sharpe_variance, 6),
            "permutation_p_value": round(p_value, 6),
            "profit_factor": round(factor, 4),
            "sortino": round(sortino, 4),
            "calmar": round(calmar, 4),
            "max_drawdown": round(drawdown, 2),
            "skewness": round(deflated.skewness, 4),
            "kurtosis": round(deflated.kurtosis, 4),
            "minimum_track_record": (
                -1.0 if track_record == float("inf") else round(track_record, 1)
            ),
            "trades": int(pnl.size),
            "trial_count": item.trial_count,
            "probability_of_overfitting": (
                -1.0 if item.overfitting is None else round(item.overfitting.probability, 6)
            ),
            "walk_forward_efficiency": (
                -1.0 if item.walk_forward is None else round(item.walk_forward.efficiency, 6)
            ),
            "path_sharpe_p05": -1.0 if item.paths is None else round(item.paths.sharpe_p05, 6),
        }
        traces = tuple(
            CalculationTrace(
                trace_id=stable_id("trace", {"run": item.run_id, "metric": metric}),
                metric=metric,
                formula=self._formula(metric),
                inputs={"run_id": item.run_id, "trial_count": item.trial_count},
                value=value,
                source_tier=item.tier,
                limitations=self._limitations(metric, item, deflated),
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
    def _deflation_measurable(item: JudgeInput) -> bool:
        """A single pre-registered hypothesis needs no deflation; a search does.

        With more than one trial the hurdle depends on how spread out the trial
        Sharpes were. Assuming ``V[SR] = 1`` in per-trade units would invent a
        hurdle nothing could clear, so an unrecorded search is reported as
        unmeasurable rather than silently failed for the wrong reason.
        """
        return item.trial_count <= 1 or item.trial_sharpes is not None

    @staticmethod
    def _deflation_verdict(item: JudgeInput, deflated: DeflatedSharpe) -> bool | None:
        if not Judge._deflation_measurable(item):
            return None
        if item.trial_count <= 1:
            # Nothing was selected, so the probabilistic Sharpe against zero is
            # already the honest statistic.
            return deflated.probabilistic_sharpe_ratio >= DEFLATED_SHARPE_THRESHOLD
        return deflated.deflated_sharpe_ratio >= DEFLATED_SHARPE_THRESHOLD

    @staticmethod
    def _decide(gates: tuple[GateResult, ...]) -> Literal["PASS", "FAIL", "INCONCLUSIVE"]:
        """Any failure fails. Any missing evidence withholds the pass.

        The middle state is the one that matters: a strategy that has not been
        put through walk-forward or CSCV is not a borderline pass, it is an
        unanswered question.
        """
        if any(gate.status == "FAIL" for gate in gates):
            return "FAIL"
        if any(gate.status == "INCONCLUSIVE" for gate in gates):
            return "INCONCLUSIVE"
        return "PASS"

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
    def _evidence_gate(
        gate: str, name: str, passed: bool | None, observed: float | int | str, rule: str
    ) -> GateResult:
        if passed is None:
            return GateResult(
                gate=gate,
                name=name,
                status="INCONCLUSIVE",
                observed=observed,
                rule=rule,
                finding=f"{name} was never measured; no pass can be granted on absent evidence.",
            )
        return Judge._gate(gate, name, passed, observed, rule)

    @staticmethod
    def _scale(value: float) -> int:
        return max(0, min(100, round(value)))

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
            "sharpe_per_trade": "mean(pnl) / sample_std(pnl)",
            "sharpe_annualised": "sharpe_per_trade * sqrt(periods_per_year)",
            "probabilistic_sharpe": "Z[(SR - 0) * sqrt(T - 1) / sqrt(1 - g3*SR + (g4-1)/4*SR^2)]",
            "deflated_sharpe": "probabilistic_sharpe evaluated against expected_max_sharpe",
            "expected_max_sharpe": "sd(SR) * ((1-g)*Z^-1[1 - 1/N] + g*Z^-1[1 - 1/(N*e)])",
            "sharpe_variance": "sample variance of the per-trial Sharpe ratios",
            "permutation_p_value": "share of sign-flipped resamples with Sharpe >= observed",
            "profit_factor": "gross gains / absolute gross losses",
            "sortino": "(mean(pnl) - target) / sqrt(mean(min(pnl - target, 0)^2))",
            "calmar": "sum(pnl) / max_drawdown",
            "max_drawdown": "max(running_peak - cumulative_pnl)",
            "skewness": "mean(((pnl - mean) / sd)^3)",
            "kurtosis": "mean(((pnl - mean) / sd)^4), non-excess",
            "minimum_track_record": "1 + var_SR * (Z^-1[0.95] / SR)^2",
            "trades": "count(trade pnl)",
            "trial_count": "count(all evaluated parameter combinations)",
            "probability_of_overfitting": "share of CSCV splits where the IS winner ranks below "
            "the OOS median",
            "walk_forward_efficiency": "mean out-of-sample Sharpe / mean in-sample Sharpe",
            "path_sharpe_p05": "5th percentile Sharpe across reconstructed CPCV paths",
        }
        return formulas[metric]

    @staticmethod
    def _limitations(metric: str, item: JudgeInput, deflated: DeflatedSharpe) -> tuple[str, ...]:
        notes: list[str] = []
        if metric in {"deflated_sharpe", "expected_max_sharpe", "sharpe_variance"}:
            if item.trial_sharpes is None:
                notes.append(
                    "V[SR] assumed to be 1.0; supply per-trial Sharpes to measure it instead."
                )
            notes.append(
                f"Deflated against {deflated.trials} recorded trials. Trials run outside the "
                "ledger, including configurations discarded by hand, are invisible to this "
                "correction and make the true hurdle higher."
            )
        if metric in {"probability_of_overfitting"} and item.overfitting is None:
            notes.append("Not measured; no CSCV trial matrix was supplied.")
        if metric in {"walk_forward_efficiency"} and item.walk_forward is None:
            notes.append("Not measured; no walk-forward plan was executed.")
        if metric in {"path_sharpe_p05"} and item.paths is None:
            notes.append("Not measured; no CPCV path distribution was supplied.")
        if metric in {"sharpe_annualised"}:
            notes.append(
                f"Annualised at {item.periods_per_year} periods per year; per-trade series are "
                "not evenly spaced, so this is comparability only, not a forecast."
            )
        notes.append("Fills are modelled, not calibrated against an execution venue.")
        return tuple(notes)
